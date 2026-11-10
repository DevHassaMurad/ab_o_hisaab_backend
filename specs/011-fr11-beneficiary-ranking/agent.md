# Agent Brief: FR11 Beneficiary Ranking

## Owner role

**Backend & alerts**

## Mission

Rank households and shopkeepers for each fund release with published, deterministic, configurable rules (need, exposure, urgency, existing support), and give every beneficiary a plain-language reason in English and Urdu. Run the NFR5 fairness check before any allocation is saved, let the PDMA officer override an amount with a written reason, and own the single "save" doorway (`finalize`) that hands a ranked, priced list to payments. FR11 and FR12 together implement `AllocationService.allocate`; FR11 owns the orchestration, ranking, reasons, fairness, overrides and saving, and FR12 owns the money maths.

## Scope

**In (MVP)**
- Eligibility: active beneficiaries in the release's affected tehsils.
- Normalisation and priority score formulas (`rank-v1`) for households and shopkeepers with default weights; per-district weight overrides.
- Deterministic tie-breakers; rank 1..n within each type per release.
- Reason strings (EN + UR) for funded, unmet-need, overridden and excluded rows.
- Fairness check `fair-v1` (NFR5, UC06-7/7a) and officer acknowledgement.
- Officer override with reason (UC06-6) and reset; recompute of an unsaved list.
- `AllocationServiceImpl` orchestration: `allocate`, `finalize`, `override`, `reset_override`, `recompute`.
- REST: allocation list/detail, override, fairness, save, recompute, allocation rules (read/update).

**Out**
- Amount rules, top-ups, rounding, caps, pool filling, spillover mechanics, `UNMET_NEED` marking, allocation summary (FR12 `012-fr12-amount-rules`).
- Pool sizes and the spillover policy flag (FR9 `009-fr09-beneficiary-split`).
- Payment reservation and sending (FR17 `017-fr17-payment-tracking`); FR11 only calls its reserve entry point.
- DC approval (FR8 `008-fr08-dc-action-approval`).
- Province-level cross-district fairness (roadmap; clarify open item 2).
- LLM rephrasing of reasons (FR13, roadmap).

## Read first

1. `specs/_authoring-guide.md`
2. `.specify/memory/constitution.md` (Principles II, III, IV, V, XI)
3. `specs/000-foundation/data-model.md` §4 FundRelease/TriggerEvent, §5 Allocation/FairnessCheck/Beneficiary
4. `specs/000-foundation/plan.md` (interfaces, API conventions)
5. This folder: `spec.md`, `clarify.md`, `plan.md`
6. `specs/012-fr12-amount-rules/plan.md` (functions FR11 calls)
7. `specs/010-fr10-beneficiary-register/plan.md` (`eligible_query`, `tehsil_register_stats`)

## Depends on / Blocks

| Direction | FR | Reason |
|---|---|---|
| Depends on | FR10 `010-fr10-beneficiary-register` | Register, `eligible_query`, `tehsil_register_stats` |
| Depends on | FR12 `012-fr12-amount-rules` | `compute_targets`, `fill_release`, `refill_release`, `override_limit` (built together) |
| Depends on | FR3 `003-fr03-flood-risk-score` | Tehsil `RiskIndex` (score, tier, peak lead day) for affected tehsils and urgency |
| Depends on | FR9 `009-fr09-beneficiary-split` | `household_pool_pkr`, `shopkeeper_pool_pkr` on `FundRelease`; spillover policy |
| Depends on | FR20 `020-fr20-audit-log` | `AuditService.record` |
| Called by | FR7 `007-fr07-auto-tier-release` | `allocate()` for every new release (auto-finalises Watch/Warning) |
| Called by | FR8 `008-fr08-dc-action-approval` | `allocate()` (idempotent) to show the total; `finalize()` after approval |
| Calls | FR17 `017-fr17-payment-tracking` | Reserve entry point inside `finalize()` |
| Calls | FR14 `014-fr14-whatsapp-alerts` | `AlertService.enqueue` for the fairness-hold notice to the officer |
| Blocks | FR17, FR19, FR14 payment notices | Need saved allocations |

## Interfaces

**Consumes**
- `AuditService.record(...)`, `AlertService.enqueue(...)`, `ExplainService.explain_allocation(alloc_inputs) -> (en, ur)`, `Clock`, `ScenarioContext`
- FR10: `eligible_query(db, *, tehsil_ids, type)`, `tehsil_register_stats(db, tehsil_ids)`
- FR12: `compute_targets(db, ctx, candidates)`, `fill_release(...)`, `refill_release(...)`, `override_limit(...)`, `amount_rules(fund)`
- FR17: reserve entry point (expected `PaymentService.reserve_for_release(db, fund_release)`; confirm name in `017-fr17-payment-tracking/plan.md`)

**Provides**
- `AllocationService.allocate(db, fund_release) -> list[Allocation]` (foundation Protocol), implemented by `AllocationServiceImpl` in `app/services/allocation/service.py`
- `AllocationServiceImpl.finalize(db, fund_release, *, actor, acknowledge_fairness=False, note=None) -> FinalizeResult`
- `AllocationServiceImpl.override(...)`, `.reset_override(...)`, `.recompute(...)`
- `render_allocation_reason(inputs) -> tuple[str, str]` in `app/services/explain/allocation_templates.py` (FR3's `ExplainService.explain_allocation` delegates to it)
- REST routes listed in plan.md

## Files you own

```text
app/services/allocation/__init__.py
app/services/allocation/service.py          # AllocationServiceImpl (orchestrator)
app/services/allocation/context.py          # AllocationContext builder (fund, event, tier, tehsils, urgency, stats)
app/services/allocation/rules.py            # RankingWeights, defaults, per-district overrides, rules_version
app/services/allocation/ranking.py          # load_eligible, normalise_*, priority_score, rank
app/services/allocation/fairness.py         # run_fairness_check
app/services/allocation/overrides.py        # validate_override_reason, apply_override
app/services/allocation/reasons.py          # build_reasons (calls ExplainService)
app/services/explain/allocation_templates.py
app/api/v1/allocations.py                   # router (FR12 adds the summary route in a marked section)
app/schemas/allocations.py                  # FR12 adds AllocationSummaryOut, AmountRulesOut
alembic/versions/0011_fr11_ranking_fairness.py
tests/unit/allocation/test_normalise.py
tests/unit/allocation/test_ranking.py
tests/unit/allocation/test_fairness.py
tests/unit/allocation/test_reasons.py
tests/integration/allocation/test_allocate_flow.py
tests/integration/allocation/test_override.py
tests/integration/allocation/test_finalize_gate.py
tests/contract/test_allocations_api.py
tests/contract/test_allocation_rules_api.py
```

## Guardrails

| Principle | How it applies |
|---|---|
| II Human authority | `finalize()` refuses an `ACTION` release unless its `Approval.decision == APPROVED` (409 `APPROVAL_REQUIRED`). No override, recompute or replay path skips this. Donors cannot read or change allocations |
| III Traceability | `ALLOCATION_COMPUTED`, `ALLOCATION_OVERRIDDEN`, `ALLOCATION_OVERRIDE_RESET`, `ALLOCATION_RECOMPUTED`, `FAIRNESS_ACKNOWLEDGED`, `ALLOCATION_SAVED`, `RANKING_RULES_UPDATED` in the same transaction |
| IV Explainable | Every `Allocation.score_inputs` stores raw inputs, normalised factors, weights, contributions, imputations, rules version, tie-break values; reasons rendered from them |
| V Fair and transparent | Published rules endpoint; deterministic ranking (same inputs → same order); written override reasons; fairness check before every save with officer acknowledgement |
| VI Privacy | Allocation endpoints are 403 for donors; reasons contain no phone/CNIC; override reasons are rejected if they look like a phone or CNIC |
| IX Config over code | Weights, norms and fairness thresholds in settings with per-district override in `DisasterFund.ranking_weights` |
| XI Tests first | Ranking, fairness, override and the approval gate are money paths: tests are written first and must fail first |

## Definition of Done

- [ ] All FR11 tests written first, seen failing, now passing (unit, integration, contract)
- [ ] Worked examples in `implement.md` reproduce exactly (A=81.23, B=29.77, S1=78.50, S2=57.10)
- [ ] Shuffling the input order never changes the ranking (property test, 200 seeded shuffles)
- [ ] `allocate()` is idempotent and auto-finalises Watch/Warning only when fairness passes
- [ ] `finalize()` blocks Action without approval; holds on unacknowledged fairness failure; is idempotent
- [ ] Override validates reason and amount, re-fills via FR12, re-runs fairness, audits, and is blocked after save
- [ ] EN and UR reasons present on every row; Urdu reviewed by a native speaker
- [ ] Migration 0011 applied; proposed fields merged into data-model.md
- [ ] Donor 403 on all allocation routes; DC read-only own district
- [ ] ruff and mypy strict clean on `app/services/allocation/`

## Hand-off notes

- **FR7**: call `AllocationService.allocate(db, release)` right after creating any `FundRelease` (all tiers). For Watch/Warning it also finalises and reserves payments when fairness passes; do not call FR17 yourself. The return value is the list; check `release.allocation_saved_at` to know if payments started.
- **FR8**: show `GET /fund-releases/{id}/allocations/summary` (FR12) and `GET /fund-releases/{id}/fairness` on the approval screen. After an `APPROVED` decision, call `AllocationServiceImpl.finalize(db, release, actor=<dc user id>)`. If it returns `HELD`, the officer must acknowledge fairness; payments start then.
- **FR17**: expose an idempotent `reserve_for_release(db, fund_release)` that creates one `Payment` per `FUNDED` allocation with `final_amount_pkr > 0`, in the caller's transaction. `UNMET_NEED` and `EXCLUDED` rows never get payments.
- **FR19**: "allocated" = Σ `final_amount_pkr` of `FUNDED` rows of saved releases; "unmet need" = Σ `final_amount_pkr` of `UNMET_NEED` rows. `EXCLUDED` rows count in neither.
- **FR5**: replay runs auto-acknowledge a failed fairness check as simulated when `REPLAY_AUTO_ACK_FAIRNESS=true` (actor `replay-sim`), mirroring the simulated DC approval.
- **FR3**: `ExplainService.explain_allocation` should delegate to `render_allocation_reason` from `app/services/explain/allocation_templates.py`.
- **FR6**: per-district ranking weights are edited through FR11's `PUT /allocation-rules/{district_id}`; FR6's settings screen may link to it.

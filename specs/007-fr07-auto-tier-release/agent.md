# Agent Brief: FR7 Automatic Tier Release

## Owner role
Backend & alerts

## Mission
Implement `TriggerEngine.evaluate`: each day, turn a district's `RiskIndex` into tier firings under decision D4. It manages the `TriggerEvent` lifecycle, fires every missing tier up to today's tier exactly once per event, computes the incremental release amount, and starts the release pipeline: Watch and Warning go straight to allocation, payments and alerts; Action is created as `PENDING_APPROVAL` and handed to FR8. The DC is alerted on every tier change within 5 minutes.

## Scope
**In (MVP)**
- `DefaultTriggerEngine.evaluate(db, district_id, risk_index)` replacing the foundation no-op.
- TriggerEvent open / evaluate / close (14 consecutive days below Watch, configurable, D4).
- Incremental amount maths with cumulative rounding and a cap at the money available.
- FundRelease state machine and the release pipeline (`run_release_pipeline`, `complete_release`).
- DC `TIER_ALERT` on tier change and beneficiary `TIER_ALERT` per allocation.
- Idempotent same-day re-runs.
- Read endpoints for events and releases; an admin manual evaluate; a resume endpoint for stalled releases.

**Out**
- Score and tier production (FR3 `003-fr03-flood-risk-score`). Threshold logic comes from FR6 `tiers.py`.
- Approval OTP / WhatsApp / escalation (FR8 `008-fr08-dc-action-approval`).
- Pool split maths (FR9). Ranking and amounts (FR11 `011-fr11-beneficiary-ranking`, FR12 `012-fr12-amount-rules`). Payment state machine (FR17 `017-fr17-payment-tracking`). Message rendering and sending (FR14 `014-fr14-whatsapp-alerts`).
- Satellite confirmation of triggers (FR21, roadmap; `confirmation_status` stays `PENDING`).
- Clawback when scores fall (never, D4).

## Read first
- `specs/_authoring-guide.md`
- `.specify/memory/constitution.md` (Principles I, II, III, VIII, IX, XI; D3, D4, D5)
- `specs/000-foundation/data-model.md` §4 (TriggerEvent, FundRelease, Approval), §5 (Allocation, FairnessCheck), §7 (Alert)
- `specs/000-foundation/plan.md` (interfaces table)
- `specs/006-fr06-fund-tier-config/plan.md` (`TierConfig`, `tier_for_score`)
- `specs/008-fr08-dc-action-approval/plan.md` (`request_approval`)
- `specs/009-fr09-beneficiary-split/plan.md` (`split_pools`)

## Depends on / Blocks
| | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | Models, `Clock`, `ScenarioContext`, interfaces |
| Depends on | FR20 | `AuditService.record` (stub OK) |
| Depends on | FR6 | `load_tier_config`, `tier_for_score` |
| Depends on | FR9 | `split_pools` for the pools on each release |
| Depends on (interface only) | FR8, FR11/12, FR14, FR17 | Called through `request_approval`, `AllocationService.allocate`, `AlertService.enqueue`, `dispatch_release_payments`; stubs until they land |
| Blocks | FR3 | UC04-6 calls `TriggerEngine.evaluate` |
| Blocks | FR5 | Backtests assert firings (NFR3) |
| Blocks | FR8 | Needs `PENDING_APPROVAL` releases and `run_release_pipeline` after approval |
| Blocks | FR11/12, FR17, FR19 | Need FundRelease rows |

## Interfaces
**Consumes**
- `AuditService.record(...)`, `AlertService.enqueue(db, *, recipient, purpose, template_key, context, related)`
- `AllocationService.allocate(db, fund_release) -> list[Allocation]` (FR11+FR12)
- FR8 `request_approval(db, release) -> Approval`
- FR9 `split_pools(amount_pkr, household_share_pct) -> PoolSplit`
- FR17 `dispatch_release_payments(db, release, allocations) -> None` (`app/services/payments/dispatch.py`; one call site, adapt if FR17 names it differently)
- `Clock.now()/today()`, `current_scenario()`

**Provides**
- `TriggerEngine.evaluate(db, district_id, risk_index) -> list[FundRelease]` (foundation Protocol)
- `run_release_pipeline(db, release, actor="system") -> FundRelease` (FR8 calls it after approval)
- `complete_release(db, release, allocations, actor) -> FundRelease` (FR11 calls it after a fairness acknowledgement)
- `transition_release(db, release, to_status, actor, note) -> None`
- `available_to_release(db, fund) -> Decimal`, `planned_tier_amounts(allocated_pkr, config) -> dict[TierName, Decimal]` (FR9 preview)
- HTTP: see plan.md

## Files you own
- `app/services/triggers/engine.py`
- `app/services/triggers/events.py`
- `app/services/triggers/amounts.py`
- `app/services/triggers/release_pipeline.py`
- `app/api/v1/releases.py` (FR9 adds one route, `/releases/{id}/pools`)
- `app/schemas/releases.py`
- `alembic/versions/0007_fr07_trigger_event_state.py` (after the proposed fields are merged)
- `tests/unit/triggers/test_amounts.py`
- `tests/unit/triggers/test_release_transitions.py`
- `tests/integration/triggers/test_engine.py`
- `tests/integration/triggers/test_engine_idempotency.py`
- `tests/integration/triggers/test_event_lifecycle.py`
- `tests/integration/triggers/test_release_pipeline.py`
- `tests/integration/triggers/test_releases_api.py`
- `tests/contract/test_releases_contract.py`

## Guardrails
| Principle | How it applies |
|---|---|
| I Anticipate | No human step for Watch/Warning; firing and alert queueing happen in the same call as scoring |
| II Human authority | Action is created `PENDING_APPROVAL`. `run_release_pipeline` raises `ApprovalRequired` unless status is `ALLOCATING`/`APPROVED`, and a test proves no Allocation is ever created for an unapproved Action release |
| III Traceability | `TRIGGER_EVENT_OPENED`, `TIER_FIRED`, `TIER_CHANGED`, `RELEASE_STATUS_CHANGED`, `TRIGGER_EVENT_CLOSED` audited in the same transaction |
| IV Explainable | `FundRelease.amount_calc` stores the cumulative maths, cap and inputs |
| VIII Honest | `FundRelease.simulated=true` in replay or with the simulated rail |
| IX Config | Thresholds, increments, close days, purposes all from config |
| XI Tests | Tier evaluation and incremental maths are mandatory test-first |

## Definition of Done
- [ ] All `tasks.md` items done; tests written first and seen failing.
- [ ] Incremental maths tests: 10/30/60 of PKR 50 M → 5 M / 15 M / 30 M; odd amounts sum exactly; cap at available.
- [ ] Jump NONE→ACTION fires 3 releases, Action `PENDING_APPROVAL`, approval requested, no Action allocations.
- [ ] Same-day re-run creates no new release, alert or audit entry.
- [ ] Event closes after `event_close_days` below Watch, never while an approval is pending; no clawback.
- [ ] DC alert queued in the same transaction as the firing (NFR2).
- [ ] FR5 backtests: sindh-2022 and buner-2025 produce ≥ 1 release; control-2021 produces none.
- [ ] `ruff`, `mypy --strict app/services/triggers` clean.

## Hand-off notes
- FR3: call `engine.evaluate(db, district.id, risk_index)` once per district per run, after the `RiskIndex` row is flushed. Re-calling is safe.
- FR8: after approval, call `run_release_pipeline(db, release, actor=str(approver.id))`.
- FR11: if a `FairnessCheck` is failing and unacknowledged, `run_release_pipeline` stops at `ALLOCATING`. When the officer acknowledges and saves, call `complete_release(db, release, allocations, actor)`.
- FR12: read pools from the release; call FR9 for spillover.
- FR14: templates needed: `tier_change_dc`, `tier_alert_watch`, `tier_alert_warning`, `tier_alert_action` (Urdu first).
- FR19: `FundRelease.amount_pkr` is the released tranche; unused money remains in `DisasterFund.remaining_pkr`.

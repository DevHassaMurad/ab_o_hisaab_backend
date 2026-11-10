# Agent Brief: FR12 Amount Rules

## Owner role

**Backend & alerts**

## Mission

Decide **how much** each ranked beneficiary gets in a fund release: a flat amount per tier for households and a capped share of stock at risk for shopkeepers, rounded to PKR 100. Fund them in rank order until each pool runs out, mark the rest `UNMET_NEED`, move leftovers between pools per FR9's policy, and handle repeated tiers in one event as top-ups so nobody is paid twice for the same need. FR12 owns the money maths inside `AllocationService.allocate`; FR11 owns the orchestration and ranking.

## Scope

**In (MVP)**
- `amount-v1` rules: household flat-by-tier target, shopkeeper `min(stock × stock_pct, cap)`, nearest-PKR-100 rounding (half up).
- Repeated tiers within one `TriggerEvent`: households top up to the current tier's target; shopkeepers get one capped grant per event (top up only if earlier funding was lower).
- `fill-v1`: strict rank-order fill per pool, `UNMET_NEED` for the rest, one deterministic spillover round between pools, pinned overrides.
- Override limits for FR11's override flow and re-fill after an override.
- Allocation summary endpoint and the `amounts` section of the published rules.
- Proposed `EXCLUDED` value for `Allocation.status`.

**Out**
- Ranking, reasons, fairness, override validation and saving (FR11 `011-fr11-beneficiary-ranking`).
- Editing the amounts, cap and stock % (FR6 `006-fr06-fund-tier-config`).
- Pool sizes, pool rounding and the spillover on/off policy (FR9 `009-fr09-beneficiary-split`).
- Reserving and sending payments (FR17 `017-fr17-payment-tracking`).

## Read first

1. `specs/_authoring-guide.md` (FR12 key facts)
2. `.specify/memory/constitution.md` (Principles II, III, IV, IX, XI; D4)
3. `specs/000-foundation/data-model.md` §4 `DisasterFund`, `FundRelease`, `TriggerEvent`; §5 `Allocation`; §6 `Payment`
4. This folder: `spec.md`, `clarify.md`, `plan.md`
5. `specs/011-fr11-beneficiary-ranking/plan.md` (ownership split table, call sites)
6. `specs/009-fr09-beneficiary-split/plan.md` (pool fields and spillover setting name)

## Depends on / Blocks

| Direction | FR | Reason |
|---|---|---|
| Depends on | FR6 `006-fr06-fund-tier-config` | `household_amount_by_tier`, `shopkeeper_cap_pkr`, `shopkeeper_stock_pct` on `DisasterFund` |
| Depends on | FR9 `009-fr09-beneficiary-split` | `household_pool_pkr`, `shopkeeper_pool_pkr`; spillover policy |
| Depends on | FR10 `010-fr10-beneficiary-register` | `stock_value_pkr` |
| Built with | FR11 `011-fr11-beneficiary-ranking` | FR11 calls FR12 functions inside `allocate` and `override` |
| Reads | FR17 `017-fr17-payment-tracking` | `Payment.status` of earlier tiers (CANCELLED excluded from prior amounts) |
| Depends on | FR7 `007-fr07-auto-tier-release` | Releases of skipped tiers are allocated in tier order on the same day |
| Blocks | FR17, FR19, FR8 | Final amounts, statuses, totals |

## Interfaces

**Consumes**
- `DisasterFund`, `FundRelease`, `TriggerEvent`, `Allocation`, `Payment` (read)
- FR9 spillover policy (expected `fund.pool_spillover_enabled` or a settings fallback `ALLOCATION_POOL_SPILLOVER=true`; confirm the name in `009-fr09-beneficiary-split/plan.md`)

**Provides** (all in `app/services/allocation/`)
- `amounts.round_pkr(amount: Decimal, step: Decimal = Decimal("100")) -> Decimal`
- `amounts.household_target(fund, tier) -> Decimal`
- `amounts.shopkeeper_target(fund, stock_value_pkr) -> Decimal`
- `amounts.prior_event_amounts(db, fund_release, beneficiary_ids) -> dict[UUID, Decimal]`
- `amounts.compute_targets(db, ctx, candidates) -> dict[UUID, Target]`
- `amounts.override_limit(db, fund_release, allocation) -> Decimal`
- `amounts.amount_rules(fund) -> AmountRulesOut`
- `fill.fill_pool(rows, pool_pkr, *, pinned_pkr, mode) -> PoolFill`
- `fill.fill_release(households, shopkeepers, *, household_pool, shopkeeper_pool, spillover, mode) -> ReleaseFill`
- `fill.refill_release(db, fund_release) -> ReleaseFill`
- `summary.release_summary(db, fund_release) -> AllocationSummaryOut`
- REST: `GET /api/v1/fund-releases/{release_id}/allocations/summary`

## Files you own

```text
app/services/allocation/amounts.py
app/services/allocation/fill.py
app/services/allocation/summary.py
app/api/v1/allocations.py              # only the "# --- FR12 ---" section (summary route)
app/schemas/allocations.py             # only AllocationSummaryOut, PoolSummaryOut, AmountRulesOut
alembic/versions/0012_fr12_amount_status.py
tests/unit/allocation/test_amounts.py
tests/unit/allocation/test_fill.py
tests/unit/allocation/test_topup.py
tests/integration/allocation/test_budget_short.py
tests/integration/allocation/test_repeated_tiers.py
tests/contract/test_allocation_summary_api.py
```

## Guardrails

| Principle | How it applies |
|---|---|
| II Human authority | FR12 never saves or pays; Action amounts are only drafts until FR11 `finalize` after DC approval |
| III Traceability | FR12 writes no audit rows itself; its outputs are inside FR11's `ALLOCATION_COMPUTED` / `ALLOCATION_OVERRIDDEN` payloads |
| IV Explainable | `score_inputs.amount` (rule, tier, target, prior, top-up, stock, pct, cap, rounding) and `score_inputs.fill` (pool, remaining before, spillover) on every row |
| IX Config over code | Amounts, cap, stock % from `DisasterFund`; rounding step, fill mode and repeated-tier modes in settings |
| Money | `Decimal` only, never float; `NUMERIC(14,2)` |
| XI Tests first | Amount calculation, caps and the budget-short path are explicitly listed money paths |

## Definition of Done

- [ ] All FR12 tests written first, seen failing, now passing
- [ ] Worked examples in `implement.md` reproduce exactly (rounding, cap, fill with spillover, top-ups)
- [ ] Invariants hold on 500 seeded random releases: Σ funded per pool ≤ pool + spillover in; no unpinned `FUNDED` row ranks below an `UNMET_NEED` row in the same pool pass; every amount is a multiple of 100
- [ ] Repeated-tier test: household paid 3 000 / 4 000 / 8 000 across Watch/Warning/Action; shopkeeper paid once
- [ ] Migration 0012 (`EXCLUDED`) applied; foundation owner merged it into data-model.md
- [ ] Summary endpoint contract test passes; donor 403
- [ ] mypy strict clean

## Hand-off notes

- **FR6**: please validate `household_amount_by_tier` values as non-negative multiples of 100 and non-decreasing WATCH ≤ WARNING ≤ ACTION. FR12 rounds defensively and floors top-ups at 0, but a decreasing table silently pays nothing at higher tiers.
- **FR7**: when a score jumps several tiers, call `allocate` for the releases in tier order (WATCH, WARNING, ACTION) in the same transaction, so prior amounts are seen.
- **FR9**: FR12 implements the spillover mechanics (one round, household → shopkeeper first, then shopkeeper → household) and reads your on/off setting. Unspent money is never reserved; it stays in the fund.
- **FR17**: create payments only for `status == FUNDED and final_amount_pkr > 0`. A beneficiary may get two payments on one day when tiers fire together (one per release); that is intended.
- **FR19**: per event, "unmet need" should use the **latest saved release** of the event, not the sum over releases, because a household unmet at Watch is re-ranked at Warning. Per release, unmet need = Σ `final_amount_pkr` of `UNMET_NEED` rows.

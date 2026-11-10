# Agent Brief: FR9 Household and Shopkeeper Split

## Owner role
Backend & alerts

## Mission
Let the PDMA officer set what share of each release goes to households and what share to shopkeepers (70/30 by default, any whole number 0–100), and turn each `FundRelease` into two exact pools. When one pool is not fully used and the other type still has unmet need, move the leftover across within the same release; whatever is still unused stays in the district fund. Every rupee of the split is recorded and explainable.

## Scope
**In (MVP)**
- `validate_split()` used by FR6's settings form.
- `split_pools()` with the rounding rule; called by FR7 when a tier fires.
- `plan_spillover()` (pure) and `record_split_outcome()` (persist + audit), called by FR12 around its funding passes.
- `DisasterFund.pool_spillover_enabled` setting (proposed field), edited through FR6's form.
- Read endpoints: pool breakdown per release; split preview per district.

**Out**
- Saving the settings form (FR6 owns the endpoint and audit).
- Ranking and per-beneficiary amounts, rounding to PKR 100, `UNMET_NEED` marking (FR11 `011-fr11-beneficiary-ranking`, FR12 `012-fr12-amount-rules`).
- Moving money between releases or between districts (never).
- Per-tehsil sub-pools (roadmap).

## Read first
- `specs/_authoring-guide.md`
- `.specify/memory/constitution.md` (Principles IV, IX, XI; D5)
- `specs/000-foundation/data-model.md` §4 (DisasterFund, FundRelease), §5 (Allocation)
- `specs/006-fr06-fund-tier-config/plan.md` (settings form, validation table)
- `specs/007-fr07-auto-tier-release/plan.md` (`_fire_tier`, `planned_tier_amounts`)
- `specs/012-fr12-amount-rules/plan.md` once written (funding passes)

## Depends on / Blocks
| | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | Models, `require_role`, `AuditService` |
| Depends on | FR7 | `planned_tier_amounts` for the preview (read-only) |
| Blocks | FR6 | `validate_split` in the settings validation |
| Blocks | FR7 | `split_pools` sets the release pools |
| Blocks | FR12 | Pools, `plan_spillover`, `record_split_outcome` |
| Blocks | FR19 | `split_details.returned_to_fund_pkr` feeds "unused" per release |

## Interfaces
**Consumes**: `AuditService.record`, FR7 `planned_tier_amounts(allocated_pkr, config)`, FR6 `load_tier_config`.

**Provides** (`app/services/triggers/split.py`)
- `validate_split(household_share_pct: object) -> list[FieldError]`
- `split_pools(amount_pkr: Decimal, household_share_pct: int) -> PoolSplit`
- `plan_spillover(pools: PoolSplit, usage: PoolUsage, *, enabled: bool) -> SpilloverPlan`
- `record_split_outcome(db, release: FundRelease, pools: PoolSplit, plan: SpilloverPlan, final_usage: PoolUsage, actor: str) -> dict`
- HTTP: `GET /api/v1/releases/{release_id}/pools`, `GET /api/v1/districts/{district_id}/fund-settings/split-preview`

## Files you own
- `app/services/triggers/split.py`
- `app/schemas/split.py`
- Route `GET /releases/{release_id}/pools` in `app/api/v1/releases.py` (file owned by FR7; FR9 adds this one route)
- Route `GET /districts/{district_id}/fund-settings/split-preview` in `app/api/v1/settings.py` (file owned by FR6; FR9 adds this one route)
- `alembic/versions/0009_fr09_split_fields.py`
- `tests/unit/triggers/test_split_pools.py`
- `tests/unit/triggers/test_spillover.py`
- `tests/integration/triggers/test_split_outcome.py`
- `tests/integration/triggers/test_split_api.py`
- `tests/contract/test_split_contract.py`

## Guardrails
| Principle | How it applies |
|---|---|
| III Traceability | `record_split_outcome` audits `POOL_SPLIT_RECORDED` in the same transaction |
| IV Explainable | `FundRelease.split_details` holds the share, rounding, moves, use and money returned |
| V Fairness | Spillover never crosses into a type whose share is 0%; FR11's fairness check runs on the final allocation |
| IX Config | Share and spillover switch are fund settings; no constants in code except bounds |
| XI Tests | Pool maths is part of the release maths: property tests first |

## Definition of Done
- [ ] Tests first; all `tasks.md` items done.
- [ ] Property test: `household + shopkeeper == amount` for 10,000 random amounts and shares.
- [ ] Spillover tests cover both directions, both at once, disabled, 0%/100% shares and no unmet need.
- [ ] `record_split_outcome` writes `split_details` and one audit entry; a re-record after an override replaces it and audits again.
- [ ] Endpoints match plan.md; DC scope enforced; donors 403.
- [ ] `ruff`, `mypy --strict app/services/triggers` clean.

## Hand-off notes
- **FR6**: call `validate_split(body.household_share_pct)` inside `validate_fund_settings`; the form also edits `pool_spillover_enabled`.
- **FR7**: in `_fire_tier`, `pools = split_pools(calc.amount_pkr, fund.household_share_pct)`; store `pools.household_pkr`, `pools.shopkeeper_pkr` and `split_details = pools.as_json()`.
- **FR12**: run pass 1 per type within its initial pool → build `PoolUsage` → `plan = plan_spillover(pools, usage, enabled=fund.pool_spillover_enabled)` → pass 2 funds more `UNMET_NEED` beneficiaries of the receiving type, in rank order, within `plan.extra_for(type)` → `record_split_outcome(...)` with the final usage. After an officer override (FR11), call `record_split_outcome` again.
- **FR19**: "unused" for a release = `split_details.returned_to_fund_pkr` (money stays in the fund).

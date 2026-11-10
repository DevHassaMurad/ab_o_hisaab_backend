# Implementation Plan: FR12 Amount Rules

**Branch**: `012-fr12-amount-rules` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/012-fr12-amount-rules/spec.md`

## Summary

FR12 is a set of pure, `Decimal`-only functions plus one read endpoint. `compute_targets` turns each eligible beneficiary into a target, prior and top-up (`amount-v1`). `fill_release` walks FR11's ranked lists in strict rank order against the FR9 pools, marks `FUNDED` / `UNMET_NEED`, and applies one spillover round (`fill-v1`). `refill_release` re-runs the fill around pinned overrides. `release_summary` totals a release. FR11's `AllocationServiceImpl` calls these inside `allocate` and `override` (see the ownership table in `011-fr11-beneficiary-ranking/plan.md`).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: stdlib `decimal` (context precision 28, `ROUND_HALF_UP`, `ROUND_FLOOR`), SQLAlchemy 2.x, FastAPI, Pydantic v2

**Storage**: PostgreSQL 16; reads `disaster_fund`, `fund_release`, `trigger_event`, `allocation`, `payment`; writes `allocation.final_amount_pkr`, `status`, `score_inputs.amount/fill` (through FR11's persist)

**Testing**: pytest; seeded random property tests with `random.Random(seed)`; Postgres integration

**Target Platform**: Linux container

**Project Type**: web-service

**Performance Goals**: targets + fill for 10 000 rows < 2 s; summary p95 < 500 ms

**Constraints**: no float on money; deterministic; one SQL query for prior amounts per release

**Scale/Scope**: ≤ 10 000 rows per release, ≤ 3 releases per event

## Constitution Check

| Principle | How satisfied | Status |
|---|---|---|
| II Human authority | No save/pay in FR12; Action amounts stay draft until FR11 `finalize` after approval | ✅ |
| III Traceability | Outputs are inside FR11's audited payloads (`ALLOCATION_COMPUTED`, `ALLOCATION_OVERRIDDEN`) with `list_sha256` | ✅ |
| IV Explainable | `score_inputs.amount` and `score_inputs.fill` on every row | ✅ |
| V Fair | Strict rank order; no skip-ahead by default | ✅ |
| IX Config over code | Table, cap, stock % from `DisasterFund`; modes and rounding step in settings | ✅ |
| XI Tests first | Amounts, caps, budget-short, top-ups, spillover tested before code | ✅ |
| D4 | Top-up within event, no clawback, unspent stays in fund | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Household tier amounts as cumulative event targets (top-up) | Matches D4 increments; avoids triple payment | Full amount every tier (pays 25 000); only highest tier pays (nothing early) |
| Shopkeeper one grant per event | Stock protection is a one-time action; cap is per shopkeeper | Tier-scaled grants (not in document) |
| Strict fill | UC06-4a wording; publicly defensible | Skip-ahead (ranks jump); partial boundary grant (amounts depend on remainders) |
| One spillover round, HH → SK then SK → HH | Deterministic, simple to explain | Iterate until stable (same result in nearly all cases, harder to explain); proportional split |
| `UNMET_NEED` rows carry their would-be amount | Unmet need can be totalled for donors | Amount 0 (loses the need figure) |
| `EXCLUDED` status for override to 0 | Not funded and not a need | `FUNDED` with 0 (confuses FR17/FR19) |
| Prior via one aggregate query per release | Fast, consistent within the transaction | Per-beneficiary queries |

## Data model slice

| Entity | Fields | Access |
|---|---|---|
| `DisasterFund` | `household_amount_by_tier`, `shopkeeper_cap_pkr`, `shopkeeper_stock_pct` | read |
| `FundRelease` | `id`, `event_id`, `tier`, `household_pool_pkr`, `shopkeeper_pool_pkr`, `status` | read |
| `TriggerEvent` | `id` | read |
| `Allocation` | `fund_release_id`, `beneficiary_id`, `rank`, `computed_amount_pkr`, `final_amount_pkr`, `status`, `overridden_by`, `score_inputs` | read/write (via FR11) |
| `Payment` | `allocation_id`, `status` | read (CANCELLED excluded from prior) |
| `Beneficiary` | `type`, `stock_value_pkr` | read |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Change | Notes |
|---|---|---|
| `Allocation.status` | add enum value **`EXCLUDED`** | Override to 0. Not funded, not unmet; no payment; excluded from donor "allocated" and "unmet need" |

No new fields. Modes are settings, not columns.

## `score_inputs` sections written by FR12

```json
{
  "amount": {
    "method": "amount-v1",
    "rule": "HOUSEHOLD_FLAT_TOPUP",
    "tier": "WARNING",
    "target_pkr": "7000",
    "prior_event_pkr": "3000",
    "topup_pkr": "4000",
    "rounding": {"step_pkr": "100", "mode": "HALF_UP"},
    "warnings": []
  },
  "fill": {
    "method": "fill-v1",
    "mode": "STRICT",
    "pool": "HOUSEHOLD",
    "pool_pkr": "4200000.00",
    "remaining_before_pkr": "1855000.00",
    "pass": 1,
    "spillover_in_pkr": "0.00",
    "pinned": false
  }
}
```

Shopkeeper `amount`: `{"rule": "SHOPKEEPER_CAPPED_EVENT", "stock_value_pkr": "61234.00", "stock_pct": 40, "raw_pkr": "24493.60", "cap_pkr": "50000", "capped": false, "target_pkr": "24500", "prior_event_pkr": "0", "topup_pkr": "24500", …}`.

## API contracts

Route in `app/api/v1/allocations.py`, section `# --- FR12 ---`. Tag **Beneficiaries**.

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/api/v1/fund-releases/{release_id}/allocations/summary` | PDMA_OFFICER, ADMIN, DC (own district; else 404) | Pool and status totals |

DONOR → 403 `FORBIDDEN_ROLE`.

**Response `200`**

```json
{
  "fund_release_id": "…",
  "tier": "WARNING",
  "saved": false,
  "simulated": true,
  "pools": {
    "household": {
      "pool_pkr": "20000.00", "spillover_in_pkr": "2000.00", "spillover_out_pkr": "6000.00",
      "funded_count": 2, "funded_pkr": "14000.00",
      "unmet_count": 1, "unmet_pkr": "7000.00",
      "excluded_count": 0,
      "already_covered_count": 0,
      "last_funded_rank": 2
    },
    "shopkeeper": {
      "pool_pkr": "100000.00", "spillover_in_pkr": "6000.00", "spillover_out_pkr": "2000.00",
      "funded_count": 3, "funded_pkr": "104000.00",
      "unmet_count": 1, "unmet_pkr": "20000.00",
      "excluded_count": 0,
      "already_covered_count": 0,
      "last_funded_rank": 3
    }
  },
  "total_pool_pkr": "120000.00",
  "total_funded_pkr": "118000.00",
  "total_unmet_pkr": "27000.00",
  "unused_pkr": "2000.00",
  "amount_rules": {"…": "AmountRulesOut"}
}
```

Errors: `404 RELEASE_NOT_FOUND`, `403 FORBIDDEN_ROLE`.

**`AmountRulesOut`** (embedded under `amounts` in FR11's `GET /allocation-rules` and in the summary):

```json
{
  "method": "amount-v1",
  "household_amount_by_tier": {"WATCH": "3000", "WARNING": "7000", "ACTION": "15000"},
  "household_tier_mode": "TOPUP_TO_TARGET",
  "shopkeeper_stock_pct": 40,
  "shopkeeper_cap_pkr": "50000.00",
  "shopkeeper_tier_mode": "EVENT_ONCE",
  "rounding": {"step_pkr": "100", "mode": "HALF_UP"},
  "fill_mode": "STRICT",
  "spillover": true
}
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `AMOUNT_ROUNDING_STEP_PKR` | `100` | |
| `ALLOCATION_FILL_MODE` | `STRICT` | `SKIP_AHEAD` reserved (clarify open item 1); not implemented in MVP beyond a guard |
| `HOUSEHOLD_TIER_MODE` | `TOPUP_TO_TARGET` | |
| `SHOPKEEPER_TIER_MODE` | `EVENT_ONCE` | |
| `ALLOCATION_POOL_SPILLOVER` | `true` | Fallback only; FR9's per-fund policy wins when present |

## Scheduled jobs

None.

## Project Structure

### Documentation (this feature)

```text
specs/012-fr12-amount-rules/
├── agent.md
├── spec.md
├── clarify.md
├── plan.md
├── tasks.md
└── implement.md
```

### Source Code (repository root)

```text
app/
├── api/v1/allocations.py                  # "# --- FR12 ---" section: get_allocation_summary
├── schemas/allocations.py                 # AllocationSummaryOut, PoolSummaryOut, AmountRulesOut
└── services/allocation/
    ├── amounts.py                         # round_pkr, household_target, shopkeeper_target, prior_event_amounts,
    │                                      # compute_targets, override_limit, amount_rules
    ├── fill.py                            # PoolFill, ReleaseFill, fill_pool, fill_release, refill_release
    └── summary.py                         # release_summary
alembic/versions/0012_fr12_amount_status.py
tests/unit/allocation/{test_amounts,test_fill,test_topup}.py
tests/integration/allocation/{test_budget_short,test_repeated_tiers}.py
tests/contract/test_allocation_summary_api.py
```

**Structure Decision**: single project; FR12 modules sit beside FR11's in `app/services/allocation/` and are pure where possible (no DB in `fill_pool`/`fill_release`).

## Complexity Tracking

None.

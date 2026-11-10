# Implementation Plan: FR9 Household and Shopkeeper Split

**Branch**: `009-fr09-beneficiary-split` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/009-fr09-beneficiary-split/spec.md`

## Summary

A small, pure maths module (`app/services/triggers/split.py`) plus two read endpoints. `validate_split` plugs into FR6's settings validation. `split_pools` gives FR7 exact pools at firing time. `plan_spillover` (pure) and `record_split_outcome` (persists `FundRelease.split_details` + audit) give FR12 a deterministic way to move one type's unused pool to the other type's unmet need within the same release. Leftovers return to the fund simply by never being reserved.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x; `decimal`

**Storage**: PostgreSQL 16 (`disaster_fund.household_share_pct`, `pool_spillover_enabled`; `fund_release` pools and `split_details`; reads `allocation`)

**Testing**: pytest, Hypothesis (property tests), Postgres test DB

**Target Platform**: Linux container

**Project Type**: web-service library module

**Performance Goals**: pure functions < 1 ms; reads p95 < 500 ms

**Constraints**: `Decimal` only, `ROUND_HALF_UP`, exact sums, no floats

**Scale/Scope**: ≤ 3 releases per episode; ≤ 50k allocations per release

## Constitution Check

| Principle | How satisfied | Status |
|---|---|---|
| III Traceability | `POOL_SPLIT_RECORDED` audit in the same transaction as `split_details` | ✅ |
| IV Explainable | `split_details` holds every number from share to money returned | ✅ |
| V Fairness | No spill into a 0% type; FR11's fairness check runs on the final allocation | ✅ |
| VI Privacy | Aggregates only; DC scope; donors 403 | ✅ |
| IX Config | Share and switch from `DisasterFund`; defaults from seeds | ✅ |
| X Simplicity | Pure functions, no new tables | ✅ |
| XI Tests | Property tests and spillover matrix first | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Household pool rounded to whole PKR, shopkeeper = remainder | Exact sum by construction; deterministic | Round both: can be off by 1 PKR. Round to PKR 100: larger remainders in small tranches |
| Single-step, simultaneous spillover | Deterministic and easy to explain | Iterate until stable: harder to explain, tiny gains |
| Move only up to the receiver's unmet need | The leftover's remainder is visibly "returned to fund" | Move the whole leftover: inflates the effective pool with money nobody can use |
| Keep initial pools in the columns; outcome in `split_details` | Columns stay immutable firing facts | Overwrite pools: loses the original split |
| Pure `plan_spillover` + persisting `record_split_outcome` | FR12 controls its own funding passes; FR9 stays testable without DB | FR9 calling FR12: circular dependency |
| No FR9 write endpoint | One settings save path (FR6) | A separate PATCH: two audit shapes, two validations |

## Data model slice

| Entity | Read | Written |
|---|---|---|
| DisasterFund | household_share_pct, pool_spillover_enabled (proposed), allocated_pkr | — (FR6 writes) |
| TriggerTier | via `load_tier_config` (preview) | — |
| FundRelease | amount_pkr, tier, status, pools | household_pool_pkr, shopkeeper_pool_pkr (via FR7 at fire), split_details (proposed) |
| Allocation | type (via beneficiary), status, final_amount_pkr, computed_amount_pkr | — |
| AuditLog | — | `POOL_SPLIT_RECORDED` |

### Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Field | Type | Purpose |
|---|---|---|---|
| DisasterFund | `pool_spillover_enabled` | bool, default `true` | Per-district switch for moving unused pool money across types within a release |
| FundRelease | `split_details` | JSONB | Initial share and pools, rounding, moves, usage, unmet need, money returned (Principle IV) |

`split_details` shape:
```json
{
  "household_share_pct": 70,
  "shopkeeper_share_pct": 30,
  "rounding": {"rule": "household=round_half_up(amount*share/100, 1 PKR); shopkeeper=amount-household",
               "household_exact_pkr": "3500000.00"},
  "initial": {"household_pkr": "3500000.00", "shopkeeper_pkr": "1500000.00"},
  "spillover_enabled": true,
  "moves": [{"from": "HOUSEHOLD", "to": "SHOPKEEPER", "amount_pkr": "900000.00"}],
  "effective": {"household_pkr": "2600000.00", "shopkeeper_pkr": "2400000.00"},
  "used": {"household_pkr": "2000000.00", "shopkeeper_pkr": "2399800.00"},
  "unmet": {"household_pkr": "0.00", "shopkeeper_pkr": "0.00"},
  "returned_to_fund_pkr": "600200.00",
  "status": "FINAL",
  "recorded_at": "2022-08-18T02:12:40Z"
}
```
The example is a Watch release of PKR 5,000,000 at 70%. Before allocation (`PoolSplit.as_json()`, written by FR7 at firing) it holds only `household_share_pct`, `shopkeeper_share_pct`, `rounding`, `initial` and `"status": "PENDING_ALLOCATION"`.

Invariants checked in `record_split_outcome` (raise `SplitInvariantError` otherwise):
- `initial.household + initial.shopkeeper == amount_pkr`
- `effective.X == initial.X − moved_out(X) + moved_in(X)`
- `used.X ≤ effective.X`
- `used.household + used.shopkeeper + returned_to_fund == amount_pkr`

## Types (`app/services/triggers/split.py`)

```python
BeneficiaryType = Literal["HOUSEHOLD", "SHOPKEEPER"]

@dataclass(frozen=True)
class PoolSplit:
    amount_pkr: Decimal
    household_share_pct: int
    household_pkr: Decimal
    shopkeeper_pkr: Decimal
    household_exact_pkr: Decimal          # before rounding, for the record
    def pool(self, t: BeneficiaryType) -> Decimal: ...
    def as_json(self) -> dict[str, object]: ...

@dataclass(frozen=True)
class PoolUsage:
    household_used_pkr: Decimal           # Σ final_amount_pkr of FUNDED household allocations
    shopkeeper_used_pkr: Decimal
    household_unmet_pkr: Decimal          # Σ computed_amount_pkr of UNMET_NEED household allocations
    shopkeeper_unmet_pkr: Decimal

@dataclass(frozen=True)
class Move:
    from_type: BeneficiaryType
    to_type: BeneficiaryType
    amount_pkr: Decimal

@dataclass(frozen=True)
class SpilloverPlan:
    enabled: bool
    moves: tuple[Move, ...]               # 0, 1 or 2 moves
    effective_household_pkr: Decimal
    effective_shopkeeper_pkr: Decimal
    def extra_for(self, t: BeneficiaryType) -> Decimal: ...   # money moved INTO type t
```

## API contracts

All under `/api/v1`, tag **Triggers**. Roles: PDMA_OFFICER, ADMIN (all districts), DC (own district). DONOR → 403.

### GET `/releases/{release_id}/pools`
Response 200 (finished release):
```json
{
  "release_id": "r1…", "tier": "WATCH", "status": "RELEASED",
  "amount_pkr": "5000000.00",
  "household_share_pct": 70, "shopkeeper_share_pct": 30,
  "initial": {"household_pkr": "3500000.00", "shopkeeper_pkr": "1500000.00"},
  "spillover_enabled": true,
  "moves": [{"from": "HOUSEHOLD", "to": "SHOPKEEPER", "amount_pkr": "900000.00"}],
  "effective": {"household_pkr": "2600000.00", "shopkeeper_pkr": "2400000.00"},
  "used": {"household_pkr": "2000000.00", "shopkeeper_pkr": "2399800.00"},
  "unmet": {"household_pkr": "0.00", "shopkeeper_pkr": "0.00"},
  "returned_to_fund_pkr": "600200.00",
  "split_status": "FINAL",
  "simulated": true
}
```
For a release in `PENDING_APPROVAL`, `APPROVED` or still `ALLOCATING`: `moves`, `effective`, `used`, `unmet`, `returned_to_fund_pkr` are `null` and `"split_status": "PENDING_ALLOCATION"`. For `REJECTED`: `"split_status": "REJECTED"` and `returned_to_fund_pkr` equals `amount_pkr`.

Errors: 404 `RELEASE_NOT_FOUND`, 403 `DISTRICT_SCOPE_FORBIDDEN`.

### GET `/districts/{district_id}/fund-settings/split-preview?household_share_pct=60`
`household_share_pct` optional (defaults to the stored share). No write.

Response 200:
```json
{
  "district_id": "5d1c…", "allocated_pkr": "50000000.00",
  "household_share_pct": 60, "shopkeeper_share_pct": 40,
  "tiers": [
    {"tier": "WATCH",   "amount_pkr": "5000000.00",  "household_pool_pkr": "3000000.00",  "shopkeeper_pool_pkr": "2000000.00"},
    {"tier": "WARNING", "amount_pkr": "15000000.00", "household_pool_pkr": "9000000.00",  "shopkeeper_pool_pkr": "6000000.00"},
    {"tier": "ACTION",  "amount_pkr": "30000000.00", "household_pool_pkr": "18000000.00", "shopkeeper_pool_pkr": "12000000.00"}
  ],
  "totals": {"household_pkr": "30000000.00", "shopkeeper_pkr": "20000000.00"},
  "note": "Preview assumes the full fund is available; actual tranches are capped by money available (FR7)."
}
```
Errors: 422 `SPLIT_PCT_INVALID`, 404 `FUND_NOT_FOUND`, 403 `DISTRICT_SCOPE_FORBIDDEN`.

### Validation contribution to FR6 PUT

| Field | Rule | Code |
|---|---|---|
| `household_share_pct` | integer (not bool, not float), 0 ≤ x ≤ 100 | `SPLIT_PCT_INVALID` |
| `pool_spillover_enabled` | boolean | Pydantic type error mapped to `SPILLOVER_FLAG_INVALID` |

### Audit entry

`event_type="POOL_SPLIT_RECORDED"`, `entity_type="FundRelease"`, `entity_id=release.id`, `actor` = FR12's actor (`system` or officer id), payload = the `split_details` JSON plus `"release_tier"` and `"recorded_reason": "ALLOCATION_SAVED" | "OVERRIDE"`.

## Configuration

| Key | Source | Default |
|---|---|---|
| `fund_defaults.household_share_pct` | `data/seeds/funds.yaml` | 70 |
| `fund_defaults.pool_spillover_enabled` | same | true |

## Project Structure

### Documentation (this feature)

```text
specs/009-fr09-beneficiary-split/
├── agent.md  spec.md  clarify.md  plan.md  tasks.md  implement.md
```

### Source Code (repository root)

```text
app/
├── services/triggers/split.py             # validate_split, split_pools, plan_spillover, record_split_outcome, usage_from_allocations
├── schemas/split.py                       # PoolBreakdownOut, SplitPreviewOut
├── api/v1/releases.py                     # + GET /releases/{id}/pools (FR7's file)
└── api/v1/settings.py                     # + GET /districts/{id}/fund-settings/split-preview (FR6's file)
alembic/versions/0009_fr09_split_fields.py # pool_spillover_enabled, split_details
tests/
├── unit/triggers/test_split_pools.py
├── unit/triggers/test_spillover.py
├── integration/triggers/test_split_outcome.py
├── integration/triggers/test_split_api.py
└── contract/test_split_contract.py
```

**Structure Decision**: single project, constitution layout; two routes added to routers owned by FR6/FR7 to keep the API grouped by resource.

## Complexity Tracking

None.

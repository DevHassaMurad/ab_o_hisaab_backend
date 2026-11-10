# Implementation Plan: FR6 Fund and Tier Configuration

**Branch**: `006-fr06-fund-tier-config` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/006-fr06-fund-tier-config/spec.md`

## Summary

FR6 provides a settings API for each district's `DisasterFund` and its three `TriggerTier` rows. A single PUT validates the whole form (all errors at once), then updates the fund and tiers under optimistic locking and records one `FUND_SETTINGS_UPDATED` audit entry in the same transaction. FR6 also owns the shared tier helpers (`TierConfig`, `tier_for_score`) that FR3 and FR7 use, so threshold logic lives in one place.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic

**Storage**: PostgreSQL 16 (`disaster_fund`, `trigger_tier`, `audit_log`)

**Testing**: pytest, httpx TestClient, factory fixtures, Postgres test DB (transaction rollback per test)

**Target Platform**: Linux container; Windows/macOS dev

**Project Type**: web-service

**Performance Goals**: GET p95 < 500 ms (NFR1); PUT < 300 ms excluding the audit lock wait

**Constraints**: Money is `Decimal` (`NUMERIC(14,2)`) and serialised as a string. UTC. Atomic save with audit. No hard-coded districts or amounts.

**Scale/Scope**: 68 pilot districts (Sindh 30, KP 38) × 1 fund × 3 tiers, per scenario

## Constitution Check

| Principle | How satisfied | Status |
|---|---|---|
| II Human authority | `requires_approval` cannot be written (422 `REQUIRES_APPROVAL_LOCKED`). It is set only at fund creation: `ACTION=true`, others `false`. Test `test_requires_approval_locked`. | ✅ |
| III Traceability | `update_settings` calls `AuditService.record` before commit, in the same session. Failure rolls everything back. | ✅ |
| IV Explainable | Audit payload holds before/after values, so every amount can be traced to the settings in force. | ✅ |
| VI Privacy | `require_role(PDMA_OFFICER)` for writes; DC district scope on reads; DONOR 403. No PII. | ✅ |
| VIII Honest prototype | Responses carry `scenario_id`; replay funds are separate rows. | ✅ |
| IX Config over code | Defaults from `data/seeds/funds.yaml` → `settings.fund_defaults`. | ✅ |
| X Simplicity | One router, one service, no new tables. | ✅ |
| XI Tests first | Validation, tier mapping, atomicity and audit tests are written before code. | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Validate in a service function, not with Pydantic field constraints | Gives stable domain error codes and collects all errors at once | Pydantic `conint` errors: generic messages, one shape, hard to map to codes |
| One PUT for the whole form | UC01 is a single screen; atomic save of fund + tiers | PATCH per field: partial states could break the sum-to-100 invariant |
| Optimistic locking on `DisasterFund.version` | Cheap, already in the data model | Row locks held across a UI session |
| `tier_for_score` lives in FR6 | FR3 (RiskIndex.tier) and FR7 (firing) must agree exactly | Each FR computing tiers itself |
| Copy returns a draft (GET), no write | UC01-1a says "copies, then edits"; nothing should be saved before review | POST copy that saves immediately: bypasses review |
| No-op detection | Avoids empty audit entries and version churn | Always bump version |

## Data model slice

| Entity | Fields read | Fields written |
|---|---|---|
| District | id, name, province, is_active | — |
| DisasterFund | all | allocated_pkr, remaining_pkr (recomputed), household_share_pct, household_amount_by_tier, shopkeeper_cap_pkr, shopkeeper_stock_pct, event_close_days, version, pool_spillover_enabled (FR9 proposal), scenario_id (on create) |
| TriggerTier | all | min_score, release_pct (requires_approval only at creation) |
| TriggerEvent | status, highest_tier (to report `open_event`) | — |
| AuditLog | — | via `AuditService.record` |

`remaining_pkr` is recomputed as `allocated − reserved − disbursed` whenever `allocated_pkr` changes.

### Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

None for FR6. The form also edits `DisasterFund.pool_spillover_enabled`, which FR9 proposes in `009-fr09-beneficiary-split/plan.md`. Until it is merged, FR6 omits that field.

## Validation rules

All checks run; errors are collected in order of the table.

| # | Field | Rule | Code |
|---|---|---|---|
| 1 | body | `requires_approval` present on any tier | `REQUIRES_APPROVAL_LOCKED` |
| 2 | `tiers` | exactly one each of WATCH, WARNING, ACTION | `TIER_SET_INVALID` |
| 3 | `tiers[].min_score` | integer 1–100 | `TIER_THRESHOLD_RANGE_INVALID` |
| 4 | `tiers[].min_score` | WATCH < WARNING < ACTION | `TIER_THRESHOLD_ORDER_INVALID` |
| 5 | `tiers[].release_pct` | integer 0–100 | `TIER_PCT_RANGE_INVALID` |
| 6 | `tiers[].release_pct` | sum = 100 | `TIER_PCT_SUM_INVALID` |
| 7 | `allocated_pkr` | ≥ 0, ≤ 999,999,999,999.99, ≤ 2 decimal places | `FUND_AMOUNT_INVALID` |
| 8 | `allocated_pkr` | ≥ reserved_pkr + disbursed_pkr | `FUND_BELOW_COMMITTED` |
| 9 | `household_share_pct` | integer 0–100 (FR9 `validate_split`) | `SPLIT_PCT_INVALID` |
| 10 | `shopkeeper_cap_pkr` | ≥ 0, ≤ 2 dp | `SHOPKEEPER_CAP_INVALID` |
| 11 | `shopkeeper_stock_pct` | integer 0–100 | `STOCK_PCT_INVALID` |
| 12 | `household_amount_by_tier` | keys exactly WATCH, WARNING, ACTION; values ≥ 0, ≤ 2 dp | `HOUSEHOLD_AMOUNTS_INVALID` |
| 13 | `event_close_days` | integer 1–60 | `EVENT_CLOSE_DAYS_INVALID` |
| 14 | `version` | equals stored version (checked after 1–13) | `VERSION_CONFLICT` (409) |

Rules 3 and 5 skip the order and sum checks (4, 6) when a value is not an integer, to avoid misleading duplicates.

## API contracts

All under `/api/v1`, tag **Triggers**. Errors use problem JSON (foundation plan).

### GET `/districts/{district_id}/fund-settings`
Roles: PDMA_OFFICER, ADMIN (any district); DC (own district). DONOR → 403.

Response 200:
```json
{
  "district_id": "5d1c…",
  "district_name": "Dadu",
  "fund_id": "a0e2…",
  "scenario_id": null,
  "allocated_pkr": "50000000.00",
  "reserved_pkr": "0.00",
  "disbursed_pkr": "0.00",
  "remaining_pkr": "50000000.00",
  "tiers": [
    {"name": "WATCH",   "min_score": 40, "release_pct": 10, "cumulative_pct": 10,  "requires_approval": false},
    {"name": "WARNING", "min_score": 60, "release_pct": 30, "cumulative_pct": 40,  "requires_approval": false},
    {"name": "ACTION",  "min_score": 80, "release_pct": 60, "cumulative_pct": 100, "requires_approval": true}
  ],
  "household_share_pct": 70,
  "shopkeeper_share_pct": 30,
  "pool_spillover_enabled": true,
  "shopkeeper_cap_pkr": "50000.00",
  "shopkeeper_stock_pct": 40,
  "household_amount_by_tier": {"WATCH": "3000.00", "WARNING": "7000.00", "ACTION": "15000.00"},
  "event_close_days": 14,
  "version": 3,
  "updated_at": "2026-11-10T09:12:44Z",
  "applies_from": "next_daily_run",
  "open_event": null
}
```
`open_event`, when set: `{"event_id": "…", "opened_at": "…", "highest_tier": "WARNING"}`.

Errors: 403 `DISTRICT_SCOPE_FORBIDDEN`, 404 `DISTRICT_NOT_FOUND`, 404 `FUND_NOT_FOUND`.

### PUT `/districts/{district_id}/fund-settings`
Role: PDMA_OFFICER.

Request:
```json
{
  "version": 3,
  "allocated_pkr": "60000000.00",
  "tiers": [
    {"name": "WATCH",   "min_score": 40, "release_pct": 10},
    {"name": "WARNING", "min_score": 60, "release_pct": 30},
    {"name": "ACTION",  "min_score": 80, "release_pct": 60}
  ],
  "household_share_pct": 70,
  "pool_spillover_enabled": true,
  "shopkeeper_cap_pkr": "50000.00",
  "shopkeeper_stock_pct": 40,
  "household_amount_by_tier": {"WATCH": "3000.00", "WARNING": "7000.00", "ACTION": "15000.00"},
  "event_close_days": 14
}
```
For a district without a fund, send `"version": 0`.

Response 200: same shape as GET, plus `"changed_fields": ["allocated_pkr"]` and `"audit_seq": 418` (null when nothing changed).

Response 422:
```json
{
  "type": "https://aab.demo/problems/validation",
  "title": "Fund settings are invalid",
  "status": 422,
  "detail": "2 problems found. Nothing was saved.",
  "code": "TIER_PCT_SUM_INVALID",
  "errors": [
    {"field": "tiers[].release_pct", "code": "TIER_PCT_SUM_INVALID", "message": "Release increments add up to 90; they must add up to 100."},
    {"field": "shopkeeper_cap_pkr", "code": "SHOPKEEPER_CAP_INVALID", "message": "Cap must be zero or more."}
  ]
}
```
Other errors: 403 `FORBIDDEN_ROLE`, 404 `DISTRICT_NOT_FOUND`, 409 `VERSION_CONFLICT` (`detail` includes the current version).

### GET `/districts/{district_id}/fund-settings/draft?copy_from={source_district_id}&include_amount=false`
Role: PDMA_OFFICER. No write.

Response 200: a body ready for PUT (the target's `version` and, unless `include_amount=true`, the target's `allocated_pkr`), plus `"copied_from": {"district_id": "…", "district_name": "Dadu", "version": 3}`.

Errors: 404 `SOURCE_FUND_NOT_FOUND`, 422 `COPY_SOURCE_SAME_AS_TARGET`.

### GET `/fund-settings?province=SINDH&page=1&page_size=50`
Roles: PDMA_OFFICER, ADMIN. DC gets only their district.

Response 200:
```json
{
  "items": [
    {"district_id": "5d1c…", "district_name": "Dadu", "province": "SINDH",
     "allocated_pkr": "50000000.00", "remaining_pkr": "46000000.00",
     "thresholds": [40, 60, 80], "increments": [10, 30, 60],
     "household_share_pct": 70, "open_event_tier": "WARNING", "version": 3}
  ],
  "total": 30, "page": 1, "page_size": 50
}
```

### Audit entry
`event_type="FUND_SETTINGS_UPDATED"`, `entity_type="DisasterFund"`, `entity_id=fund.id`, `actor=str(user.id)`:
```json
{
  "district_id": "5d1c…",
  "created": false,
  "version_before": 3,
  "version_after": 4,
  "changed_fields": ["allocated_pkr", "tiers.WARNING.min_score"],
  "before": {"allocated_pkr": "50000000.00", "tiers": {"WATCH": [40, 10], "WARNING": [60, 30], "ACTION": [80, 60]}, "household_share_pct": 70, "shopkeeper_cap_pkr": "50000.00", "shopkeeper_stock_pct": 40, "household_amount_by_tier": {"WATCH": "3000.00", "WARNING": "7000.00", "ACTION": "15000.00"}, "event_close_days": 14},
  "after":  {"allocated_pkr": "60000000.00", "tiers": {"WATCH": [40, 10], "WARNING": [65, 30], "ACTION": [80, 60]}, "...": "..."},
  "open_event_id": null
}
```

## Shared tier helpers (`app/services/triggers/tiers.py`)

```python
TierName = Literal["WATCH", "WARNING", "ACTION"]
TierLevel = Literal["NONE", "WATCH", "WARNING", "ACTION"]
TIER_ORDER: tuple[TierName, ...] = ("WATCH", "WARNING", "ACTION")

@dataclass(frozen=True)
class TierRule:
    name: TierName
    min_score: int
    release_pct: int
    requires_approval: bool

@dataclass(frozen=True)
class TierConfig:
    rules: tuple[TierRule, TierRule, TierRule]   # in TIER_ORDER
    def rule(self, name: TierName) -> TierRule: ...
    def cumulative_pct(self, name: TierName) -> int: ...   # 10, 40, 100 by default
    def tiers_up_to(self, level: TierLevel) -> list[TierRule]: ...

def load_tier_config(db: Session, fund: DisasterFund) -> TierConfig: ...
def tier_for_score(score: int, config: TierConfig) -> TierLevel: ...
def tier_rank(level: TierLevel) -> int: ...   # NONE=0, WATCH=1, WARNING=2, ACTION=3
```

## Configuration

`data/seeds/funds.yaml` (foundation-owned) provides `fund_defaults`, read into `settings.fund_defaults`:
```yaml
fund_defaults:
  thresholds: {WATCH: 40, WARNING: 60, ACTION: 80}
  increments: {WATCH: 10, WARNING: 30, ACTION: 60}
  household_share_pct: 70
  shopkeeper_cap_pkr: "50000.00"
  shopkeeper_stock_pct: 40
  household_amount_by_tier: {WATCH: "3000.00", WARNING: "7000.00", ACTION: "15000.00"}
  event_close_days: 14
  pool_spillover_enabled: true
```

## Project Structure

### Documentation (this feature)

```text
specs/006-fr06-fund-tier-config/
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
├── api/v1/settings.py                    # 4 routes above (+ FR9 split-preview route)
├── schemas/fund_settings.py              # TierIn, TierOut, FundSettingsIn, FundSettingsOut, FundSettingsListItem, FieldError
└── services/triggers/
    ├── __init__.py
    ├── tiers.py                          # TierConfig, load_tier_config, tier_for_score, tier_rank
    ├── validation.py                     # validate_fund_settings -> list[FieldError]
    └── fund_settings.py                  # FundSettingsService (get, update, build_copy_draft, list)
tests/
├── unit/triggers/test_tier_for_score.py
├── unit/triggers/test_fund_settings_validation.py
├── integration/triggers/conftest.py      # make_fund(), make_tiers(), dc_headers(district)
├── integration/triggers/test_fund_settings_api.py
├── integration/triggers/test_fund_settings_audit.py
└── contract/test_fund_settings_contract.py
```

**Structure Decision**: single FastAPI project, constitution layout. No migration is needed: all fields exist in the foundation migration.

## Complexity Tracking

None.

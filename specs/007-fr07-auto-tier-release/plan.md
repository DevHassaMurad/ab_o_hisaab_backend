# Implementation Plan: FR7 Automatic Tier Release

**Branch**: `007-fr07-auto-tier-release` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/007-fr07-auto-tier-release/spec.md`

## Summary

`DefaultTriggerEngine.evaluate()` replaces the foundation no-op. Per district per run date it locks the fund, maps the score to a tier (FR6), opens or updates the open `TriggerEvent`, fires each missing tier up to today's tier with the cumulative incremental amount (D4), splits it into pools (FR9), and either starts the release pipeline (Watch/Warning) or requests DC approval (Action, FR8). Tier changes queue a DC alert in the same transaction (NFR2). The pipeline (allocation → payments → beneficiary alerts) runs per release inside a nested savepoint, so failures never undo a firing and are resumable.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, APScheduler (indirect via the daily pipeline)

**Storage**: PostgreSQL 16 (`trigger_event`, `fund_release`, reads `risk_index`, `disaster_fund`, `trigger_tier`, `fairness_check`)

**Testing**: pytest; Hypothesis for the amount property test; Postgres test DB; stub `AllocationService`, `AlertService`, payments

**Target Platform**: Linux container

**Project Type**: web-service + in-process job step

**Performance Goals**: < 0.5 s per district for evaluate excluding allocation; 68 districts < 30 s

**Constraints**: Decimal money, whole-PKR tranche rounding, UTC, one open event per district per scenario, idempotent re-runs

**Scale/Scope**: 68 districts/day; ≤ 3 releases per episode

## Constitution Check

| Principle | How satisfied | Status |
|---|---|---|
| I Anticipate | Firing and DC alert happen inside `evaluate`, called right after scoring | ✅ |
| II Human authority | Action → `PENDING_APPROVAL`. `run_release_pipeline` guard: allowed only from `ALLOCATING` or `APPROVED`; `ApprovalRequired` otherwise. Test `test_action_never_allocated_without_approval` | ✅ |
| III Traceability | 5 event types audited in the same session before commit | ✅ |
| IV Explainable | `FundRelease.amount_calc` JSON + `risk_index_id` | ✅ |
| VII Degrade | `is_fallback`/`is_stale_input` from the RiskIndex copied into the `TIER_FIRED` payload and the DC alert context | ✅ |
| VIII Honest | `simulated` set on every release; `scenario_id` on every row | ✅ |
| IX Config | Thresholds, increments, close days, purposes from DB/config | ✅ |
| X Simplicity | No queue; savepoints instead of a saga | ✅ |
| XI Tests | Amount maths, tier evaluation, gating, idempotency written first | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Cumulative-target amount rule | Exact sums, handles mid-episode fund changes and rejected tranches in one formula | `allocated × pct` per tier: rounding drift; ignores changes |
| Cap at `available_to_release` (remaining − earmarked) | Never promises the same rupee twice | Cap at `remaining_pkr`: double counts unallocated tranches |
| Episode state fields (`current_tier`, `last_evaluated_date`, `below_watch_streak`) | O(1) idempotency and close check; no history scan | Recompute from RiskIndex history: breaks on missing days, slower |
| `SELECT … FOR UPDATE` on the fund row | Serialises evaluation per district (re-runs, manual evaluate, replay step) | Advisory locks: extra moving part |
| Nested savepoint per release pipeline | Firing survives downstream errors; resumable | One transaction: an allocation bug would erase a trigger. Separate commits: harder to test |
| Recompute tier from score | One threshold source (FR6) | Trust `RiskIndex.tier`: two sources could disagree |
| Alerts enqueued synchronously | NFR2 satisfied by construction | Post-commit hook: extra failure mode |

## TriggerEvent lifecycle

```text
                     score ≥ Watch and no OPEN event
      (no event) ───────────────────────────────────────▶ OPEN
                                                          │ each run date:
                                                          │  tier ≥ WATCH → streak = 0, fire missing tiers ≤ tier
                                                          │  tier = NONE  → streak += 1 (once per date)
                                                          │  tier changed → DC alert + TIER_CHANGED audit
                                                          ▼
     streak ≥ fund.event_close_days AND no release in PENDING_APPROVAL ──▶ CLOSED (terminal)
```

## FundRelease state machine

```text
 fire WATCH/WARNING ──▶ ALLOCATING ──allocations saved──▶ RELEASED | PARTIALLY_RELEASED
 fire ACTION ──▶ PENDING_APPROVAL ──DC approves (FR8)──▶ APPROVED ──pipeline──▶ ALLOCATING ──▶ …
                        └──DC rejects (FR8)──▶ REJECTED (terminal)
```

`RELEASE_TRANSITIONS = {PENDING_APPROVAL: {APPROVED, REJECTED}, APPROVED: {ALLOCATING}, ALLOCATING: {RELEASED, PARTIALLY_RELEASED}}`. Anything else raises `Conflict("INVALID_TRANSITION")` (409).

## Data model slice

| Entity | Read | Written |
|---|---|---|
| RiskIndex | id, district_id, run_date, score, tier, is_fallback, is_stale_input, explanation_en/ur | — |
| DisasterFund | allocated_pkr, remaining_pkr, household_share_pct, event_close_days | — (FR17 moves balances) |
| TriggerTier | via `load_tier_config` | — |
| TriggerEvent | all | district_id, opened_at, closed_at, highest_tier, status, confirmation_status (`PENDING` on open), scenario_id, + proposed fields |
| FundRelease | all | event_id, fund_id, tier, amount_pkr, household_pool_pkr, shopkeeper_pool_pkr, purpose, risk_index_id, status, released_at, simulated, scenario_id, + proposed fields |
| FairnessCheck | passed, acknowledged_by | — |
| Allocation | status (UNMET_NEED check) | — (FR11/12) |
| Alert, AuditLog | — | via services |

### Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Field | Type | Purpose |
|---|---|---|---|
| TriggerEvent | `current_tier` | enum `NONE`,`WATCH`,`WARNING`,`ACTION`, default `NONE` | Tier-change detection and idempotent alerts |
| TriggerEvent | `last_evaluated_date` | date, nullable | Same-day idempotency; ignore out-of-order dates |
| TriggerEvent | `below_watch_streak` | int, default 0 | D4 close rule without scanning history |
| FundRelease | `amount_calc` | JSONB | Principle IV: `{cumulative_pct, target_pkr, prior_released_pkr, uncapped_pkr, available_pkr, capped, allocated_pkr_at_fire}` |
| FundRelease | `pipeline_error` | text, nullable | Last downstream failure; cleared on success; drives `is_stalled` |

FR9 separately proposes `FundRelease.split_details` (JSONB) and `DisasterFund.pool_spillover_enabled`.

## Amount rule

```
cum_pct      = Σ release_pct of tiers up to T          (10, 40, 100 by default)
target       = allocated_pkr                          if cum_pct == 100
             = round_half_up(allocated_pkr × cum_pct / 100, 0 dp)   otherwise
prior        = Σ amount_pkr of this event's releases with status ≠ REJECTED
uncapped     = max(0, target − prior)
available    = remaining_pkr − Σ amount_pkr of fund releases in {PENDING_APPROVAL, APPROVED, ALLOCATING}
amount_pkr   = min(uncapped, max(0, available))
```

Worked example, fund PKR 50,000,000 at 10/30/60: Watch 5,000,000; Warning 15,000,000; Action 30,000,000. Fund PKR 1,000,001: Watch 100,000; Warning 300,000; Action 600,001 (sum exact).

## API contracts

All under `/api/v1`, tag **Triggers**. Money is a string decimal.

### GET `/districts/{district_id}/trigger-events?status=OPEN&page=1&page_size=20`
Roles: PDMA_OFFICER, ADMIN; DC (own district). DONOR 403.
```json
{
  "items": [{
    "id": "e1…", "district_id": "5d1c…", "status": "OPEN",
    "opened_at": "2022-08-18T02:10:00Z", "closed_at": null,
    "current_tier": "WARNING", "highest_tier": "ACTION",
    "below_watch_streak": 0, "last_evaluated_date": "2022-08-24",
    "confirmation_status": "PENDING", "scenario_id": "sc-sindh-2022",
    "released_total_pkr": "20000000.00",
    "releases": [{"id": "r1…", "tier": "WATCH", "amount_pkr": "5000000.00", "status": "RELEASED"}]
  }],
  "total": 1, "page": 1, "page_size": 20
}
```

### GET `/trigger-events/{event_id}`
Same item shape with full releases. 404 `EVENT_NOT_FOUND`, 403 `DISTRICT_SCOPE_FORBIDDEN`.

### GET `/releases?district_id=&event_id=&tier=&status=&page=&page_size=`
Roles as above.

### GET `/releases/{release_id}`
```json
{
  "id": "r3…", "event_id": "e1…", "fund_id": "f1…", "district_id": "5d1c…",
  "tier": "ACTION", "status": "PENDING_APPROVAL",
  "amount_pkr": "30000000.00",
  "household_pool_pkr": "21000000.00", "shopkeeper_pool_pkr": "9000000.00",
  "purpose": "Cash support",
  "risk_index": {"id": "ri…", "run_date": "2022-08-24", "score": 86, "is_fallback": false, "is_stale_input": false},
  "amount_calc": {"cumulative_pct": 100, "allocated_pkr_at_fire": "50000000.00", "target_pkr": "50000000.00",
                  "prior_released_pkr": "20000000.00", "uncapped_pkr": "30000000.00",
                  "available_pkr": "30000000.00", "capped": false},
  "approval_id": "ap…",
  "allocation_counts": {"FUNDED": 0, "UNMET_NEED": 0},
  "released_at": null, "pipeline_error": null, "is_stalled": false,
  "simulated": true, "scenario_id": "sc-sindh-2022", "created_at": "2022-08-24T02:11:07Z"
}
```
404 `RELEASE_NOT_FOUND`.

### POST `/releases/{release_id}/resume`
Roles: PDMA_OFFICER, ADMIN. Re-runs the pipeline for a release in `APPROVED` or `ALLOCATING`. Idempotent.

Response 200: the release (as above). Errors: 409 `RELEASE_NOT_RESUMABLE` (any other status, including `PENDING_APPROVAL`), 404.

### POST `/triggers/evaluate`
Role: ADMIN. Re-evaluates one district for one run date using the stored district-level RiskIndex. Used for recovery and the idempotency demo.
```json
{"district_id": "5d1c…", "run_date": "2022-08-24"}
```
Response 200:
```json
{"district_id": "5d1c…", "run_date": "2022-08-24", "tier": "ACTION",
 "fired": [], "event_id": "e1…", "noop": true}
```
Errors: 404 `RISK_INDEX_NOT_FOUND`, 422 `RUN_DATE_OUT_OF_ORDER` (older than `last_evaluated_date`).

## Audit entries

| event_type | entity | payload (no PII) |
|---|---|---|
| `TRIGGER_EVENT_OPENED` | TriggerEvent | district_id, run_date, score, tier |
| `TIER_FIRED` | FundRelease | event_id, district_id, tier, score, risk_index_id, run_date, amount_calc, amount_pkr, pools, status, requires_approval, is_fallback, is_stale_input, simulated |
| `TIER_CHANGED` | TriggerEvent | from_tier, to_tier, score, run_date |
| `RELEASE_STATUS_CHANGED` | FundRelease | from_status, to_status, note, allocation_counts |
| `TRIGGER_EVENT_CLOSED` | TriggerEvent | closed_on, below_watch_streak, highest_tier, released_total_pkr |

## Alerts enqueued

| When | recipient | purpose | template_key | context |
|---|---|---|---|---|
| `current_tier` changes | DC user of the district | `TIER_ALERT` | `tier_change_dc` | district, from/to tier, score, peak_lead_day, amount fired (if any), purpose, explanation_ur/en, is_fallback, is_stale |
| After allocation saved | each allocated beneficiary | `TIER_ALERT` | `tier_alert_watch` / `_warning` / `_action` | tier, safety advice key, amount (FUNDED) or null |

`related={"fund_release_id": release.id}` where a release exists.

## Scheduled jobs

None of its own. `evaluate` runs as the trigger step of `app/jobs/daily_pipeline.py`, called by FR3 per district.

## Configuration

| Key | Default | Notes |
|---|---|---|
| `fund_defaults.purposes` | WATCH "Preposition boats and food", WARNING "Evacuate, open camps", ACTION "Cash support" | Seed file |
| `PAYMENT_RAIL_SIMULATED` | `true` | Foundation setting; sets `simulated` |
| `RELEASE_STALLED_AFTER_MIN` | `10` | `is_stalled` in responses |

## Project Structure

### Documentation (this feature)

```text
specs/007-fr07-auto-tier-release/
├── agent.md  spec.md  clarify.md  plan.md  tasks.md  implement.md
```

### Source Code (repository root)

```text
app/
├── api/v1/releases.py                  # events, releases, resume, evaluate (+ FR9 pools route)
├── schemas/releases.py                 # TriggerEventOut, FundReleaseOut, AmountCalc, EvaluateIn/Out
└── services/triggers/
    ├── amounts.py                      # round_pkr, cumulative_target, compute_tier_amount, available_to_release, planned_tier_amounts
    ├── events.py                       # get_open_event, open_event, record_day, close_event_if_due
    ├── engine.py                       # DefaultTriggerEngine.evaluate, _fire_tier, _notify_dc_tier_change
    └── release_pipeline.py             # RELEASE_TRANSITIONS, transition_release, run_release_pipeline, complete_release
alembic/versions/0007_fr07_trigger_event_state.py   # proposed fields, once merged into data-model.md
tests/
├── unit/triggers/test_amounts.py
├── unit/triggers/test_release_transitions.py
├── integration/triggers/test_engine.py
├── integration/triggers/test_engine_idempotency.py
├── integration/triggers/test_event_lifecycle.py
├── integration/triggers/test_release_pipeline.py
├── integration/triggers/test_releases_api.py
└── contract/test_releases_contract.py
```

The engine is registered in the foundation container: `interfaces.TriggerEngine` → `DefaultTriggerEngine` in `app/main.py` dependency wiring (one-line change).

**Structure Decision**: single project, constitution layout.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Nested savepoints inside the caller's transaction | A failing allocation must not erase a trigger firing (Principle I, III) | Single flat transaction loses the firing; separate commits break test isolation |

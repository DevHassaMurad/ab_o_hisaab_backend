# Implementation Plan: FR5 Event Replay

**Branch**: `005-fr05-event-replay` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/005-fr05-event-replay/spec.md`, decisions in `clarify.md`

## Summary

A `ReplayEngine` advances a per-scenario `ReplayClock` one day at a time and calls the foundation's `run_daily_pipeline(db, clock)` inside `use_scenario(scenario_id)`. Source FRs detect the scenario and read committed caches with no lookahead. After the pipeline, the engine drains payment and alert jobs, then resolves pending Action approvals (simulated, or pause for a real DC). Reset deletes the scenario's operational rows and bumps `run_no` (new audit chain). A backtest evaluator turns a completed run into a pass/fail report; `tests/backtest/` asserts NFR3.

## Technical Context

| | |
|---|---|
| **Language/Version** | Python 3.12 |
| **Primary Dependencies** | FastAPI, SQLAlchemy 2.x, APScheduler (autoplay), pyyaml, pandas/pyarrow (cache checks), hashlib |
| **Storage** | PostgreSQL (scenario rows); `data/replay/<key>/` committed files (~a few MB per scenario) |
| **Testing** | pytest unit + integration (mini fixture), contract, `-m backtest` (three real scenarios) |
| **Target Platform** | Demo laptop (offline) and Linux container |
| **Project Type** | web-service + engine |
| **Performance Goals** | One day ≤ 6 s for 5 districts; full `sindh-2022` ≤ 4 min; state/timeline p95 < 500 ms |
| **Constraints** | No network during replay (except optional live WhatsApp approval); deterministic given caches + artifacts |
| **Scale/Scope** | 3 scenarios; ≤ 5 districts, ≤ ~60 tehsils, ≤ 41 days each |

## Constitution Check

| Principle | How satisfied | |
|---|---|---|
| I Anticipate | Backtests require lead before onset (min lead days) | ✅ |
| II Human authority | `REPLAY_SIM` approval rows, live approval pause; backtest asserts every Action has an approval | ✅ |
| III Traceability | Audit never deleted; new chain per run via `run_no` | ✅ |
| IV Explainable | Report lists model versions, provenance; scores trace as live | ✅ |
| VI Privacy | Donor views use `DonorScope` aggregates; DC scoped | ✅ |
| VII Degrade gracefully | Optional `outages.json` lets a replay demonstrate the stale path | ✅ |
| VIII Honest prototype | `simulated: true` on every replay response; `X-Simulated-Data` header; proxy share shown | ✅ |
| IX Config over code | Scenarios in YAML; no district names in code | ✅ |
| X Simplicity | Same process and DB; contextvars for scenario and clock | ✅ |
| XI Tests | Backtests are acceptance tests; isolation test | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Run the real pipeline per simulated day | The demo proves the actual code path; one code path to test | Precomputed replay results (a movie, not a system) |
| Committed caches + manifest hashes | Offline, deterministic, reviewable | Live API calls during replay (Wi-Fi, archive drift) |
| Each source FR owns its replay reader | Keeps parsing identical to live (FR1 pattern) | FR5 parsing all sources (duplicated parsers) |
| Reset = delete operational rows + `run_no` + 1 | Respects append-only audit (FR20), stable `scenario_id` | New scenario row per run; DB snapshots/rollbacks (slow, fragile) |
| Seek backwards = reset + re-run | Effects (payments, alerts) cannot be undone; FR7 ignores older dates | "Time travel" deletes of later days only (partial state, broken events) |
| Drain async jobs inside each step | Day N is complete before day N+1 | Let jobs run in background (non-deterministic timeline) |
| APScheduler interval job for autoplay | Already in the stack | Frontend timer calling step (stage network risk, double steps) |

## Scenario definitions (`data/replay/scenarios.yaml`)

```yaml
defaults:
  spinup_days: 30
  pipeline_time_utc: "01:00"
  funds: {allocated_pkr: "50000000.00"}          # per district; tiers/split/cap use FR6 defaults
  approval_mode: AUTO_SIM
scenarios:
  - key: sindh-2022
    name: "Sindh monsoon floods 2022"
    start_date: 2022-08-01
    end_date: 2022-09-10
    expected_outcome: TRIGGER
    districts: [{name: Dadu, province: SINDH}, {name: Qambar Shahdadkot, province: SINDH},
                {name: Larkana, province: SINDH}, {name: Jamshoro, province: SINDH},
                {name: Khairpur, province: SINDH}]
    hero_district: Dadu
    reference_onset: {Dadu: 2022-08-22}          # verify (NDMA sitreps)
    min_lead_days: 3
    stretch_lead_days: 7
  - key: buner-2025
    name: "Buner flash flood 2025"
    start_date: 2025-08-01
    end_date: 2025-08-25
    expected_outcome: TRIGGER
    districts: [{name: Buner, province: KP}, {name: Swat, province: KP}, {name: Shangla, province: KP}]
    hero_district: Buner
    reference_onset: {Buner: 2025-08-15}         # verify
    min_lead_days: 1
    stretch_lead_days: 2
  - key: control-2021
    name: "Normal Sindh monsoon 2021 (control)"
    start_date: 2021-08-01
    end_date: 2021-09-10
    expected_outcome: NO_TRIGGER
    districts: [{name: Dadu, province: SINDH}, {name: Qambar Shahdadkot, province: SINDH},
                {name: Larkana, province: SINDH}, {name: Jamshoro, province: SINDH},
                {name: Khairpur, province: SINDH}]
```

## Cache layout (`data/replay/<key>/`)

| Path | Owner / writer | Content |
|---|---|---|
| `nasa_power/<district_pcode>.json` | FR1 `scripts/ingest_nasa_power.py --scenario <key> --to-cache` | Raw POWER JSON, spin-up + window |
| `outages.json` (optional) | FR1 | Simulated outage ranges to demo staleness |
| `imerg_daily.csv` | FR2 `scripts/cache_replay_imerg.py --scenario <key>` | tehsil_pcode, date, rainfall_mm, rainfall_max_3h_mm |
| `forecast_rain.parquet` | FR3 `scripts/replay/forecast_rain_cache.py --scenario <key>` | tehsil_pcode, issue_date, lead_day, target_date, rainfall_mm, rainfall_max_3h_mm, provenance, forecast_model |
| `s1/…` | FR21 precompute (D1), trigger scenarios only | Before/after maps + per-tehsil flooded share |
| `manifest.json` | FR5 `scripts/replay/build_cache.py` | See below |

```json
{"key":"sindh-2022","built_at":"2026-11-10T09:00:00Z","window":{"start":"2022-08-01","end":"2022-09-10","spinup_start":"2022-07-02"},
 "districts":[{"name":"Dadu","pcode":"PK6xx","tehsils":4}],
 "files":{"imerg_daily.csv":{"sha256":"…","rows":12054},"forecast_rain.parquet":{"sha256":"…","rows":8610}},
 "forecast_provenance":{"ARCHIVED_SHORT_LEAD_PROXY":7380,"ARCHIVED_FORECAST":1230,"PERFECT_FORECAST_PROXY":0},
 "builders":{"fr1":"1.0","fr2":"1.0","fr3":"1.0"}}
```

## Replay engine design

```
start(key, approval_mode)
  lock → verify manifest → use_scenario(sid):
    create funds+tiers (FR6) → config.modes = snapshot_modes (FR4) → load S1 maps (FR21, if present)
    set_clock(ReplayClock(start−1)) → spin-up: all_ingestors().ingest(start−30 … start−1)
  status RUNNING, sim_date = start−1, started_at

step(key, days=n)
  try_lock(key) else 409 REPLAY_BUSY
  for each day while n > 0:
    if status AWAITING_APPROVAL: if pending approvals remain → stop (409 on first day) else status RUNNING
    if sim_date == end_date: status COMPLETED; stop
    d = sim_date + 1
    with use_scenario(sid), clock_scope(ReplayClock(d)):
        run_daily_pipeline(db, clock)               # FR1 → FR2 → FR3 → FR7 (→ FR11/12, FR17, FR14, FR8)
        drain(REPLAY_DRAIN_JOBS)                    # payments.dispatch, messaging.dispatch
        resolve_approvals(mode)                     # AUTO_SIM → FR8 decide(REPLAY_SIM); else AWAITING_APPROVAL
        drain(REPLAY_DRAIN_JOBS)                    # approval-started payments/alerts
    sim_date = d; last_step_at = now; commit
  on exception: rollback, status FAILED, last_error; re-step re-runs day d (idempotent)

seek(key, target, reset=False)
  target > sim_date → step(target − sim_date days)
  target ≤ sim_date → reset required (409 REPLAY_SEEK_BACKWARD) → reset → start(same mode) → step to target

reset(key)
  stop autoplay → lock → delete scenario rows (children first) → run_no += 1 → status IDLE, sim_date NULL
```

## Data model slice

| Entity | Read | Write |
|---|---|---|
| ReplayScenario | all | status, current_date (proposed rename `sim_date`), run_no, config, timestamps |
| District / Tehsil | names, pcodes, active | — |
| DisasterFund / TriggerTier | — | via FR6 at start (scenario rows) |
| FundRelease, TriggerEvent, RiskIndex, Approval, Payment, Alert | backtest + timeline | delete on reset |
| All operational tables with `scenario_id` | — | delete on reset |
| AuditLog | verify by `run_no` (FR20) | via `AuditService.record` (`REPLAY_STARTED`, `REPLAY_RESET`, `REPLAY_COMPLETED`) |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity.field | Type | Why |
|---|---|---|
| `ReplayScenario.run_no` | int NOT NULL default 1 | New audit chain per run (also proposed by FR20) |
| `ReplayScenario.status` | add `AWAITING_APPROVAL`, `FAILED` | Live-approval pause; failed step |
| `ReplayScenario.config` | JSONB | `hero_district_id`, `reference_onset`, `min_lead_days`, `stretch_lead_days`, `approval_mode`, `modes` (FR4 snapshot), `manifest_sha256`, `forecast_provenance`, `funds` |
| `ReplayScenario.current_date` → `sim_date` | rename | `current_date` is an SQL reserved function name |
| `ReplayScenario.started_at`, `completed_at`, `last_step_at` | timestamptz nullable | State endpoint, demo timing |
| `ReplayScenario.last_error` | text nullable | Failed step diagnosis |
| `DisasterFund.scenario_id` | FK nullable (if FR6 has not added it) | data-model says "unique per scenario" but lists no column |
| `scenario_id` on `Approval`, `Allocation`, `FairnessCheck`, `PaymentEvent`, `Confirmation` | FK nullable, denormalised | Uniform scenario filters and reset deletes; until merged, reset deletes them through their parents |

## API contracts

Router `app/api/v1/replay.py`, tag **Forecasting**. All responses include `"simulated": true`. Errors use RFC 7807.

### GET `/api/v1/replay/scenarios` (OFFICER, ADMIN, DC, DONOR)
```json
{"items":[{"key":"sindh-2022","scenario_id":"5b1e…","name":"Sindh monsoon floods 2022","start_date":"2022-08-01",
 "end_date":"2022-09-10","expected_outcome":"TRIGGER","status":"IDLE","sim_date":null,"run_no":3,"simulated":true}],
 "total":3,"page":1,"page_size":50}
```

### POST `/api/v1/replay/scenarios/{key}/start` (OFFICER, ADMIN)
Request `{"approval_mode":"AUTO_SIM"}` (`AUTO_SIM` | `LIVE_WHATSAPP` | `DASHBOARD`). Idempotent: already running → 200 with state. 201 on first start:
```json
{"key":"sindh-2022","scenario_id":"5b1e…","status":"RUNNING","sim_date":"2022-07-31","run_no":3,
 "approval_mode":"AUTO_SIM","forecast_provenance":{"ARCHIVED_SHORT_LEAD_PROXY":0.86,"ARCHIVED_FORECAST":0.14},"simulated":true}
```
Errors: 404 `SCENARIO_NOT_FOUND`, 422 `REPLAY_CACHE_INVALID` (detail names the file), 409 `REPLAY_BUSY`.

### POST `/api/v1/replay/scenarios/{key}/step` (OFFICER, ADMIN)
Request `{"days":1}` (1–60). Response 200 = state (below) + `"stepped":["2022-08-01"]`. Errors: 409 `REPLAY_NOT_STARTED`, 409 `REPLAY_AWAITING_APPROVAL` (detail lists `approval_ids`), 409 `REPLAY_COMPLETED`, 409 `REPLAY_BUSY`, 500 `REPLAY_STEP_FAILED` (status FAILED).

### POST `/api/v1/replay/scenarios/{key}/seek` (OFFICER, ADMIN)
Request `{"date":"2022-08-18","reset":false}`. Response 200 = state. Errors: 409 `REPLAY_SEEK_BACKWARD`, 422 `DATE_OUT_OF_WINDOW`, plus step errors.

### POST `/api/v1/replay/scenarios/{key}/reset` (OFFICER, ADMIN)
Response 200 `{"key":"sindh-2022","status":"IDLE","run_no":4,"deleted":{"risk_index":205,"fund_release":3,"payment":1240,"alert":2610},"simulated":true}`

### GET `/api/v1/replay/scenarios/{key}/state` (OFFICER, ADMIN, DC, DONOR)
```json
{"key":"sindh-2022","scenario_id":"5b1e…","status":"RUNNING","sim_date":"2022-08-20","run_no":3,"approval_mode":"AUTO_SIM",
 "districts":[{"district_id":"a3f…","name":"Dadu","score":81,"tier":"ACTION","open_event":true,
   "released_pkr":"50000000.00","fund_remaining_pkr":"0.00","pending_approval_id":null}],
 "pending_approvals":[],"forecast_provenance":{"ARCHIVED_SHORT_LEAD_PROXY":0.86,"ARCHIVED_FORECAST":0.14},
 "backtest_so_far":{"expected":"TRIGGER","first_release":{"district":"Dadu","tier":"WATCH","run_date":"2022-08-16"}},
 "autoplay":{"running":false,"interval_seconds":null},"simulated":true}
```
DC: `districts` filtered to own district. DONOR: district aggregates only (no ids of approvals or beneficiaries).

### GET `/api/v1/replay/scenarios/{key}/timeline?district_id=` (OFFICER, ADMIN, DC, DONOR)
```json
{"key":"sindh-2022","run_no":3,"days":[
 {"date":"2022-08-16","districts":[{"district_id":"a3f…","name":"Dadu","score":47,"tier":"WATCH","is_fallback":false,
   "forecast_is_proxy":true,"releases":[{"tier":"WATCH","amount_pkr":"5000000.00","status":"RELEASED"}]}]}],
 "simulated":true}
```

### GET `/api/v1/replay/scenarios/{key}/backtest` (OFFICER, ADMIN, DONOR)
Report for the current run (also when not complete; `complete:false`):
```json
{"key":"sindh-2022","run_no":3,"complete":true,"expected":"TRIGGER","passed":true,
 "hero_district":"Dadu","reference_onset":"2022-08-22","min_lead_days":3,"stretch_lead_days":7,
 "first_release":{"tier":"WATCH","run_date":"2022-08-16","lead_days":6},
 "tier_first_dates":{"WATCH":"2022-08-16","WARNING":"2022-08-18","ACTION":"2022-08-20"},
 "max_score":{"district":"Dadu","value":94,"run_date":"2022-08-24"},
 "districts":[{"name":"Dadu","max_score":94,"releases":3},{"name":"Larkana","max_score":71,"releases":2}],
 "action_releases":1,"action_releases_with_approval":1,
 "forecast_provenance":{"ARCHIVED_SHORT_LEAD_PROXY":0.86,"ARCHIVED_FORECAST":0.14},
 "model_versions":{"SLOW_ONSET":"slow-lgbm-2026.11.1"},"failures":[],"simulated":true}
```
(Numbers are illustrative.) For `NO_TRIGGER`: `"releases_total":0,"max_score":{"value":31,…},"watch_margin":9`.

### POST `/api/v1/replay/scenarios/{key}/play` and `/pause` (OFFICER, ADMIN)
Play request `{"interval_seconds":3,"until":"2022-08-26"}` (interval 2–30). Response = state with `autoplay.running=true`. Pause → `running=false`.

## Scheduled jobs

| Job id | Trigger | Does |
|---|---|---|
| `replay.autoplay.<key>` | APScheduler interval (`interval_seconds`), `max_instances=1`, `coalesce=True` | `engine.step(key, 1)`; removes itself on COMPLETED, FAILED, AWAITING_APPROVAL, `until` reached |

No daily job: replays run only on request.

## Project Structure

### Documentation (this feature)
```text
specs/005-fr05-event-replay/
├── agent.md  spec.md  clarify.md  plan.md  tasks.md  implement.md
```

### Source Code (repository root)
```text
app/services/replay/
├── clock.py          # ReplayClock, clock_scope()
├── scenarios.py      # load_definitions(), ensure_scenarios(db), resolve_districts()
├── cache.py          # verify_manifest(key), write_manifest(key)
├── engine.py         # ReplayEngine: start, step, seek, reset, state, timeline
├── approvals.py      # resolve_approvals(db, scenario, mode)
├── reset.py          # purge_scenario_rows(db, sid), RESET_ORDER
├── backtest.py       # evaluate_backtest(db, scenario) -> BacktestReport
└── autoplay.py       # play(key, interval, until), pause(key)
app/schemas/replay.py
app/api/v1/replay.py
alembic/versions/0005_fr05_replay.py     # run_no, status values, config, timestamps, last_error, sim_date rename
data/replay/scenarios.yaml
data/replay/<key>/manifest.json
scripts/replay/build_cache.py            # orchestrates FR1/FR2/FR3/FR21 writers + manifest
scripts/replay/run_backtest.py           # headless run + JSON/Markdown report
tests/unit/replay/{test_replay_clock,test_backtest_eval,test_scenarios_yaml}.py
tests/integration/replay/{test_replay_engine,test_replay_reset}.py
tests/contract/test_replay_api.py
tests/fixtures/replay/mini/              # scenarios.yaml entry + tiny caches
tests/backtest/{conftest,test_nfr3_backtests,test_replay_isolation}.py
```

**Structure Decision**: single project per constitution; replay router listed in the constitution's `api/v1/` set.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Deleting scenario rows on reset | Repeatable rehearsals and backtests with a stable scenario id | Keeping all runs' rows needs `run_no` on every table and every query |

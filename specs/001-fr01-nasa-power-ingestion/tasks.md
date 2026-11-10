---

description: "Task list for FR1 NASA POWER Ingestion"
---

# Tasks: FR1 NASA POWER Ingestion

**Input**: Design documents from `/specs/001-fr01-nasa-power-ingestion/`

**Prerequisites**: plan.md, spec.md, clarify.md; `000-foundation` merged (models, `ScenarioContext`, `Clock`, `register_step`, test DB fixture)

**Tests**: Included. FR1 is not a money path, but tests protect the parser, validation, stale path, idempotency, replay no-lookahead and API contracts. Write them first and see them fail.

**Organization**: Tasks are grouped by user story.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Add FR1 settings with defaults to `app/core/config.py` (`NASA_POWER_BASE_URL`, `POWER_LOOKBACK_DAYS=10`, `POWER_EXPECTED_LAG_DAYS=3`, `POWER_MAX_COPY_FORWARD_DAYS=3`, `POWER_MAX_WORKERS=4`, `POWER_TIMEOUT_S=60`, `POWER_MAX_RETRIES=3`, `POWER_RAIN_MAX_MM=600`, `REPLAY_POWER_LAG_DAYS=2`, `REPLAY_SPINUP_DAYS=30`, `VULNERABILITY_WEIGHTS`, `VULNERABILITY_INVERTED`, `CONTEXT_INDICATORS_FILE`, `CONTEXT_REFRESH_CRON`) and document them in `.env.example`
- [ ] T002 [P] Record 4 POWER response fixtures (real calls for Dadu centroid, then hand-edit) in `tests/fixtures/nasa_power/{ok_7d,fill_values,bad_units,pct_soil}.json`
- [ ] T003 [P] Create package skeleton `app/services/ingestion/__init__.py` and `app/schemas/ingestion.py` (empty models)

---

## Phase 2: Foundational (blocking for all FR1 stories and for FR2)

- [ ] T004 Define `Ingestor` protocol, `IngestResult` dataclass and `IngestError` in `app/services/ingestion/base.py`
- [ ] T005 [P] Implement `register_ingestor`, `get_ingestor`, `all_ingestors` in `app/services/ingestion/registry.py`
- [ ] T006 [P] Implement `upsert_observations()` (INSERT … ON CONFLICT on the unique key), `latest_valid_date()` and `list_observations()` in `app/services/ingestion/observations.py`
- [ ] T007 Write the proposed additions (`soil_wetness_top`, `quality_flags`, NULLS NOT DISTINCT unique key, `District.vulnerability_inputs`, `ContextIndicator` unique key) into `alembic/versions/fr01_0001_ingestion_additions.py` **after** the foundation owner merges them into data-model.md; until then, guard reads/writes of the new columns behind `hasattr` checks in `observations.py`
- [ ] T008 [P] Write `tests/unit/ingestion/test_staleness.py`: not-yet-due days skipped; overdue days copied forward with `stale_since`; cap of 3 days; no previous valid row → no copy; health transition detection
- [ ] T009 Implement `plan_copy_forward()` and `update_source_status()` (returns `TRANSITION_DOWN | TRANSITION_UP | NONE`, skips live table when `scenario_id` is set) in `app/services/ingestion/staleness.py`

**Checkpoint**: Framework ready. FR2 can start its ingestor in parallel.

---

## Phase 3: User Story 1 - Daily weather ingest (Priority: P1) 🎯 MVP

**Goal**: Fresh, validated NASA POWER observations for every active district each day, then hand-off to FR3.

**Independent Test**: `pytest tests/integration/ingestion/test_nasa_power_ingest.py` with `httpx.MockTransport` serving the fixtures.

### Tests for User Story 1 ⚠️

- [ ] T010 [P] [US1] Write `tests/unit/ingestion/test_nasa_power_parse.py`: date keys `YYYYMMDD` parsed; `-999` → `None`; header `fill_value` honoured; units extracted
- [ ] T011 [P] [US1] Write `tests/unit/ingestion/test_validation.py`: rain clamp [−0.5,0) → 0; rain 2000 → rejected; GWETROOT 45 → 0.45 with flag; T2M 70 → rejected; unit mismatch raises `UnitMismatch`
- [ ] T012 [P] [US1] Write `tests/integration/ingestion/test_nasa_power_ingest.py`: 2 districts × 10 days stored; inactive district skipped; re-run = no duplicates and values updated; one `DATA_INGESTED` audit row; pipeline step returns normally
- [ ] T013 [P] [US1] Write `tests/contract/test_ingestion_api.py`: `POST /ingestion/run` ADMIN-only, 422 for > 31 days, 409 when lock held; `GET /ingestion/observations` officer OK, DC other district 403, pagination shape

### Implementation for User Story 1

- [ ] T014 [US1] Implement `parse_power_json()`, `NasaPowerClient` protocol and `HttpNasaPowerClient.fetch_daily()` with retries/backoff in `app/integrations/nasa_power.py`
- [ ] T015 [US1] Implement `validate_power_day()` and `RANGES` in `app/services/ingestion/validation.py`
- [ ] T016 [US1] Implement `NasaPowerIngestor.ingest()` (window defaults from `Clock`, thread-pool fetch, advisory lock, one transaction, upsert, audit) in `app/services/ingestion/nasa_power_ingestor.py`
- [ ] T017 [US1] Implement `run_nasa_power_step()` (catch-all, never raises) and `register_ingestion_jobs()` calling `register_step(10, "ingest.nasa_power", …)` and `register_ingestor(NasaPowerIngestor())` in `app/services/ingestion/jobs.py`; add the one-line call in `app/main.py`
- [ ] T018 [US1] Implement request/response schemas in `app/schemas/ingestion.py` and routes `POST /run`, `GET /observations` in `app/api/v1/ingestion.py`; include router in `app/api/v1/__init__.py`
- [ ] T019 [US1] Implement `scripts/ingest_nasa_power.py --start --end [--districts]` backfill (chunks by calendar year, writes live rows) for FR3 training data

**Checkpoint**: Daily ingest works end-to-end with healthy data.

---

## Phase 4: User Story 2 - Stale path (Priority: P1)

**Goal**: Outages produce flagged stale data, one admin alert and a recovery record; the pipeline continues.

**Independent Test**: `pytest tests/integration/ingestion/test_nasa_power_ingest.py -k "outage or recovery"`.

### Tests for User Story 2 ⚠️

- [ ] T020 [P] [US2] Add tests to `tests/integration/ingestion/test_nasa_power_ingest.py`: all 503 → stale rows, `is_healthy=false`, one `AlertService.enqueue` call with purpose `DATA_SOURCE_DOWN`, `DATA_SOURCE_STALE` audit; second outage day → no second alert; recovery → stale rows overwritten fresh, `DATA_SOURCE_RECOVERED`; normal 3-day latency → no stale flags; partial outage → only failed districts stale

### Implementation for User Story 2

- [ ] T021 [US2] Wire `plan_copy_forward()` and `update_source_status()` into `NasaPowerIngestor.ingest()` in `app/services/ingestion/nasa_power_ingestor.py`
- [ ] T022 [US2] Enqueue the admin alert on `TRANSITION_DOWN` via `AlertService.enqueue(recipient=admin users, purpose="DATA_SOURCE_DOWN", template_key="data_source_down", context={source,last_valid_date,error})` in `app/services/ingestion/staleness.py`; fall back to `log.error` if the enum value is not yet merged

**Checkpoint**: NFR9 satisfied for NASA POWER.

---

## Phase 5: User Story 3 - Replay-ready inputs (Priority: P2)

**Goal**: Cached POWER data for the three scenarios; replay ingest with no lookahead.

**Independent Test**: `pytest tests/integration/ingestion/test_nasa_power_replay.py`.

### Tests for User Story 3 ⚠️

- [ ] T023 [P] [US3] Write `tests/integration/ingestion/test_nasa_power_replay.py`: with a fixture cache and `ReplayClock` at 2021-08-15, no row dated after 2021-08-13; all rows carry `scenario_id`; `DataSourceStatus` unchanged; `outages.json` window → stale path in replay

### Implementation for User Story 3

- [ ] T024 [US3] Implement `CachedNasaPowerClient(scenario_key, replay_today, lag_days)` (reads `data/replay/<key>/nasa_power/<pcode>.json`, truncates dates, raises `SourceUnavailable` inside `outages.json` windows) in `app/integrations/nasa_power.py`
- [ ] T025 [US3] Select client by `ScenarioContext` in `NasaPowerIngestor.client_for()` in `app/services/ingestion/nasa_power_ingestor.py`
- [ ] T026 [US3] Add `--scenario <key> --to-cache` mode to `scripts/ingest_nasa_power.py` (window from `ReplayScenario` row or FR5's `data/replay/scenarios.yaml`, minus `REPLAY_SPINUP_DAYS`); expose `write_replay_cache(key)` so FR5's `scripts/replay/build_cache.py` can call it
- [ ] T027 [US3] Generate and commit caches: `data/replay/sindh-2022/nasa_power/`, `data/replay/buner-2025/nasa_power/`, `data/replay/control-2021/nasa_power/`

**Checkpoint**: FR5 can replay FR1 offline.

---

## Phase 6: User Story 4 - Context indicators and vulnerability (Priority: P3)

**Goal**: Monthly refresh of indicators and reproducible `vulnerability_weight`.

**Independent Test**: `pytest tests/integration/ingestion/test_context_refresh.py`.

### Tests for User Story 4 ⚠️

- [ ] T028 [P] [US4] Write `tests/unit/ingestion/test_vulnerability.py`: min-max normalisation; inverted indicator; province-median imputation sets `imputed`; result in [0,1]; deterministic
- [ ] T029 [P] [US4] Write `tests/integration/ingestion/test_context_refresh.py`: CSV upsert idempotent; changed weight audited with old/new; World Bank down → indicators kept, `WORLD_BANK` unhealthy

### Implementation for User Story 4

- [ ] T030 [P] [US4] Implement `fetch_indicator()` for `https://api.worldbank.org/v2/country/PAK/indicator/{code}?format=json&date={y0}:{y1}` in `app/integrations/world_bank.py`
- [ ] T031 [US4] Implement `compute_vulnerability()` in `app/services/ingestion/vulnerability.py`
- [ ] T032 [US4] Implement `ContextIndicatorService.refresh()` (CSV load, World Bank reference, upsert, weights, audit) in `app/services/ingestion/context_indicators.py`
- [ ] T033 [US4] Add `run_context_refresh` monthly job to `register_ingestion_jobs()` in `app/services/ingestion/jobs.py`; add `GET /context-indicators` and `POST /context-indicators/refresh` to `app/api/v1/ingestion.py`
- [ ] T034 [US4] Curate `data/seeds/context_indicators.csv` for the 12 active pilot districts (columns `district_pcode,indicator,value,period,source`)

**Checkpoint**: All FR1 stories complete.

---

## Phase 7: Polish & Cross-Cutting

- [ ] T035 [P] Time a 68-district run against live POWER and record it in `specs/001-fr01-nasa-power-ingestion/implement.md` (SC-001)
- [ ] T036 [P] Run a 2022 backfill for active districts and check SC-002 with `SELECT count(*) FROM climate_observation WHERE rainfall_mm < 0 OR rainfall_mm > 600`
- [ ] T037 Verify `POWER_EXPECTED_LAG_DAYS` against a week of live calls (clarify open item 1)
- [ ] T038 ruff + mypy clean on `app/services/ingestion` and `app/integrations/nasa_power.py`

---

## Dependencies & Execution Order

### Phase Dependencies
- Setup (1) → Foundational (2) → US1 (3) → US2 (4). US3 (5) needs US1. US4 (6) needs only Phase 2. Polish last.
- T007 waits for the foundation owner to merge the proposed fields; everything else can proceed with the guard.

### User Story Dependencies
- **US1**: after Phase 2.
- **US2**: extends US1's ingestor (same file); do after US1.
- **US3**: needs US1's parser and ingestor; also needs FR5's scenario windows (use the defaults in FR5 clarify, or the seed YAML).
- **US4**: independent of US1–US3.

### Within each story
Tests first (must fail) → integration client → service → job/API.

### Parallel opportunities
- T002, T003 in Setup.
- T005, T006, T008 in Phase 2.
- All test tasks marked [P] inside a story.
- US4 (T028–T034) can run in parallel with US1–US3 by a second developer.
- FR2 can start its ingestor as soon as T004–T006 and T009 are merged.

---

## Implementation Strategy

1. Phase 1–2, then US1 → demo-able daily ingest.
2. US2 immediately (NFR9 is a judging point: stale banner).
3. US3 before the replay rehearsal (FR5 blocks on it).
4. US4 last; until then `vulnerability_weight` comes from seeds.

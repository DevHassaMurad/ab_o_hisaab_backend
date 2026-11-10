---

description: "Task list for FR2 Earth Engine Ingestion"
---

# Tasks: FR2 Earth Engine Ingestion

**Input**: Design documents from `/specs/002-fr02-earth-engine-ingestion/`

**Prerequisites**: plan.md, spec.md, clarify.md; `000-foundation` merged; FR1 Phase 2 merged (`base.py`, `registry.py`, `observations.py`, `staleness.py`)

**Tests**: Included (not a money path). They protect the 3-hour maths, idempotency, stale path and replay cut-off. EE is faked in unit and integration tests; live tests are opt-in (`-m ee`).

**Organization**: Tasks are grouped by user story.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Add `earthengine-api` (exact pinned version) to `pyproject.toml`; add `secrets/` to `.gitignore`
- [ ] T002 Add FR2 settings to `app/core/config.py` and `.env.example`: `GEE_PROJECT`, `GEE_HIGH_VOLUME=true`, `IMERG_COLLECTION=NASA/GPM_L3/IMERG_V07`, `IMERG_BAND=precipitation`, `IMERG_REDUCE_SCALE_M=5000`, `IMERG_LOOKBACK_DAYS=3`, `IMERG_EXPECTED_LAG_DAYS=2`, `IMERG_MIN_IMAGES_PARTIAL=40`, `IMERG_MAX_COPY_FORWARD_DAYS=3`, `REPLAY_IMERG_LAG_DAYS=1`, `EE_TEHSIL_BATCH=300`, `EE_SIMPLIFY_DEG=0.002`, `S1_COLLECTION=COPERNICUS/S1_GRD`, `S1_DIFF_DB=-3.0`, `S1_AFTER_MAX_DB=-15.0`, `S1_SPECKLE_RADIUS_M=50`, `JRC_COLLECTION=JRC/GSW1_4/GlobalSurfaceWater`, `JRC_PERM_OCCURRENCE=80`, `SRTM_COLLECTION=USGS/SRTMGL1_003`, `S1_MAX_SLOPE_DEG=5`, `S1_MIN_CONNECTED_PX=8`, `S1_AREA_SCALE_M=30`, `THUMB_DIMENSIONS=1024`
- [ ] T003 [P] Write `scripts/ee_smoke_test.py` (init, print latest `IMERG_COLLECTION` image time and band names, list S1 acquisitions over Dadu 2022-08-15..2022-09-10) and run it to resolve clarify open item 1

---

## Phase 2: Foundational

- [ ] T004 Define `EarthEngineGateway` protocol, `S1Params`, `TehsilRain`, `TehsilFlood`, `S1Change`, `EEUnavailable` and `init_ee()` / `ee_health()` with retry wrapper `_call(fn)` (3 retries on `ee.EEException` with "Too many"/"timed out"/"Internal error", backoff 5/10/20 s) in `app/integrations/earth_engine.py`
- [ ] T005 [P] Implement `tehsil_geojson(db, tehsil_ids)` with `ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, :tol))` in `app/services/ingestion/ee_boundaries.py`
- [ ] T006 [P] Write `tests/unit/integrations/test_earth_engine.py`: `init_ee` reads settings and fails cleanly when `GEE_KEY_FILE` is missing (returns unhealthy, no crash); retry wrapper retries 3× then raises `EEUnavailable`
- [ ] T007 Implement `tehsil_feature_collection()` in `app/integrations/earth_engine.py` (GeoJSON → `ee.FeatureCollection`, property `pcode`, per-process cache keyed by tehsil id set)

**Checkpoint**: EE seam ready; US1, US3 and US4 can proceed in parallel.

---

## Phase 3: User Story 1 - Daily IMERG per tehsil (Priority: P1) 🎯 MVP

**Goal**: One observation per active tehsil per complete day with daily total and max 3-hour total.

**Independent Test**: `pytest tests/integration/ingestion/test_imerg_ingest.py` with `FakeEEGateway` returning fixture reductions.

### Tests for User Story 1 ⚠️

- [ ] T008 [P] [US1] Write `tests/unit/ingestion/test_imerg_math.py`: 48 half-hourly rates → daily total = Σ×0.5; rolling 6-slot max; a 3-hour burst straddling 03:00 UTC is captured; < 6 slots → `None`
- [ ] T009 [P] [US1] Write `tests/integration/ingestion/test_imerg_ingest.py`: rows for each tehsil with `district_id` and `tehsil_id`; incomplete newest day skipped (not stale); 44/48 old day stored with `imerg_partial:44/48`; null reduction → centroid fallback flag; re-run idempotent; one `DATA_INGESTED` audit; step never raises

### Implementation for User Story 1

- [ ] T010 [P] [US1] Implement `rolling_max_3h()` and `daily_total()` in `app/services/ingestion/imerg_math.py`
- [ ] T011 [US1] Implement `imerg_daily_image(day)`, `imerg_image_count(day)` and `reduce_imerg(day, fc)` (batching by `EE_TEHSIL_BATCH`, centroid fallback for nulls) in `app/integrations/earth_engine.py`
- [ ] T012 [US1] Implement `ImergIngestor.ingest()` (window from `Clock`, per-day completeness check, one transaction, upsert via FR1 `upsert_observations`, audit) in `app/services/ingestion/imerg_ingestor.py`
- [ ] T013 [US1] Implement `run_imerg_step()` and `register_ee_jobs()` (`register_ingestor(ImergIngestor(...))`, `register_step(20, "ingest.gpm_imerg", run_imerg_step)`) in `app/services/ingestion/ee_jobs.py`; one-line call in `app/main.py`
- [ ] T014 [US1] Add `tests/live/test_ee_live.py::test_imerg_dadu_2022_08_26` (marker `ee`) comparing EE `max3h_mm` with `rolling_max_3h` on the same tehsil's half-hourly mean series (SC-003, tolerance documented)

**Checkpoint**: Daily IMERG ingest works with a fake gateway and live.

---

## Phase 4: User Story 2 - Replay-ready IMERG (Priority: P1)

**Goal**: Committed caches and offline replay ingest without lookahead.

**Independent Test**: `pytest tests/integration/ingestion/test_imerg_replay.py`.

### Tests for User Story 2 ⚠️

- [ ] T015 [P] [US2] Write `tests/integration/ingestion/test_imerg_replay.py`: `ReplayClock` at 2025-08-15 with a fixture CSV → no row after 2025-08-14; `scenario_id` on every row; `DataSourceStatus` untouched

### Implementation for User Story 2

- [ ] T016 [US2] Implement `CachedImergSource(scenario_key, replay_today, lag_days)` reading `data/replay/<key>/imerg_daily.csv` and selection by `ScenarioContext` in `app/services/ingestion/imerg_ingestor.py`
- [ ] T017 [US2] Implement `scripts/cache_replay_imerg.py --scenario <key>` (window from `ReplayScenario`/seed YAML minus 30 days spin-up; all tehsils of scenario districts; writes `date,tehsil_pcode,rainfall_mm,rainfall_max_3h_mm,n_images`)
- [ ] T018 [US2] Generate and commit `data/replay/sindh-2022/imerg_daily.csv`, `data/replay/buner-2025/imerg_daily.csv`, `data/replay/control-2021/imerg_daily.csv`; eyeball Buner 2025-08-15 max3h vs neighbours

**Checkpoint**: FR5 can replay FR2 offline.

---

## Phase 5: User Story 3 - Terrain per tehsil (Priority: P2)

**Goal**: Elevation and slope per tehsil from SRTM, reproducible from seed.

**Independent Test**: `pytest tests/integration/ingestion/test_terrain_seed.py`.

### Tests for User Story 3 ⚠️

- [ ] T019 [P] [US3] Write `tests/integration/ingestion/test_terrain_seed.py`: loading `tehsil_terrain.csv` sets `elevation_mean_m` (and `slope_mean_deg` when the column exists) for every pilot tehsil; unknown pcode → warning, not crash; one `TERRAIN_COMPUTED` audit

### Implementation for User Story 3

- [ ] T020 [US3] Implement `compute_terrain(gateway, fc)` (SRTM `elevation` mean at 90 m, `ee.Terrain.slope` mean, `tileScale=4`) and `load_terrain_seed(db, path)` in `app/services/ingestion/terrain.py`
- [ ] T021 [US3] Implement `scripts/compute_tehsil_terrain.py` (writes DB and `data/seeds/tehsil_terrain.csv`); run it for Sindh + KP and commit the CSV
- [ ] T022 [US3] Write `alembic/versions/fr02_0001_tehsil_slope.py` once `slope_mean_deg` is merged into data-model.md; hook `load_terrain_seed` into `scripts/seed.py` via a documented one-line call

---

## Phase 6: User Story 4 - Sentinel-1 primitives (Priority: P2)

**Goal**: FR21 can compute and render before/after flood maps.

**Independent Test**: `pytest -m ee tests/live/test_ee_live.py -k s1` for Dadu, Aug 2022; unit tests check parameter wiring with the fake gateway.

### Tests for User Story 4 ⚠️

- [ ] T023 [P] [US4] Add unit tests to `tests/unit/integrations/test_earth_engine.py`: `S1Params.from_settings()`; `s1_flood_change` returns `NO_IMAGERY` when either window is empty (fake gateway); area conversion m² → km²; share computed against PostGIS area
- [ ] T024 [P] [US4] Add `tests/live/test_ee_live.py::test_s1_dadu_2022` (marker `ee`): Dadu tehsils total new water > 10 km² for after window 2022-08-25..2022-09-08 (SC-006); `thumbnail_png` returns PNG bytes (`\x89PNG` header)

### Implementation for User Story 4

- [ ] T025 [US4] Implement `s1_collection()`, `s1_acquisitions()`, `permanent_water_mask()`, `slope_mask()` in `app/integrations/earth_engine.py`
- [ ] T026 [US4] Implement `s1_flood_change(region, before, after, params)` returning `S1Change(image, before_dates, after_dates, status)` in `app/integrations/earth_engine.py`
- [ ] T027 [US4] Implement `flood_area_by_tehsil(change, fc, params)` and `thumbnail_png(image, bbox, vis, dimensions)` (getThumbURL + httpx download) in `app/integrations/earth_engine.py`

---

## Phase 7: User Story 5 - Earth Engine failure path (Priority: P2)

**Goal**: NFR9 for IMERG.

**Independent Test**: `pytest tests/integration/ingestion/test_imerg_ingest.py -k "outage or recovery"`.

### Tests for User Story 5 ⚠️

- [ ] T028 [P] [US5] Add tests: gateway raises `EEUnavailable` → per-tehsil copy-forward (cap 3 days), `GPM_IMERG` unhealthy, one admin alert, `DATA_SOURCE_STALE` audit; recovery overwrites stale rows; credentials missing → unhealthy with `last_error="EE not configured"`

### Implementation for User Story 5

- [ ] T029 [US5] Wire FR1's `plan_copy_forward()` (per tehsil) and `update_source_status()` into `ImergIngestor.ingest()` in `app/services/ingestion/imerg_ingestor.py`

---

## Phase 8: Polish

- [ ] T030 [P] Time a 3-day IMERG run over all Sindh + KP tehsils and record it in `implement.md` (SC-001)
- [ ] T031 [P] Tune S1 thresholds on Dadu 2022 and record the chosen values and a screenshot path in `implement.md` (clarify open item 3)
- [ ] T032 ruff + mypy clean on `app/integrations/earth_engine.py` and `app/services/ingestion/{imerg_*,terrain,ee_*}.py`
- [ ] T033 [P] Add `--backfill-days 30` to `scripts/cache_replay_imerg.py` (live mode: writes live rows) and run it before the first live pipeline run, so FR3's 30-day windows are full (FR3 hand-off)
- [ ] T034 [P] Add `--export ml/data/imerg_tehsil_daily.parquet --start 2001-01-01 --end 2025-12-31` to `scripts/cache_replay_imerg.py` for FR3 model training (pilot tehsils; one EE call per day, run overnight)

---

## Dependencies & Execution Order

### Phase Dependencies
- Setup → Foundational (needs FR1 Phase 2) → US1 → US2 and US5. US3 and US4 need only Phase 2. Polish last.

### User Story Dependencies
- **US1**: Phase 2.
- **US2**: US1's ingestor (same file) and FR5 scenario windows.
- **US3**: Phase 2 only.
- **US4**: Phase 2 only; FR21's precompute script depends on US4.
- **US5**: US1 and FR1's staleness helper.

### Within each story
Tests first (fail) → gateway functions → ingestor/service → job/script.

### Parallel opportunities
- T003 alongside T001–T002.
- T005, T006 in Phase 2.
- US3 (T019–T022) and US4 (T023–T027) in parallel with US1 by a second developer, since they touch different functions; coordinate edits to `earth_engine.py` by section (IMERG / S1 / terrain).
- All [P] test tasks inside a story.

---

## Implementation Strategy

1. T003 first: the smoke test resolves the IMERG latency question on day one.
2. US1 + US2 before the first replay rehearsal (FR5 needs the caches).
3. US4 early enough for FR21 to precompute maps (D1 demo asset).
4. US3 before FR3 model training; US5 before the demo.

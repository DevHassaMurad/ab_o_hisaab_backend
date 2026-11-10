# Agent Brief: FR2 Earth Engine Ingestion

## Owner role
**Satellite & GIS**

## Mission
Bring tehsil-level satellite data into the system through Google Earth Engine. Every day, reduce GPM IMERG half-hourly rainfall over each active tehsil to a daily total and a maximum 3-hour intensity. Once, compute SRTM elevation and slope per tehsil. Provide the Sentinel-1 change-detection building blocks (with the JRC permanent-water mask) that FR21 uses for flood maps.

## Scope

**In (MVP)**
- Earth Engine service-account client with retries and a health check.
- Tehsil boundaries (OCHA COD-AB adm3, pilot Sindh + KP) turned into an EE `FeatureCollection` from PostGIS.
- Daily IMERG ingest per tehsil: `rainfall_mm` (daily sum) and `rainfall_max_3h_mm` (UC03-3), as step 20 of the daily pipeline, plugged into FR1's ingestion framework.
- Stale path for EE failures or incomplete IMERG days (NFR9), reusing FR1's staleness helper.
- Replay cache of IMERG tehsil values for `sindh-2022`, `buner-2025`, `control-2021` in `data/replay/<key>/imerg_daily.csv`.
- One-off SRTM terrain script: `Tehsil.elevation_mean_m` (and proposed `slope_mean_deg`), also written to a seed CSV so seeding needs no EE.
- Sentinel-1 primitives in `app/integrations/earth_engine.py`: collection filter, VV change detection, JRC mask, slope mask, per-tehsil flooded area, PNG thumbnail export. FR21 calls these.

**Out**
- Persisting `SatelliteFloodMap` rows, the precompute script, the read endpoint and live verification (FR21, D1).
- NASA POWER and context indicators (FR1). Forecast rainfall (FR3).
- Roadmap: live Sentinel-1 ingest on new acquisitions (FR21 `FR21_LIVE_VERIFY`), IMERG Early run via NASA PPS direct download if the EE collection lags (clarify #1), national rollout to all 577 tehsils.

## Read first
- `specs/_authoring-guide.md`, `.specify/memory/constitution.md` (Principles VII, VIII, IX, D1)
- `specs/000-foundation/data-model.md` §1 (`Tehsil`), §2 (`ClimateObservation`, `DataSourceStatus`, `SatelliteFloodMap`)
- `specs/000-foundation/plan.md`, `specs/000-foundation/implement.md`
- `specs/001-fr01-nasa-power-ingestion/plan.md` (ingestion framework: `Ingestor`, registry, staleness helper)
- `specs/021-fr21-satellite-verification/plan.md` (how the S1 primitives are used)
- Earth Engine catalog pages for `NASA/GPM_L3/IMERG_V07`, `COPERNICUS/S1_GRD`, `JRC/GSW1_4/GlobalSurfaceWater`, `USGS/SRTMGL1_003`

## Depends on / Blocks

| | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | `Tehsil` geometries, settings `GEE_SERVICE_ACCOUNT`/`GEE_KEY_FILE`, `register_step`, `ScenarioContext`, `Clock` |
| Depends on | FR1 (`001-fr01-nasa-power-ingestion`) | `Ingestor` protocol, `register_ingestor`, `upsert_observations`, `staleness.*`, `POST /ingestion/run` |
| Depends on | FR20 (`020-fr20-audit-log`) | `AuditService.record` (stub OK) |
| Depends on | FR5 (`005-fr05-event-replay`) | Scenario windows and districts for the cache |
| Blocks | FR3 (`003-fr03-flood-risk-score`) | Tehsil rainfall and elevation features |
| Blocks | FR4 (`004-fr04-flood-modes`) | `rainfall_max_3h_mm` and slope for `FLASH` mode |
| Blocks | FR5 | IMERG replay caches |
| Blocks | FR21 (`021-fr21-satellite-verification`) | EE client and S1 change-detection primitives |

## Interfaces

**Consumes**: `Ingestor`, `register_ingestor`, `upsert_observations`, `plan_copy_forward`, `update_source_status` (FR1); `AuditService.record`; `AlertService.enqueue` (via FR1 helper); `Clock`; `ScenarioContext`; `register_step(order, name, func)`.

**Provides**:
- `ImergIngestor` (`source = GPM_IMERG`), registered with FR1's registry and pipeline step 20.
- `app/integrations/earth_engine.py`:
  - `init_ee(settings) -> None`, `ee_health() -> EEHealth`
  - `tehsil_feature_collection(db, tehsil_ids) -> ee.FeatureCollection`
  - `imerg_daily_image(day) -> ee.Image` (bands `daily_mm`, `max3h_mm`, property `n_images`)
  - `reduce_imerg(day, fc) -> list[TehsilRain]`
  - `s1_collection(region, start, end, orbit_pass=None) -> ee.ImageCollection`
  - `s1_flood_change(region, before, after, params: S1Params) -> S1Change` (image with bands `before_vv`, `after_vv`, `flooded`, plus acquisition dates)
  - `flood_area_by_tehsil(change, fc, params) -> list[TehsilFlood]`
  - `thumbnail_png(image, region, vis, dimensions) -> bytes`
  - `s1_acquisitions(region, start, end) -> list[date]` (used by FR21 roadmap)
- `S1Params` dataclass (thresholds live in settings, Principle IX).

## Files you own
```
app/integrations/earth_engine.py
app/services/ingestion/imerg_ingestor.py
app/services/ingestion/ee_boundaries.py
app/services/ingestion/terrain.py
app/services/ingestion/imerg_math.py
app/services/ingestion/ee_jobs.py
alembic/versions/fr02_0001_tehsil_slope.py
scripts/ee_smoke_test.py
scripts/compute_tehsil_terrain.py
scripts/cache_replay_imerg.py
data/seeds/tehsil_terrain.csv
data/replay/<key>/imerg_daily.csv   (generated, committed)
tests/fixtures/earth_engine/*.json
tests/unit/ingestion/test_imerg_math.py
tests/unit/integrations/test_earth_engine.py
tests/integration/ingestion/test_imerg_ingest.py
tests/integration/ingestion/test_imerg_replay.py
tests/integration/ingestion/test_terrain_seed.py
tests/live/test_ee_live.py          (marker `ee`, skipped without credentials)
```
One-line edit: `register_ee_jobs()` call in `app/main.py`.

## Guardrails
- **VII Degrade gracefully**: EE errors and incomplete IMERG days never stop the pipeline; FR1's staleness helper flags and alerts.
- **IX Config over code**: dataset IDs, band names, scales and S1 thresholds are settings. Tehsils come from PostGIS.
- **VIII Honest**: IMERG and S1 data are real. Replay caches are real historical data, not simulated.
- **III Traceability**: one `DATA_INGESTED` audit entry per IMERG run; `TERRAIN_COMPUTED` once per terrain load.
- **FR5 isolation**: IMERG rows carry `scenario_id`. Replay never touches `DataSourceStatus`.
- **Security**: the service-account key file is never committed. Path in `GEE_KEY_FILE`; `.gitignore` covers `*.json` keys under `secrets/`.

## Definition of Done
- [ ] `python scripts/ee_smoke_test.py` authenticates and prints the latest IMERG image time and a 2022-08-26 Dadu S1 acquisition list
- [ ] Daily IMERG ingest writes one row per active tehsil per complete day in < 5 min
- [ ] `rainfall_max_3h_mm` matches the pure-Python reference (`imerg_math.rolling_max_3h`) on fixture data
- [ ] EE down → stale rows, unhealthy status, one admin alert, pipeline reaches FR3
- [ ] Caches committed for all three scenarios; replay never returns a day after `replay_today − REPLAY_IMERG_LAG_DAYS`
- [ ] `elevation_mean_m` set for every pilot tehsil from `data/seeds/tehsil_terrain.csv`
- [ ] FR21 can call `s1_flood_change` + `flood_area_by_tehsil` + `thumbnail_png` for Dadu, Aug 2022
- [ ] ruff and mypy clean; unit tests do not need EE credentials

## Hand-off notes
- **To FR3/FR4**: IMERG rows have `source=GPM_IMERG`, `tehsil_id` and `district_id` both set, `rainfall_mm` (UTC day total) and `rainfall_max_3h_mm` (tehsil mean of per-pixel max rolling 3-hour total). The newest complete day is usually `today − 1` (Late run) or `today − 2`.
- **To FR21**: use the primitives; do not call `ee.*` directly from FR21 except via `earth_engine.py`. Thumbnails are temporary URLs, so `thumbnail_png` returns bytes for you to save.
- **To FR5**: `scripts/cache_replay_imerg.py --scenario <key>` writes one CSV per scenario. Live ingest in replay reads it through `CachedImergSource`.

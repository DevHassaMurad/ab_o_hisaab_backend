# Implementation Plan: FR2 Earth Engine Ingestion

**Branch**: `002-fr02-earth-engine-ingestion` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-fr02-earth-engine-ingestion/spec.md`

## Summary

`app/integrations/earth_engine.py` wraps the Earth Engine Python API: service-account init, retries, tehsil `FeatureCollection` from PostGIS, an IMERG daily image with two bands (`daily_mm`, `max3h_mm`) built server-side, a single `reduceRegions` per day, Sentinel-1 VV change detection with JRC and slope masks, per-tehsil flooded area and PNG thumbnails. `ImergIngestor` implements FR1's `Ingestor` protocol, runs as pipeline step 20, writes `ClimateObservation(source=GPM_IMERG)` in one transaction and reuses FR1's stale path. A cached source replays committed CSVs. A one-off script computes SRTM elevation and slope per tehsil into a seed CSV.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: `earthengine-api` (pin an exact version ≥ 1.x; verify), `google-auth` (pulled by earthengine-api), httpx (thumbnail download), sqlalchemy/geoalchemy2, shapely (simplify fallback). New dependency: `earthengine-api`.

**Storage**: PostgreSQL + PostGIS (`tehsil`, `climate_observation`, `data_source_status`). Cache files in `data/replay/<key>/imerg_daily.csv`. Terrain seed `data/seeds/tehsil_terrain.csv`.

**Testing**: pytest; a `FakeEE` seam (`EarthEngineGateway` protocol) so unit/integration tests never call EE; a `tests/live/` suite marked `ee` that runs only with credentials.

**Target Platform**: Linux container; dev on Windows/macOS. The key file is mounted at `GEE_KEY_FILE`.

**Project Type**: web-service (scheduled job + scripts).

**Performance Goals**: IMERG step < 5 min for 3 look-back days over ≈ 260 tehsils (one `getInfo` per day, ≈ 10–40 s each). Terrain script < 10 min, once.

**Constraints**: EE request payload ≤ ~10 MB; `getInfo` collection limit 5 000 elements; EE concurrency quota (keep calls sequential in the job). Thumbnail URLs expire, so bytes are saved immediately.

**Scale/Scope**: Pilot 12 active districts (≈ 60 tehsils); Sindh + KP ≈ 260 tehsils; national 577.

## Constitution Check

| Principle | How satisfied | ✅/⚠️ |
|---|---|---|
| I Anticipate | Adds tehsil detail and flash-flood intensity; runs inside the 15 min budget | ✅ |
| II Human authority | No money path | ✅ |
| III Traceability | `DATA_INGESTED` per run, `DATA_SOURCE_STALE`/`RECOVERED` via FR1 helper, `TERRAIN_COMPUTED` once | ✅ |
| IV Explainable | Dataset IDs, band, scale and image count per day go into the run audit payload; partial days flagged in `quality_flags` | ✅ |
| VI Privacy | No PII | ✅ |
| VII Degrade gracefully | Shared stale path; step never raises; "EE not configured" is an unhealthy state, not a crash | ✅ |
| VIII Honest prototype | Real data; replay caches are real history, not simulated | ✅ |
| IX Config over code | Dataset IDs, bands, scales, S1 thresholds in settings; tehsils from DB | ✅ |
| X Simplicity | One sync job; no Dataflow/Export pipelines | ✅ |
| XI Tests | Not a money path; tests for the 3-hour maths, idempotency, stale path and replay cut-off | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Server-side 2-band daily image, one `reduceRegions` per day | One request returns both values for all tehsils | 48 reductions per day (slow, hits the 5 000-element limit) |
| Per-pixel rolling 3 h max, then tehsil mean | Captures local bursts; cheap | Max of tehsil-mean series (48× cost); fixed 3 h bins (miss bursts across bin edges) |
| `scale=5000` with centroid fallback | Small tehsils get values | Native 11 km (nulls for small polygons) |
| Inline simplified geometries | No asset management for a hackathon | EE table asset upload (needs GCS or manual upload; optional later) |
| `EarthEngineGateway` protocol around EE | Testable without credentials; one place for retries | Calling `ee` from services (untestable) |
| `getInfo()` for ≤ 600 features, `ee.data.computeFeatures(..., fileFormat="PANDAS_DATAFRAME")` above that (verify availability in the pinned version) | Simple and synchronous | `Export.table.toDrive` (async, needs Drive) |
| S1 before = median of a dry window, after = latest-on-top mosaic | Stable reference; after reflects the newest look | min composite (amplifies speckle false positives) |
| dB difference + absolute threshold, JRC > 80 %, slope < 5°, ≥ 8 connected px | Standard, explainable change detection (UN-SPIDER-style) | Otsu auto-threshold (less predictable for a demo), ML segmentation (time) |
| PNG thumbnails in EPSG:3857 | Frontend overlay with Leaflet without reprojection | EE map tiles (`getMapId`; URLs expire, need a live token) |
| Terrain once into seed CSV | Reproducible seeding without EE | Compute at every seed (needs creds everywhere) |

## Data model slice

| Entity | Fields read | Fields written |
|---|---|---|
| Tehsil | `id`, `pcode`, `district_id`, `geom` | `elevation_mean_m` (+ proposed `slope_mean_deg`) |
| District | `is_active` | — |
| ClimateObservation | — | `district_id`, `tehsil_id`, `obs_date`, `source=GPM_IMERG`, `rainfall_mm`, `rainfall_max_3h_mm`, `temp_c=NULL`, `soil_wetness=NULL`, `lead_day=NULL`, `is_stale`, `stale_since`, `scenario_id` (+ FR1-proposed `quality_flags`) |
| DataSourceStatus | — | `GPM_IMERG` row (live only) |
| ReplayScenario | `key`, window, `districts` | — |
| AuditLog / Alert | — | via FR1 helpers |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Change | Why |
|---|---|---|
| Tehsil | `slope_mean_deg: float, nullable` (SRTM, degrees) | FR4 `FLASH` mode uses "slope or elevation" (guide) |
| ClimateObservation | Reuse FR1's proposed `quality_flags` (e.g. `"imerg_partial:44/48"`, `"centroid_fallback"`) | Principle IV |
| ClimateObservation | Clarify in notes: IMERG rows set both `tehsil_id` and `district_id` | Simpler district queries; no schema change |

## API contracts

FR2 adds no router. It registers `GPM_IMERG` with FR1's ingestion registry, so the shared endpoint works:

### `POST /api/v1/ingestion/run`, role `ADMIN` (owned by FR1)

Request:
```json
{ "source": "GPM_IMERG", "start": "2026-11-06", "end": "2026-11-09", "district_pcodes": ["PK605"] }
```
Response `200`:
```json
{
  "source": "GPM_IMERG",
  "start": "2026-11-06",
  "end": "2026-11-09",
  "scenario_id": null,
  "districts_requested": 1,
  "tehsils_requested": 4,
  "rows_fresh": 12,
  "rows_stale": 0,
  "rows_not_yet_published": 4,
  "days": [
    {"date": "2026-11-06", "n_images": 48, "status": "COMPLETE"},
    {"date": "2026-11-09", "n_images": 31, "status": "NOT_YET_PUBLISHED"}
  ],
  "last_valid_date": "2026-11-08",
  "is_healthy": true,
  "errors": [],
  "duration_s": 27.9
}
```
Errors (from FR1's router): `422 RANGE_TOO_LARGE` (> 31 days), `409 INGEST_ALREADY_RUNNING`, `422 UNKNOWN_SOURCE`. EE failures return `200` with `is_healthy:false`, `errors:[{"scope":"earth_engine","error":"EEException: Too many concurrent aggregations"}]`.

### `GET /api/v1/ingestion/observations?source=GPM_IMERG&tehsil_id=…` (owned by FR1)

Same shape as FR1, with `tehsil_id` and `rainfall_max_3h_mm` filled.

### Health

`GET /api/v1/health/data-sources` (foundation) shows the `GPM_IMERG` row FR2 maintains.

## Scheduled jobs

| Job | Schedule | Function | Notes |
|---|---|---|---|
| Pipeline step 20 `ingest.gpm_imerg` | Foundation `PIPELINE_CRON` (01:00 UTC) | `app/services/ingestion/ee_jobs.py: run_imerg_step(db, clock)` | After FR1 (10), before FR3 (30). Never raises |

## Project Structure

### Documentation (this feature)
```text
specs/002-fr02-earth-engine-ingestion/
├── agent.md  spec.md  clarify.md  plan.md  tasks.md  implement.md
```

### Source Code (repository root)
```text
app/
├── integrations/
│   └── earth_engine.py          # EarthEngineGateway protocol, EEGateway (real), init_ee, ee_health,
│                                # imerg_daily_image, reduce_imerg, s1_collection, s1_flood_change,
│                                # flood_area_by_tehsil, s1_acquisitions, thumbnail_png, S1Params
└── services/ingestion/
    ├── ee_boundaries.py         # tehsil_geojson(db, tehsil_ids) → simplified GeoJSON features
    ├── imerg_math.py            # rolling_max_3h(), daily_total() (pure Python reference for tests)
    ├── imerg_ingestor.py        # ImergIngestor, CachedImergSource
    ├── terrain.py               # compute_terrain(gateway, fc) → rows; load_terrain_seed(db, path)
    └── ee_jobs.py               # run_imerg_step, register_ee_jobs()
alembic/versions/fr02_0001_tehsil_slope.py      # after the proposal is merged
scripts/
├── ee_smoke_test.py             # auth + latest IMERG time + S1 acquisitions over Dadu Aug 2022
├── compute_tehsil_terrain.py    # writes DB + data/seeds/tehsil_terrain.csv
└── cache_replay_imerg.py        # --scenario <key> → data/replay/<key>/imerg_daily.csv; --backfill-days N (live rows); --export <parquet> (FR3 training)
data/seeds/tehsil_terrain.csv    # tehsil_pcode,elevation_mean_m,slope_mean_deg,source,computed_at
data/replay/{sindh-2022,buner-2025,control-2021}/imerg_daily.csv   # date,tehsil_pcode,rainfall_mm,rainfall_max_3h_mm,n_images
tests/
├── fixtures/earth_engine/{imerg_reduce_2022-08-26.json, imerg_halfhourly_series.json}
├── unit/ingestion/test_imerg_math.py
├── unit/integrations/test_earth_engine.py
├── integration/ingestion/{test_imerg_ingest,test_imerg_replay,test_terrain_seed}.py
└── live/test_ee_live.py         # @pytest.mark.ee
```

**Structure Decision**: single project; FR2 adds files to FR1's `app/services/ingestion/` package without editing FR1's files.

## Complexity Tracking

None.

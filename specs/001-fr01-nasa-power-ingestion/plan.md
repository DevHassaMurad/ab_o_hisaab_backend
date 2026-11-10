# Implementation Plan: FR1 NASA POWER Ingestion

**Branch**: `001-fr01-nasa-power-ingestion` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-fr01-nasa-power-ingestion/spec.md`

## Summary

A sync `httpx` client calls the NASA POWER daily point API for each active district centroid (4 workers in a thread pool), parses and validates the JSON, and upserts `ClimateObservation` rows in one transaction with the source status and audit entries. A shared staleness helper writes copy-forward rows for overdue days and drives `DataSourceStatus` and the admin alert. The ingest is step 10 of the foundation's daily pipeline and never raises, so FR3 always runs. In replay, a cached client reads raw POWER JSON from `data/replay/<key>/nasa_power/` with a no-lookahead cut-off. A separate monthly job refreshes context indicators and recomputes `District.vulnerability_weight`.

FR1 also creates the small ingestion framework (`Ingestor` protocol, registry, generic endpoints) that FR2 plugs into.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: fastapi, pydantic v2, sqlalchemy 2.x, httpx (sync client), apscheduler (via foundation wrapper), structlog. No new dependencies.

**Storage**: PostgreSQL 16 + PostGIS. Tables `climate_observation`, `context_indicator`, `data_source_status`, `district` (foundation). Replay cache files under `data/replay/`.

**Testing**: pytest, `httpx.MockTransport` for POWER, recorded fixtures in `tests/fixtures/nasa_power/`, a Postgres test DB from the foundation.

**Target Platform**: Linux container; Windows/macOS dev.

**Project Type**: web-service (REST + scheduled job).

**Performance Goals**: < 3 min for 12 pilot districts, < 6 min for 68 districts (10-day window). One POWER call per district per run.

**Constraints**: NASA POWER asks clients to throttle; max 4 concurrent requests, 60 s timeout, 3 retries with exponential backoff (2, 4, 8 s) on 429/5xx/transport errors. No floats for money (not applicable here). UTC dates.

**Scale/Scope**: 12 active pilot districts (foundation open item 2), up to 160 nationally. Backfill 2001–2025 for training: 25 years × 68 districts, one request per district-year.

## Constitution Check

| Principle | How satisfied | ✅/⚠️ |
|---|---|---|
| I Anticipate | Ingest is the first pipeline step and keeps the 15 min budget small (SC-001) | ✅ |
| II Human authority | No money paths touched | ✅ |
| III Traceability | `DATA_INGESTED`, `DATA_SOURCE_STALE`, `DATA_SOURCE_RECOVERED`, `CONTEXT_INDICATORS_REFRESHED`, `VULNERABILITY_WEIGHT_UPDATED` written in the same transaction as the data | ✅ |
| IV Explainable | Raw validated values stored; `quality_flags` records every fix; `vulnerability_inputs` stores normalised inputs and weights | ✅ |
| V Fair | Imputed (sparse-data) flags exposed for FR11's fairness check | ✅ |
| VI Privacy | No PII. DC reads scoped to own district | ✅ |
| VII Degrade gracefully | Copy-forward with `is_stale`/`stale_since`, `DataSourceStatus`, admin alert, step never raises | ✅ |
| VIII Honest prototype | Data is real. Stale rows are labelled. Nothing simulated | ✅ |
| IX Config over code | Districts and centroids from DB; lags, ranges, weights from settings; indicator file is data | ✅ |
| X Simplicity | Thread pool + sync httpx, no queue | ✅ |
| XI Tests | Not a money path; tests protect the parser, validation, staleness, idempotency and API contracts | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Point API per district centroid | Guide and data model; ≤ 160 calls a day is trivial | Regional API (bbox limits, more parsing); per-tehsil points (same grid cell repeated) |
| `community=AG`, `time-standard=UTC` (verify that `time-standard` is honoured on the daily endpoint; if it is not, accept POWER's default day boundary and record `"day_boundary":"POWER_DEFAULT"` in the run audit payload) | Units mm/day and °C; UTC aligns with IMERG and the constitution | `RE`/`SB` communities (different units for some params) |
| Re-fetch a 10-day window every run | Captures late publication and revisions; upsert makes it idempotent | Only fetch "yesterday" (misses latency and revisions) |
| Fetch all, then one write transaction | Atomic run, audit in the same transaction, simple retry | Per-district commits (partial runs, many audit rows) |
| Copy-forward the whole last valid row, cap 3 days | Literal NFR9; errs toward anticipation; cap prevents runaway repetition | Zero rain (hides risk); unlimited copy (stale forever) |
| Cache raw POWER JSON for replay | Same parser and validation in replay; files are diff-able | Cache parsed CSV (two code paths) |
| Thread pool (4) with sync httpx | APScheduler jobs are sync; simplest | asyncio inside a sync job (event-loop juggling) |
| `pg_try_advisory_xact_lock(hashtext('ingest:NASA_POWER'))` | Prevents scheduler and manual runs overlapping | Table lock (heavier), no lock (duplicate alerts) |
| District indicators from a curated CSV; World Bank API for national reference | World Bank API has no district series for Pakistan | Scraping EasyData (fragile, unclear API, verify) |
| Min-max normalisation across active districts | Transparent, bounded 0–1, easy to explain | z-scores (unbounded), PCA (opaque) |

## Data model slice

| Entity | Fields read | Fields written |
|---|---|---|
| District | `id`, `pcode`, `province`, `centroid_lat`, `centroid_lon`, `is_active` | `vulnerability_weight` (+ proposed `vulnerability_inputs`) |
| ClimateObservation | — | `district_id`, `tehsil_id=NULL`, `obs_date`, `source=NASA_POWER`, `rainfall_mm`, `rainfall_max_3h_mm=NULL`, `temp_c`, `soil_wetness`, `lead_day=NULL`, `is_stale`, `stale_since`, `scenario_id` (+ proposed `soil_wetness_top`, `quality_flags`) |
| DataSourceStatus | all | `source ∈ {NASA_POWER, WORLD_BANK, EASYDATA}`: `last_success_at`, `last_valid_date`, `last_error`, `is_healthy` (live runs only) |
| ContextIndicator | all | `district_id`, `indicator`, `value`, `period`, `source` |
| ReplayScenario | `key`, `start_date`, `end_date`, `districts`, `sim_date` | — |
| AuditLog | — | via `AuditService.record` |
| Alert | — | via `AlertService.enqueue` (admin outage notice) |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Change | Why |
|---|---|---|
| ClimateObservation | `soil_wetness_top: float 0–1, nullable` (NASA POWER `GWETTOP`) | The guide lists `GWETTOP`; flash mode (FR4) benefits from top-soil wetness |
| ClimateObservation | `quality_flags: JSONB, default []` e.g. `["gwetroot_pct_to_fraction","rain_neg_clamped"]` | Principle IV: record every validation fix (UC03-5). FR2 reuses it for partial IMERG days |
| ClimateObservation | Make the unique key `UNIQUE NULLS NOT DISTINCT (district_id, tehsil_id, obs_date, source, lead_day, scenario_id)` (PostgreSQL 15+) | With plain `UNIQUE`, NULL `tehsil_id`/`lead_day`/`scenario_id` make every row distinct and re-ingest duplicates rows |
| ContextIndicator | Unique `(district_id, indicator, period, source)` | Idempotent refresh |
| District | `vulnerability_inputs: JSONB, nullable`: `{"computed_at","weights","indicators":{"poverty_headcount":{"raw":0.61,"norm":0.82,"imputed":false,"period":"2019-20","source":"PSLM"}},"imputed":false}` | Principle IV traceability; NFR5 sparse-data flag for FR11 |
| Alert | `recipient_type` += `ADMIN`; `purpose` += `DATA_SOURCE_DOWN` | UC03-2a "alerts the admin". Shared with FR2 |

## API contracts

Router `app/api/v1/ingestion.py`, prefix `/api/v1/ingestion`, OpenAPI tag `Data`. All responses follow foundation conventions.

### `POST /api/v1/ingestion/run`, role `ADMIN`

Runs one registered ingestor synchronously. Works for any source in the registry (FR2 registers `GPM_IMERG`).

Request:
```json
{ "source": "NASA_POWER", "start": "2026-10-31", "end": "2026-11-09", "district_pcodes": ["PK605", "PK403"] }
```
`start`/`end`/`district_pcodes` optional: defaults are the look-back window and all active districts. Honors `X-Scenario-Id` (replay ingest for one day).

Response `200`:
```json
{
  "source": "NASA_POWER",
  "start": "2026-10-31",
  "end": "2026-11-09",
  "scenario_id": null,
  "districts_requested": 12,
  "rows_fresh": 84,
  "rows_stale": 0,
  "rows_not_yet_published": 36,
  "values_rejected": 1,
  "last_valid_date": "2026-11-07",
  "is_healthy": true,
  "errors": [],
  "duration_s": 38.4
}
```
Errors: `422 VALIDATION_ERROR` (start > end), `422 RANGE_TOO_LARGE` (> 31 days; use the CLI), `422 UNKNOWN_SOURCE`, `404 DISTRICT_NOT_FOUND`, `409 INGEST_ALREADY_RUNNING`. An upstream outage is **not** an HTTP error: it returns `200` with `is_healthy:false` and `errors:[{"district_pcode":"PK605","error":"HTTP 503 after 3 retries"}]`.

### `GET /api/v1/ingestion/observations`, roles `PDMA_OFFICER`, `ADMIN`, `DC` (own district)

Query: `district_id` (UUID) or `district_pcode`, optional `tehsil_id`, `source` (`NASA_POWER|GPM_IMERG|FORECAST_RAIN`), `from`, `to` (dates, max 366 days), `page`, `page_size` (≤ 500). Scoped by `X-Scenario-Id`.

Response `200`:
```json
{
  "items": [
    {
      "id": "1b0c…",
      "district_id": "7f1e…",
      "tehsil_id": null,
      "obs_date": "2026-11-07",
      "source": "NASA_POWER",
      "rainfall_mm": 12.4,
      "rainfall_max_3h_mm": null,
      "temp_c": 24.1,
      "soil_wetness": 0.41,
      "soil_wetness_top": 0.52,
      "quality_flags": [],
      "is_stale": false,
      "stale_since": null
    }
  ],
  "total": 1, "page": 1, "page_size": 50
}
```
Errors: `400 MISSING_FILTER` (no district), `403 FORBIDDEN_DISTRICT` (DC, other district), `422 RANGE_TOO_LARGE`.

### `GET /api/v1/ingestion/context-indicators?district_id=…`, roles `PDMA_OFFICER`, `ADMIN`, `DC` (own district)

```json
{
  "district_id": "7f1e…",
  "vulnerability_weight": 0.78,
  "vulnerability_inputs": { "weights": {"poverty_headcount": 0.5, "housing_kutcha_pct": 0.3, "literacy_rate": 0.2}, "imputed": false, "computed_at": "2026-11-01T01:05:00Z" },
  "items": [ { "indicator": "poverty_headcount", "value": 0.61, "period": "2019-20", "source": "PSLM via EasyData" } ]
}
```

### `POST /api/v1/ingestion/context-indicators/refresh`, role `ADMIN`

Response `200`: `{"indicators_upserted": 36, "districts_updated": 4, "imputed_districts": ["PK627"], "world_bank_healthy": true, "changes": [{"district_pcode":"PK605","old":0.74,"new":0.78}]}`. Errors: `409 INGEST_ALREADY_RUNNING`, `422 INDICATOR_FILE_INVALID`.

Health is shown by the foundation's `GET /api/v1/health/data-sources`, which reads the rows FR1 maintains.

## Scheduled jobs

| Job | Schedule | Function | Notes |
|---|---|---|---|
| Pipeline step 10 `ingest.nasa_power` | Foundation `PIPELINE_CRON` (`0 1 * * *` UTC) | `app/services/ingestion/jobs.py: run_nasa_power_step(db, clock)` | Never raises; FR2 step 20, FR3 step 30 follow |
| `refresh_context_indicators` | `0 2 1 * *` UTC (02:00 on the 1st) | `jobs.run_context_refresh()` | Changes apply from the next daily run |

## Project Structure

### Documentation (this feature)
```text
specs/001-fr01-nasa-power-ingestion/
├── agent.md  spec.md  clarify.md  plan.md  tasks.md  implement.md
```

### Source Code (repository root)
```text
app/
├── integrations/
│   ├── nasa_power.py            # NasaPowerClient protocol, HttpNasaPowerClient, CachedNasaPowerClient, parse_power_json
│   └── world_bank.py            # fetch_indicator(country, code, years) for national reference values
├── services/ingestion/
│   ├── __init__.py
│   ├── base.py                  # Ingestor protocol, IngestResult, IngestError, DataSource re-export
│   ├── registry.py              # register_ingestor / get_ingestor / all_ingestors
│   ├── validation.py            # validate_power_day(), RANGES
│   ├── staleness.py             # plan_copy_forward(), update_source_status() (shared with FR2)
│   ├── observations.py          # upsert_observations(), latest_valid_date(), list_observations()
│   ├── nasa_power_ingestor.py   # NasaPowerIngestor
│   ├── context_indicators.py    # ContextIndicatorService.refresh()
│   ├── vulnerability.py         # compute_vulnerability()
│   └── jobs.py                  # run_nasa_power_step, run_context_refresh, register_ingestion_jobs()
├── api/v1/ingestion.py
└── schemas/ingestion.py         # IngestRunRequest, IngestRunResult, ObservationOut, ContextIndicatorsOut
alembic/versions/fr01_0001_ingestion_additions.py   # only after the proposals are merged into data-model.md
scripts/ingest_nasa_power.py     # --start/--end backfill; --scenario <key> --to-cache
data/seeds/context_indicators.csv
data/replay/<key>/nasa_power/<district_pcode>.json  (+ optional outages.json)
tests/fixtures/nasa_power/{ok_7d.json, fill_values.json, bad_units.json, pct_soil.json}
tests/unit/ingestion/{test_nasa_power_parse,test_validation,test_staleness,test_vulnerability}.py
tests/integration/ingestion/{test_nasa_power_ingest,test_nasa_power_replay,test_context_refresh}.py
tests/contract/test_ingestion_api.py
```

**Structure Decision**: single project per the constitution layout. FR1 owns the shared ingestion package so FR2 adds files without editing FR1's.

## Complexity Tracking

None.

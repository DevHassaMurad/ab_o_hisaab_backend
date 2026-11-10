# Agent Brief: FR1 NASA POWER Ingestion

## Owner role
**Data & ML**

## Mission
Every day, pull NASA POWER rainfall, temperature and soil wetness for the centroid of every active district, validate it and store it as `ClimateObservation` rows. When NASA POWER is down or late beyond its normal latency, keep the pipeline moving with clearly flagged stale data and alert the admin. On a slower monthly cycle, refresh the World Bank / EasyData context indicators that produce `District.vulnerability_weight`.

## Scope

**In (MVP)**
- Daily NASA POWER point ingest (`PRECTOTCORR`, `T2M`, `GWETROOT`, `GWETTOP`) per active district centroid (UC03-1, UC03-2).
- Validation of fill values (`-999`), ranges and units (UC03-5).
- Stale path: copy forward the last valid values, flag `is_stale`, update `DataSourceStatus`, alert the admin once per outage (UC03-2a, NFR9).
- The shared ingestion framework used by FR2: `Ingestor` protocol, ingestor registry, staleness helper, `POST /ingestion/run`, `GET /ingestion/observations`.
- Registration of the ingest step in the daily pipeline, ahead of FR3 (UC03-6).
- Replay support: a cache script that writes raw POWER JSON to `data/replay/<key>/nasa_power/`, and a cached client that enforces no-lookahead on the replay clock (FR5).
- Historical backfill CLI for FR3 model training.
- Context indicator refresh and vulnerability weight (UC03-4), as the lower-priority story.

**Out**
- IMERG, Sentinel-1, SRTM (FR2, FR21).
- Forecast rainfall (FR3 owns the Open-Meteo integration).
- Feature engineering (3/7/14/30-day sums are FR3/FR4).
- Roadmap: a live EasyData API client (the MVP imports an EasyData/PSLM CSV export), the POWER regional endpoint, per-tehsil POWER queries.

## Read first
- `specs/_authoring-guide.md`, `.specify/memory/constitution.md` (Principles VII, IX, X)
- `specs/000-foundation/data-model.md` §1, §2 (`District`, `ClimateObservation`, `ContextIndicator`, `DataSourceStatus`)
- `specs/000-foundation/plan.md` (interfaces, API conventions), `specs/000-foundation/implement.md` (config, `register_step`)
- `specs/001-fr01-nasa-power-ingestion/{spec,clarify,plan,tasks,implement}.md`
- `specs/005-fr05-event-replay/clarify.md` (scenario windows and districts) once written

## Depends on / Blocks

| | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | Models, `ScenarioContext`, `Clock`, scheduler, `register_step`, `DataSourceStatus`, seeds with centroids |
| Depends on | FR20 (`020-fr20-audit-log`) | `AuditService.record`. The foundation stub is enough to start |
| Depends on | FR14 (`014-fr14-whatsapp-alerts`) | `AlertService.enqueue` for the admin stale alert. The stub (log only) is enough to start |
| Depends on | FR5 (`005-fr05-event-replay`) | Scenario keys and windows for the replay cache |
| Blocks | FR3 (`003-fr03-flood-risk-score`) | Rainfall, soil wetness and temperature features; `last_valid_date`; stale flags |
| Blocks | FR4 (`004-fr04-flood-modes`) | Cumulative rain and soil saturation for `SLOW_ONSET` |
| Blocks | FR5 | Cached POWER inputs for `sindh-2022`, `buner-2025`, `control-2021` |
| Blocks | FR11 (`011-fr11-beneficiary-ranking`) | `vulnerability_weight` and sparse-data flags for the fairness check (NFR5) |
| Blocks | FR2 (`002-fr02-earth-engine-ingestion`) | FR2 plugs into the ingestion framework (`Ingestor`, registry, staleness helper) |

## Interfaces

**Consumes**: `AuditService.record`, `AlertService.enqueue`, `Clock.today()`, `ScenarioContext`, `daily_pipeline.register_step(order, name, func)`, `flags.is_enabled` (not used by FR1 itself).

**Provides** (FR-local protocols in `app/services/ingestion/base.py`; propose to foundation for `interfaces.py` if other teams want them):
- `Ingestor` protocol: `source: DataSource`, `ingest(db, *, start: date | None, end: date | None, district_ids: list[UUID] | None) -> IngestResult`
- `register_ingestor(ingestor)` and `get_ingestor(source)` in `app/services/ingestion/registry.py`
- `staleness.plan_copy_forward(...)` and `staleness.update_source_status(...)`, shared with FR2
- `NasaPowerIngestor`, `ContextIndicatorService.refresh(db) -> RefreshResult`
- Query helper for FR3: `observations.latest_valid_date(db, source, district_id) -> date | None`

## Files you own
```
app/integrations/nasa_power.py
app/integrations/world_bank.py
app/services/ingestion/__init__.py
app/services/ingestion/base.py
app/services/ingestion/registry.py
app/services/ingestion/staleness.py
app/services/ingestion/validation.py
app/services/ingestion/nasa_power_ingestor.py
app/services/ingestion/observations.py
app/services/ingestion/context_indicators.py
app/services/ingestion/vulnerability.py
app/services/ingestion/jobs.py
app/api/v1/ingestion.py
app/schemas/ingestion.py
alembic/versions/fr01_0001_ingestion_additions.py
scripts/ingest_nasa_power.py
data/seeds/context_indicators.csv
data/replay/<key>/nasa_power/*.json   (generated, committed)
tests/fixtures/nasa_power/*.json
tests/unit/ingestion/test_nasa_power_parse.py
tests/unit/ingestion/test_validation.py
tests/unit/ingestion/test_staleness.py
tests/unit/ingestion/test_vulnerability.py
tests/integration/ingestion/test_nasa_power_ingest.py
tests/integration/ingestion/test_nasa_power_replay.py
tests/integration/ingestion/test_context_refresh.py
tests/contract/test_ingestion_api.py
```
One-line edits in foundation-owned files: router include in `app/api/v1/__init__.py`, `register_ingestion_jobs()` call in `app/main.py`.

## Guardrails
- **VII Degrade gracefully**: a POWER failure never aborts the pipeline. The ingest step catches, flags `is_stale`, records `stale_since`, alerts the admin and returns.
- **IX Config over code**: no district list in code. Active districts and centroids come from the `District` table. Lags, ranges and weights are settings.
- **III Traceability**: one `DATA_INGESTED` audit entry per run, plus `DATA_SOURCE_STALE` / `DATA_SOURCE_RECOVERED` on health transitions and `VULNERABILITY_WEIGHT_UPDATED` per changed district, all in the run's transaction.
- **FR5 isolation**: every row carries `scenario_id` from `ScenarioContext`. Replay never touches `DataSourceStatus` (a live table).
- **IV Explainable**: store raw validated values. Validation fixes (unit conversions, clamps) go into `quality_flags`.
- **VIII Honest**: POWER data is real (`simulated` does not apply). Copy-forward rows are labelled stale, never passed off as fresh.

## Definition of Done
- [ ] Daily run ingests all active districts and finishes in < 3 min for the 12-district pilot (budget inside NFR1's 15 min)
- [ ] `-999` and out-of-range values never reach `ClimateObservation` as numbers
- [ ] Outage test: POWER mocked down → stale rows written, `DataSourceStatus.is_healthy=false`, one admin alert, pipeline continues to FR3
- [ ] Recovery test: next good run overwrites stale rows with fresh values and records `DATA_SOURCE_RECOVERED`
- [ ] Re-running the same range twice produces no duplicate rows
- [ ] Replay caches exist for `sindh-2022`, `buner-2025`, `control-2021` and replay ingest never returns a date after `replay_today − REPLAY_POWER_LAG_DAYS`
- [ ] Context refresh sets `vulnerability_weight` for every active district, with imputed inputs flagged
- [ ] Contract tests for `/ingestion/*` pass. ruff and mypy are clean

## Hand-off notes
- **To FR3/FR4**: read `ClimateObservation` with `source=NASA_POWER`, `district_id=…`, scoped by `ScenarioContext`. The latest date may be `today − 3` because of POWER latency. Use `observations.latest_valid_date()` and propagate `is_stale` into `FloodForecast.is_stale_input`. Copy-forward rows exist for at most `POWER_MAX_COPY_FORWARD_DAYS` (3) days.
- **To FR2**: implement `Ingestor` and call `register_ingestor(ImergIngestor())`. Reuse `staleness.update_source_status()` so the banner and admin alert behave the same.
- **To FR5**: the replay cache is raw POWER JSON per district, so live and replay share one parser. Call `scripts/ingest_nasa_power.py --scenario <key> --to-cache` once per scenario (needs internet), then commit the files.
- **To FR11**: `District.vulnerability_inputs.imputed` (proposed field) tells the fairness check which districts have sparse data.

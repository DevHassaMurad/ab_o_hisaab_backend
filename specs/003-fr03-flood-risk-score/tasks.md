---
description: "Task list for FR3 Flood Risk Score"
---

# Tasks: FR3 Flood Risk Score

**Input**: Design documents from `specs/003-fr03-flood-risk-score/`

**Prerequisites**: plan.md, spec.md, clarify.md; foundation merged (`get_clock()`, `current_scenario()`, `register_step`, models, `AuditService` stub); FR4 ModeSpec contract (`specs/004-fr04-flood-modes/plan.md`)

**Tests**: Mandatory. Scoring, tier mapping and fallback feed money paths (Principle XI); write them first and see them fail.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 Add `httpx`, `pandas`, `numpy`, `scikit-learn`, `lightgbm`, `joblib`, `pyyaml`, `respx` (dev) to `pyproject.toml`
- [ ] T002 [P] Create package skeletons `app/services/forecasting/__init__.py`, `app/services/explain/__init__.py`, `app/jobs/steps/__init__.py`, `tests/unit/forecasting/__init__.py`
- [ ] T003 [P] Implement `ForecastSettings` (env prefixes `FORECAST_`, `OPEN_METEO_`) in `app/services/forecasting/settings.py`
- [ ] T004 [P] Add the FR3 variables with defaults to `.env.example` (append-only block "FR3")

## Phase 2: Foundational

- [ ] T005 Migration `alembic/versions/0003_fr03_forecasting.py`: `forecast_provenance` enum, unique constraints on `flood_forecast` and `risk_index` (`NULLS NOT DISTINCT`), and the proposed fields once the foundation owner merges them
- [ ] T006 [P] Pydantic schemas (`RiskMapFeature`, `DistrictRiskDetail`, `LeadPoint`, `RiskIndexTrace`, `ForecastRunRequest/Result`, `ModelInfo`) in `app/schemas/forecasting.py`
- [ ] T007 [P] Define `ForecastRainSource` Protocol and `ForecastRainRecord` dataclass in `app/services/forecasting/rain_ingest.py`
- [ ] T008 [P] Define `RiskExplainInputs` dataclass and tier label maps in `app/services/explain/templates.py`
- [ ] T009 Create fixture factory for districts, tehsils, observations, tiers in `tests/factories/forecasting.py`

**Checkpoint**: schemas, protocols and migration ready.

## Phase 3: User Story 1 - Daily 7-day risk score (P1) 🎯 MVP

**Goal**: tehsil and district probabilities for leads 1–7, scores 0–100, saved with features.

**Independent Test**: `pytest tests/integration/forecasting/test_forecast_run.py -k daily`

### Tests (write first)
- [ ] T010 [P] [US1] Window blending tests (IMERG → NASA POWER → OM past → forecast; sources recorded) in `tests/unit/forecasting/test_features.py`
- [ ] T011 [P] [US1] Score tests: `round_half_up` (0.405→41, 0.995→100), clip, peak lead tie → earliest, lead weights in `tests/unit/forecasting/test_scoring.py`
- [ ] T012 [P] [US1] Aggregation tests: pop-share filter 60/32/8, no-population fallback, all-below-10% fallback, source tehsil recorded in `tests/unit/forecasting/test_aggregation.py`
- [ ] T013 [US1] Integration test: 2 districts × 3 tehsils → 7 FloodForecast per tehsil + district, 1 RiskIndex each, features non-empty, inactive district skipped, re-run idempotent in `tests/integration/forecasting/test_forecast_run.py`

### Implementation
- [ ] T014 [P] [US1] Feature library + `FeatureBuilder.build(db, run_date, tehsils, mode_spec)` in `app/services/forecasting/features.py`
- [ ] T015 [P] [US1] `ModelRegistry` (load `CURRENT`, model + calibrator, feature-list check, `predict_proba`, `contributions`) in `app/services/forecasting/model_registry.py`
- [ ] T016 [P] [US1] `round_half_up_score`, `apply_lead_weights`, `peak`, `top_factors` in `app/services/forecasting/scoring.py`
- [ ] T017 [P] [US1] `aggregate_district` in `app/services/forecasting/aggregation.py`
- [ ] T018 [US1] `ForecastService.run` (per-district loop, upsert rows, re-run lock check, scenario + clock aware) in `app/services/forecasting/service.py`
- [ ] T019 [US1] Register `forecast_score` step in `app/jobs/steps/forecast.py`

**Checkpoint**: scores saved for a date.

## Phase 4: User Story 2 - Tier mapping and trigger hand-off (P1)

**Independent Test**: `pytest tests/unit/forecasting/test_scoring.py -k tier tests/integration/forecasting/test_forecast_run.py -k trigger`

### Tests (write first)
- [ ] T020 [P] [US2] `tier_for` tests: 39→NONE, 40→WATCH, 63→WARNING, 80→ACTION, custom 30/50/70 in `tests/unit/forecasting/test_scoring.py`
- [ ] T021 [US2] Integration test: spy `TriggerEngine` called once per district after commit; raising engine leaves scores saved and lists the district in `trigger_errors`; `RISK_SCORED` audit row per district in `tests/integration/forecasting/test_forecast_run.py`

### Implementation
- [ ] T022 [US2] `tier_for(score, tiers)` reading scenario-scoped `TriggerTier` (default 40/60/80 when absent) in `app/services/forecasting/scoring.py`
- [ ] T023 [US2] Per-district transaction: rows + `AuditService.record(event_type="RISK_SCORED", …)`; then separate transaction calling `TriggerEngine.evaluate` in `app/services/forecasting/service.py`

## Phase 5: User Story 3 - Forecast rainfall input (P1)

**Independent Test**: `pytest tests/unit/forecasting/test_rain_client.py tests/integration/forecasting/test_stale_and_fallback.py -k rain`

### Tests (write first)
- [ ] T024 [P] [US3] respx-mocked Open-Meteo responses (multi-location list): daily sums in UTC, rolling 3 h max, 7 leads, provenance `LIVE_FORECAST`, batching by 50 in `tests/unit/forecasting/test_rain_client.py`
- [ ] T025 [P] [US3] Previous-runs and historical-forecast parsing → `ARCHIVED_FORECAST` / `ARCHIVED_SHORT_LEAD_PROXY` in `tests/unit/forecasting/test_rain_client.py`
- [ ] T026 [US3] Provider down → yesterday's forecast shifted, lead 7 persisted, `is_stale=true`, `DataSourceStatus(FORECAST_RAIN).is_healthy=false`, admin alert enqueued in `tests/integration/forecasting/test_stale_and_fallback.py`

### Implementation
- [ ] T027 [US3] `OpenMeteoClient.fetch_forecast/fetch_previous_runs/fetch_historical_forecast` with timeouts and retries in `app/integrations/forecast_rain.py`
- [ ] T028 [US3] `OpenMeteoForecastSource` (tehsil points via `ST_PointOnSurface`) and `ForecastRainIngestService.ingest` (upsert, stale shift, status update, admin alert) in `app/services/forecasting/rain_ingest.py`
- [ ] T029 [US3] Register `forecast_rain_ingest` step in `app/jobs/steps/forecast.py`
- [ ] T030 [US3] Propagate `forecast_provenance` to FloodForecast/RiskIndex (worst-of rule) in `app/services/forecasting/service.py`
- [ ] T030a [US3] `ReplayForecastRainSource` (reads `data/replay/<key>/forecast_rain.parquet`, only `issue_date == get_clock().today()`) and `get_forecast_source()` selector in `app/services/forecasting/rain_ingest.py`; test in `tests/unit/forecasting/test_rain_client.py -k replay`
- [ ] T030b [US3] Cache writer `scripts/replay/forecast_rain_cache.py --scenario <key> --provenance auto` (previous-runs → historical-forecast → perfect proxy from FR2's `data/replay/<key>/imerg_daily.csv`), records provenance per row

## Phase 6: User Story 4 - Fallback and stale flags (P2)

**Independent Test**: `pytest tests/unit/forecasting/test_fallback.py tests/integration/forecasting/test_stale_and_fallback.py`

### Tests (write first)
- [ ] T031 [P] [US4] Fallback formula tests: slow and flash defaults, sanity cases from plan.md (24 and 96), clipping, contributions sum in `tests/unit/forecasting/test_fallback.py`
- [ ] T032 [US4] Missing artifact → whole mode fallback, `model_version="rules-fallback-1"`; NaN prediction → that tehsil only; stale NASA POWER → `is_stale_input` + `stale_since` on rows, API and audit payload in `tests/integration/forecasting/test_stale_and_fallback.py`

### Implementation
- [ ] T033 [US4] `FallbackScorer.score(frame, mode_spec)` in `app/services/forecasting/fallback.py`
- [ ] T034 [US4] Failure detection and per-tehsil/per-mode switch to fallback in `app/services/forecasting/service.py`
- [ ] T035 [US4] Stale propagation (`any`, `min(stale_since)`) in `app/services/forecasting/features.py`

## Phase 7: User Story 5 - Template explanations (P2)

**Independent Test**: `pytest tests/unit/explain/test_risk_templates.py`

### Tests (write first)
- [ ] T036 [P] [US5] Golden EN/UR strings for the Dadu 82 example; fallback, stale and proxy notices; every number in output ∈ inputs in `tests/unit/explain/test_risk_templates.py`

### Implementation
- [ ] T037 [P] [US5] Templates and factor phrases (EN/UR) in `app/services/explain/templates.py`
- [ ] T038 [US5] `TemplateExplainService.explain_risk` (+ `explain_allocation` delegating to FR11 when present) in `app/services/explain/template_explainer.py`; bind it as the `ExplainService` implementation
- [ ] T039 [US5] Call `explain_risk` for every RiskIndex in `app/services/forecasting/service.py`

## Phase 8: User Story 6 - Read APIs (P3)

**Independent Test**: `pytest tests/contract/test_forecasts_api.py`

### Tests (write first)
- [ ] T040 [P] [US6] Contract tests for all 7 endpoints: shapes, role scoping (DC own district, DONOR 403), `X-Scenario-Id` filter, error codes, `forecast_is_proxy` in `tests/contract/test_forecasts_api.py`

### Implementation
- [ ] T041 [US6] Router with risk-map, district detail, history, tehsil detail, risk-index trace, runs (ADMIN), model-info in `app/api/v1/forecasts.py`; register in `app/api/v1/__init__.py`
- [ ] T042 [US6] Index `risk_index(run_date, district_id, scenario_id)` and simplified-geometry query for the map in `alembic/versions/0003_fr03_forecasting.py`

## Phase 9: User Story 7 - Training and calibration (P3)

**Independent Test**: `pytest tests/unit/forecasting/test_training_smoke.py`

### Tests (write first)
- [ ] T043 [P] [US7] Smoke test on a 200-row fixture: train writes model, calibrator, model card; holdout years excluded; registry loads it in `tests/unit/forecasting/test_training_smoke.py`

### Implementation
- [ ] T044 [P] [US7] Curate `data/labels/flood_events.csv` (GFD, NDMA sitreps; pilot districts) and document sources in `ml/README.md`
- [ ] T045 [US7] `ml/build_training_set.py` (labels, features via `app/services/forecasting/features.py`, noise), `ml/train.py` (logit + lgbm, GroupKFold by year, isotonic/Platt), `ml/evaluate.py` (metrics, reliability, tier hit/false-alarm, `model_card.json`)

## Phase 10: Polish

- [ ] T046 [P] Native-speaker review of Urdu strings; update goldens in `tests/unit/explain/test_risk_templates.py`
- [ ] T047 [P] Runtime test: 68 districts / 260 tehsils with mocked HTTP < 120 s in `tests/perf/test_forecast_runtime.py` (`-m perf`)
- [ ] T048 [P] Score-reproduction check (SC-002) in `tests/integration/forecasting/test_forecast_run.py -k reproduce`
- [ ] T049 `mypy --strict app/services/forecasting app/services/explain`, `ruff check .`
- [ ] T050 Train and commit first artifacts for both modes; run `pytest -m backtest` with FR5

## Dependencies & Execution Order

- Setup → Foundational → US1 → US2 (needs US1 rows) → US3 can start in parallel with US1 (separate files) and is merged before US2's integration test → US4 (needs US1 service) → US5 (needs US1 inputs) → US6 → US7 (independent of API; needed before T050).
- Within each story: tests → pure functions (`features`, `scoring`, `aggregation`, `fallback`) → service → step/router.
- External: FR4 ModeSpec YAML needed by T014/T033 (use a stub YAML in tests until FR4 lands); FR7 `TriggerEngine` stub is enough for T021; FR5 needed only for T050.

## Parallel opportunities

- T002–T004; T006–T008.
- US1 tests T010–T012 together; implementations T014–T017 together (different files).
- US3 (T024–T029) in parallel with US1 by a second developer.
- T031, T036, T040, T043 test-writing in parallel once Foundational is done.

## Implementation Strategy

MVP = US1 + US2 + US3 + US4 with the fallback rule only (no trained model): the backtests can already run. Then US5 (explanations for the demo), US6 (dashboard), US7 (trained model replaces the rule when it beats it on held-out years).

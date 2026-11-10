# Implementation Plan: FR3 Flood Risk Score

**Branch**: `003-fr03-flood-risk-score` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-fr03-flood-risk-score/spec.md`, decisions in `clarify.md`

## Summary

A daily pipeline step that (1) fetches forecast rainfall per tehsil from Open-Meteo, (2) builds a feature frame per tehsil × lead day from observed + forecast rain, soil wetness, terrain and vulnerability, (3) predicts a calibrated flood probability with the district mode's model (FR4) or a published rule when the model fails, (4) aggregates tehsils to districts, maps scores to tiers, renders English/Urdu template explanations, (5) commits `FloodForecast` + `RiskIndex` + a `RISK_SCORED` audit row per district, and (6) calls `TriggerEngine.evaluate`. The same code runs in replay because every date comes from `Clock` and every write carries `scenario_id`.

## Technical Context

| | |
|---|---|
| **Language/Version** | Python 3.12 |
| **Primary Dependencies** | FastAPI, Pydantic v2, SQLAlchemy 2.x, GeoAlchemy2, httpx, pandas, numpy, scikit-learn, lightgbm, joblib, pyyaml, structlog; tests: respx (HTTP mocks), pytest, freezegun not needed (Clock injected) |
| **Storage** | PostgreSQL 16 + PostGIS; model artifacts on disk under `ml/artifacts/<mode>/<version>/` |
| **Testing** | pytest (unit, integration with Postgres, contract with TestClient), `-m perf` runtime test |
| **Target Platform** | Linux container; in-process APScheduler step |
| **Project Type** | web-service + batch step |
| **Performance Goals** | All pilot districts (68) / ~260 tehsils × 7 leads in < 2 min excluding upstream ingest; NFR1 15 min end to end. Reads p95 < 500 ms |
| **Constraints** | No wall-clock reads (use `Clock`); deterministic given inputs; scores never block on network beyond the forecast-rain fetch (timeouts 20 s, 2 retries) |
| **Scale/Scope** | ~1 820 tehsil forecasts + ~476 district forecasts + ~330 risk indexes per day; trivial for Postgres |

## Constitution Check

| Principle | How satisfied | |
|---|---|---|
| I Anticipate | Leads 1–7 with forecast rain; scoring path has no LLM or human wait; evaluate called right after commit | ✅ |
| II Human authority | FR3 never releases money; Action still waits for FR8 | ✅ |
| III Traceability | `RISK_SCORED` audit row per district RiskIndex in the same transaction | ✅ |
| IV Explainable | `FloodForecast.features` holds every value + window sources; RiskIndex holds `top_factors`, template text, mode, model version (proposed denormalised fields) | ✅ |
| V Fair allocation | Not applicable (no allocation); vulnerability weight is a feature, not a filter | ✅ |
| VI Privacy | No PII touched | ✅ |
| VII Degrade gracefully | Rule fallback (`is_fallback`), stale propagation (`is_stale_input`, `stale_since`), flags in API + audit | ✅ |
| VIII Honest prototype | `forecast_provenance` on every row; `forecast_is_proxy` in responses | ✅ |
| IX Config over code | Thresholds from `TriggerTier`; mode params from FR4 YAML; aggregation share and provider in settings | ✅ |
| X Simplicity | One step in the existing process; pandas in memory; artifacts as files | ✅ |
| XI Tests | Scoring, aggregation, fallback and tier mapping are unit-tested first; backtests in FR5 | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Open-Meteo forecast API for live forecast rain | Free, keyless (non-commercial), hourly precipitation (needed for 3-hour intensity), multi-location requests, has archives for replay | ECMWF open data GRIB (heavy decoding, no point API); GFS via NOMADS (GRIB, no archive API); PMD forecasts (no public API) |
| Previous Runs → Historical Forecast → perfect proxy for replay, labelled | Uses the most honest data available per event, never hides hindsight | Always perfect proxy (overstates skill silently); skip replays without archives (loses the hero demo) |
| One model per mode, `lead_day` as a feature, tehsil × lead rows | One artifact per mode (FR4); lead-dependent uncertainty learned once | 7 models per mode (too few positives per lead); one model for both modes (different physics) |
| Logistic baseline + LightGBM candidate with monotone constraints on rain/soil | Logistic is robust with few labels and trivially explainable; LightGBM with monotone constraints captures thresholds without "more rain → less risk" artefacts | Deep models (no data, no time); random forest (no monotone constraints, worse calibration) |
| Isotonic calibration (Platt if < 50 positives) on grouped out-of-fold predictions | `score = 100 × p` only means something if p is calibrated | Uncalibrated scores (40/60/80 would mean different things per mode) |
| Perfect-prognosis training + lead-scaled log-normal noise | No archived forecasts before ~2021; noise stops over-trust in long leads | Training only on 2021+ archives (too few events, and those years are the backtests) |
| District = max over tehsils with pop share ≥ 10% | Local floods move money; tiny tehsils can't fire the fund alone | Mean (dilutes), plain max (false alarms) |
| Feature code shared by `ml/` and `app/` (`app/services/forecasting/features.py`) | No train/serve skew | Separate notebook features |
| Contributions: logistic `coef × z`, LightGBM `pred_contrib=True`, rule = weighted terms | Native, fast, exact for each model family | SHAP library (extra dependency) |
| Evaluate trigger engine in a separate transaction after the score commit | A trigger bug cannot erase scores; FR7 is idempotent so a retry is safe | One transaction (trigger failure rolls back scores) |

## Feature list

Rows are (tehsil, run_date `t`, lead `d`), target date `T = t + d`. "Window ending at T" mixes observed and forecast days (clarify.md). All rain in mm, UTC days.

| Feature | Definition | Modes |
|---|---|---|
| `rain_3d_mm` | Sum of rain over [T−2, T] | both (UC04-1) |
| `rain_7d_mm` | Sum over [T−6, T] | both (UC04-1) |
| `rain_14d_mm` | Sum over [T−13, T] | SLOW_ONSET |
| `rain_30d_mm` | Sum over [T−29, T] | SLOW_ONSET |
| `rain_fcst_target_mm` | Forecast daily rain on T (issued t) | both |
| `rain_24h_fcst_mm` | Forecast rain on t+1 | FLASH |
| `rain_48h_fcst_mm` | Forecast rain on t+1 + t+2 | FLASH |
| `max_3h_fcst_mm` | Forecast max rolling 3-hour rain on T | FLASH |
| `max_3h_obs_mm` | IMERG `rainfall_max_3h_mm` on last observed day | FLASH |
| `soil_wetness_root` | NASA POWER `GWETROOT` (0–1), last valid day ≤ t | both (UC04-1) |
| `soil_wetness_top` | NASA POWER `GWETTOP` (0–1) | both |
| `elevation_mean_m` | `Tehsil.elevation_mean_m` (SRTM) | both (UC04-1) |
| `slope_mean_deg` | Proposed `Tehsil.slope_mean_deg`; null → district median, else 0 | FLASH |
| `vulnerability_weight` | `District.vulnerability_weight` | both (UC04-1) |
| `lead_day` | d | both |

FR4's ModeSpec lists which features each mode uses; FR3 computes the union. Model inputs apply `log1p` to rain features. `FloodForecast.features` also stores `window_sources` (per-day `IMERG|NASA_POWER|OM_PAST|FORECAST`), `obs_last_date`, `forecast_issue_date`, `forecast_provenance`.

## Model training and calibration (offline, `ml/`)

1. **Labels** (`ml/build_training_set.py`): `data/labels/flood_events.csv` columns `admin_level (TEHSIL|DISTRICT), pcode, start_date, end_date, source (GFD|S1|NDMA), confidence (HIGH|MED)`. A tehsil-day is positive if it falls inside an event for the tehsil (or its district for district-level records) **or** FR2's Sentinel-1 flooded share ≥ 2%. Negatives: all other monsoon tehsil-days. Training days: 15 Jun–30 Sep, years 2001–2024 **minus 2021, 2022, 2025**.
2. **Inputs**: IMERG history (FR2 export to `ml/data/imerg_tehsil_daily.parquet`), NASA POWER history (FR1 export), static terrain/vulnerability. Forecast features = observed rain on the target day(s) × `LogNormal(0, 0.15 × d)` noise (seeded).
3. **Train** (`ml/train.py --mode slow_onset|flash`): per mode only the districts of that mode. Candidates:
   - `logit`: `StandardScaler` + `LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000)`.
   - `lgbm`: `LGBMClassifier(num_leaves=15, min_child_samples=50, learning_rate=0.05, n_estimators=400, subsample=0.8, colsample_bytree=0.8, monotone_constraints=[+1 for rain/soil/intensity features, 0 otherwise], scale_pos_weight=neg/pos)`.
   - `GroupKFold(n_splits=5, groups=year)`; select by PR-AUC, tie → logit.
4. **Calibrate**: fit `IsotonicRegression(out_of_bounds="clip")` on out-of-fold predictions (Platt `LogisticRegression` on the logit if positives < 50). Refit the base model on all training years; save calibrator separately.
5. **Evaluate** (`ml/evaluate.py`): Brier vs climatology, ROC-AUC, PR-AUC, reliability table (10 bins), and per-threshold (40/60/80) hit rate and false-alarm days per season at district level. Writes `model_card.json`.
6. **Publish**: `ml/artifacts/<mode>/<version>/{model.joblib, calibrator.joblib, model_card.json, oof.parquet}` (`oof.parquet` = calibrated out-of-fold predictions aggregated to districts with the FR3 rule, consumed by FR4's threshold report) and `ml/artifacts/<mode>/CURRENT` (one line: version). Version format `<mode-short>-<family>-<YYYY.MM.N>`, e.g. `slow-lgbm-2026.11.1`, `flash-logit-2026.11.1`. `model_card.json` holds `features`, `train_years`, `holdout_years`, `n_pos`, `n_neg`, metrics, `training_set_sha256`, `forecast_noise`, `created_at`.

## Rule-based fallback (UC04-3a)

`p_fb(lead) = clip(Σ_k w_k × min(1, x_k / ref_k), 0, 1)`; `score = round_half_up(100 × max_lead p_fb × lead_weight)`. Defaults (owned in FR4 YAML, quoted here):

| Mode | Term (x_k) | w_k | ref_k |
|---|---|---|---|
| SLOW_ONSET | `rain_7d_mm` | 0.45 | 150 mm |
| | `rain_30d_mm` | 0.25 | 300 mm |
| | `soil_wetness_root` | 0.20 | 1.0 |
| | `vulnerability_weight` | 0.10 | 1.0 |
| FLASH | `max_3h_fcst_mm` (or `max_3h_obs_mm` if larger) | 0.40 | 40 mm |
| | `rain_48h_fcst_mm` | 0.25 | 120 mm |
| | `slope_mean_deg` | 0.15 | 30° |
| | `soil_wetness_top` | 0.10 | 1.0 |
| | `vulnerability_weight` | 0.10 | 1.0 |

Sanity check (slow): a normal week (7 d 20 mm, 30 d 60 mm, soil 0.35, vuln 0.6) → 0.06+0.05+0.07+0.06 = 0.24 → 24 (None). Sindh Aug 2022-like (≥150, ≥300, 0.9, 0.8) → 0.45+0.25+0.18+0.08 = 0.96 → 96 (Action).

## Explanation templates (D2)

Rendered by `TemplateExplainService.explain_risk(RiskExplainInputs)`. Numbers formatted: integers as-is, mm rounded to whole mm, wetness as whole %. Western digits in both languages. Tier labels:

| Tier | EN | UR |
|---|---|---|
| NONE | Normal | معمول |
| WATCH | Watch | نگرانی |
| WARNING | Warning | انتباہ |
| ACTION | Action | کارروائی |

**Main sentence**
- EN: `{district}: flood risk {score}/100 ({tier_en}). Highest in {peak_lead_day} day(s), on {target_date:%d %b}.`
- UR: `{district_ur}: سیلاب کا خطرہ {score}/100 ({tier_ur})۔ سب سے زیادہ خطرہ {peak_lead_day} دن بعد، {target_date:%d-%m} کو۔`

**Reasons sentence** — top 2–3 factors with positive contribution, joined by ", " / "، ":
- EN: `Main reasons: {f1}, {f2}{, f3}.` UR: `بڑی وجوہات: {f1}، {f2}{، f3}۔`

| Factor | EN phrase | UR phrase |
|---|---|---|
| `rain_3d_mm` | `{v} mm of rain over 3 days` | `3 دن میں {v} ملی میٹر بارش` |
| `rain_7d_mm` | `{v} mm of rain over 7 days` | `7 دن میں {v} ملی میٹر بارش` |
| `rain_14d_mm` | `{v} mm of rain over 14 days` | `14 دن میں {v} ملی میٹر بارش` |
| `rain_30d_mm` | `{v} mm of rain over 30 days` | `30 دن میں {v} ملی میٹر بارش` |
| `rain_fcst_target_mm` | `{v} mm of rain forecast that day` | `اس دن {v} ملی میٹر بارش کی پیشگوئی` |
| `rain_24h_fcst_mm` / `rain_48h_fcst_mm` | `{v} mm of rain forecast in the next 24 h` / `48 h` | `اگلے 24 / 48 گھنٹوں میں {v} ملی میٹر بارش متوقع` |
| `max_3h_fcst_mm`, `max_3h_obs_mm` | `bursts of up to {v} mm in 3 hours` | `3 گھنٹوں میں {v} ملی میٹر تک تیز بارش` |
| `soil_wetness_root`, `soil_wetness_top` | `soil already {v}% wet` | `زمین پہلے ہی {v} فیصد گیلی ہے` |
| `slope_mean_deg` | `steep slopes ({v}°) make water run fast` | `ڈھلوان ({v} ڈگری) کی وجہ سے پانی تیزی سے بہتا ہے` |
| `elevation_mean_m` | `low-lying land ({v} m)` | `نشیبی علاقہ ({v} میٹر)` |
| `vulnerability_weight` | `many vulnerable households (index {v}%)` | `کمزور گھرانے زیادہ ہیں (اشاریہ {v} فیصد)` |

**Notices** (appended in this order, each only when true):
- fallback — EN `Model unavailable: score from the rainfall rule.` UR `ماڈل دستیاب نہیں: یہ اسکور بارش کے اصول سے بنا ہے۔`
- stale — EN `Some data is old (last valid {stale_since:%d %b}).` UR `کچھ معلومات پرانی ہیں (آخری درست تاریخ {stale_since:%d-%m})۔`
- proxy — EN `Replay: forecast rain is a proxy built from observed data.` UR `ری پلے: بارش کی پیشگوئی اصل مشاہدے سے بنائی گئی ہے۔`

Example (EN): `Dadu: flood risk 82/100 (Action). Highest in 2 day(s), on 24 Aug. Main reasons: 184 mm of rain over 7 days, soil already 91% wet.`
Example (UR): `Dadu: سیلاب کا خطرہ 82/100 (کارروائی)۔ سب سے زیادہ خطرہ 2 دن بعد، 24-08 کو۔ بڑی وجوہات: 7 دن میں 184 ملی میٹر بارش، زمین پہلے ہی 91 فیصد گیلی ہے۔`

Urdu strings need a native-speaker review before the demo (tasks.md T046).

## Data model slice

| Entity | Fields read | Fields written |
|---|---|---|
| District | id, pcode, name, province, flood_mode, vulnerability_weight, centroid_lat/lon, is_active | — |
| Tehsil | id, district_id, name, population, elevation_mean_m, geom | — |
| ClimateObservation | all (NASA_POWER, GPM_IMERG) | FORECAST_RAIN rows: tehsil_id, obs_date (= target date), lead_day, rainfall_mm, rainfall_max_3h_mm, is_stale, stale_since, scenario_id |
| DataSourceStatus | NASA_POWER, GPM_IMERG | FORECAST_RAIN row |
| DisasterFund / TriggerTier | min_score per tier (scenario-scoped in replay) | — |
| FloodForecast | — | all fields |
| RiskIndex | previous day (for re-run checks) | all fields except `explanation_llm_*` (FR13) |
| FundRelease | risk_index_id (re-run lock check) | — |
| AuditLog | — | via `AuditService.record` |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity.field | Type | Why |
|---|---|---|
| `ClimateObservation.provenance` | enum nullable `LIVE_FORECAST`, `ARCHIVED_FORECAST`, `ARCHIVED_SHORT_LEAD_PROXY`, `PERFECT_FORECAST_PROXY` (only for `FORECAST_RAIN`) | Principle VIII labelling of forecast rain |
| `ClimateObservation.forecast_model` | str nullable, e.g. `open-meteo:best_match` | NFR4 trace |
| `ClimateObservation.rainfall_max_3h_mm` | (note change) also filled for `FORECAST_RAIN` | Flash mode needs forecast intensity |
| `ClimateObservation.soil_wetness_top` | float 0–1 nullable (NASA POWER `GWETTOP`, written by FR1) | FR1 ingests GWETTOP but the model has no column; flash fallback uses it |
| `FloodForecast.forecast_provenance` | same enum | Per-row label |
| `RiskIndex.forecast_provenance` | same enum (worst over inputs: proxy > short-lead proxy > archived > live) | API `forecast_is_proxy` |
| `RiskIndex.mode`, `RiskIndex.model_version` | enum, str | Principle IV says RiskIndex stores them (today only on FloodForecast) |
| `RiskIndex.stale_since` | date nullable | Stale banner date (UC05-1a) |
| `District.name_ur`, `Tehsil.name_ur` | str nullable | Urdu explanations (NFR10) |
| Unique `FloodForecast(district_id, tehsil_id, run_date, lead_day, scenario_id)` and `RiskIndex(district_id, tehsil_id, run_date, scenario_id)`, both `NULLS NOT DISTINCT` (PG 15+) | constraint | Idempotent re-runs |

## API contracts

Router `app/api/v1/forecasts.py`, tag **Forecasting**. All reads honour `X-Scenario-Id`. Roles: `PDMA_OFFICER`, `ADMIN` see all; `DC` only own district (403 `DISTRICT_SCOPE`). `DONOR`: 403.

### GET `/api/v1/forecasts/risk-map?run_date=2022-08-22&level=tehsil|district&province=SINDH`
Default `run_date` = latest run for the scenario. Response (GeoJSON, geometry simplified to 0.005°):
```json
{"type":"FeatureCollection","run_date":"2022-08-22","level":"tehsil","forecast_is_proxy":true,
 "features":[{"type":"Feature","geometry":{"type":"MultiPolygon","coordinates":[]},
  "properties":{"tehsil_id":"7d1…","pcode":"PK60401","name":"Dadu","district_id":"a3f…",
   "score":84,"tier":"ACTION","peak_lead_day":2,"is_fallback":false,"is_stale_input":false,"stale_since":null}}]}
```

### GET `/api/v1/forecasts/districts/{district_id}?run_date=`
```json
{"district_id":"a3f…","name":"Dadu","run_date":"2022-08-22","mode":"SLOW_ONSET","model_version":"slow-lgbm-2026.11.1",
 "score":82,"tier":"ACTION","peak_lead_day":2,
 "series":[{"lead_day":1,"target_date":"2022-08-23","probability":0.71,"score":71},
           {"lead_day":2,"target_date":"2022-08-24","probability":0.82,"score":82}],
 "explanation_en":"Dadu: flood risk 82/100 (Action). …","explanation_ur":"Dadu: سیلاب کا خطرہ 82/100 …",
 "top_factors":[{"factor":"rain_7d_mm","value":184,"contribution":0.41},{"factor":"soil_wetness_root","value":0.91,"contribution":0.22}],
 "thresholds":{"WATCH":40,"WARNING":60,"ACTION":80},"fund_remaining_pkr":"42000000.00",
 "is_fallback":false,"is_stale_input":false,"stale_since":null,"forecast_provenance":"ARCHIVED_SHORT_LEAD_PROXY",
 "forecast_is_proxy":true,"risk_index_id":"c9…","simulated":false}
```
`series` has 7 items; `score` per lead = `round_half_up(100 × p_adj)`.

### GET `/api/v1/forecasts/districts/{district_id}/history?from=2022-08-01&to=2022-09-10`
`{"items":[{"run_date":"2022-08-01","score":12,"tier":"NONE","peak_lead_day":7,"is_fallback":false,"is_stale_input":false}],"total":41,"page":1,"page_size":100}`

### GET `/api/v1/forecasts/tehsils/{tehsil_id}?run_date=`
Same shape as district detail, without `fund_remaining_pkr`, plus `district_id`.

### GET `/api/v1/forecasts/risk-index/{risk_index_id}`
Full trace (FR8 approval view, NFR4): the district detail plus `forecasts[]` with each lead's `features` JSON (incl. `window_sources`), `aggregation` (`{"rule":"max_pop_share","min_share":0.10,"eligible_tehsil_ids":[…],"source_tehsil_id":"…"}`), `audit_entry_seq`.

### POST `/api/v1/forecasts/runs` (ADMIN)
Request `{"run_date":"2026-11-10","district_ids":null}` (defaults: `get_clock().today()`, all active). Runs synchronously (< 2 min) and returns 200:
```json
{"run_date":"2026-11-10","districts_scored":68,"tehsils_scored":261,"fallback_districts":["…"],"stale_districts":[],
 "skipped_locked":[],"trigger_errors":[],"duration_s":41.2,"model_versions":{"SLOW_ONSET":"slow-lgbm-2026.11.1","FLASH":"flash-logit-2026.11.1"}}
```

### GET `/api/v1/forecasts/model-info`
`{"modes":[{"mode":"SLOW_ONSET","model_version":"slow-lgbm-2026.11.1","loaded":true,"family":"lgbm","metrics":{"brier":0.031,"pr_auc":0.42},"holdout_years":[2021,2022,2025]}]}`

| Error code | Status | When |
|---|---|---|
| `RISK_INDEX_NOT_FOUND` | 404 | Unknown id or not in scenario |
| `NO_FORECAST_FOR_DATE` | 404 | No run for that date |
| `DISTRICT_SCOPE` | 403 | DC reads another district |
| `FORECAST_RUN_IN_PROGRESS` | 409 | Advisory lock held |
| `INVALID_RUN_DATE` | 422 | Future date, or before data window |

## Scheduled jobs

Registered in `app/jobs/steps/forecast.py` with the foundation `register_step(order, name, func)` (`app/jobs/daily_pipeline.py`, cron `PIPELINE_CRON` 01:00 UTC), after FR1 (step 10) and FR2 (step 20):

| Step | Order | Does |
|---|---|---|
| `forecast_rain_ingest` | 30 | `forecast_rain_step(db, clock)` → `ForecastRainIngestService.ingest(db, issue_date=clock.today())` |
| `forecast_score` | 40 | `forecast_score_step(db, clock)` → `ForecastService.run(db, run_date=clock.today())` → evaluate per district (FR7) |

Concurrency guard: `pg_advisory_xact_lock(hashtext('forecast:' || coalesce(scenario_id::text,'live')))` held per run.

## Project Structure

### Documentation (this feature)
```text
specs/003-fr03-flood-risk-score/
├── agent.md  spec.md  clarify.md  plan.md  tasks.md  implement.md
```

### Source Code (repository root)
```text
app/
├── integrations/forecast_rain.py          # OpenMeteoClient (forecast, previous-runs, historical-forecast)
├── services/forecasting/
│   ├── settings.py                        # ForecastSettings (pydantic-settings, env prefix FORECAST_/OPEN_METEO_)
│   ├── rain_ingest.py                     # ForecastRainSource Protocol, OpenMeteoForecastSource, ForecastRainIngestService
│   ├── features.py                        # FeatureBuilder, window blending, feature library
│   ├── model_registry.py                  # ModelRegistry, LoadedModel, contributions
│   ├── fallback.py                        # FallbackScorer
│   ├── scoring.py                         # round_half_up_score, apply_lead_weights, tier_for, top_factors
│   ├── aggregation.py                     # aggregate_district
│   └── service.py                         # ForecastService.run
├── services/explain/
│   ├── templates.py                       # EN/UR strings, factor phrases, tier labels
│   └── template_explainer.py              # TemplateExplainService (ExplainService impl)
├── schemas/forecasting.py
├── api/v1/forecasts.py
└── jobs/steps/forecast.py                # register_step(30, "forecast_rain_ingest", …), register_step(40, "forecast_score", …)
scripts/replay/forecast_rain_cache.py     # replay cache writer (called by FR5 build_cache.py)
alembic/versions/0003_fr03_forecasting.py  # provenance enum, unique constraints, proposed fields once merged
ml/build_training_set.py  ml/train.py  ml/evaluate.py  ml/README.md
ml/artifacts/{slow_onset,flash}/<version>/  (written by train; FR4 owns mode YAML)
data/labels/flood_events.csv
tests/unit/forecasting/…  tests/unit/explain/test_risk_templates.py
tests/integration/forecasting/…  tests/contract/test_forecasts_api.py  tests/perf/test_forecast_runtime.py
```

**Structure Decision**: single project per constitution layout. `app/jobs/steps/` is a new sub-package so each FR registers its pipeline step without editing the foundation file.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| External forecast provider (new dependency beyond the PDF's sources) | 7-day lead is impossible from observations alone (Principle I) | Observation-only score cannot anticipate; it would only react |

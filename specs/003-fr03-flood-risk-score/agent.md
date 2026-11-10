# Agent Brief: FR3 Flood Risk Score

## Owner role
**Data & ML**

## Mission
Every day, compute a 0–100 flood risk score for each active district and tehsil, for each of the next 7 days, and save it with every input that produced it. Turn the score into a tier with the district's own thresholds, write a plain-language explanation in English and Urdu, and hand each district result to the trigger engine. When the model fails, a published rainfall rule still produces a score, and the result is clearly flagged.

## Scope
**In (MVP)**
- Forecast-rainfall input: Open-Meteo forecast for live runs, with provenance labels (see clarify.md). Writes `ClimateObservation(source=FORECAST_RAIN)`.
- Feature library (3/7/14/30-day rain windows, max 3-hour intensity, soil wetness, elevation, slope, vulnerability, lead day), shared by training and serving.
- Model runner: loads the per-mode artifact named by FR4's mode spec, predicts p for leads 1–7 per tehsil, applies calibration and lead weights.
- Rule-based fallback score (UC04-3a) with `is_fallback=true`.
- Stale propagation (`is_stale_input`, `stale_since`) from any input (NFR9).
- District score from tehsils (max over tehsils with population share ≥ 10%).
- `FloodForecast` and `RiskIndex` rows, tier mapping, `top_factors`.
- `TemplateExplainService.explain_risk` (D2), English + Urdu.
- Call `TriggerEngine.evaluate` for each district after its scores commit (UC04-6).
- Read APIs: risk map, district detail (7-day chart), history, tehsil detail, risk-index trace, model info; admin manual run.
- Offline training and calibration pipeline (`ml/`), model cards.

**Out**
- Mode definitions, per-mode feature lists, fallback weights, lead weights and artifacts' calibration policy: **FR4** (`004-fr04-flood-modes`). FR3 runs whatever the mode spec says.
- NASA POWER and IMERG ingestion: FR1, FR2. FR3 only reads `ClimateObservation`.
- Replay engine, replay caches and backtests: **FR5**. FR3 must run unchanged under `ReplayClock` (via `get_clock()`) + `current_scenario()`.
- Tier lifecycle, releases: FR7. Allocation reasons (`explain_allocation`): FR11.
- LLM rewrite of explanations: **FR13** (roadmap, flag `FR13_LLM`).

## Read first
- `specs/_authoring-guide.md`, `.specify/memory/constitution.md` (Principles I, IV, VII, VIII, IX)
- `specs/000-foundation/data-model.md` §1, §2, §3, §4 (TriggerTier), §9
- `specs/000-foundation/plan.md` (interfaces, API conventions)
- `specs/004-fr04-flood-modes/plan.md` (ModeSpec contract), `specs/005-fr05-event-replay/plan.md` (replay sources)
- This folder: `spec.md`, `clarify.md`, `plan.md`

## Depends on / Blocks
| | FR | Reason |
|---|---|---|
| Depends on | Foundation | `get_clock()`, `current_scenario()`, `register_step`, `AuditService`, models |
| Depends on | FR20 | Real `AuditService.record` (stub is enough to start) |
| Depends on | FR1 | NASA POWER rain, soil wetness, `DataSourceStatus` |
| Depends on | FR2 | IMERG tehsil rain + max 3 h, SRTM elevation (+ proposed slope) |
| Depends on | FR4 | `ModeRegistry` / ModeSpec YAMLs, per-mode artifacts |
| Depends on | FR6 | `TriggerTier` thresholds per district (defaults 40/60/80 until FR6 lands) |
| Blocks | FR7 | Needs `RiskIndex` rows + `evaluate` call |
| Blocks | FR5 | Replay runs the real FR3 step; backtests judge FR3's output |
| Blocks | FR8 | Approval view shows score, explanation, 7-day forecast |
| Blocks | FR13 | Rewrites FR3's template text |

## Interfaces
**Consumes**: `get_clock().today()/now()`, `current_scenario()`, `register_step(order, name, func)`, `AuditService.record`, `TriggerEngine.evaluate(db, district_id, risk_index)`, FR4 `ModeRegistry.get(mode) -> ModeSpec`, FR5 `ReplayScenario` (key, dates, districts) for replay reads.
**Provides**: `ExplainService.explain_risk(risk_inputs) -> (en, ur)` (template implementation), `ForecastService.run(db, run_date, district_ids=None)`, `ForecastRainSource` Protocol + `OpenMeteoForecastSource`, `FeatureBuilder`, `ModelRegistry`, daily-pipeline steps `forecast_rain_ingest` and `forecast_score`.

## Files you own
```text
app/integrations/forecast_rain.py
app/services/forecasting/__init__.py
app/services/forecasting/settings.py
app/services/forecasting/rain_ingest.py
app/services/forecasting/features.py
app/services/forecasting/model_registry.py
app/services/forecasting/fallback.py
app/services/forecasting/scoring.py
app/services/forecasting/aggregation.py
app/services/forecasting/service.py
app/services/explain/__init__.py
app/services/explain/templates.py
app/services/explain/template_explainer.py
app/schemas/forecasting.py
app/api/v1/forecasts.py
app/jobs/steps/forecast.py            # register_step(30, …) and register_step(40, …)
scripts/replay/forecast_rain_cache.py # writes data/replay/<key>/forecast_rain.parquet
alembic/versions/0003_fr03_forecasting.py
ml/build_training_set.py
ml/train.py
ml/evaluate.py
ml/README.md
data/labels/flood_events.csv
tests/unit/forecasting/test_rain_client.py
tests/unit/forecasting/test_features.py
tests/unit/forecasting/test_fallback.py
tests/unit/forecasting/test_scoring.py
tests/unit/forecasting/test_aggregation.py
tests/unit/explain/test_risk_templates.py
tests/integration/forecasting/test_forecast_run.py
tests/integration/forecasting/test_stale_and_fallback.py
tests/contract/test_forecasts_api.py
tests/perf/test_forecast_runtime.py
```

## Guardrails
| Principle | How it applies here |
|---|---|
| I Anticipate | Leads 1–7; scores commit before trigger evaluation; no LLM call on the scoring path |
| IV Explainable | Every `FloodForecast` stores the exact feature values, model version, mode, provenance; `RiskIndex` stores `top_factors` and template text |
| VII Degrade gracefully | Model failure → rule score, `is_fallback=true`. Stale input → `is_stale_input=true` + `stale_since`. Both flags in API and audit payload |
| VIII Honest prototype | Forecast-rain provenance on every row; replay "perfect forecast proxy" is labelled and surfaced as `forecast_is_proxy=true` |
| IX Config over code | Thresholds from `TriggerTier`; mode params from FR4 YAML; aggregation share from settings. No district names in code |
| III Traceability | One `RISK_SCORED` audit entry per district RiskIndex, same transaction |
| X Simplicity | In-process, pandas + sklearn/LightGBM; artifacts on disk |

## Definition of Done
- [ ] All tasks in `tasks.md` checked; unit, integration, contract tests green.
- [ ] `pytest -m backtest` (FR5) passes with the committed artifacts.
- [ ] Removing a mode's artifact still yields scores flagged `is_fallback=true` for every district of that mode.
- [ ] Marking NASA POWER stale yields `is_stale_input=true` and `stale_since` on scores and API responses.
- [ ] Daily run for all seeded districts < 2 min with mocked network (`tests/perf`), well under NFR1's 15 min.
- [ ] Every district RiskIndex has non-empty `explanation_en` and `explanation_ur`, and a `RISK_SCORED` audit row.
- [ ] Model cards committed for both modes; training excludes 2021, 2022 and 2025 seasons.
- [ ] `ruff`, `mypy --strict app/services/forecasting` clean.

## Hand-off notes
- **FR7**: you are called once per district per run, even when the tier did not rise (so you can count days below Watch for D4 event closing). `risk_index.tier` is already mapped with the district's thresholds; `risk_index.is_fallback` / `is_stale_input` are set.
- **FR8**: use `GET /api/v1/forecasts/risk-index/{id}` for the approval detail (score, explanation, 7-day series, flags).
- **FR5**: like FR1, FR3 owns its own replay reader: `ReplayForecastRainSource` reads `data/replay/<key>/forecast_rain.parquet` (no lookahead: only rows with `issue_date == replay today`), selected automatically when `current_scenario()` is set. FR3 also owns the cache writer `scripts/replay/forecast_rain_cache.py --scenario <key>`, which FR5's `scripts/replay/build_cache.py` calls. FR3 never reads the system clock directly.
- **FR13**: template text is in `RiskIndex.explanation_en/ur`; inputs are reconstructable from `RiskExplainInputs` (see plan.md).
- **FR2**: please backfill 30 days of IMERG before the first live run (30-day windows) and add `Tehsil.slope_mean_deg` if accepted.
- **FR1**: FR3 owns forecast-rain ingestion (`DataSourceStatus.source=FORECAST_RAIN`); it is not part of your step.

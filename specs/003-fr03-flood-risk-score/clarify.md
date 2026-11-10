# Clarifications: FR3 Flood Risk Score

## Clarifications

### Session 2026-11-10

- Q: **The forecast-rainfall gap.** NASA POWER and IMERG are observations. How can the score look 7 days ahead? → A: Add forecast rainfall as an input.
  - **Live runs**: the Open-Meteo Forecast API (`https://api.open-meteo.com/v1/forecast`), `models=best_match` (ECMWF IFS / GFS blend chosen by Open-Meteo), `hourly=precipitation`, `forecast_days=8`, `past_days=3`, `timezone=UTC`. Daily total = sum of 24 UTC hours; max 3-hour = largest rolling 3-hour sum in that UTC day. Provenance `LIVE_FORECAST`.
  - **Replays**, in this order of preference:
    1. Open-Meteo **Previous Runs API** (`https://previous-runs-api.open-meteo.com/v1/forecast`, variables `precipitation_previous_day1…7`): real archived forecasts at each lead. Provenance `ARCHIVED_FORECAST`. Coverage: **verify** (believed to start around 2024 for most models, so it should cover Buner 2025 but not 2021 or 2022).
    2. Open-Meteo **Historical Forecast API** (`https://historical-forecast-api.open-meteo.com/v1/forecast`): a continuous series stitched from the first hours of each archived run. It is a real forecast only at short lead, so leads 2–7 built from it are labelled `ARCHIVED_SHORT_LEAD_PROXY`. Coverage: **verify** (believed to start around 2021–2022 depending on the model; check that it includes Jul–Sep 2022 and Jul–Sep 2021 for Sindh).
    3. **Perfect forecast proxy**: forecast rain for target date T = the IMERG observed rain on T for that tehsil (from FR2's replay cache). Provenance `PERFECT_FORECAST_PROXY`.
  - Any proxy makes `forecast_is_proxy=true` in every API response and in the audit payload (Principle VIII). The backtest report states the provenance per scenario. *Why*: Open-Meteo is free for non-commercial use, needs no key, has an archive for honest replays, and returns hourly data so flash mode gets 3-hour intensity. The labels stop a demo from passing hindsight off as foresight.
- Q: What spatial unit is the forecast rain fetched for? → A: One point per tehsil, `ST_PointOnSurface(tehsil.geom)` (always inside the polygon), batched up to 50 points per request (**verify** the multi-location limit). ~260 pilot tehsils → about 6 requests. *Why*: tehsil is the map unit (Q11); the forecast grid (~9–25 km) is about tehsil size.
- Q: What does "up to 7 days ahead" mean exactly? → A: Lead days 1–7, target date = run date + lead. Lead 0 (today) is not scored; today's rain is in the observed/`past_days` part of the windows.
- Q: What is the probability the model predicts? → A: P(tehsil is flooded on the target date), where "flooded" follows the label definition in plan.md (curated event list + Sentinel-1 flooded share ≥ 2% of tehsil area). The district probability is derived (FR3-007), not predicted.
- Q: How do observed and forecast rainfall combine in a window? → A: For each day in a window ending at the target date: use IMERG if the day is ≤ the last IMERG date; else NASA POWER `PRECTOTCORR` at the district centroid if available; else the Open-Meteo `past_days` value; else the forecast issued on the run date at lead = day − run date. The day-by-day source is stored in `features.window_sources`.
- Q: What exactly is the score? → A: `p_adj(lead) = p_calibrated(lead) × lead_weight[mode][lead]`; `score = round_half_up(100 × max_lead p_adj)`, clipped 0–100; earliest lead wins ties. *Why*: matches the guide (`round(100 × p_calibrated)`); lead weights let flash mode focus on 24–48 h (FR4). Python's `round()` is banker's rounding, so we use `Decimal.quantize(ROUND_HALF_UP)`.
- Q: **District score from tehsils.** → A: Per lead day, the district probability is the **maximum over tehsils whose population share in the district is ≥ 10%** (`FORECAST_DISTRICT_MIN_POP_SHARE=0.10`); if no tehsil qualifies or populations are missing, all tehsils count. District score = round(100 × max over leads). The district `FloodForecast.features` records the rule, the eligible tehsils and the source tehsil. *Why*: a flood in one populated tehsil should move district money (a mean would dilute Dadu-type local floods); the 10% floor stops a tiny, sparsely populated hill tehsil from releasing a whole district's fund on its own. Alternatives rejected: population-weighted mean (dilutes), plain max (one empty tehsil can fire the fund), area-weighted mean (ignores people).
- Q: Which thresholds give a tehsil its tier? → A: Its district's `TriggerTier` thresholds. The tehsil tier only colours the map; money is district level (Q11).
- Q: When is `TriggerEngine.evaluate` called? The guide says "if the tier rose". → A: Once for **every** district every run, after its scores commit. FR7 is idempotent and decides whether anything fires. *Why*: D4 closes an event after 14 consecutive days below Watch, so FR7 needs the below-Watch days too. Calling on every run is a superset of "if the tier rose" and fires exactly the same tiers.
- Q: What counts as "model fails" (UC04-3a)? → A: The artifact or calibrator is missing or fails to load; the version is incompatible with the mode spec's feature list; `predict` raises; or the output is NaN or outside [0, 1]. Load failures fall back for the whole mode; prediction failures fall back per tehsil.
- Q: What is the fallback formula? → A: `p_fb = clip(Σ_k w_k × min(1, x_k / ref_k), 0, 1)` with weights and reference values per mode in FR4's YAML (defaults in plan.md). Model version `rules-fallback-1`. Lead weights still apply.
- Q: How is staleness propagated? → A: `is_stale_input = any input row used had is_stale=true`; `stale_since = min(stale_since)` over those rows. The forecast-rain provider failing → shift yesterday's forecast one day (yesterday's lead 2 becomes today's lead 1, etc.), fill lead 7 with lead 6 (persistence), mark all of it stale, set `DataSourceStatus(FORECAST_RAIN).is_healthy=false`, alert the admin. If there is no previous forecast either, forecast rain = 0 and the result is stale **and** fallback (the model is not trusted without forecasts).
- Q: IMERG missing for a tehsil? → A: Use the district's NASA POWER rainfall for observed days and record `"obs_source":"NASA_POWER"`. Not stale by itself.
- Q: Is a re-run for the same date allowed? → A: Yes, idempotent upsert on (district, tehsil, run_date, lead_day, scenario). A district whose RiskIndex is referenced by a `FundRelease` is skipped and logged (`RISK_RERUN_SKIPPED`), so the money keeps pointing at the score that fired it.
- Q: Is every score audited? → A: One `RISK_SCORED` audit entry per **district** RiskIndex (68/day in the pilot), payload `{risk_index_id, district_id, run_date, score, tier, peak_lead_day, mode, model_version, is_fallback, is_stale_input, stale_since, forecast_provenance}`. Tehsil rows are not audited (map only). *Why*: makes every score that can move money chain-verifiable, at a trivial row count.
- Q: Who owns `ExplainService.explain_allocation`? → A: FR11 (reason strings per beneficiary). FR3's `TemplateExplainService` implements `explain_risk` and delegates `explain_allocation` to FR11's `build_allocation_reason` when it is available (returns `("", "")` before that).
- Q: Which digits in Urdu text? → A: Western digits (0–9), as in Pakistani SMS and news. FR13's number guardrail normalises Urdu and Arabic-Indic digits anyway.
- Q: Urdu district names? → A: Proposed `District.name_ur` / `Tehsil.name_ur`; until merged, the Urdu template uses the English name.
- Q: How are training labels built, and how is leakage avoided? → A: `data/labels/flood_events.csv` from the Global Flood Database (MODIS, 2000–2018), Sentinel-1 flooded share from FR2 (2015+), and NDMA situation reports. Monsoon months (15 Jun–30 Sep) of 2001–2024 minus **2021, 2022 and 2025**, which are reserved for the FR5 backtests. Cross-validation groups by year.
- Q: Forecast rain in training (no archived forecasts before ~2021)? → A: Train with observed rain as forecast ("perfect prognosis"), with multiplicative log-normal noise σ = 0.15 × lead day so the model does not over-trust long leads. Stated in the model card.
- Q: Open-Meteo terms? → A: Free API for non-commercial use (hackathon). Production needs the commercial endpoint and `OPEN_METEO_API_KEY`; the client supports both by config.

## Resolved by constitution decisions

- **D2**: explanations are templates; LLM is FR13 behind `FR13_LLM`. UC04-5 says "LLM writes"; D2 wins.
- **D4**: incremental tiers and event closing are FR7's; FR3 just supplies one district RiskIndex per run.
- **D1**: satellite maps do not feed the score in the MVP (Sentinel-1 is used only to build training labels offline).

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | District aggregation rule | Max over tehsils with population share ≥ 10% | A mean lowers district scores and may break the Sindh backtest; a plain max raises false alarms | Data & ML lead with PDMA product owner |
| 2 | Call `TriggerEngine.evaluate` every run vs only on tier rise | Every run (FR7 idempotent) | Only-on-rise needs FR7 to add its own daily job to close events | Data & ML + Backend leads |
| 3 | Forecast provider | Open-Meteo `best_match` | Another provider changes `forecast_rain.py` only (Protocol stays) | Data & ML lead |
| 4 | Replay forecast provenance per scenario | buner-2025 `ARCHIVED_FORECAST`; sindh-2022 `ARCHIVED_SHORT_LEAD_PROXY` or `PERFECT_FORECAST_PROXY`; control-2021 `PERFECT_FORECAST_PROXY` — all **verify** archive coverage | A perfect proxy flatters lead time; pitch must say so | Data & ML lead + pitch lead |
| 5 | Perfect-proxy noise in replays | No noise (honest "best case" label) | Adding noise makes replay results seed-dependent | Data & ML lead |
| 6 | Model family shipped | Whichever of logistic / LightGBM has the better grouped-CV PR-AUC **and** passes backtests; rule if neither | Explanations change from coefficients to SHAP contributions | Data & ML lead |
| 7 | Label threshold for "flooded" tehsil-day | Event list hit, or Sentinel-1 flooded share ≥ 2% | Higher threshold → fewer positives, more conservative scores | Data & ML + GIS leads |
| 8 | Urdu name fields | Propose `name_ur`; fallback English name | Without it, Urdu text mixes scripts | Foundation owner |
| 9 | Audit volume | One `RISK_SCORED` per district per run | Auditing tehsils adds ~260 rows/day | Backend lead (FR20) |

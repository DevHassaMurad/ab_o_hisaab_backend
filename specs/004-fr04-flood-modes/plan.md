# Implementation Plan: FR4 Flood Modes

**Branch**: `004-fr04-flood-modes` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-fr04-flood-modes/spec.md`, decisions in `clarify.md`

## Summary

Two YAML mode definitions (`ml/config/modes/slow_onset.yaml`, `flash.yaml`) validated by a pydantic `ModeSpec`, loaded once by `ModeRegistry`. FR3 asks `effective_mode(db, district)` for each district and `ModeRegistry.get(mode)` for the spec, then builds that mode's features, loads that mode's artifact, applies its lead weights and, on failure, its fallback terms. Officers switch a district's mode by API (audited, effective next run). Replays snapshot modes at start. `ml/threshold_report.py` turns out-of-fold predictions into a per-mode thresholds calibration report.

## Technical Context

| | |
|---|---|
| **Language/Version** | Python 3.12 |
| **Primary Dependencies** | pydantic v2, pyyaml, FastAPI, SQLAlchemy 2.x, pandas, numpy (report) |
| **Storage** | YAML in repo; `District.flood_mode` in Postgres; model cards and `oof.parquet` under `ml/artifacts/<mode>/<version>/` |
| **Testing** | pytest unit (spec validation, report maths), integration (routing with FR3), contract (API) |
| **Target Platform** | Linux container |
| **Project Type** | web-service (config + small API) |
| **Performance Goals** | Spec lookup O(1) in memory; mode endpoints p95 < 100 ms |
| **Constraints** | Fail fast on invalid config; no network |
| **Scale/Scope** | 2 modes, 68 pilot districts |

## Constitution Check

| Principle | How satisfied | |
|---|---|---|
| II Human authority | Threshold suggestions never write `TriggerTier` | ✅ |
| III Traceability | `DISTRICT_FLOOD_MODE_CHANGED` audited in the same transaction | ✅ |
| IV Explainable | `mode` on every forecast; spec hash in model card; explanation text names the mode via the district detail | ✅ |
| VII Degrade gracefully | Per-mode fallback params; artifact mismatch → fallback, not crash | ✅ |
| VIII Honest prototype | Unvalidated provinces labelled in `GET /forecasting/modes` | ✅ |
| IX Config over code | Modes are YAML; district mode is data; province defaults in seed | ✅ |
| X Simplicity | One pydantic model, two files, three endpoints | ✅ |
| XI Tests | Validation and routing tests protect the scoring contract | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| YAML + pydantic validation | Human-editable, diff-able, validated in CI | Python classes per mode (code change to retune); DB table (needs admin UI, harder to review) |
| Lead weights for lead-time policy | One visible knob; keeps tier thresholds identical across modes | Mode-specific thresholds (confuses officers); per-tier lead windows (couples to FR6) |
| Shared probability scale via per-mode calibration | "60" means the same thing everywhere | Raw model scores (incomparable) |
| Snapshot modes in replay config | Reproducible backtests | Read live `District.flood_mode` (a demo-day edit would change the backtest) |
| Report from out-of-fold predictions | Keeps backtest years untouched | Report on backtest years (leaks into threshold choice) |

## Mode definitions

### `ml/config/modes/slow_onset.yaml`
```yaml
mode: SLOW_ONSET
spec_version: 1
mode_dir: slow_onset
description: "Rain that builds over days on flat, saturated land (Sindh)."
artifact_version: current            # or a pinned version string
features: [rain_3d_mm, rain_7d_mm, rain_14d_mm, rain_30d_mm, rain_fcst_target_mm,
           soil_wetness_root, soil_wetness_top, elevation_mean_m, vulnerability_weight, lead_day]
monotone: {rain_3d_mm: 1, rain_7d_mm: 1, rain_14d_mm: 1, rain_30d_mm: 1, rain_fcst_target_mm: 1,
           soil_wetness_root: 1, soil_wetness_top: 1, vulnerability_weight: 1}
lead_weights: {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0, 7: 1.0}
lead_focus: {WATCH: 7, WARNING: 4, ACTION: 2}     # target lead (days) used by the report and backtests
label: {min_flooded_pct: 2.0}
calibration: {method: isotonic, min_positives: 50, false_alarm_budget: {WATCH: 3.0, WARNING: 1.5, ACTION: 0.5}}
fallback:
  version: rules-fallback-1
  terms:
    - {feature: rain_7d_mm,           weight: 0.45, ref: 150}
    - {feature: rain_30d_mm,          weight: 0.25, ref: 300}
    - {feature: soil_wetness_root,    weight: 0.20, ref: 1.0}
    - {feature: vulnerability_weight, weight: 0.10, ref: 1.0}
```

### `ml/config/modes/flash.yaml`
```yaml
mode: FLASH
spec_version: 1
mode_dir: flash
description: "Short, intense bursts on steep slopes (KP)."
artifact_version: current
features: [rain_3d_mm, rain_7d_mm, rain_24h_fcst_mm, rain_48h_fcst_mm, rain_fcst_target_mm,
           max_3h_fcst_mm, max_3h_obs_mm, soil_wetness_root, soil_wetness_top,
           elevation_mean_m, slope_mean_deg, vulnerability_weight, lead_day]
monotone: {rain_3d_mm: 1, rain_7d_mm: 1, rain_24h_fcst_mm: 1, rain_48h_fcst_mm: 1, rain_fcst_target_mm: 1,
           max_3h_fcst_mm: 1, max_3h_obs_mm: 1, soil_wetness_root: 1, soil_wetness_top: 1,
           slope_mean_deg: 1, vulnerability_weight: 1}
lead_weights: {1: 1.0, 2: 1.0, 3: 0.85, 4: 0.7, 5: 0.6, 6: 0.6, 7: 0.6}
lead_focus: {WATCH: 3, WARNING: 2, ACTION: 1}
label: {min_flooded_pct: 1.0}                     # flash floods cover less area
calibration: {method: isotonic, min_positives: 50, false_alarm_budget: {WATCH: 3.0, WARNING: 1.5, ACTION: 0.5}}
fallback:
  version: rules-fallback-1
  terms:
    - {feature: max_3h_fcst_mm, alt_feature: max_3h_obs_mm, weight: 0.40, ref: 40}
    - {feature: rain_48h_fcst_mm,     weight: 0.25, ref: 120}
    - {feature: slope_mean_deg,       weight: 0.15, ref: 30}
    - {feature: soil_wetness_top,     weight: 0.10, ref: 1.0}
    - {feature: vulnerability_weight, weight: 0.10, ref: 1.0}
```

Validator rules (`ModeSpec`): `mode` ∈ enum; features ⊆ FR3 `FEATURES` keys and ⊇ UC04 core `{rain_3d_mm, rain_7d_mm, soil_wetness_root, elevation_mean_m, vulnerability_weight}`; `lead_day` included; `lead_weights` keys exactly 1–7, values in (0, 1], non-increasing; fallback weights sum to 1.0 ± 1e-6, `ref > 0`, fallback features (and `alt_feature`) ⊆ features; `monotone` keys ⊆ features, values ∈ {−1, 0, 1}; `false_alarm_budget` has the three tiers.

Flash sanity check (fallback): Buner-like day 2 (burst 80 mm/3 h → term 0.40, 48 h 130 mm → 0.25, slope 28° → 0.14, soil 0.7 → 0.07, vuln 0.7 → 0.07) = 0.93 → 93 at lead 2 (weight 1.0) → Action. The same burst at lead 6 → 0.93 × 0.6 = 56 → Watch.

## Data model slice

| Entity | Read | Write |
|---|---|---|
| District | id, province, flood_mode, is_active | flood_mode (US4) |
| Tehsil | slope_mean_deg (proposed, FR2), elevation_mean_m | — |
| ReplayScenario | config["modes"] (proposed by FR5) | via FR5 at start |
| FloodForecast / RiskIndex | mode (written by FR3) | — |
| AuditLog | — | `DISTRICT_FLOOD_MODE_CHANGED` |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

None of FR4's own. FR4 relies on proposals made elsewhere:
- `Tehsil.slope_mean_deg` (FR2), `ClimateObservation.soil_wetness_top` (FR1, also listed by FR3).
- `ReplayScenario.config` JSONB holding `modes` (FR5).
- `RiskIndex.mode` denormalised (FR3).

## API contracts

Router `app/api/v1/forecast_modes.py`, tag **Forecasting**. Registered in `app/api/v1/__init__.py`.

### GET `/api/v1/forecasting/modes` (PDMA_OFFICER, DC, ADMIN)
```json
{"items":[
 {"mode":"SLOW_ONSET","spec_version":1,"spec_sha256":"9b1…","description":"Rain that builds over days…",
  "features":["rain_3d_mm","rain_7d_mm","rain_14d_mm","rain_30d_mm","…"],
  "lead_weights":{"1":1.0,"2":1.0,"3":1.0,"4":1.0,"5":1.0,"6":1.0,"7":1.0},
  "fallback":{"version":"rules-fallback-1","terms":[{"feature":"rain_7d_mm","weight":0.45,"ref":150}]},
  "model_version":"slow-lgbm-2026.11.1","model_loaded":true,"district_count":30,
  "validated_provinces":["SINDH"],"unvalidated_provinces":["PUNJAB"]}],
 "total":2,"page":1,"page_size":50}
```

### GET `/api/v1/forecasting/modes/{mode}`
One item of the list above. 404 `MODE_NOT_FOUND`.

### GET `/api/v1/forecasting/modes/{mode}/calibration` (PDMA_OFFICER, ADMIN)
```json
{"mode":"FLASH","model_version":"flash-logit-2026.11.1","source":"out_of_fold","seasons":21,
 "thresholds":[{"threshold":40,"hit_rate":0.86,"median_lead_days":2,"false_alarm_days_per_season":2.4},
               {"threshold":60,"hit_rate":0.71,"median_lead_days":1,"false_alarm_days_per_season":1.1},
               {"threshold":80,"hit_rate":0.52,"median_lead_days":1,"false_alarm_days_per_season":0.4}],
 "suggested_thresholds":{"WATCH":40,"WARNING":55,"ACTION":80},
 "false_alarm_budget":{"WATCH":3.0,"WARNING":1.5,"ACTION":0.5},"advisory":true}
```
404 `CALIBRATION_NOT_AVAILABLE` when the artifact or `oof.parquet` is missing.

### PATCH `/api/v1/districts/{district_id}/flood-mode` (PDMA_OFFICER, ADMIN)
Request `{"flood_mode":"SLOW_ONSET","reason":"Nowshera floods from the Kabul river, not cloudbursts"}`
Response 200:
```json
{"district_id":"…","flood_mode":"SLOW_ONSET","previous_mode":"FLASH","effective_from":"2026-11-11","audit_seq":5123}
```
Same mode → 200 with `"changed": false`, no audit. Errors: 422 `INVALID_MODE`, 422 `REASON_REQUIRED` (< 10 chars), 403 `FORBIDDEN_ROLE`, 404 `DISTRICT_NOT_FOUND`, 409 `MODE_LOCKED_IN_REPLAY` (request has `X-Scenario-Id`).

`effective_from` = `get_clock().today() + 1 day` if today's run already completed, else today.

## Project Structure

### Documentation (this feature)
```text
specs/004-fr04-flood-modes/
├── agent.md  spec.md  clarify.md  plan.md  tasks.md  implement.md
```

### Source Code (repository root)
```text
app/services/forecasting/modes.py        # ModeSpec, FallbackTerm, ModeRegistry, effective_mode, set_district_mode, snapshot_modes
app/services/forecasting/mode_seed.py    # apply_flood_mode_seed(db) called by scripts/seed.py hook
app/schemas/forecast_modes.py            # ModeOut, CalibrationOut, ModeChangeIn/Out
app/api/v1/forecast_modes.py
ml/config/modes/slow_onset.yaml
ml/config/modes/flash.yaml
ml/threshold_report.py                   # oof.parquet + labels → calibration.json beside the artifact
data/seeds/flood_modes.yaml
tests/unit/forecasting/test_mode_spec.py
tests/unit/forecasting/test_threshold_report.py
tests/integration/forecasting/test_mode_routing.py
tests/contract/test_forecast_modes_api.py
```

**Structure Decision**: single project per constitution. `forecast_modes.py` is a new router file under the Forecasting tag so FR3 and FR4 do not edit the same router.

## Complexity Tracking

None.

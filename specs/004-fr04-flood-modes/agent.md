# Agent Brief: FR4 Flood Modes

## Owner role
**Data & ML**

## Mission
Give the forecasting engine two flood modes, `SLOW_ONSET` (Sindh: rain that builds over days on flat, saturated land) and `FLASH` (KP: intense bursts on steep slopes). Each mode is a validated data file that names its features, lead-day weights, fallback rule, calibration policy and model artifact, so a district's mode is a config value and adding or retuning a mode needs no code change. Publish a per-mode thresholds calibration report so officials can see what 40/60/80 mean in each mode.

## Scope
**In (MVP)**
- `ModeSpec` schema + `ModeRegistry` loading `ml/config/modes/{slow_onset,flash}.yaml`, validated at startup and in CI.
- Mode-specific feature lists (SLOW: 7/14/30-day cumulative rain + soil saturation; FLASH: max 3-hour intensity, 24/48 h forecast rain, slope/elevation).
- Lead-day weights: SLOW keeps all 7 days (7-day lead for Watch); FLASH focuses on 24–48 h for Action (document Q10).
- Per-mode fallback parameters (formula engine is FR3's).
- Per-mode model artifact selection and calibration method (training code is FR3's `ml/train.py`).
- `District.flood_mode` defaults by province from `data/seeds/flood_modes.yaml`; officer/admin change endpoint with reason and audit; effective from the next run.
- Replay mode snapshot (a scenario keeps the modes it started with).
- Thresholds calibration report per mode (`ml/threshold_report.py`), advisory only.
- Read endpoints listing modes, their specs and calibration.

**Out**
- Glacial lake outburst floods (GLOF) mode: roadmap (Q7). The enum and registry are open for a third mode.
- Per-tehsil modes: roadmap; a district has exactly one mode.
- Changing tier thresholds: FR6. FR4 only *suggests* thresholds.
- Feature computation, scoring, fallback formula, training loop: FR3.

## Read first
- `specs/_authoring-guide.md` (FR4 facts), `.specify/memory/constitution.md` (IV, VII, IX, X)
- `specs/000-foundation/data-model.md` §1 (District.flood_mode, Tehsil), §3, §9
- `specs/003-fr03-flood-risk-score/plan.md` (feature list, fallback formula, training)
- `specs/005-fr05-event-replay/plan.md` (scenario config snapshot)
- `specs/001-fr01-nasa-power-ingestion/plan.md` (proposed `soil_wetness_top`), `specs/002-fr02-earth-engine-ingestion/agent.md` (max 3 h, slope)

## Depends on / Blocks
| | FR | Reason |
|---|---|---|
| Depends on | Foundation | `District.flood_mode`, seeds, `AuditService`, `current_scenario()` |
| Depends on | FR2 | `rainfall_max_3h_mm`, `Tehsil.slope_mean_deg` (proposed) for FLASH |
| Depends on | FR1 | `soil_wetness_top` (proposed) |
| Depends on | FR3 | Feature registry names, training script that produces per-mode artifacts |
| Blocks | FR3 | FR3 loads `ModeSpec` for every district |
| Blocks | FR5 | Buner backtest is a FLASH-mode test; replay snapshots modes |
| Blocks | FR6 | Calibration report informs default thresholds (advisory) |

## Interfaces
**Consumes**: `AuditService.record`, `current_scenario()`, FR3 `FEATURES` registry keys (`app/services/forecasting/features.py`), FR5 `ReplayScenario.config["modes"]`.
**Provides**: `ModeRegistry.get(mode) -> ModeSpec`, `ModeRegistry.all()`, `effective_mode(db, district) -> FloodMode`, `set_district_mode(db, district_id, mode, reason, actor)`, `snapshot_modes(db, district_ids) -> dict[str, str]` (for FR5).

## Files you own
```text
app/services/forecasting/modes.py
app/services/forecasting/mode_seed.py
app/schemas/forecast_modes.py
app/api/v1/forecast_modes.py
ml/config/modes/slow_onset.yaml
ml/config/modes/flash.yaml
ml/threshold_report.py
data/seeds/flood_modes.yaml
tests/unit/forecasting/test_mode_spec.py
tests/unit/forecasting/test_threshold_report.py
tests/integration/forecasting/test_mode_routing.py
tests/contract/test_forecast_modes_api.py
```
Artifacts under `ml/artifacts/{slow_onset,flash}/` are produced by FR3's `ml/train.py` using these YAMLs; FR4 signs off on them.

## Guardrails
| Principle | How it applies |
|---|---|
| IX Config over code | Modes, features, weights, fallback params and province defaults are YAML; changing a district's mode is an API call |
| IV Explainable | Every forecast row stores its `mode`; the mode spec version (file hash) is in the model card |
| VII Degrade gracefully | Missing or incompatible artifact → FR3 uses this mode's fallback params; an invalid YAML fails startup loudly (never silently) |
| III Traceability | `DISTRICT_FLOOD_MODE_CHANGED` audit entry with reason |
| X Simplicity | Two YAML files and one pydantic model; no plugin system |
| II Human authority | Threshold suggestions are advisory; only FR6 changes thresholds, by an officer |

## Definition of Done
- [ ] Both YAMLs validate; CI test fails on any invalid change.
- [ ] Sindh districts score with the SLOW artifact, KP districts with the FLASH artifact (integration test).
- [ ] A mode change via API is audited, needs a reason and takes effect on the next run with no code change.
- [ ] A running replay is unaffected by a live mode change (snapshot).
- [ ] FLASH lead weights make leads 1–2 dominate; Buner 2025 backtest triggers (FR5).
- [ ] Calibration report generated for both modes and shown by `GET /forecasting/modes/{mode}/calibration`.

## Hand-off notes
- **FR3**: call `effective_mode(db, district)` (not `district.flood_mode`) when grouping districts, so replays use their snapshot. Use `spec.features` order exactly as the model input order. `spec.fallback.terms[*].alt_feature` means "use the max of the two features".
- **FR5**: at scenario start, store `config["modes"] = snapshot_modes(db, district_ids)`.
- **FR6**: `GET /forecasting/modes/{mode}/calibration` returns `suggested_thresholds`; showing them next to the threshold form is optional.
- **FR2 / FR1**: FLASH needs `slope_mean_deg` and `soil_wetness_top`; if either is not merged, remove it from `flash.yaml` (the spec validator then tells FR3 to retrain).

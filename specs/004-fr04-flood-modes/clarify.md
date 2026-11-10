# Clarifications: FR4 Flood Modes

## Clarifications

### Session 2026-11-10

- Q: Is the mode chosen per district, per tehsil, or detected automatically? → A: Per district, as a config value (`District.flood_mode`). Tehsils inherit it. *Why*: money is district level (Q11), and the guide says "the mode is a district config value, not code". Per-tehsil modes and automatic detection are roadmap.
- Q: Defaults outside the pilot? → A: `data/seeds/flood_modes.yaml` `province_defaults`: SINDH → SLOW_ONSET, KP → FLASH, PUNJAB → SLOW_ONSET, BALOCHISTAN → FLASH, GB → FLASH, AJK → FLASH, ICT → FLASH. Only Sindh and KP are validated; others are labelled "unvalidated" in `GET /forecasting/modes`.
- Q: Should some KP districts default to slow onset (riverine plains such as Nowshera, Charsadda, Peshawar, D.I. Khan)? → A: Not by default. The seed file has an empty `overrides` list and the officer can change it (US4). Open item 1.
- Q: How is "7-day lead for Watch" (Sindh) and "24–48 h lead for Action" (KP) implemented? → A: With per-mode lead weights applied to calibrated probabilities before the max over leads: SLOW `[1,1,1,1,1,1,1]`; FLASH `[1.0,1.0,0.85,0.7,0.6,0.6,0.6]`. With the default 80 Action threshold, a flash district can reach Action from lead 3 only if p ≥ 0.94, and never from leads 4–7. *Why*: a single transparent policy knob; forecast skill for convective bursts beyond 48 h is low, so releasing the largest tranche on day-5 bursts would be a false-alarm generator. Alternatives rejected: separate per-tier lead windows (couples FR4 to FR6 thresholds), training per lead (too few positives).
- Q: Where do mode-specific features come from? → A: FR3's feature library computes all features; the mode YAML chooses the list. SLOW: `rain_3d_mm, rain_7d_mm, rain_14d_mm, rain_30d_mm, rain_fcst_target_mm, soil_wetness_root, soil_wetness_top, elevation_mean_m, vulnerability_weight, lead_day`. FLASH: `rain_3d_mm, rain_7d_mm, rain_24h_fcst_mm, rain_48h_fcst_mm, rain_fcst_target_mm, max_3h_fcst_mm, max_3h_obs_mm, soil_wetness_root, soil_wetness_top, elevation_mean_m, slope_mean_deg, vulnerability_weight, lead_day`. The UC04-1 core (`rain_3d_mm, rain_7d_mm, soil_wetness_root, elevation_mean_m, vulnerability_weight`) is mandatory in every mode (validator).
- Q: What is "soil saturation" for slow onset? → A: NASA POWER `GWETROOT` (root zone, 0–1) as `soil_wetness_root`, plus `GWETTOP` as `soil_wetness_top` when FR1's proposed column exists. The 30-day rain sum carries the longer memory.
- Q: Slope or elevation for flash? → A: Both: `slope_mean_deg` (FR2's proposed SRTM slope) and `elevation_mean_m`. If slope is missing for a tehsil, FR3 uses the district median, else 0 (the term then adds nothing). If FR2's slope never lands, `flash.yaml` swaps the term to `{feature: elevation_mean_m, weight: 0.15, ref: 2000}` (a YAML change, validated).
- Q: What does "thresholds calibration" mean per mode? → A: Two things. (1) **Probability calibration** per mode (isotonic, Platt if < 50 positives), so score 60 means "about a 60% chance" in both modes and the same tier thresholds work everywhere. (2) A **threshold report**: for thresholds 30–90 step 5, district-level hit rate (event caught with score ≥ t at least `lead_focus` days before onset), median lead, false-alarm days per monsoon season; plus `suggested_thresholds` = the smallest threshold per tier whose false-alarm days stay within the mode's budget (WATCH ≤ 3, WARNING ≤ 1.5, ACTION ≤ 0.5 days/season by default). Suggestions are advisory; FR6 holds the real thresholds (NFR12).
- Q: Which data feeds the threshold report, given 2021, 2022 and 2025 are held out? → A: The out-of-fold cross-validation predictions over the training years (FR3 `ml/train.py` saves them as `oof.parquet` beside the artifact). The backtests remain independent.
- Q: Who may change a district's mode? → A: `PDMA_OFFICER` and `ADMIN`, with a reason of at least 10 characters. Audited as `DISTRICT_FLOOD_MODE_CHANGED` with `{district_id, from, to, reason}`. DC and DONOR get 403.
- Q: Does a mode change apply to replays? → A: No. At start, FR5 stores `ReplayScenario.config["modes"] = {district_id: mode}`; `effective_mode()` reads it inside a scenario. A mode change request carrying `X-Scenario-Id` is refused (409 `MODE_LOCKED_IN_REPLAY`); to replay with other modes, reset the scenario after changing the live mode, or edit the scenario snapshot via FR5's start options.
- Q: When does a change take effect? → A: From the next forecast run that starts after the commit. A run in progress keeps the mode it read at its start (FR3 groups districts once per run).
- Q: How is an invalid YAML handled? → A: `ModeRegistry.load_all()` runs at app start and in `tests/unit/forecasting/test_mode_spec.py`; any `ValidationError` aborts start-up. A missing *artifact* is not a config error; it triggers FR3's fallback (UC04-3a) and shows `loaded:false` on `model-info`.
- Q: Is a mode spec versioned? → A: Yes: `spec_version` (int, bumped by hand) and the file's SHA-256 are written into each model card. FR3's registry refuses an artifact whose `features` differ from the spec (fallback + warning).

## Resolved by constitution decisions

- None of D1–D6 decides modes directly. D4 (tiers per event) is unaffected: a mode change mid-event does not reset fired tiers.
- Principle IX decides that modes are configuration; Principle II/NFR12 decides that threshold suggestions are advisory only.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | KP riverine districts as SLOW_ONSET by default | No overrides; officer can switch | Overrides change which artifact scores them; Buner unaffected | PDMA product owner + Data & ML lead |
| 2 | FLASH lead weights | `1.0, 1.0, 0.85, 0.7, 0.6, 0.6, 0.6` | Higher late weights → earlier but noisier Action; lower → later Watch in KP | Data & ML lead |
| 3 | False-alarm budgets for suggestions | WATCH 3, WARNING 1.5, ACTION 0.5 days/season | Changes suggested thresholds only | PDMA product owner |
| 4 | `soil_wetness_top` and `slope_mean_deg` availability | Assume FR1 and FR2 proposals merge | Without them, flash YAML drops the features and the flash model is retrained | Foundation owner |
| 5 | Third mode (GLOF) | Roadmap | Needs new data (glacial lake inventory) | Team lead |
| 6 | Mode change in replay | Refused (409); snapshot at start | Allowing it makes backtests non-reproducible | Data & ML lead |

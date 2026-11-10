# Feature Specification: FR4 Flood Modes

**Feature Branch**: `004-fr04-flood-modes`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: "Support two flood modes: slow onset (Sindh) and flash flood (KP)" (FR4, Forecasting, MVP)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Each district is scored in its own flood mode (Priority: P1)

When the daily forecast runs, the system picks the flood mode for each district (UC04-2): slow onset for Sindh districts and flash flood for KP districts by default. Each mode uses its own features, model and calibration.

**Why this priority**: Sindh 2022 and Buner 2025 are physically different floods; one model would miss one of them, and the NFR3 backtests would fail.

**Independent Test**: Seed Dadu (Sindh) and Buner (KP), run the forecast for one date, and check that Dadu's forecasts carry mode `SLOW_ONSET` and the slow model version, and Buner's carry `FLASH` and the flash model version.

**Acceptance Scenarios**:

1. **Given** a Sindh district with no override, **When** the forecast runs, **Then** it is scored in slow-onset mode with the slow-onset model and features.
2. **Given** a KP district with no override, **When** the forecast runs, **Then** it is scored in flash mode with the flash model and features.
3. **Given** the flash model artifact is missing, **When** the forecast runs, **Then** KP districts are scored with the flash fallback rule and flagged fallback, while Sindh districts still use the slow model.

---

### User Story 2 - Slow-onset mode looks a week ahead (Priority: P1)

Slow-onset mode uses rain accumulated over 7, 14 and 30 days plus soil saturation, so a district can reach Watch up to 7 days before the flood (document Q10).

**Why this priority**: The hero demo (Sindh 2022) depends on the week-ahead Watch.

**Independent Test**: For a fixture where 30-day rain and soil saturation are high and heavy rain is forecast 6 days ahead, the slow-onset score at lead 6 is not discounted and reaches Watch.

**Acceptance Scenarios**:

1. **Given** a slow-onset district, **When** scored, **Then** its features include 7-, 14- and 30-day cumulative rain and soil saturation, and all 7 lead days count fully.
2. **Given** saturated soil and 30-day rain well above normal, **When** the same forecast rain falls on dry soil in another district, **Then** the saturated district scores higher.

---

### User Story 3 - Flash mode focuses on the next 24–48 hours (Priority: P1)

Flash mode uses the maximum 3-hour rain intensity, forecast rain over the next 24 and 48 hours, and slope or elevation. Its lead weights keep days 1–2 at full weight and discount later days, so Action normally fires 24–48 hours ahead (Q10).

**Why this priority**: Buner 2025 was a cloudburst; daily totals alone under-state it.

**Independent Test**: For a fixture with an 80 mm/3 h burst forecast for day 2 on steep slopes, the flash score peaks at lead 2 and reaches Action; the same burst at day 6 cannot reach Action.

**Acceptance Scenarios**:

1. **Given** a flash district, **When** scored, **Then** its features include max 3-hour intensity, 24 h and 48 h forecast rain, and slope or elevation.
2. **Given** identical probabilities at lead 2 and lead 6, **When** the score is computed, **Then** lead 2 produces the higher contribution and is the peak.
3. **Given** default weights, **When** only lead 5–7 probabilities are high, **Then** the score cannot exceed 60.

---

### User Story 4 - An officer changes a district's mode through configuration (Priority: P2)

A PDMA officer or admin can switch a district between modes with a written reason, for example a riverine KP district that behaves like a slow-onset plain. The change is audited and applies from the next daily run, with no code change (NFR11).

**Why this priority**: Province defaults are a starting point; local knowledge must be able to override them.

**Independent Test**: Change Nowshera to `SLOW_ONSET` with a reason, run the forecast, and check that Nowshera now uses the slow model and that an audit entry exists.

**Acceptance Scenarios**:

1. **Given** an officer, **When** they change a district's mode with a reason, **Then** the change is saved, audited, and the next run uses the new mode.
2. **Given** no reason, **When** they submit, **Then** the change is refused and nothing is saved.
3. **Given** a DC or donor, **When** they try to change a mode, **Then** access is refused.
4. **Given** a replay is running, **When** a live mode change happens, **Then** the replay keeps the modes it started with.

---

### User Story 5 - Thresholds calibration report per mode (Priority: P3)

For each mode, the system publishes what the tier thresholds mean on historical data: how often a flood was caught at each threshold, how early, and how many false-alarm days per season, with suggested thresholds. The officer decides; nothing changes automatically.

**Why this priority**: Answers "What about false alarms?" and helps PDMA pick thresholds; not needed to trigger.

**Independent Test**: Run the report on a fixture of scores and labels; it lists hit rate, median lead and false-alarm days for 40/60/80 and a suggestion per tier.

**Acceptance Scenarios**:

1. **Given** a trained mode, **When** the report is requested, **Then** it shows, for each threshold, hit rate, median lead days and false-alarm days per season.
2. **Given** a suggestion differs from a district's threshold, **When** the forecast runs, **Then** the district's configured threshold is still the one used.

### Edge Cases

- A district outside the pilot provinces: its mode comes from the province default in the seed file; if the province has none, `SLOW_ONSET`.
- Slope missing for a tehsil: use the district median slope, else 0; if slope is unavailable for all tehsils, the flash definition swaps the slope term for elevation (configuration change).
- `soil_wetness_top` not available: flash spec falls back to `soil_wetness_root` (YAML change, validated).
- Invalid mode YAML (weights not summing to 1, unknown feature, lead weight outside (0, 1]): the service refuses to start and CI fails.
- Artifact trained on a different feature list than the YAML: treated as a model failure (fallback) and reported on `model-info`.
- A district's mode changes while an event is open: the event continues; scores from then on use the new mode; the explanation states the mode.
- Changing to the same mode: no-op, no audit entry.

## Requirements *(mandatory)*

### Functional Requirements

- **FR4-001**: The system MUST support exactly two flood modes in the MVP, `SLOW_ONSET` and `FLASH`, and each district MUST have exactly one mode stored as configuration.
- **FR4-002**: Default modes MUST come from a seed file by province: Sindh → slow onset, KP → flash; per-district overrides MUST be possible in the same file.
- **FR4-003**: Slow-onset mode MUST use 7-, 14- and 30-day cumulative rainfall and soil saturation in addition to the common features (3- and 7-day rain, soil wetness, elevation, vulnerability).
- **FR4-004**: Slow-onset mode MUST count all 7 lead days fully, so Watch can be reached up to 7 days ahead.
- **FR4-005**: Flash mode MUST use maximum 3-hour rainfall intensity (forecast and observed), 24-hour and 48-hour forecast rain, and slope (or elevation) in addition to the common features.
- **FR4-006**: Flash mode MUST weight lead days so that leads 1–2 count fully and later leads are discounted (defaults 1.0, 1.0, 0.85, 0.7, 0.6, 0.6, 0.6).
- **FR4-007**: Each mode MUST have its own model artifact, version, calibration and fallback parameters; no artifact is shared between modes.
- **FR4-008**: Mode definitions MUST be data files validated at start-up; an invalid definition MUST stop the service with a clear error.
- **FR4-009**: A PDMA officer or admin MUST be able to change a district's mode with a written reason; the change MUST be audited and take effect from the next daily run.
- **FR4-010**: A replay MUST use the district modes captured when it started.
- **FR4-011**: Every forecast, risk index, explanation and API response MUST state the mode used.
- **FR4-012**: For each mode the system MUST provide a thresholds calibration report (hit rate, median lead, false-alarm days per season per threshold, and suggested thresholds); suggestions MUST NOT change any configured threshold.
- **FR4-013**: The system MUST list the available modes with their features, lead weights, fallback rule, active model version and the number of districts using each.
- **FR4-014**: Glacial lake outburst floods MUST NOT be modelled in the MVP; adding a third mode later MUST need only a new definition file, an artifact and an enum value.

### Key Entities

- **District**: `flood_mode` (config value).
- **FloodForecast / RiskIndex**: `mode` recorded on each row.
- **ReplayScenario**: start-time snapshot of district modes (in its config).
- **AuditLog**: `DISTRICT_FLOOD_MODE_CHANGED`.
- **Mode definition** (file, not a table): features, lead weights, fallback terms, calibration policy, artifact version.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of Sindh pilot districts are scored in slow-onset mode and 100% of KP pilot districts in flash mode on a default seed.
- **SC-002**: Changing a district's mode takes one API call and zero code changes, and is effective on the next run (NFR11).
- **SC-003**: Buner 2025 (flash) and Sindh 2022 (slow onset) both trigger in FR5 backtests and the 2021 control does not (NFR3).
- **SC-004**: In the Buner backtest, the first Action-level score has its peak at lead 1 or 2.
- **SC-005**: The calibration report exists for both modes and covers thresholds 30–90 in steps of 5.
- **SC-006**: 0 invalid mode definitions reach the main branch (CI validation).

## Assumptions

- FR2 provides tehsil max 3-hour rain (IMERG) and slope from SRTM; FR3 provides forecast max 3-hour rain.
- Both modes share FR3's scoring scale, so tier thresholds mean the same calibrated probability in both.
- Pilot provinces are Sindh (30 districts) and KP (38 districts); other provinces get defaults but are not validated.
- Officers have the domain knowledge to override modes; the system does not detect the mode automatically.

## Traceability

| Item | How FR4 covers it |
|---|---|
| UC04 step 2 | US1 (mode per district) |
| Q7 (one engine, two modes; GLOF roadmap) | FR4-001, FR4-014 |
| Q10 (7 days for Watch in Sindh; 24–48 h for Action in KP) | US2, US3, FR4-004, FR4-006 |
| NFR3 | SC-003 (via FR5) |
| NFR4 | FR4-011 |
| NFR11 | US4, FR4-008, FR4-009 |
| NFR12 | FR4-012 suggestions are advisory |
| Decisions | D4 unaffected (tiers per event); no other D applies directly |

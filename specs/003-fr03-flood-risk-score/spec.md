# Feature Specification: FR3 Flood Risk Score

**Feature Branch**: `003-fr03-flood-risk-score`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: "Calculate a 0 to 100 flood risk score per district and tehsil, up to 7 days ahead" (FR3, Forecasting, MVP)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Daily 7-day risk score per district and tehsil (Priority: P1)

After the daily data ingest (UC03-6), the system builds features for every active tehsil (UC04-1), predicts a flood probability for each of the next 7 days (UC04-3), converts it to a 0–100 score (UC04-4), derives each district's score from its tehsils, and saves everything with the inputs that produced it.

**Why this priority**: This is the core of the product. Without a score, no tier fires and no money moves.

**Independent Test**: Seed 2 districts with tehsils, observations and a model artifact; run the forecast step for one date; check that 7 forecast rows per tehsil and per district exist, and one risk index per tehsil and district, each 0–100 and each holding its features.

**Acceptance Scenarios**:

1. **Given** today's observations are stored for every tehsil, **When** the daily forecast runs, **Then** each active tehsil and district gets one forecast per lead day 1–7 with a probability between 0 and 1, and one risk index whose score is `round(100 × the highest adjusted probability)` and whose peak lead day is the lead that produced it.
2. **Given** a district with three tehsils holding 60%, 32% and 8% of its population, **When** the scores are computed, **Then** the district score equals the higher of the first two tehsils' scores and ignores the 8% tehsil.
3. **Given** a district marked inactive, **When** the run executes, **Then** no rows are written for it.
4. **Given** the run has already completed for a date, **When** it runs again with the same inputs, **Then** no duplicate rows appear and the scores are unchanged.

---

### User Story 2 - Tier mapping and hand-off to the trigger engine (Priority: P1)

Each score maps to a tier using that district's own thresholds (Watch 40, Warning 60, Action 80 by default). Each district result is handed to the trigger engine so it can fire tiers (UC04-6) or close an episode.

**Why this priority**: The tier is what releases money.

**Independent Test**: Configure custom thresholds (e.g. 30/50/70) for one district, run the forecast with a known score of 55, check that the tier is Watch and that the trigger engine received that risk index.

**Acceptance Scenarios**:

1. **Given** thresholds 40/60/80 and a district score of 63, **When** scored, **Then** the tier is Warning.
2. **Given** a score of 39, **When** scored, **Then** the tier is None.
3. **Given** a district's scores are saved, **When** the run continues, **Then** the trigger engine is called once for that district with the saved district risk index, after the scores are committed.
4. **Given** the trigger engine fails for one district, **When** the run continues, **Then** that district's scores stay saved, other districts are unaffected, and the failure is reported in the run summary.

---

### User Story 3 - Forecast rainfall input with honest provenance (Priority: P1)

Observations alone cannot forecast 7 days ahead. For each tehsil, the system obtains forecast rainfall for days 1–7 (daily total and maximum 3-hour intensity) and records where each value came from: a live forecast, an archived forecast, or an observed-rain "perfect forecast proxy" used only in replays.

**Why this priority**: Without forecast rainfall, the score cannot look ahead and Principle I fails.

**Independent Test**: Mock the forecast provider for 2 tehsils and run the forecast-rain step; check 7 forecast-rain observations per tehsil with provenance `LIVE_FORECAST`.

**Acceptance Scenarios**:

1. **Given** the forecast provider responds, **When** the step runs, **Then** each tehsil gets forecast rain for lead days 1–7 with daily total, maximum 3-hour total and provenance.
2. **Given** the forecast provider is down, **When** the step runs, **Then** yesterday's forecast is shifted by one day, marked stale with the date of the last valid forecast, and the admin is alerted (UC03-2a).
3. **Given** a replay day whose inputs come from a perfect forecast proxy, **When** its scores are read, **Then** every response states that the forecast is a proxy.

---

### User Story 4 - Rule-based fallback and stale flags (Priority: P2)

If the model fails, the system uses a published rule-based rainfall score and flags the result as fallback (UC04-3a). If any input is stale, the result is flagged stale with the date of the last valid data (NFR9).

**Why this priority**: The system must never go silent during a flood because a model file is broken.

**Independent Test**: Delete the model artifact for one mode and run; every district of that mode gets a score with `is_fallback=true` and model version `rules-fallback-1`.

**Acceptance Scenarios**:

1. **Given** the model artifact cannot be loaded, **When** the run executes, **Then** each affected tehsil and district is scored with the rule, flagged fallback, and still mapped to a tier and handed to the trigger engine.
2. **Given** the model returns an invalid value (missing, not a number, or outside 0–1), **When** scoring that tehsil, **Then** that tehsil uses the rule and is flagged fallback.
3. **Given** NASA POWER data for a district is copied forward from three days ago, **When** scored, **Then** the district and its tehsils show `is_stale_input=true` and the last valid date.
4. **Given** a fallback or stale result, **When** the audit entry is written, **Then** its payload contains both flags.

---

### User Story 5 - Plain-language explanation in English and Urdu (Priority: P2)

Each score carries a short explanation in English and Urdu, generated from templates over the stored inputs (UC04-5, D2): the score, the tier, when the risk peaks, and the two or three biggest reasons, plus a notice when the result is fallback, stale or based on a proxy forecast.

**Why this priority**: Officials must understand why money is about to move (NFR4, judge question "Why trust the model?").

**Independent Test**: Render the explanation for a fixed input set and compare to golden English and Urdu strings.

**Acceptance Scenarios**:

1. **Given** a district score of 82 (Action) peaking in 2 days, driven by 7-day rain of 184 mm and soil wetness of 91%, **When** explained, **Then** both texts mention 82, the Action tier, 2 days, 184 mm and 91%.
2. **Given** a fallback result, **When** explained, **Then** both texts say the score came from the rainfall rule.
3. **Given** any explanation, **When** its numbers are extracted, **Then** every number appears in the stored inputs.

---

### User Story 6 - Read APIs for the map, the chart and the trace (Priority: P3)

The dashboard reads the tehsil risk map, a district's 7-day chart with tier and explanation, its history, and a full trace of any risk index (UC05-1 to UC05-3, UC07-2 data side).

**Why this priority**: The data exists after US1–US5; the API exposes it.

**Independent Test**: Call each endpoint with seeded rows and the right roles; check shapes, role scoping and the `X-Scenario-Id` filter.

**Acceptance Scenarios**:

1. **Given** today's scores, **When** an officer requests the risk map at tehsil level, **Then** every active tehsil appears with its score, tier and flags.
2. **Given** a DC of Dadu, **When** they request Buner's detail, **Then** access is refused.
3. **Given** a replay scenario id in the request header, **When** reading, **Then** only that scenario's rows are returned.

---

### User Story 7 - Train and calibrate the model per mode (Priority: P3)

A data scientist builds a labelled training set from historical rainfall and flood records, trains a model per mode, calibrates its probabilities, evaluates it on held-out years and publishes a versioned artifact with a model card.

**Why this priority**: The MVP can run on the fallback rule; a trained model improves skill and is needed for a credible pitch.

**Independent Test**: Run the training script on a small fixture dataset; it writes an artifact, a calibrator and a model card containing metrics, and the model registry loads it.

**Acceptance Scenarios**:

1. **Given** the training command, **When** it runs, **Then** the replay years (2021, 2022, 2025) are excluded from training and calibration.
2. **Given** a new artifact, **When** the registry loads it, **Then** the version string appears on every forecast row it produces.

### Edge Cases

- A tehsil has no IMERG data: use its district's NASA POWER rainfall for the observed part and record the substitution in the features.
- No population data for a district's tehsils, or no tehsil reaches the 10% share: all tehsils are eligible for the district maximum.
- The forecast covers fewer than 7 days (provider outage, stale shift): the missing lead uses the last available lead's value (persistence) and is flagged stale.
- Two lead days produce the same top probability: the earlier lead is the peak.
- Probability exactly 0.405 → score 41 (round half up), not 40.
- A district has no fund or tier configuration: score is saved with tier mapped against the default thresholds from settings, and a warning is logged.
- Thresholds change mid-day: the run uses the thresholds read at the start of that district's scoring (UC01 postcondition: next run).
- The district's mode changes between runs: the next run uses the new mode; past rows keep their old mode.
- Re-run on a date whose district risk index already fired a release: that district is skipped so the release keeps pointing at the score that fired it.
- Run date in the future or before the data window: rejected.

## Requirements *(mandatory)*

### Functional Requirements

- **FR3-001**: System MUST compute, for each active tehsil and each lead day 1–7, a flood probability between 0 and 1, where the target date is the run date plus the lead day.
- **FR3-002**: System MUST build features from 3-day and 7-day rainfall, soil wetness, elevation and vulnerability for every mode (UC04-1), plus the extra features the district's mode lists (FR4).
- **FR3-003**: Rainfall windows MUST combine observed rainfall for past days with forecast rainfall for future days, and record which source supplied each day.
- **FR3-004**: System MUST obtain forecast rainfall (daily total and maximum 3-hour total) for lead days 1–7 per tehsil each day, and store its provenance: live forecast, archived forecast, archived short-lead proxy, or perfect forecast proxy.
- **FR3-005**: System MUST use the model and calibration belonging to the district's flood mode, and apply that mode's lead-day weights.
- **FR3-006**: Score MUST equal `round_half_up(100 × max over lead days of the adjusted probability)`, clipped to 0–100; the peak lead day MUST be stored (earliest lead on ties).
- **FR3-007**: The district score MUST be derived from its tehsils: per lead day, the maximum probability over tehsils whose population share is at least 10% (configurable); if none qualifies, all tehsils count.
- **FR3-008**: The tier MUST be the highest tier whose minimum score the score reaches, using the district's configured thresholds; below Watch the tier is None.
- **FR3-009**: Every forecast MUST store the exact feature values, the model version, the mode and the forecast provenance (NFR4).
- **FR3-010**: Every risk index MUST store the top contributing factors with their values and relative contributions.
- **FR3-011**: If the model cannot be loaded, fails, or returns an invalid value, the system MUST score with the mode's rule-based rainfall formula and set the fallback flag (UC04-3a).
- **FR3-012**: If any input was copied forward from older data, the result MUST be flagged stale with the oldest last-valid date among its inputs (NFR9).
- **FR3-013**: Fallback, stale and proxy flags MUST appear in every API response that shows the score and in the audit entry.
- **FR3-014**: System MUST produce an English and an Urdu explanation for every risk index from templates over stored inputs (D2); every number in the text MUST come from the inputs.
- **FR3-015**: After a district's scores are saved, the system MUST hand the district risk index to the trigger engine (UC04-6), once per district per run.
- **FR3-016**: Each district's scores MUST be saved together with one audit entry (`RISK_SCORED`) in the same transaction.
- **FR3-017**: Re-running the same date MUST be idempotent and MUST NOT change a risk index that a fund release already references.
- **FR3-018**: All writes MUST carry the current scenario id, and all dates MUST come from the injected clock, so the same code runs live and in replay (FR5).
- **FR3-019**: The daily run for all configured districts MUST complete within 15 minutes (NFR1).
- **FR3-020**: The system MUST expose read access to the risk map, district detail, history, tehsil detail, risk-index trace and active model versions, scoped by role (a DC sees only their district) and by scenario.
- **FR3-021**: An administrator MUST be able to start a forecast run for a date on demand.
- **FR3-022**: Model training MUST exclude the replay event years (2021, 2022, 2025) and publish a versioned artifact with a model card.

### Key Entities

- **ClimateObservation** (source `FORECAST_RAIN`): forecast rain per tehsil and target date with lead day, stale flags and provenance.
- **FloodForecast**: probability per tehsil (and district aggregate) per run date and lead day, with features, mode, model version and flags.
- **RiskIndex**: one per tehsil and district per run date; score, peak lead day, tier, explanations, top factors, flags.
- **District / Tehsil**: mode, vulnerability weight, population, elevation, activity.
- **TriggerTier / DisasterFund**: thresholds read for tier mapping.
- **DataSourceStatus**: health of `FORECAST_RAIN`.
- **AuditLog**: `RISK_SCORED` entries.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The daily forecast for all configured districts (pilot: 68 districts, about 260 tehsils) finishes in under 15 minutes end to end, and in under 2 minutes excluding upstream ingestion (NFR1).
- **SC-002**: For 100% of a random sample of 50 risk indexes, recomputing the score from the stored features and model version gives the stored score (NFR4).
- **SC-003**: With a mode's model artifact removed, 100% of that mode's districts still receive a score, tier and explanation in the same run, all flagged fallback (UC04-3a).
- **SC-004**: The FR5 backtests pass: Sindh 2022 and Buner 2025 trigger, the 2021 control produces no trigger (NFR3).
- **SC-005**: 100% of district risk indexes have English and Urdu explanations; 0 explanations contain a number absent from the inputs.
- **SC-006**: Forecast read endpoints answer at p95 under 500 ms with a full day of pilot data (NFR1).
- **SC-007**: On held-out years, the calibrated model's Brier score is at least 20% better than climatology, and each reliability bin with 30 or more samples is within ±0.10 of the observed frequency (target; if missed, the logistic baseline or the rule ships and the model card says so).

## Assumptions

- FR1 and FR2 store observations before the forecast step starts; FR2 backfills 30 days of IMERG history for each pilot tehsil.
- An external forecast rainfall service with a free tier is acceptable for the hackathon (see clarify.md).
- Tehsil populations come from the seed data (census 2017/2023, OCHA COD-PS); missing values are allowed.
- Training labels come from public flood records (Global Flood Database, Sentinel-1 derived maps, NDMA situation reports) curated into one file; label quality is limited and stated in the model card.
- Thresholds default to 40/60/80 until FR6 configuration exists.
- All days are UTC calendar days; displays convert to Asia/Karachi.

## Traceability

| Item | How FR3 covers it |
|---|---|
| UC04 main flow 1, 3, 4, 6 | US1, US2 |
| UC04 step 2 | Mode picked per district, implemented with FR4 |
| UC04 step 5 | US5 (templates, D2); LLM rewrite in FR13 |
| UC04 alt 3a | US4 |
| UC03 step 6, alt 2a | Forecast step after ingest; stale forecast rain |
| UC05 steps 1–3, alt 1a | US6 data side |
| UC07 step 2 | Risk-index trace endpoint |
| NFR1 | SC-001, SC-006 |
| NFR3 | SC-004 (via FR5) |
| NFR4 | FR3-009, FR3-010, SC-002 |
| NFR9 | FR3-012, FR3-013 |
| NFR11 | Thresholds, modes, aggregation share are config |
| NFR13 | Forecast provenance and proxy labelling |
| D2 | Template explanations; LLM is FR13 |
| D4 | Engine called every run so it can count days below Watch |

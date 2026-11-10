# Feature Specification: FR1 NASA POWER Ingestion

**Feature Branch**: `001-fr01-nasa-power-ingestion`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: User description: "FR1 Ingest daily NASA POWER rainfall, temperature and soil wetness for each district" (Area: Data, Priority: MVP). Use case UC03 Ingest climate data, steps 1, 2, 4, 5, 6 and alternate flow 2a.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Daily weather ingest for every active district (Priority: P1)

Each morning the daily job pulls rainfall, air temperature and root-zone and top-soil wetness for the centre point of every active district, checks the values and stores them. The forecast then starts on fresh data (UC03-1, -2, -5, -6).

**Why this priority**: Every risk score (FR3) and both flood modes (FR4) depend on these observations. Without them nothing triggers.

**Independent Test**: Run the ingest for a fixed date range against recorded NASA POWER responses. Check that one observation per district per day is stored with correct values and that the forecast step is invoked afterwards.

**Acceptance Scenarios**:

1. **Given** 12 active districts with centroids and NASA POWER reachable, **When** the daily job runs, **Then** each district has an observation for every day of the look-back window that NASA POWER has published, with rainfall in mm/day, temperature in °C and soil wetness as a 0–1 fraction.
2. **Given** a response where some days carry the fill value `-999`, **When** it is ingested, **Then** those values are treated as missing and never stored as numbers.
3. **Given** a value outside its valid range (e.g. rainfall 2 000 mm/day, soil wetness 1.7), **When** it is validated, **Then** it is rejected as missing and a quality note is recorded. **Given** soil wetness reported as a percentage (e.g. 45), **Then** it is converted to 0.45 and the conversion is noted.
4. **Given** the same date range is ingested twice, **When** the second run finishes, **Then** there are no duplicate observations and later values replace earlier ones.
5. **Given** ingestion finishes (successfully or not), **When** the daily job continues, **Then** the forecast step (FR3) starts.
6. **Given** an inactive district, **When** the job runs, **Then** it is skipped.

---

### User Story 2 - Keep going on stale data when NASA POWER is down (Priority: P1)

If NASA POWER fails or falls further behind than its normal latency, the system uses the last valid data, marks it stale with the date of that data, shows the staleness to users and alerts the admin (UC03-2a, NFR9).

**Why this priority**: A data outage during the monsoon must not stop the forecast or silently hide risk (Principle VII).

**Independent Test**: Simulate an outage (HTTP 503 for all calls). Check stale rows, the source status, one admin alert and that the forecast step still runs. Then simulate recovery.

**Acceptance Scenarios**:

1. **Given** NASA POWER returns errors after retries, **When** the daily job runs, **Then** for each district each overdue day is filled from the last valid observation, flagged stale, with `stale_since` set to the last valid date.
2. **Given** the source was healthy and now fails, **When** the job runs, **Then** the source status becomes unhealthy, records the error and last valid date, and the admin gets exactly one alert for this outage.
3. **Given** the source stays down for several days, **When** the job runs again, **Then** no extra admin alert is sent and copy-forward stops after the configured maximum (3 days) so very old data is not repeated indefinitely.
4. **Given** the source recovers, **When** the next run succeeds, **Then** stale rows for days now published are replaced with fresh values, the status returns to healthy and a recovery entry is recorded.
5. **Given** data is only late by the normal publication latency (≤ 3 days), **When** the job runs, **Then** nothing is marked stale.

---

### User Story 3 - Replay-ready historical inputs (Priority: P2)

The team caches NASA POWER data for the replay events once, so the replay (FR5) runs the real ingest code offline, day by day, without seeing future data.

**Why this priority**: The 3-minute live demo and the NFR3 backtests run on replays. Live internet must not be required on stage.

**Independent Test**: Generate the cache for `control-2021`, start that scenario with the replay clock at a given date and run ingest. Check that rows carry the scenario id and that no observation date is later than the replay date minus the simulated lag.

**Acceptance Scenarios**:

1. **Given** a scenario key and window, **When** the cache command runs, **Then** one raw response file per scenario district is saved, covering the window plus a 30-day spin-up.
2. **Given** a running replay at date D, **When** ingest runs, **Then** observations are read from the cache, only dates ≤ D − simulated lag are stored, and every row carries the scenario id.
3. **Given** a replay run, **When** ingest finishes, **Then** the live source status is unchanged.
4. **Given** an optional outage list in the cache folder, **When** the replay date falls inside it, **Then** the stale path of User Story 2 is exercised (for a demo of the stale banner).

---

### User Story 4 - Refresh context indicators and vulnerability weights (Priority: P3)

On a monthly cycle (or on demand by the admin) the system refreshes World Bank and EasyData indicators for each district and recomputes the district's vulnerability weight (UC03-4).

**Why this priority**: Vulnerability is a forecast feature (UC04-1) and drives the fairness check (NFR5), but it changes slowly. A seeded value is enough to start.

**Independent Test**: Load a small indicator file, run the refresh, and check stored indicators, recomputed weights between 0 and 1, imputed flags for missing inputs and an audit entry for each changed weight.

**Acceptance Scenarios**:

1. **Given** indicator values for all active districts, **When** the refresh runs, **Then** each district's vulnerability weight is recomputed as the configured weighted combination of normalised indicators, within 0–1.
2. **Given** a district missing an indicator, **When** the refresh runs, **Then** the province median is used, the district is marked as having imputed (sparse) data and the weight is still produced.
3. **Given** a weight changes, **When** the refresh saves it, **Then** an audit entry records the old and new value and the inputs, and the new weight applies from the next daily run.
4. **Given** the external World Bank service is unreachable, **When** the refresh runs, **Then** the previous indicators are kept, the source is marked unhealthy and nothing else changes.

---

### Edge Cases

- A district centroid falls in a NASA POWER grid cell that is mostly outside the district (thin or coastal districts). The centroid is still used. An admin may override the point via `centroid_lat/lon` in seeds.
- NASA POWER returns HTTP 200 with a header warning and all values `-999` for recent days: treated as "not yet published", not an error, unless older than the expected lag.
- Units in the response header differ from the expected units: the whole response for that district is rejected (not guessed) and counted as an error.
- Small negative rainfall (−0.01) from processing artefacts: clamped to 0 with a quality note.
- Partial outage (some districts fail): only failed districts get stale rows. The source is unhealthy if any active district needed copy-forward.
- No previous valid observation exists (first run during an outage): no row is written for that district and the error is reported. FR3 must then use its own fallback.
- Two runs at once (scheduler and admin manual run): the second is refused with a conflict.
- A new district is activated mid-season: it is ingested from the next run with no code change (NFR11). A backfill can be run for it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR01-001**: System MUST fetch daily rainfall (corrected precipitation), air temperature at 2 m, root-zone soil wetness and top-soil wetness from NASA POWER for the centroid of every active district.
- **FR01-002**: System MUST run this ingest once a day as the first step of the daily pipeline, re-fetching a configurable look-back window (default 10 days) so late-published and revised values are captured.
- **FR01-003**: System MUST treat the NASA POWER fill value (`-999`, or the value given in the response header) as missing.
- **FR01-004**: System MUST validate ranges (rainfall 0–600 mm/day, temperature −30 to 55 °C, soil wetness 0–1) and units, convert percentage soil wetness to a fraction, clamp tiny negative rainfall to 0, reject other out-of-range values as missing, and record every fix or rejection as a quality note on the observation.
- **FR01-005**: System MUST store observations idempotently: one row per district, date, source and scenario. Re-ingest updates in place.
- **FR01-006**: System MUST distinguish normal publication latency (days newer than `today − expected lag`, default 3) from overdue data, and MUST NOT flag data stale for normal latency.
- **FR01-007**: When data for an overdue day is missing for a district, System MUST write a copy of the last valid observation for that day, flagged stale with the last valid date, for at most a configurable number of days (default 3) after the last valid date.
- **FR01-008**: System MUST maintain the NASA POWER source status: last success time, last valid date (the earliest of the per-district latest fresh dates), last error, and healthy flag.
- **FR01-009**: System MUST alert the admin once when the source turns unhealthy and record a recovery when it turns healthy again.
- **FR01-010**: Ingest failures MUST NOT stop the daily pipeline. The forecast step MUST start after ingest in every case (UC03-6).
- **FR01-011**: Every observation and the run summary MUST carry the active scenario (null for live). A replay run MUST NOT change the live source status.
- **FR01-012**: In replay, System MUST read cached historical responses and MUST NOT expose any observation dated later than the replay date minus the configured simulated lag (default 2 days).
- **FR01-013**: System MUST provide a command to cache NASA POWER responses for a scenario's districts and window (plus 30-day spin-up), and to backfill any date range for model training.
- **FR01-014**: The admin MUST be able to trigger an ingest for a date range (max 31 days via the API) and a subset of districts, and see a run summary.
- **FR01-015**: PDMA officers and admins MUST be able to list observations by district, source and date range. A DC MUST see only their own district.
- **FR01-016**: System MUST write one audit entry per ingest run, plus entries for source health transitions, in the same transaction as the data it describes.
- **FR01-017**: System MUST refresh district context indicators from World Bank and EasyData sources on a monthly schedule and on admin demand, store them with source and period, and recompute each district's vulnerability weight (0–1) from configurable indicator weights.
- **FR01-018**: Missing indicators MUST be imputed with the province median and flagged, so the fairness check (FR11) can identify sparse-data districts.
- **FR01-019**: Each changed vulnerability weight MUST be audited with old value, new value and inputs, and MUST apply from the next daily run.
- **FR01-020**: No district names, centroids, thresholds or weights may be hard-coded. All come from the database or settings.

### Key Entities

- **District**: the unit queried. Uses `centroid_lat`, `centroid_lon`, `is_active`. FR1 writes `vulnerability_weight`.
- **ClimateObservation**: one row per district per day with `source=NASA_POWER`: `rainfall_mm`, `temp_c`, `soil_wetness`, stale flags, `scenario_id`.
- **DataSourceStatus**: health of `NASA_POWER`, `WORLD_BANK`, `EASYDATA`.
- **ContextIndicator**: district indicators such as `poverty_headcount`, `housing_kutcha_pct`, `literacy_rate`.
- **AuditLog**: run summaries, health transitions, weight changes.
- **Alert**: one admin notice per outage.
- **ReplayScenario**: supplies the scenario key, window and districts for the cache.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The daily NASA POWER ingest for the 12 pilot districts finishes in under 3 minutes, and for all 68 Sindh + KP districts in under 6 minutes, leaving the rest of the 15-minute budget to forecasting (NFR1).
- **SC-002**: 0 observations stored with a fill value or out-of-range number (checked by a database query after a 1-year backfill).
- **SC-003**: During a simulated outage, 100% of active districts still have an observation for every overdue day up to the copy-forward limit, all flagged stale with the correct last valid date, and the forecast step still runs (NFR9).
- **SC-004**: Exactly 1 admin alert per outage, regardless of its length.
- **SC-005**: In replay, 0 observations dated after replay date minus the simulated lag (no lookahead), across all three scenarios (supports NFR3).
- **SC-006**: Activating a new district requires only a data change and produces observations on the next run (NFR11).
- **SC-007**: Every vulnerability weight is reproducible from its stored indicators and settings (NFR4).

## Assumptions

- NASA POWER daily data is published with a latency of about 2–3 days; the expected-lag default (3) is adjustable.
- The district centroid is a fair point estimate. Tehsil-level rainfall detail comes from IMERG (FR2).
- World Bank's API gives national or province values only. District-level indicators come from an EasyData / PSLM / MPI export kept as a seed file, refreshed when a new release appears.
- The daily pipeline runs at 01:00 UTC (foundation setting `PIPELINE_CRON`).
- Days are UTC calendar days.

## Traceability

| Item | How FR1 covers it |
|---|---|
| UC03 steps 1, 2 | Daily scheduled pull per district (US1) |
| UC03 step 4 | Indicator refresh (US4) |
| UC03 step 5 | Validation of missing values and units (US1) |
| UC03 step 6 | Pipeline continues to FR3 (FR01-010) |
| UC03 alt 2a | Stale path and admin alert (US2) |
| UC04 step 1 | Supplies rainfall, soil wetness and vulnerability features |
| NFR1 | SC-001 |
| NFR3 | Replay caches (US3), SC-005 |
| NFR4 | Quality notes and stored indicator inputs |
| NFR5 | Imputed flags for sparse-data districts |
| NFR9 | US2, SC-003 |
| NFR11 | SC-006 |
| Decisions | None of D1–D6 apply directly |

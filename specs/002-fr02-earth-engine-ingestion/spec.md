# Feature Specification: FR2 Earth Engine Ingestion

**Feature Branch**: `002-fr02-earth-engine-ingestion`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: User description: "FR2 Ingest GPM IMERG rainfall and Sentinel 1 flood maps through Google Earth Engine" (Area: Data, Priority: MVP). Use case UC03 Ingest climate data, step 3 ("System pulls GPM IMERG rainfall per tehsil through Google Earth Engine"), and the data side of UC02 Verify flood by satellite.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Daily satellite rainfall per tehsil (Priority: P1)

Each morning the daily job asks Earth Engine for the previous days' satellite rainfall, summarises it for every active tehsil as a daily total and the heaviest 3-hour burst, and stores it. The forecast uses it for tehsil-level detail that the coarse district weather data cannot give (UC03-3, document Q15 judge answer).

**Why this priority**: Tehsil scores (FR3) and flash-flood mode (FR4, KP) depend on it. Max 3-hour intensity is the main flash-flood signal.

**Independent Test**: With Earth Engine responses replaced by recorded fixtures, run the ingest for two days and check one row per tehsil per day with correct totals and 3-hour maxima.

**Acceptance Scenarios**:

1. **Given** active pilot tehsils and a complete day of 48 half-hourly rainfall images, **When** the job runs, **Then** each tehsil gets one observation for that day with the daily total in mm and the maximum 3-hour total in mm.
2. **Given** the most recent day has fewer than 48 images (not yet complete), **When** the job runs, **Then** that day is skipped and picked up by a later run; nothing is marked stale.
3. **Given** a tehsil smaller than one rainfall grid cell, **When** it is summarised, **Then** it still gets a value (from the cell covering it), not a blank.
4. **Given** the same day is ingested twice, **Then** no duplicate rows exist and values are updated.
5. **Given** ingestion finishes, **When** the daily pipeline continues, **Then** the forecast step starts.

---

### User Story 2 - Replay-ready satellite rainfall (Priority: P1)

The team caches tehsil rainfall for the replay events once, so replays run offline through the same ingest code without seeing future days.

**Why this priority**: The live demo (Sindh 2022, Buner 2025, control 2021) runs on replays, and Buner's flash-flood trigger depends on 3-hour intensity.

**Independent Test**: Generate the cache for one scenario, run a replay day, and check rows carry the scenario id and stop at the replay date minus the simulated lag.

**Acceptance Scenarios**:

1. **Given** a scenario key, **When** the cache command runs, **Then** a file holds daily total and 3-hour maximum for every scenario tehsil and day in the window plus 30 days of spin-up.
2. **Given** a replay at date D, **When** ingest runs, **Then** only days ≤ D − simulated lag (default 1) are stored, each with the scenario id, and the live source status is untouched.

---

### User Story 3 - Terrain per tehsil (Priority: P2)

The system knows each tehsil's mean elevation and mean slope, computed once from SRTM and kept as seed data (UC04-1 "elevation"; FR4 flash mode "slope or elevation").

**Why this priority**: Static inputs. Needed before model training, but computed once.

**Independent Test**: Run the seed load from the terrain file and check every pilot tehsil has elevation and slope; run the compute script against Earth Engine once to regenerate the file.

**Acceptance Scenarios**:

1. **Given** tehsil boundaries, **When** the terrain script runs, **Then** each tehsil gets mean elevation (m) and mean slope (degrees), written to the database and to a seed file.
2. **Given** a fresh database without Earth Engine access, **When** seeding runs, **Then** terrain values load from the seed file.

---

### User Story 4 - Sentinel-1 flood-change building blocks (Priority: P2)

Satellite & GIS can ask: "for this area, compared with a dry reference period, where is there new water on the radar image after the event, excluding permanent water and steep slopes, and how many km² per tehsil?", and get images for the before and after views (UC02-1, -2, -3 data side).

**Why this priority**: D1 makes precomputed before/after flood maps for the replay events an MVP deliverable (built in FR21). FR2 owns the Earth Engine logic they use.

**Independent Test**: For Dadu, before window June–mid July 2022 and after window late August 2022, compute flooded km² per tehsil and save three images (before, after, flood mask).

**Acceptance Scenarios**:

1. **Given** a region and two date windows, **When** change detection runs, **Then** it returns a flood mask where radar backscatter dropped by more than the threshold and is low enough to be water, with permanent water (JRC occurrence > 80%) and slopes > 5° removed.
2. **Given** a tehsil collection, **When** flooded area is reduced, **Then** each tehsil gets new-water km² and share of its area.
3. **Given** no radar image in a window, **When** change detection runs, **Then** it reports "no imagery" explicitly instead of zero flooding.
4. **Given** the change image, **When** thumbnails are requested, **Then** PNG bytes are returned for before, after and flood mask over the same bounding box.

---

### User Story 5 - Keep going when Earth Engine fails (Priority: P2)

If Earth Engine is unreachable, quota-limited or IMERG is overdue, the system uses the last valid tehsil rainfall, flags it stale with its date, and alerts the admin (UC03-2a, NFR9).

**Why this priority**: Same as FR1's stale path; tehsil scores must keep running.

**Independent Test**: Make the EE client raise for every call. Check stale rows, unhealthy status, one admin alert and that the pipeline continues.

**Acceptance Scenarios**:

1. **Given** EE raises after retries, **When** the job runs, **Then** overdue days are copied forward from the last valid row per tehsil (max 3 days), flagged stale, and the IMERG source is marked unhealthy with one admin alert.
2. **Given** EE recovers, **When** the next run succeeds, **Then** stale rows are replaced and the source is healthy again.

---

### Edge Cases

- Earth Engine credentials missing in dev: ingestion reports "not configured" as unhealthy and does not crash the app; unit tests run without credentials.
- IMERG collection version changes (V07 → V08) or band renamed: dataset ID and band are settings; a missing band is an error, not zeros.
- A day with 40–47 images that is older than the expected lag: stored with a `partial_day` quality note rather than skipped forever.
- Request too large for one EE call (national rollout): tehsils are batched (default 300 per call).
- Sentinel-1 coverage gap (only one satellite in 2022, 12-day revisit): windows return "no imagery" rather than an empty map.
- Mountain tehsils (Buner): radar shadow and layover produce false water; slope mask removes it, and little detected water is a valid result.
- Mixed orbit directions between before and after: comparisons are restricted to one pass direction when configured.

## Requirements *(mandatory)*

### Functional Requirements

- **FR02-001**: System MUST authenticate to Earth Engine with a service account and a configured cloud project, and expose its health.
- **FR02-002**: System MUST build the active tehsil boundaries (OCHA COD-AB adm3) as an Earth Engine feature collection from the stored geometries, simplified to stay within request limits.
- **FR02-003**: System MUST compute, for every active tehsil and every complete UTC day, the area-mean daily rainfall total (mm) from GPM IMERG half-hourly data.
- **FR02-004**: System MUST compute, for the same tehsil and day, the maximum 3-hour rainfall total (mm), defined as the tehsil mean of each pixel's highest rolling 3-hour accumulation within the day.
- **FR02-005**: System MUST skip days that are not yet complete and are within the expected latency (default 2 days), MUST store partial days older than that with a quality note when at least 40 of 48 images exist, and MUST treat older days with fewer images as missing (stale path).
- **FR02-006**: System MUST store results as observations with source `GPM_IMERG`, linked to tehsil and district, idempotently.
- **FR02-007**: System MUST run as step 20 of the daily pipeline after NASA POWER and before forecasting, and MUST NOT stop the pipeline on failure.
- **FR02-008**: On Earth Engine failure or overdue data, System MUST apply the shared stale path: copy forward per tehsil (max 3 days), flag stale with the last valid date, update source status, alert the admin once.
- **FR02-009**: Every observation MUST carry the active scenario. In replay, System MUST read cached values and MUST NOT expose days later than replay date minus the simulated lag (default 1 day). Replay MUST NOT change the live source status.
- **FR02-010**: System MUST provide a command to cache tehsil IMERG values for a scenario's districts and window plus 30 days of spin-up.
- **FR02-011**: System MUST compute mean elevation (m) and mean slope (degrees) per tehsil from SRTM once, store them on the tehsil, and save them in a seed file for reproducible seeding.
- **FR02-012**: System MUST provide Sentinel-1 change detection: same-mode (IW, VV) radar collections for a before and an after window, speckle smoothing, a backscatter-drop and absolute-darkness rule for new water, a permanent-water mask from JRC Global Surface Water occurrence above 80%, and a slope mask.
- **FR02-013**: System MUST report flooded new-water area (km²) and share of area per tehsil, and the acquisition dates used; "no imagery" MUST be distinguishable from "no flooding".
- **FR02-014**: System MUST render before, after and flood-mask PNG images for a bounding box, for storage by FR21.
- **FR02-015**: All dataset identifiers, band names, scales and change-detection thresholds MUST be configuration, not code.
- **FR02-016**: System MUST write one audit entry per IMERG run and one when terrain values are loaded.
- **FR02-017**: Admins MUST be able to trigger an IMERG ingest for a date range through the shared ingestion run endpoint (FR1).

### Key Entities

- **Tehsil**: boundaries used for reduction; FR2 writes `elevation_mean_m` (+ proposed `slope_mean_deg`).
- **ClimateObservation**: `source=GPM_IMERG`, `tehsil_id`, `rainfall_mm`, `rainfall_max_3h_mm`, stale flags, `scenario_id`.
- **DataSourceStatus**: health of `GPM_IMERG` (and `SENTINEL1` when FR21 live verification runs).
- **SatelliteFloodMap**: not written by FR2; FR2 supplies the values FR21 stores.
- **AuditLog**, **Alert**, **ReplayScenario**: as in FR1.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The daily IMERG step for the pilot tehsils (≈ 60 active, ≈ 260 Sindh + KP) finishes in under 5 minutes for a 3-day look-back, within NFR1's 15-minute budget.
- **SC-002**: 100% of active tehsils have a value for every complete day, including tehsils smaller than a grid cell.
- **SC-003**: Max 3-hour intensity from Earth Engine matches the reference calculation within 0.1 mm on fixture data.
- **SC-004**: During a simulated Earth Engine outage, the pipeline still completes and every active tehsil has a stale-flagged value for each overdue day up to the cap (NFR9).
- **SC-005**: Replays use 0 days past the no-lookahead cut-off across all three scenarios (supports NFR3).
- **SC-006**: Dadu's tehsils show substantial new water (well above 10 km² in total) for late August 2022, and Sindh's 2021 control period shows far less (sanity check for D1 maps and NFR3 credibility).
- **SC-007**: Adding a tehsil or district needs only boundary data and a terrain seed row, not code (NFR11).

## Assumptions

- The Earth Engine project is registered for (noncommercial) Earth Engine use and the service account has the `Earth Engine Resource Viewer`/`Writer` role.
- The EE IMERG collection's latency is to be verified (clarify #1). For the demo, replays use cached data, so the live latency does not affect the pitch.
- UTC calendar days. The pipeline runs at 01:00 UTC, when the last hours of day D−1 are usually not yet published (Early run ≈ 4 h, Late run ≈ 12–14 h latency). D−1 is therefore often skipped as incomplete and picked up by the next run's 3-day look-back.
- Pilot: Sindh + KP tehsils from foundation seeds; only active districts' tehsils are ingested.
- Sentinel-1 work in the MVP is offline precompute for replay events (D1); live S1 is roadmap (FR21).

## Traceability

| Item | How FR2 covers it |
|---|---|
| UC03 step 3 | Daily IMERG per tehsil (US1) |
| UC03 alt 2a | Stale path (US5) |
| UC02 steps 1–3 (data side) | S1 change detection, JRC mask, per-tehsil new water (US4) |
| UC04 step 1 | Tehsil rainfall and elevation features (US1, US3) |
| Document Q11, Q12 | OCHA boundaries with `reduceRegions`; IMERG, Sentinel-1, JRC, SRTM |
| NFR1 | SC-001 |
| NFR3 | Replay caches (US2), SC-005, SC-006 |
| NFR9 | US5, SC-004 |
| NFR11 | SC-007 |
| Decisions | D1 (S1 maps precomputed for replays in the MVP; FR2 supplies the algorithm) |

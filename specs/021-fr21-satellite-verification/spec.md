# Feature Specification: FR21 Satellite Verification

**Feature Branch**: `021-fr21-satellite-verification`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: Roadmap (D1: precomputed before/after maps for the replay events are MVP)

**Input**: User description: "FR21 Confirm actual flooding with satellite data after the event" (Area: Reporting, Priority: Roadmap). Use case UC02 Verify flood by satellite: "Confirm that real flooding happened where a trigger fired." Precondition: "A trigger fired for the district within the last 7 days." Trigger: "A new Sentinel 1 image becomes available." Postcondition: "Each trigger is marked Confirmed, Not confirmed or Pending."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Precomputed flood maps for the replay events (Priority: P1, MVP per D1)

Before the demo, the Satellite & GIS lead runs one command per replay event. It produces, for every tehsil of the event's districts, a before image (dry period), one or more after images (at and near the peak) and a flood overlay, with the newly flooded area in km² and as a share of the tehsil, from real Sentinel-1 radar with permanent water removed. The results are saved with the project so the demo needs no satellite access.

**Why this priority**: D1 makes these maps the MVP form of FR21. They answer "did it really flood?" on stage and appear in the DC's approval screen (UC07-2).

**Independent Test**: Run the command for `sindh-2022`, check the manifest and images exist, then load them into an empty database and count rows.

**Acceptance Scenarios**:

1. **Given** event windows in configuration, **When** the precompute command runs for `sindh-2022`, **Then** each scenario tehsil gets a before image, an after image and a flood overlay for every configured after-window, plus flooded km² and share, and a district-level image set and total.
2. **Given** an after-window with no radar pass over a tehsil, **When** it runs, **Then** that entry is recorded as "no imagery" and no zero-flood map is invented.
3. **Given** the saved results, **When** they are loaded for a scenario, **Then** one map record per tehsil per after-date exists for that scenario, labelled precomputed and not simulated; loading twice changes nothing.
4. **Given** a replay is reset, **When** it starts again, **Then** the maps are available again.

---

### User Story 2 - View flood maps on the replay timeline (Priority: P1, MVP per D1)

During the replay, the PDMA officer (and the DC for their district) selects a tehsil or district and sees the latest before/after radar maps captured on or before the replay date, with the flooded area and an honest label.

**Why this priority**: This is how the maps show up in the 3-minute demo, and it must never reveal an image "from the future" of the replay.

**Independent Test**: With maps loaded for `sindh-2022` and the replay at a date between two after-dates, request a tehsil's maps and check only the earlier map returns; move the date forward and the later one returns.

**Acceptance Scenarios**:

1. **Given** maps dated 22 Aug and 31 Aug 2022 and replay date 25 Aug, **When** the officer requests the tehsil's maps, **Then** only the 22 Aug map is returned.
2. **Given** a requested date later than the replay date, **When** the request is made, **Then** the replay date is used instead.
3. **Given** a DC of Dadu, **When** they request a Buner tehsil, **Then** access is denied.
4. **Given** any returned map, **Then** it states that it is precomputed from real Sentinel-1 images captured on the shown date (English and Urdu), and is marked not simulated.
5. **Given** a returned map, **When** the client fetches its images, **Then** the before, after and flood images are served only to authenticated users allowed to see that district.
6. **Given** live mode (no scenario), **When** maps are requested for a replay-event tehsil, **Then** none of the replay maps appear.

---

### User Story 3 - District flood summary for the approval screen (Priority: P2, MVP per D1)

When the DC opens an Action-tier approval request, the detail view shows the district's latest satellite summary: total newly flooded area, share, the worst tehsils and the district before/after images, if any exist up to now (UC07-2).

**Why this priority**: UC07-2 lists "satellite map (if any)". It strengthens the human decision without blocking it.

**Independent Test**: Call the summary for Dadu at a replay date after the first after-date and check totals equal the sum of tehsil rows.

**Acceptance Scenarios**:

1. **Given** tehsil maps for a district up to the as-of date, **When** the summary is requested, **Then** it returns the latest after-date, total km², share of district area, top tehsils by share and the district images.
2. **Given** no maps up to the as-of date, **Then** the summary is empty and the approval view shows nothing, without error.

---

### User Story 4 - Automatic confirmation after a trigger (Priority: P3, Roadmap behind `FR21_LIVE_VERIFY`)

A daily job checks every district where a tier fired in the last 7 days. When a new Sentinel-1 image covers the district, it removes normal water using the JRC baseline, calculates newly flooded area per tehsil, and marks the trigger event Confirmed if the area passes the threshold. Before/after maps appear on the dashboard and an audit entry is written (UC02 main flow).

**Why this priority**: Roadmap per the document and D1. It closes the loop for donors and PDMA learning, but is not needed to move money.

**Independent Test**: With a fake satellite gateway returning a new image with 40 km² of new water, run the job for an event that fired 3 days ago; the event becomes Confirmed with an audit entry and new map rows.

**Acceptance Scenarios**:

1. **Given** an event whose latest tier fired ≤ 7 days ago and a new image showing new water above the threshold, **When** the job runs, **Then** the event is Confirmed, map records are stored (not precomputed) and an audit entry records the area, threshold and image dates.
2. **Given** no new image yet (UC02-1a), **When** the job runs, **Then** the event stays Pending, the check time is recorded, and the job retries the next day.
3. **Given** an image with little new water while the verification window is still open, **When** the job runs, **Then** the event stays Pending (water may still be coming, since triggers fire before floods).
4. **Given** the window has closed and the images showed little new water (UC02-4a), **When** the job runs, **Then** the event is Not confirmed, flagged for PDMA review, and the PDMA officer is notified.
5. **Given** the window has closed with no image at all, **When** the job runs, **Then** the event stays Pending and is flagged for PDMA review as "no imagery".
6. **Given** the flag is off, **Then** no verification job runs and statuses are never changed by FR21.

---

### User Story 5 - Review verification results (Priority: P4, Roadmap)

The PDMA officer lists trigger events with their confirmation status, flagged items first, and can ask for an immediate re-check of one event.

**Why this priority**: Supports UC02-4a "flagged for PDMA review"; low demo value.

**Independent Test**: Seed events in each status and list them filtered by status.

**Acceptance Scenarios**:

1. **Given** events in all three statuses, **When** the officer filters by "review required", **Then** only flagged events return, newest first.
2. **Given** an event, **When** the admin requests verification now, **Then** the same rules as User Story 4 run for that event only.

---

### Edge Cases

- Sentinel-1 revisit: in 2022 only one satellite flew (about 12-day repeat), so a 7-day window can contain no image. That is the Pending + review path, not Not confirmed.
- Mountain districts (Buner): radar shadow and quick recession make flash floods hard to see. Slope-masked results may show little water; the label explains radar limits for steep terrain.
- Permanent rivers and lakes (Indus, Manchar Lake): removed by the JRC baseline; seasonal high water above the baseline still counts as new water.
- An event closes (D4) before verification finishes: verification continues until the window ends.
- Two after-images in one window: the one with the larger new-water area decides the result and both are stored.
- The map image files are missing on disk although rows exist: the image endpoint returns not found; listing still works.
- A later tier fires after the window started: the window end moves to 7 days after the latest tier firing.

## Requirements *(mandatory)*

### Functional Requirements

**MVP (D1)**

- **FR21-001**: System MUST provide an offline command that, for a configured replay event, computes per tehsil and per district: new-water area (km²) and share from Sentinel-1 radar, before/after/flood images, and the acquisition dates used, and saves them with a manifest in the project's replay data folder.
- **FR21-002**: Event windows (before window, one or more after windows), districts and orbit settings MUST be configuration, not code.
- **FR21-003**: Missing imagery MUST be recorded as "no imagery", never as zero flooding.
- **FR21-004**: System MUST load a saved manifest into flood map records scoped to the replay scenario, idempotently, without needing satellite access, marked precomputed and not simulated.
- **FR21-005**: Loading MUST be available on replay start/reset (for FR5), as a command and as an admin action, and MUST be audited.
- **FR21-006**: Users with PDMA officer, admin, DC (own district only) or donor roles MUST be able to list flood maps for a tehsil or district as of a date.
- **FR21-007**: In a replay, the as-of date MUST default to and never exceed the replay date. In live mode it defaults to today.
- **FR21-008**: Only maps with image date on or before the as-of date MAY be returned, newest first.
- **FR21-009**: Each map MUST carry `precomputed`, `simulated`, before and after image dates, flooded km², share, bounding box and image links, and a plain-language label in English and Urdu stating it was precomputed from real Sentinel-1 radar.
- **FR21-010**: Images MUST be served only to authenticated users permitted to see the district.
- **FR21-011**: System MUST provide a district summary as of a date (latest after-date, total area, share, top tehsils, district images) for the approval screen; it MUST return empty when nothing exists.
- **FR21-012**: Live-mode reads MUST never include replay maps, and replay reads MUST include only that scenario's maps.

**Roadmap (behind `FR21_LIVE_VERIFY`, default off)**

- **FR21-013**: A daily job MUST select trigger events where a tier fired within the verification window (default 7 days after the latest tier firing) and whose status is Pending.
- **FR21-014**: For each, it MUST look for Sentinel-1 acquisitions over the district since the last check; with none, the event stays Pending and the check time is recorded (UC02-1a).
- **FR21-015**: With new images, it MUST compute new water against a dry baseline before the event opened, with permanent water removed, per tehsil, and store the results as (non-precomputed) flood map records linked to the event.
- **FR21-016**: It MUST mark the event Confirmed when district new water ≥ 5 km² or any tehsil's share ≥ 1% (both configurable).
- **FR21-017**: It MUST mark the event Not confirmed only after the window closes with at least one image and no pass, flag it for PDMA review and notify the PDMA officer (UC02-4a).
- **FR21-018**: When the window closes with no image, the event MUST stay Pending and be flagged for review as "no imagery".
- **FR21-019**: Every status change MUST be audited (`FLOOD_VERIFIED`) with area, thresholds, image dates and rule, in the same transaction.
- **FR21-020**: Verification MUST NOT change any release, approval, allocation, payment or fund balance.
- **FR21-021**: The officer MUST be able to list events by confirmation status and review flag; the admin MUST be able to request verification for one event now.
- **FR21-022**: Source failures MUST leave the event Pending, mark the Sentinel-1 source unhealthy and retry the next day (NFR9).

### Key Entities

- **SatelliteFloodMap**: one tehsil (or district, proposed) per after-image date: flooded area and share, before/after image links, `precomputed`, `scenario_id`.
- **TriggerEvent**: the flood episode; FR21 writes `confirmation_status` (roadmap).
- **FundRelease**: read only, for tier firing times (roadmap).
- **ReplayScenario**: scenario key, current replay date.
- **DataSourceStatus**: `SENTINEL1` health (roadmap).
- **AuditLog**, **Alert** (roadmap review notice).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For `sindh-2022`, 100% of scenario tehsils have a precomputed entry (map or explicit "no imagery") for each configured after-window, and Dadu district shows new water well above 10 km² at its peak after-date.
- **SC-002**: 0 maps returned with an image date after the replay date, across all tests and the rehearsal (honest replay, NFR3 credibility).
- **SC-003**: Flood map list and summary reads respond in < 500 ms p95 (NFR1 dashboard reads); images in < 1 s.
- **SC-004**: 100% of map responses carry `precomputed`, `simulated` and the label (NFR13).
- **SC-005**: Loading a scenario's maps takes < 5 s and needs no network.
- **SC-006** (roadmap): Every confirmation status change has exactly one audit entry; 0 changes to money tables from FR21 code (Principle II).
- **SC-007** (roadmap): Replaying Sindh 2022 with verification on marks the Dadu event Confirmed within the window; control 2021 has no trigger and therefore nothing to verify.

## Assumptions

- Sentinel-1 radar is the only verification sensor (cloud-free during monsoon); optical imagery is out of scope.
- Exact dates and districts of scenarios come from FR5; FR21's YAML holds only satellite windows; after-windows must end within FR5's scenario window.
- Precomputed images are real satellite data processed in advance, so `simulated=false` per D1; the "precomputed" label carries the honesty requirement.
- Confirmation is per district trigger event (D4), with tehsil detail for maps.
- Donors may see maps (no personal data); the donor report (FR19) decides whether to show them.

## Traceability

| Item | How FR21 covers it |
|---|---|
| UC02 main 1–3 | Precompute (US1, MVP) and verification job (US4, roadmap), using FR2's primitives |
| UC02 main 4 | Confirmed rule (FR21-016) |
| UC02 main 5 | Maps on the dashboard (US2, US3) and audit (FR21-005, FR21-019) |
| UC02 alt 1a | Pending + retry (FR21-014) |
| UC02 alt 4a | Not confirmed + review flag (FR21-017) |
| UC07 step 2 | District summary "satellite map (if any)" (US3) |
| Solution step 5 | "Sentinel 1 confirms where flooding actually happened" |
| NFR1 | SC-003 |
| NFR3 | No lookahead (SC-002), SC-007 |
| NFR7 | Audit of loads and status changes |
| NFR12 | Advisory only, no money effects (FR21-020) |
| NFR13 | Labels (SC-004) |
| Decisions | **D1** (precomputed maps MVP, live job roadmap), **D4** (one event per district episode; no claw-back) |

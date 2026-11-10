# Clarifications: FR21 Satellite Verification

## Clarifications

### Session 2026-11-10

- Q: What exactly is MVP? → A: D1. The precompute script, the loader, the read endpoints (list, district summary, images) and honest labels. The daily verification job and status changes are roadmap behind `FR21_LIVE_VERIFY` (default off).
- Q: Precomputed maps: `simulated` true or false? → A: `simulated=false, precomputed=true`, as D1 states. The images are real Sentinel-1 acquisitions; only the timing (processed in advance) differs. Principle VIII's list mentions "precomputed satellite maps" among things to label; that is satisfied by the mandatory `precomputed` flag and the label text "Precomputed from real Sentinel-1 radar images captured on {date}". D1 is the more specific rule. Recorded as a contradiction for the constitution owner.
- Q: The data model has no `simulated` column on `SatelliteFloodMap`. → A: Proposed addition (`simulated bool default false`) so the D1 values are stored, not just implied. Until merged, the API sets `simulated: false` from the code constant tied to `precomputed`.
- Q: Where do precomputed maps live so a replay reset doesn't destroy them? → A: The source of truth is files: `data/replay/<key>/satellite/manifest.json` + PNGs (committed, like FR1/FR2 caches). The loader inserts rows with the scenario's `scenario_id` and is idempotent. FR5 calls it on start/reset. Rows are cheap to recreate, so FR5's reset may delete them.
- Q: Why not store them as live rows (`scenario_id=NULL`)? → A: A live dashboard would then show 2022 maps as "latest" for Dadu. Scenario scoping keeps live and replay apart (FR5 isolation rule).
- Q: How is lookahead prevented? → A: `as_of = min(requested date, ReplayScenario.sim_date)` when `X-Scenario-Id` is set; default `as_of = sim_date` (or `end_date` if not running). Only rows with `image_date ≤ as_of` are returned.
- Q: Before window: just before the trigger, or a dry season? → A: A dry pre-monsoon window. In 2022 Sindh was already wet in July, so a "just before" image would hide much of the flood. For the roadmap job, the baseline is a 60-day window ending 7 days before the event opened, median composite.
- Q: Event windows for the demo (verify acquisitions with `scripts/ee_smoke_test.py`)? → A: In `data/seeds/satellite_events.yaml`:
  - `sindh-2022`: before 2022-06-01 → 2022-07-10; after windows 2022-08-15 → 2022-08-24 and 2022-08-25 → 2022-09-08. Pass `DESCENDING` if both windows have descending coverage, otherwise `null`.
  - `buner-2025`: before 2025-07-01 → 2025-08-10; after window 2025-08-15 → 2025-08-25 (FR5 window ends 2025-08-25). Pass `null`.
  - `control-2021`: no maps (no trigger expected). Optional "nothing happened" map 2021-08-15 → 2021-09-05 can be added for the pitch.
  After-windows must end on or before FR5's scenario end date (`data/replay/scenarios.yaml`: sindh-2022 ends 2022-09-10, buner-2025 ends 2025-08-25), or the map can never appear in the replay. Before-windows may precede the replay. Districts come from FR5's scenario definition (Sindh: Dadu, Qambar Shahdadkot, Larkana, Jamshoro, Khairpur; KP: Buner, Swat, Shangla).
- Q: Which date is `image_date`? → A: The latest acquisition date in the after window that covers the tehsil (latest-on-top mosaic). Before dates are stored in the proposed `before_image_date` (latest of the before window) and the full lists in `params`.
- Q: District-level maps: the data model has tehsil-level rows only. → A: Proposed: add `district_id` (required) and make `tehsil_id` nullable; a row with `tehsil_id=NULL` is the district image set and total (same pattern as `RiskIndex`). The summary endpoint uses it, with tehsil rows for the top list.
- Q: Image format and serving? → A: 1024 px PNGs in EPSG:3857 with the bounding box stored, served by `GET /api/v1/satellite/flood-maps/{id}/image/{before|after|flood}` with role checks (not a public static mount, so DC scoping holds). `before_tile_url`/`after_tile_url` store the repo-relative file path; the API returns the API URL.
- Q: Who can read maps? → A: PDMA_OFFICER, ADMIN, DC (own district), DONOR. Maps contain no personal data, and the solution text says Sentinel-1 confirms where flooding happened as part of tracing for donors.
- Q: Confirmation rule (roadmap)? → A: Confirmed when district new water ≥ `FR21_CONFIRM_MIN_KM2` (5 km²) or any tehsil share ≥ `FR21_CONFIRM_MIN_PCT` (1%). These are settings (Principle IX). Tuned so Dadu 2022 passes by a wide margin and an ordinary monsoon week does not.
- Q: "Within the last 7 days" and "new image": what window? → A: Eligible while `today ≤ latest tier firing date + FR21_VERIFY_WINDOW_DAYS` (7, from UC02's precondition). Confirmed can happen at any image in the window. Not confirmed is decided only once the window has closed with ≥ 1 image and no pass, because triggers fire before the water arrives; an early image may predate the flood. No image at all → stays Pending and is flagged "no imagery" for PDMA review.
- Q: Does the verification change money? → A: No. It is advisory evidence for PDMA review and donor reporting (Principle II, D4: nothing is clawed back).
- Q: Which pass/orbit for the roadmap job? → A: Compare after images with a baseline from the same `relativeOrbitNumber_start` when available, else same pass direction, else any (recorded in `params`).
- Q: What about the DC view of maps in replay before approval? → A: Uses the same as-of rule, so the DC sees only images up to the replay date.

## Resolved by constitution decisions

- **D1**: MVP = precomputed Sentinel-1 before/after maps for Sindh 2022 and Buner 2025 stored as `SatelliteFloodMap` with `simulated=false, precomputed=true`; live job roadmap.
- **D4**: one `TriggerEvent` per district flood episode, so confirmation is per event; no claw-back on Not confirmed.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Verification window length | 7 days after the latest tier firing (UC02 precondition). Consider 14 days because Sentinel-1 revisit was ~12 days in 2022 (6 days from 2025 with S1C) | 7 days → more "Pending, no imagery" outcomes; 14 → slower verdicts | PDMA domain lead / Satellite & GIS |
| 2 | Confirmation thresholds | ≥ 5 km² district or ≥ 1% of any tehsil | Confirmation rate; false confirmations from rice paddies/wet soil | Satellite & GIS |
| 3 | Run verification in replay from precomputed rows (no EE) so the demo shows "Confirmed"? | No in MVP; flag stays off. A `ReplayVerificationSource` is part of the roadmap build | +20 s of demo story, small build | Frontend & pitch with Satellite & GIS |
| 4 | Donors can read maps | Yes (aggregate, no PII) | FR19 may hide them | FR19 owner |
| 5 | Event windows and orbit pass | As listed above, verify acquisitions | Which dates appear in the demo | Satellite & GIS |
| 6 | Data-model additions (`simulated`, `district_id`, nullable `tehsil_id`, `before_image_date`, `flood_mask_url`, `bbox`, `params`, `event_id`; TriggerEvent review fields) | Proposed in plan.md | Loader and API fall back to constants/manifest values until merged | Foundation owner |
| 7 | Principle VIII vs D1 on the `simulated` value | Follow D1 (`false`) + mandatory `precomputed` label | If VIII wins, set `simulated=true` and the `X-Simulated-Data` header appears on map responses | Constitution owner |

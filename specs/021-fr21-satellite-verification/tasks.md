---

description: "Task list for FR21 Satellite Verification"
---

# Tasks: FR21 Satellite Verification

**Input**: Design documents from `/specs/021-fr21-satellite-verification/`

**Prerequisites**: plan.md, spec.md, clarify.md; `000-foundation` merged; FR2 US4 (S1 primitives) merged for the precompute script; FR5 `ReplayScenario` rows seeded for the loader

**Tests**: Included. FR21 is not a money path, but tests protect no-lookahead, DC scoping, labels (NFR13), loader idempotency and, in the roadmap build, the verification rules and the "no money writes" guarantee.

**Organization**: Part A is the **MVP (D1) plus the flag stub**. Part B is the **Roadmap build** behind `FR21_LIVE_VERIFY`.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Add settings to `app/core/config.py` and `.env.example`: `SATELLITE_EVENTS_FILE=data/seeds/satellite_events.yaml`, `SATELLITE_DATA_ROOT=data/replay`, `SATELLITE_LIVE_ROOT=data/satellite`, `FR21_VERIFY_WINDOW_DAYS=7`, `FR21_CONFIRM_MIN_KM2=5.0`, `FR21_CONFIRM_MIN_PCT=1.0`, `FR21_BASELINE_DAYS=60`, `FR21_BASELINE_GAP_DAYS=7`, `FR21_VERIFY_CRON=0 3 * * *`
- [ ] T002 [P] Write `data/seeds/satellite_events.yaml` with the windows from clarify.md (sindh-2022, buner-2025; control-2021 empty)
- [ ] T003 [P] Create `tests/fixtures/satellite/manifest_small.json` (1 district row + 2 tehsils × 2 after-dates + 1 `NO_IMAGERY`) and a 1×1 `px.png`

---

## Phase 2: Foundational

- [ ] T004 Define pydantic `Manifest`, `ManifestMap`, `EventWindows` in `app/services/satellite/loader.py` and `app/services/satellite/precompute.py`
- [ ] T005 [P] Define response schemas `FloodMapOut`, `FloodMapPage`, `DistrictFloodSummary`, `LoadRequest`, `LoadResult`, `VerificationOut` in `app/schemas/satellite.py` (labels required, `precomputed` and `simulated` required)
- [ ] T006 Write `alembic/versions/fr21_0001_satellite_additions.py` once the proposed fields are merged into data-model.md; until then the loader keeps `bbox`, `before_image_date`, `flood_mask_url`, `status`, `params` in a per-scenario in-memory index built from the manifest (documented fallback)

**Checkpoint**: Schemas and manifest model ready.

---

# Part A: MVP (D1) + flag stub

## Phase 3: User Story 1 - Precomputed maps (Priority: P1) 🎯 MVP

**Goal**: Committed manifests and PNGs for sindh-2022 and buner-2025; idempotent loader.

**Independent Test**: `pytest tests/unit/satellite/test_loader.py` and a manual `python scripts/precompute_flood_maps.py --scenario sindh-2022 --dry-run`.

### Tests for User Story 1 ⚠️

- [ ] T007 [P] [US1] Write `tests/unit/satellite/test_loader.py`: loads fixture manifest → rows with `precomputed=true`, `simulated=false`, `scenario_id` set; second load → 0 inserts; `NO_IMAGERY` entry stored with status and no fabricated area; missing PNG reported in `files_missing`; unknown scenario → `SCENARIO_NOT_FOUND`; one `SATELLITE_MAPS_LOADED` audit row

### Implementation for User Story 1

- [ ] T008 [US1] Implement `precompute_scenario(key, tehsils=None, dry_run=False)` in `app/services/satellite/precompute.py`: read YAML + scenario districts, build FC via FR2, for each after-window call `s1_flood_change`, `flood_area_by_tehsil`, district total, `thumbnail_png` × 3 per row; compute shares with PostGIS area; write PNGs and `manifest.json`
- [ ] T009 [US1] Implement `scripts/precompute_flood_maps.py` (argparse: `--scenario`, `--tehsil`, `--dry-run`; prints a table of km²/share per tehsil)
- [ ] T010 [US1] Implement `load_flood_maps_for_scenario(db, key)` (upsert on the unique key, audit) in `app/services/satellite/loader.py` and `scripts/load_flood_maps.py`
- [ ] T011 [US1] Run precompute for `sindh-2022` and `buner-2025`; review images; commit `data/replay/{sindh-2022,buner-2025}/satellite/`
- [ ] T012 [US1] Document the FR5 hook (call loader on scenario start/reset) in `specs/021-fr21-satellite-verification/implement.md` and notify the FR5 owner (no edit to FR5 files)

**Checkpoint**: Maps exist as rows for each scenario.

---

## Phase 4: User Story 2 - View maps on the replay timeline (Priority: P1) 🎯 MVP

**Goal**: List and image endpoints with no lookahead and role scoping.

**Independent Test**: `pytest tests/integration/satellite/test_flood_maps_replay.py tests/contract/test_satellite_api.py`.

### Tests for User Story 2 ⚠️

- [ ] T013 [P] [US2] Write `tests/unit/satellite/test_as_of.py`: no scenario → `clock.today()`; scenario with `sim_date` → defaults to it; requested date after `sim_date` → clamped; scenario not running → `end_date`
- [ ] T014 [P] [US2] Write `tests/integration/satellite/test_flood_maps_replay.py`: maps 2022-08-22 and 2022-08-31, replay at 2022-08-25 → only 08-22; at 2022-09-01 → 08-31 first; live mode → no replay maps; `latest_only` returns one per tehsil
- [ ] T015 [P] [US2] Write `tests/contract/test_satellite_api.py`: role matrix (officer/admin/donor OK, DC own district OK, DC other district 403); every item has `precomputed`, `simulated`, `label_en`, `label_ur`; `400 MISSING_FILTER`; image endpoint returns `image/png`, rejects path traversal, 404 when file missing; schema contains no PII field names

### Implementation for User Story 2

- [ ] T016 [US2] Implement `resolve_as_of()`, `SatelliteService.list_maps()` and `image_path()` in `app/services/satellite/flood_maps.py`
- [ ] T017 [US2] Implement `GET /flood-maps`, `GET /flood-maps/{map_id}/image/{kind}` and `POST /flood-maps/load` in `app/api/v1/satellite.py`; include the router in `app/api/v1/__init__.py`
- [ ] T018 [US2] Add label templates (EN/UR, date formatted in Asia/Karachi) in `app/services/satellite/flood_maps.py`

**Checkpoint**: Frontend can show the before/after slider on the replay map.

---

## Phase 5: User Story 3 - District summary for approval (Priority: P2) MVP

**Goal**: FR8's detail view gets "satellite map (if any)".

**Independent Test**: `pytest tests/integration/satellite/test_flood_maps_replay.py -k summary`.

### Tests for User Story 3 ⚠️

- [ ] T019 [P] [US3] Add tests: summary totals equal the district row; top tehsils sorted by share; nothing up to as-of → empty body; DC scoping

### Implementation for User Story 3

- [ ] T020 [US3] Implement `SatelliteService.district_summary()` in `app/services/satellite/flood_maps.py` and `GET /flood-maps/district-summary` in `app/api/v1/satellite.py`

---

## Phase 6: Flag stub (MVP)

- [ ] T021 [P] Write a test in `tests/contract/test_satellite_api.py`: with `FR21_LIVE_VERIFY` off, `GET /satellite/verifications` and `POST /satellite/verify` return `404 FEATURE_DISABLED`, and `register_satellite_jobs()` schedules nothing
- [ ] T022 Implement `register_satellite_jobs(scheduler)` (no-op when the flag is off) in `app/services/satellite/jobs.py` and the two flag-gated routes in `app/api/v1/satellite.py`; one-line call in `app/main.py`

**Checkpoint (MVP done)**: D1 delivered; FR21 roadmap is dormant.

---

# Part B: Roadmap build (behind `FR21_LIVE_VERIFY`)

## Phase 7: User Story 4 - Automatic confirmation (Priority: P3)

**Goal**: UC02 main flow and alternates 1a and 4a.

**Independent Test**: `pytest tests/unit/satellite/test_verifier_rules.py tests/integration/satellite/test_verifier_job.py` with a fake FR2 gateway.

### Tests for User Story 4 ⚠️

- [ ] T023 [P] [US4] Write `tests/unit/satellite/test_verifier_rules.py` for `decide()`: pass by km²; pass by tehsil %; below threshold inside window → PENDING; below threshold after window with ≥ 1 image → NOT_CONFIRMED + review; no image after window → PENDING + review "no imagery"; window end moves with a later tier
- [ ] T024 [P] [US4] Write `tests/integration/satellite/test_verifier_job.py`: eligible-event selection (latest tier ≤ 7 days, status PENDING, scenario scope); CONFIRMED writes map rows (`precomputed=false`, `event_id`) and one `FLOOD_VERIFIED` audit; NOT_CONFIRMED enqueues `VERIFICATION_REVIEW` alert to PDMA officers; EE failure → PENDING + `SENTINEL1` unhealthy; snapshot of `fund_release`, `payment`, `disaster_fund`, `approval`, `allocation` unchanged (Principle II)

### Implementation for User Story 4

- [ ] T025 [US4] Implement `eligible_events()`, `window_for(event)`, `decide()` and `FloodVerifier.verify_event()` in `app/services/satellite/verifier.py` (baseline = median of `FR21_BASELINE_DAYS` ending `FR21_BASELINE_GAP_DAYS` before `opened_at`; after = each new acquisition date since `confirmation_checked_at`)
- [ ] T026 [US4] Save live PNGs under `data/satellite/<district_pcode>/<event_id>/` and rows with `precomputed=false` in `app/services/satellite/verifier.py`
- [ ] T027 [US4] Implement `run_verification_job()` with advisory lock and per-event transactions, and register it in `register_satellite_jobs()` when the flag is on, in `app/services/satellite/jobs.py`
- [ ] T028 [US4] Implement `ReplayVerificationSource` (reads precomputed rows with `image_date ≤ replay date` instead of calling EE) so FR5 can call `run_verification_job` per replay day (clarify open item 3)

---

## Phase 8: User Story 5 - Review results (Priority: P4)

### Tests for User Story 5 ⚠️

- [ ] T029 [P] [US5] Add contract tests: `GET /satellite/verifications?review_required=true` filters and sorts newest first; `POST /satellite/verify` runs one event; roles enforced

### Implementation for User Story 5

- [ ] T030 [US5] Implement the list and verify-now routes (replace the 404 stubs when the flag is on) in `app/api/v1/satellite.py`

---

## Phase 9: Polish

- [ ] T031 [P] Measure list/summary p95 with 100 rows (SC-003) and record in `implement.md`
- [ ] T032 [P] Check PNG total size per scenario; move to Git LFS if > 50 MB
- [ ] T033 ruff + mypy clean on `app/services/satellite` and `app/api/v1/satellite.py`

---

## Dependencies & Execution Order

### Phase Dependencies
- Setup → Foundational → Part A (US1 → US2 → US3, flag stub anytime after Phase 2) → Part B (US4 → US5) → Polish.
- T008–T011 need FR2's S1 primitives and EE credentials. T010 and US2/US3 can be built against the fixture manifest before real maps exist.

### User Story Dependencies
- **US1**: FR2 US4 (precompute only); FR5 scenario rows (loader).
- **US2**: rows from US1 (fixture is enough for tests).
- **US3**: US2's service.
- **US4**: FR7 `TriggerEvent`/`FundRelease` in place; FR14 `AlertService`; flag on.
- **US5**: US4.

### Parallel opportunities
- T002, T003 in Setup; T005 alongside T004.
- T007, T013, T014, T015 (all test files) in parallel.
- US2/US3 API work in parallel with T011 (real precompute) because tests use the fixture.
- T023 and T024 in parallel.

---

## Implementation Strategy

1. Part A with the fixture manifest first (API usable by the frontend early).
2. Real precompute (T011) as soon as FR2's primitives land; review images with the pitch lead.
3. Stop after Part A for the hackathon. Part B only if time remains, with the flag on in a branch.

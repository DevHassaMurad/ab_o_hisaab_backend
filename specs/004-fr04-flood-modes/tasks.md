---
description: "Task list for FR4 Flood Modes"
---

# Tasks: FR4 Flood Modes

**Input**: Design documents from `specs/004-fr04-flood-modes/`

**Prerequisites**: plan.md, spec.md, clarify.md; foundation merged; FR3 feature registry names (`app/services/forecasting/features.py` `FEATURES`) agreed

**Tests**: Mandatory for spec validation and mode routing (they protect the scoring contract that releases money).

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 [P] Create `ml/config/modes/` and `data/seeds/flood_modes.yaml` (province defaults, empty overrides)
- [ ] T002 [P] Write `ml/config/modes/slow_onset.yaml` and `ml/config/modes/flash.yaml` exactly as in plan.md

## Phase 2: Foundational

- [ ] T003 Write validation tests first: valid files load; unknown feature, missing core feature, weights ≠ 1, lead weight 0 or > 1, increasing lead weights, fallback feature not in features → `ValidationError` in `tests/unit/forecasting/test_mode_spec.py`
- [ ] T004 Implement `ModeSpec`, `FallbackTerm`, `ModeRegistry.load_all/get/all` (cached, file SHA-256) in `app/services/forecasting/modes.py`
- [ ] T005 Call `ModeRegistry.load_all()` from app start-up (lifespan hook registered by FR4 in `app/services/forecasting/__init__.py`) so an invalid file aborts start

**Checkpoint**: FR3 can import `ModeRegistry.get(mode)`.

## Phase 3: User Story 1 - Each district scored in its own mode (P1) 🎯 MVP

**Independent Test**: `pytest tests/integration/forecasting/test_mode_routing.py -k routing`

### Tests (write first)
- [ ] T006 [US1] Integration test: Dadu (Sindh) rows carry `SLOW_ONSET` + slow artifact version; Buner (KP) rows carry `FLASH` + flash version; flash artifact removed → Buner fallback only in `tests/integration/forecasting/test_mode_routing.py`
- [ ] T007 [P] [US1] Seed test: province defaults applied; override wins; unknown province → `SLOW_ONSET` in `tests/unit/forecasting/test_mode_spec.py -k seed`

### Implementation
- [ ] T008 [US1] `effective_mode(db, district)` (scenario snapshot first, else `District.flood_mode`) in `app/services/forecasting/modes.py`
- [ ] T009 [US1] `apply_flood_mode_seed(db)` in `app/services/forecasting/mode_seed.py`, hooked from the foundation seed script
- [ ] T010 [US1] Confirm FR3 `group_active_districts` uses `effective_mode` (pair with FR3 owner; change lives in `app/services/forecasting/service.py`)

## Phase 4: User Story 2 - Slow-onset week-ahead (P1)

**Independent Test**: `pytest tests/integration/forecasting/test_mode_routing.py -k slow`

- [ ] T011 [US2] Test: slow district with high 30-day rain and soil 0.9 vs same forecast on soil 0.3 → saturated scores higher; lead 6 not discounted, in `tests/integration/forecasting/test_mode_routing.py`
- [ ] T012 [US2] Tune slow fallback refs against the sanity cases (24 and 96) — values in `ml/config/modes/slow_onset.yaml`

## Phase 5: User Story 3 - Flash 24–48 h focus (P1)

**Independent Test**: `pytest tests/integration/forecasting/test_mode_routing.py -k flash`

- [ ] T013 [US3] Test: 80 mm/3 h burst at lead 2 → peak lead 2, Action; same burst at lead 6 → ≤ 60; only leads 5–7 high → score ≤ 60, in `tests/integration/forecasting/test_mode_routing.py`
- [ ] T014 [US3] Verify the flash feature list against FR2 (`rainfall_max_3h_mm`, `slope_mean_deg`) and FR1 (`soil_wetness_top`); adjust `ml/config/modes/flash.yaml` if a field is not merged

## Phase 6: User Story 4 - Mode change by configuration (P2)

**Independent Test**: `pytest tests/contract/test_forecast_modes_api.py -k patch`

### Tests (write first)
- [ ] T015 [P] [US4] Contract tests: PATCH by officer/admin (200, audit row, `effective_from`), reason < 10 chars (422), DC/DONOR (403), same mode (no audit), `X-Scenario-Id` present (409) in `tests/contract/test_forecast_modes_api.py`
- [ ] T016 [P] [US4] Replay snapshot test: `snapshot_modes` stored at scenario start; live change does not alter `effective_mode` inside the scenario in `tests/integration/forecasting/test_mode_routing.py -k snapshot`

### Implementation
- [ ] T017 [US4] `set_district_mode(db, district_id, mode, reason, actor)` with `AuditService.record("DISTRICT_FLOOD_MODE_CHANGED", …)` and `snapshot_modes(db, district_ids)` in `app/services/forecasting/modes.py`
- [ ] T018 [US4] Schemas `ModeChangeIn/Out`, `ModeOut` in `app/schemas/forecast_modes.py`
- [ ] T019 [US4] PATCH `/api/v1/districts/{district_id}/flood-mode` and GET `/api/v1/forecasting/modes[/{mode}]` in `app/api/v1/forecast_modes.py`; register router

## Phase 7: User Story 5 - Thresholds calibration report (P3)

**Independent Test**: `pytest tests/unit/forecasting/test_threshold_report.py`

- [ ] T020 [P] [US5] Unit test on a fixture (3 seasons, 2 events): hit rate, median lead, false-alarm days, suggested thresholds under budget in `tests/unit/forecasting/test_threshold_report.py`
- [ ] T021 [US5] `ml/threshold_report.py --mode <mode> --version current` → `calibration.json` beside the artifact (requires FR3 `ml/train.py` to save `oof.parquet`)
- [ ] T022 [US5] GET `/api/v1/forecasting/modes/{mode}/calibration` + `CalibrationOut` in `app/api/v1/forecast_modes.py`, `app/schemas/forecast_modes.py`

## Phase 8: Polish

- [ ] T023 [P] CI step: `python -c "from app.services.forecasting.modes import ModeRegistry; ModeRegistry.load_all()"`
- [ ] T024 [P] Document how to add a mode (YAML + enum value + `ml/train.py --mode`) in `ml/README.md` section "Modes" (coordinate with FR3, who owns the file)
- [ ] T025 Run `pytest -m backtest -k buner` with FR5 and record the peak lead in the Buner backtest report

## Dependencies & Execution Order

- Setup → Foundational (T003–T005) → US1 → US2/US3 (parallel) → US4 → US5 → Polish.
- T006, T010, T011, T013 need FR3's `ForecastService` (use FR3 fixtures); T016 needs FR5's `ReplayScenario.config`; T021 needs FR3 training output; T025 needs FR5.

## Parallel opportunities

- T001/T002; T007 with T006; T015/T016; T020 with any implementation task.
- FR4 Phase 2 can be done before FR3 US1 so FR3 never needs a stub YAML for long.

## Implementation Strategy

MVP = Phases 1–5 (two validated modes routed correctly, with flash lead focus). US4 (mode changes) and US5 (report) follow; both are small.

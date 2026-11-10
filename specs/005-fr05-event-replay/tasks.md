---
description: "Task list for FR5 Event Replay"
---

# Tasks: FR5 Event Replay

**Input**: Design documents from `specs/005-fr05-event-replay/`

**Prerequisites**: plan.md, spec.md, clarify.md; foundation (`get_clock/set_clock`, `use_scenario`, pipeline runner, scheduler); FR1/FR2/FR3 replay readers; FR6 `create_fund_with_tiers`; FR7; FR8 decide API; FR20 `run_no` chains

**Tests**: Mandatory. Backtests are NFR3 acceptance tests (Principle XI); the approval-record check protects Principle II.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 [P] Create `app/services/replay/` package and `tests/{unit,integration}/replay/`, `tests/backtest/`
- [ ] T002 [P] Write `data/replay/scenarios.yaml` exactly as plan.md
- [ ] T003 [P] Register pytest marker `backtest` and default `-m "not backtest"` in `pyproject.toml`
- [ ] T004 [P] Build the `mini` fixture (1 district, 2 tehsils, 5 days, caches for FR1/FR2/FR3 formats, manifest) in `tests/fixtures/replay/mini/`

## Phase 2: Foundational

- [ ] T005 Migration `alembic/versions/0005_fr05_replay.py`: `run_no`, status values `AWAITING_APPROVAL`/`FAILED`, `config` JSONB, timestamps, `last_error`, rename `current_date` → `sim_date` (accepted in data-model v1.1)
- [ ] T006 [P] Tests for `ReplayClock` (today, monotonic now, contextvar restore) in `tests/unit/replay/test_replay_clock.py`
- [ ] T007 [P] `ReplayClock` + `clock_scope()` in `app/services/replay/clock.py`
- [ ] T008 [P] Tests: YAML loads, districts resolve to exactly one row, ambiguous/missing name fails in `tests/unit/replay/test_scenarios_yaml.py`
- [ ] T009 `load_definitions`, `ensure_scenarios(db)` (upsert by key; called at start-up and by `scripts/seed.py` hook) in `app/services/replay/scenarios.py`
- [ ] T010 [P] Schemas (`ScenarioOut`, `StartIn`, `StepIn`, `SeekIn`, `PlayIn`, `ReplayState`, `TimelineOut`, `BacktestReport`; donor variants inherit `DonorScope`) in `app/schemas/replay.py`

## Phase 3: User Story 1 - Day-by-day replay through the real pipeline (P1) 🎯 MVP

**Independent Test**: `pytest tests/integration/replay/test_replay_engine.py -k "start or step"`

### Tests (write first)
- [ ] T011 [US1] Integration: start mini → funds/tiers created for scenario, sim_date = start−1, spin-up observations present; step ×5 → RiskIndex per day with scenario_id; step after end → COMPLETED; concurrent step → `REPLAY_BUSY` in `tests/integration/replay/test_replay_engine.py`
- [ ] T012 [P] [US1] Isolation: live counts per table and `DataSourceStatus` unchanged after the mini run in `tests/integration/replay/test_replay_engine.py -k isolation`

### Implementation
- [ ] T013 [US1] `ReplayEngine.start` (lock, manifest verify, FR6 funds, FR4 snapshot, FR21 maps if present, spin-up via `all_ingestors()`), audit `REPLAY_STARTED` in `app/services/replay/engine.py`
- [ ] T014 [US1] `ReplayEngine.step` (per-day loop, `use_scenario`, `clock_scope`, `run_daily_pipeline`, drain `REPLAY_DRAIN_JOBS`, failure → FAILED) in `app/services/replay/engine.py`
- [ ] T015 [US1] `ReplayEngine.state` in `app/services/replay/engine.py`
- [ ] T016 [US1] Endpoints list/start/step/state with role checks in `app/api/v1/replay.py`; register router

## Phase 4: User Story 2 - Reproducible cached inputs (P1)

**Independent Test**: `pytest tests/integration/replay/test_replay_engine.py -k manifest`

- [ ] T017 [P] [US2] Test: tampered cache byte → start returns `REPLAY_CACHE_INVALID`, no rows written in `tests/integration/replay/test_replay_engine.py`
- [ ] T018 [US2] `verify_manifest(key)` / `write_manifest(key)` (sha256, rows, provenance counts from `forecast_rain.parquet`) in `app/services/replay/cache.py`
- [ ] T019 [US2] `scripts/replay/build_cache.py --scenario <key>|--all` calling FR1, FR2, FR3 and FR21 writers with the spin-up + window range, then `write_manifest`
- [ ] T020 [US2] Build and commit caches + manifests for `sindh-2022`, `buner-2025`, `control-2021` in `data/replay/<key>/` (needs EE + network once)

## Phase 5: User Story 4 - Simulated or live DC approval (P1)

**Independent Test**: `pytest tests/integration/replay/test_replay_engine.py -k approval`

### Tests (write first)
- [ ] T021 [US4] AUTO_SIM: Action in mini → Approval `channel=REPLAY_SIM`, `simulated=true`, release proceeds same step; LIVE: status `AWAITING_APPROVAL`, step → 409, approve via FR8 → next step continues; reject → money stays in scenario fund, in `tests/integration/replay/test_replay_engine.py`

### Implementation
- [ ] T022 [US4] `resolve_approvals(db, scenario, mode)` calling FR8 decide (`REPLAY_SIM`, actor `replay-sim`, seeded DC as approver when present) in `app/services/replay/approvals.py`
- [ ] T023 [US4] Awaiting-approval handling in `ReplayEngine.step` in `app/services/replay/engine.py`

## Phase 6: User Story 3 - Backtests (NFR3) (P1)

**Independent Test**: `pytest -m backtest`

### Tests (write first)
- [ ] T024 [P] [US3] Unit tests for `evaluate_backtest` on synthetic rows: TRIGGER pass/fail on lead, NO_TRIGGER fail on one release or one score ≥ Watch, Action-without-approval fails in `tests/unit/replay/test_backtest_eval.py`
- [ ] T025 [US3] Parametrised backtests (`sindh-2022`, `buner-2025`, `control-2021`): reset → start AUTO_SIM → step to end → `evaluate_backtest(...).passed` with failure messages in `tests/backtest/test_nfr3_backtests.py`
- [ ] T026 [P] [US3] Live isolation across all three scenarios in `tests/backtest/test_replay_isolation.py`

### Implementation
- [ ] T027 [US3] `evaluate_backtest(db, scenario) -> BacktestReport` in `app/services/replay/backtest.py`
- [ ] T028 [US3] `GET /replay/scenarios/{key}/backtest` in `app/api/v1/replay.py`
- [ ] T029 [US3] `scripts/replay/run_backtest.py --scenario <key>|--all --out reports/` (JSON + Markdown table; exit code 1 on failure)

## Phase 7: User Story 5 - Seek and reset (P2)

**Independent Test**: `pytest tests/integration/replay/test_replay_reset.py`

- [ ] T030 [US5] Tests: seek forward runs each day; seek backward without reset → 409; with reset → re-run to date; reset deletes all scenario operational rows, `run_no`+1, old chain verifies, new chain starts at genesis in `tests/integration/replay/test_replay_reset.py`
- [ ] T031 [US5] `purge_scenario_rows(db, sid)` with explicit `RESET_ORDER` (children first; parent-join deletes for tables without `scenario_id`) in `app/services/replay/reset.py`
- [ ] T032 [US5] `ReplayEngine.seek` and `ReplayEngine.reset` (+ audit `REPLAY_RESET` written in the **old** run's chain before bumping) in `app/services/replay/engine.py`; endpoints in `app/api/v1/replay.py`

## Phase 8: User Story 6 - Timeline and autoplay (P2)

**Independent Test**: `pytest tests/contract/test_replay_api.py -k "timeline or play"`

- [ ] T033 [P] [US6] Contract tests: all endpoints' shapes, `simulated:true`, roles (DC filtered, DONOR aggregate schema has no beneficiary fields), error codes in `tests/contract/test_replay_api.py`
- [ ] T034 [US6] `ReplayEngine.timeline` (one query joining RiskIndex district rows and FundRelease by run_date) in `app/services/replay/engine.py`
- [ ] T035 [US6] `play/pause` with APScheduler interval job in `app/services/replay/autoplay.py`; endpoints in `app/api/v1/replay.py`

## Phase 9: Polish

- [ ] T036 [P] Perf check: one mini step < 2 s, full `sindh-2022` < 4 min (`scripts/replay/run_backtest.py --timing`)
- [ ] T037 [P] Determinism check: two runs (reset between) give identical tier dates and scores (SC-006) in `tests/backtest/test_nfr3_backtests.py -k deterministic`
- [ ] T038 Demo rehearsal script in `implement.md` "Demo notes" executed end to end with one live WhatsApp approval
- [ ] T039 `ruff`, `mypy --strict app/services/replay`

## Dependencies & Execution Order

- Setup → Foundational → US1 → US2 → US4 → US3 → US5 → US6 → Polish.
- US1 needs FR1/FR2/FR3 pipeline steps (mini fixture can use FR3 fallback only); US4 needs FR7 + FR8; US3 needs real caches (T020) and FR3 artifacts or fallback; US5 needs FR20 `run_no` chains.

## Parallel opportunities

- T001–T004; T006/T008/T010.
- T017 and T024 test-writing while T013/T014 are implemented.
- T020 (cache building, needs network/EE) runs in parallel with engine work by the GIS teammate.

## Implementation Strategy

MVP = US1 + US2 + US4 (AUTO_SIM) + US3: the backtests run and pass. Then US5 for rehearsals and US6 for the stage (timeline + autoplay). Live WhatsApp approval is the last 10% and is optional on stage.

---
description: "Task list for FR7 Automatic Tier Release"
---

# Tasks: FR7 Automatic Tier Release

**Input**: Design documents from `/specs/007-fr07-auto-tier-release/`

**Prerequisites**: plan.md, spec.md, clarify.md; 000-foundation; FR6 `tiers.py` (Phase 2 of FR6); FR9 `split_pools`

**Tests**: MANDATORY (Principle XI: tier evaluation, incremental release maths, Action gating). Write first, see them fail.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Write migration for proposed fields (`trigger_event.current_tier`, `last_evaluated_date`, `below_watch_streak`; `fund_release.amount_calc`, `pipeline_error`) in `alembic/versions/0007_fr07_trigger_event_state.py` (after the foundation owner merges them into data-model.md)
- [ ] T002 [P] Add test doubles `RecordingAlertService`, `FakeAllocationService(result=…)`, `FakePaymentsDispatch`, `FakeApprovalRequester` and a `risk_index(district, run_date, score)` factory in `tests/integration/triggers/conftest.py`
- [ ] T003 [P] Add `fund_defaults.purposes` to the seed config read path (foundation PR if missing) and `RELEASE_STALLED_AFTER_MIN` to `app/core/config.py` (foundation PR)

---

## Phase 2: Foundational (amount maths and release transitions)

**⚠️ Blocks every story.**

- [ ] T004 [P] Failing unit tests: `cumulative_target` (50 M → 5 M/20 M/50 M), odd amounts (1,000,001 → 100,000/300,000/600,001), paisa amounts, `compute_tier_amount` with prior releases, rejected excluded, floor at 0, cap at available, in `tests/unit/triggers/test_amounts.py`
- [ ] T005 [P] Hypothesis property test: for random `allocated` (0–10^11, 2 dp) and increments summing to 100, uncapped Watch+Warning+Action == allocated, in `tests/unit/triggers/test_amounts.py`
- [ ] T006 [P] Failing unit tests for `RELEASE_TRANSITIONS` (all allowed pairs pass; e.g. `PENDING_APPROVAL→ALLOCATING`, `REJECTED→*` raise `INVALID_TRANSITION`) in `tests/unit/triggers/test_release_transitions.py`
- [ ] T007 Implement `round_pkr`, `cumulative_target`, `compute_tier_amount`, `available_to_release`, `planned_tier_amounts` in `app/services/triggers/amounts.py`
- [ ] T008 Implement `RELEASE_TRANSITIONS`, `transition_release` (with `RELEASE_STATUS_CHANGED` audit) in `app/services/triggers/release_pipeline.py`

**Checkpoint**: maths and state machine green.

---

## Phase 3: User Story 1 - Watch and Warning release automatically (P1) 🎯 MVP

**Independent Test**: scores 45 then 65 → Watch 5 M, Warning 15 M, both through allocation.

### Tests for User Story 1 ⚠️

- [ ] T009 [P] [US1] Integration test: first score 45 opens event, fires Watch 5,000,000 with pools 3,500,000/1,500,000, status `ALLOCATING`→`RELEASED`, `TIER_FIRED` + `TRIGGER_EVENT_OPENED` audited, in `tests/integration/triggers/test_engine.py`
- [ ] T010 [P] [US1] Integration test: next day 65 fires only Warning 15,000,000; a further day at 45 fires nothing, in `tests/integration/triggers/test_engine.py`
- [ ] T011 [P] [US1] Integration test: pipeline marks `PARTIALLY_RELEASED` when any allocation is `UNMET_NEED` or the amount was capped; `RELEASED` with zero allocations; stops at `ALLOCATING` when FairnessCheck failed and unacknowledged, in `tests/integration/triggers/test_release_pipeline.py`
- [ ] T012 [P] [US1] Integration test: tranche capped when an earlier episode used funds; `amount_calc.capped=true`, in `tests/integration/triggers/test_engine.py`

### Implementation for User Story 1

- [ ] T013 [US1] Implement `get_open_event`, `open_event` in `app/services/triggers/events.py`
- [ ] T014 [US1] Implement `DefaultTriggerEngine.evaluate` (lock fund, tier, event, fire loop) and `_fire_tier` in `app/services/triggers/engine.py`
- [ ] T015 [US1] Implement `run_release_pipeline` and `complete_release` (allocate → fairness gate → payments dispatch → beneficiary alerts → final status, nested savepoint, `pipeline_error`) in `app/services/triggers/release_pipeline.py`
- [ ] T016 [US1] Wire `DefaultTriggerEngine` as the `TriggerEngine` implementation in `app/main.py`

**Checkpoint**: automatic tiers work with stubs for FR11/12/17/14.

---

## Phase 4: User Story 2 - Skipped tiers fire; Action waits (P1)

### Tests for User Story 2 ⚠️

- [ ] T017 [P] [US2] Integration test: score 85 with no event fires WATCH, WARNING (`ALLOCATING`) and ACTION (`PENDING_APPROVAL`) in order; `request_approval` called once, in `tests/integration/triggers/test_engine.py`
- [ ] T018 [P] [US2] Integration test `test_action_never_allocated_without_approval`: `run_release_pipeline` on `PENDING_APPROVAL` raises `ApprovalRequired`; `FakeAllocationService` never called for ACTION, in `tests/integration/triggers/test_release_pipeline.py`
- [ ] T019 [P] [US2] Integration test: after a REJECTED Action, scores ≥ 80 for 5 more days create no new release; `available_to_release` excludes the rejected amount, in `tests/integration/triggers/test_engine.py`

### Implementation for User Story 2

- [ ] T020 [US2] Add the `requires_approval` branch in `_fire_tier` calling FR8 `request_approval(db, release)` in `app/services/triggers/engine.py`
- [ ] T021 [US2] Add the `ApprovalRequired` guard to `run_release_pipeline` in `app/services/triggers/release_pipeline.py`

---

## Phase 5: User Story 3 - DC hears about every tier change (P1)

### Tests for User Story 3 ⚠️

- [ ] T022 [P] [US3] Integration test: NONE→WATCH→WARNING→WATCH yields 3 DC `TIER_ALERT`s with `queued_at` == firing time (NFR2), a `TIER_CHANGED` audit each, and none for an unchanged day, in `tests/integration/triggers/test_engine.py`
- [ ] T023 [P] [US3] Integration test: beneficiary `TIER_ALERT` enqueued per allocation after allocation, with `related.fund_release_id`, in `tests/integration/triggers/test_release_pipeline.py`

### Implementation for User Story 3

- [ ] T024 [US3] Implement `_notify_dc_tier_change` and the tier-change branch in `app/services/triggers/engine.py`
- [ ] T025 [US3] Implement `_alert_beneficiaries(release, allocations)` in `app/services/triggers/release_pipeline.py`

---

## Phase 6: User Story 4 - Episodes open/close; re-runs safe (P1)

### Tests for User Story 4 ⚠️

- [ ] T026 [P] [US4] Idempotency tests: same day twice (same score, higher score, lower score) → no duplicate releases/alerts/audit; out-of-order date ignored, in `tests/integration/triggers/test_engine_idempotency.py`
- [ ] T027 [P] [US4] Lifecycle tests: 14 low days close; 13 low + 1 high resets; pending approval blocks close; new episode after close fires Watch again; `event_close_days=3` honoured, in `tests/integration/triggers/test_event_lifecycle.py`
- [ ] T028 [P] [US4] Scenario isolation test: a replay event does not see the live open event; releases carry `scenario_id` and `simulated=true`, in `tests/integration/triggers/test_event_lifecycle.py`
- [ ] T029 [P] [US4] Test that settings changed mid-episode apply to later tiers only, in `tests/integration/triggers/test_engine.py`
- [ ] T030 [P] [US4] Test a downstream failure keeps the firing, sets `pipeline_error`, and the next evaluate or resume completes it without duplicates, in `tests/integration/triggers/test_release_pipeline.py`

### Implementation for User Story 4

- [ ] T031 [US4] Implement `record_day` (streak, `current_tier`, `last_evaluated_date`) and `close_event_if_due` in `app/services/triggers/events.py`
- [ ] T032 [US4] Add the out-of-order guard and `_resume_unfinished(event)` to `evaluate` in `app/services/triggers/engine.py`

---

## Phase 7: User Story 5 - Visibility and recovery (P2)

### Tests for User Story 5 ⚠️

- [ ] T033 [P] [US5] Contract tests for event/release/evaluate shapes, string money, `amount_calc` keys in `tests/contract/test_releases_contract.py`
- [ ] T034 [P] [US5] API tests: role matrix (officer, admin, DC own/other, donor 403), filters, resume 409 on `PENDING_APPROVAL`, evaluate no-op in `tests/integration/triggers/test_releases_api.py`

### Implementation for User Story 5

- [ ] T035 [P] [US5] Create schemas in `app/schemas/releases.py`
- [ ] T036 [US5] Implement routes in `app/api/v1/releases.py` and register in `app/api/v1/__init__.py`

---

## Phase 8: Polish

- [ ] T037 [P] Add `TRIGGER_EVENT_OPENED`, `TIER_FIRED`, `TIER_CHANGED`, `RELEASE_STATUS_CHANGED`, `TRIGGER_EVENT_CLOSED` to `app/services/audit/event_types.py`
- [ ] T038 [P] Add release assertions to FR5 backtests (sindh-2022 ≥ 1 release, buner-2025 ≥ 1 release with ACTION pending or approved, control-2021 0 releases) in `tests/backtest/test_trigger_backtests.py` (coordinate with FR5 owner)
- [ ] T039 Perf check: 68 districts evaluate in < 30 s with stub allocation (`@pytest.mark.perf`) in `tests/integration/triggers/test_engine.py`
- [ ] T040 `ruff`, `mypy --strict app/services/triggers`, full FR7 selector

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 → (US2 ‖ US3 ‖ US4) → US5 → Polish.
- US2, US3, US4 each extend `engine.py`; run them sequentially if one developer, or split by file (`events.py` for US4, `release_pipeline.py` for US3 beneficiary alerts) if two.
- T001 waits for the data-model merge; until then tests run against a local migration on a branch.
- FR8 needs T008 + T015 + T021 (pipeline and guard) before its approve path works.

## Parallel opportunities

- T002, T003; T004–T006 together.
- All test tasks within a story phase.
- T035 alongside T033–T034.
- T037, T038 together.

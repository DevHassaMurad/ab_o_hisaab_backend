---
description: "Task list for FR9 Household and Shopkeeper Split"
---

# Tasks: FR9 Household and Shopkeeper Split

**Input**: Design documents from `/specs/009-fr09-beneficiary-split/`

**Prerequisites**: plan.md, spec.md, clarify.md; 000-foundation; FR6 settings validation hook; FR7 `planned_tier_amounts` (preview only)

**Tests**: MANDATORY. Pool maths is part of the incremental release maths (Principle XI). Write first, see them fail.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Write migration adding `disaster_fund.pool_spillover_enabled` (default true) and `fund_release.split_details` (JSONB, nullable) in `alembic/versions/0009_fr09_split_fields.py` (after the foundation merge)
- [ ] T002 [P] Confirm `fund_defaults.household_share_pct: 70` and `pool_spillover_enabled: true` in `data/seeds/funds.yaml` (foundation PR if missing)

---

## Phase 2: Foundational (types)

- [ ] T003 Create `PoolSplit`, `PoolUsage`, `Move`, `SpilloverPlan`, `SplitInvariantError` dataclasses in `app/services/triggers/split.py`

---

## Phase 3: User Story 1 - Officer sets the split (P1) 🎯 MVP

### Tests for User Story 1 ⚠️

- [ ] T004 [P] [US1] Unit tests for `validate_split`: 0, 70, 100 pass; -1, 101, 62.5, "70", True, None fail with `SPLIT_PCT_INVALID`, in `tests/unit/triggers/test_split_pools.py`
- [ ] T005 [P] [US1] Integration test via FR6 PUT: share 60 saved and audited; 101 → 422 and nothing saved; new fund defaults to 70, in `tests/integration/triggers/test_split_api.py`
- [ ] T006 [P] [US1] Preview tests: 50 M at 60% → 3/2, 9/6, 18/12 M; stored share used when the query is absent; 422 on bad share; DC other district 403; donor 403, in `tests/integration/triggers/test_split_api.py`

### Implementation for User Story 1

- [ ] T007 [US1] Implement `validate_split` in `app/services/triggers/split.py` and hook it into FR6 `validate_fund_settings` (one-line call in `app/services/triggers/validation.py`, coordinated with FR6)
- [ ] T008 [P] [US1] Create `SplitPreviewOut` in `app/schemas/split.py`
- [ ] T009 [US1] Add `GET /districts/{district_id}/fund-settings/split-preview` in `app/api/v1/settings.py`

---

## Phase 4: User Story 2 - Exact pools per release (P1)

### Tests for User Story 2 ⚠️

- [ ] T010 [P] [US2] Unit tests for `split_pools`: 5,000,000@70 → 3,500,000/1,500,000; 100,001@70 → 70,001/30,000; share 0 and 100; amount 0; amount 0.50@50; paisa amount 600,001.50@70, in `tests/unit/triggers/test_split_pools.py`
- [ ] T011 [P] [US2] Hypothesis property test (10,000 cases): pools ≥ 0, sum == amount, household is whole PKR, in `tests/unit/triggers/test_split_pools.py`
- [ ] T012 [P] [US2] Integration test with FR7 engine: Watch at 70% then share changed to 50% then Warning → Watch pools unchanged, Warning uses 50%, `split_details.status == "PENDING_ALLOCATION"`, in `tests/integration/triggers/test_split_outcome.py`

### Implementation for User Story 2

- [ ] T013 [US2] Implement `split_pools` and `PoolSplit.as_json` in `app/services/triggers/split.py`

---

## Phase 5: User Story 3 - Spillover within a release (P2)

### Tests for User Story 3 ⚠️

- [ ] T014 [P] [US3] Unit matrix for `plan_spillover`: H→S only; S→H only; both at once; disabled; receiver share 0 blocked; no unmet need; leftover > unmet (move = unmet); leftover < unmet (move = leftover); usage > pool raises, in `tests/unit/triggers/test_spillover.py`
- [ ] T015 [P] [US3] Integration tests for `record_split_outcome`: writes `split_details` with all invariants, audits `POOL_SPLIT_RECORDED` once, re-record after override replaces and audits with `recorded_reason="OVERRIDE"`, invariant breach raises and writes nothing, in `tests/integration/triggers/test_split_outcome.py`
- [ ] T016 [P] [US3] Helper test: `usage_from_allocations(allocations)` sums FUNDED `final_amount_pkr` and UNMET_NEED `computed_amount_pkr` per type, in `tests/unit/triggers/test_spillover.py`

### Implementation for User Story 3

- [ ] T017 [US3] Implement `plan_spillover`, `SpilloverPlan.extra_for`, `usage_from_allocations` in `app/services/triggers/split.py`
- [ ] T018 [US3] Implement `record_split_outcome` with invariant checks and audit in `app/services/triggers/split.py`

---

## Phase 6: User Story 4 - Pool breakdown per release (P2)

### Tests for User Story 4 ⚠️

- [ ] T019 [P] [US4] Contract test for `GET /releases/{id}/pools` (string money, null blocks while pending, REJECTED shape) in `tests/contract/test_split_contract.py`
- [ ] T020 [P] [US4] API tests: finished release numbers add up; pending release shows initial only; role matrix, in `tests/integration/triggers/test_split_api.py`

### Implementation for User Story 4

- [ ] T021 [P] [US4] Create `PoolBreakdownOut` in `app/schemas/split.py`
- [ ] T022 [US4] Add `GET /releases/{release_id}/pools` in `app/api/v1/releases.py`

---

## Phase 7: Polish

- [ ] T023 [P] Register `POOL_SPLIT_RECORDED` in `app/services/audit/event_types.py`
- [ ] T024 [P] Cross-FR test with FR12 stub: pass 1 → `plan_spillover` → pass 2 → `record_split_outcome`; `used + returned == amount` (SC-003), in `tests/integration/triggers/test_split_outcome.py`
- [ ] T025 `ruff`, `mypy --strict app/services/triggers`, full FR9 selector

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 ‖ US2 → US3 → US4 → Polish.
- US2 (T013) must merge before FR7's `_fire_tier` can store pools; ship it early.
- US3 (T017–T018) must merge before FR12 implements its funding passes.
- T007 touches FR6's `validation.py`: coordinate one line with the FR6 owner.

## Parallel opportunities

- T004–T006 and T010–T012 (tests of two P1 stories) together.
- T014–T016 together; T019–T021 together.
- T023, T024 together.

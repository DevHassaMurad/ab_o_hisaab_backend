---
description: "Task list for FR11 Beneficiary Ranking"
---

# Tasks: FR11 Beneficiary Ranking

**Input**: Design documents from `/specs/011-fr11-beneficiary-ranking/`

**Prerequisites**: plan.md, spec.md, clarify.md; FR10 register merged; FR12 functions available (built in parallel against the signatures in `012-fr12-amount-rules/plan.md`)

**Tests**: **MANDATORY and FIRST** (Principle XI: allocation ranking, Action-tier gating). Each test task must be committed and seen failing before its implementation task.

**Organization**: US1 ranking + reasons, US2 fairness, US4 Action gate (P1), US3 overrides (P2), US5 rules (P3).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Create package files `app/services/allocation/{__init__,service,context,rules,ranking,fairness,overrides,reasons}.py` and `app/services/explain/allocation_templates.py`
- [ ] T002 [P] Add settings `RANKING_DEFAULT_WEIGHTS`, `RANKING_NORMS`, `FAIRNESS_THRESHOLDS`, `ALLOCATION_TEHSIL_SCOPE`, `OVERRIDE_REASON_MIN_CHARS`, `OVERRIDE_REASON_MAX_CHARS`, `REPLAY_AUTO_ACK_FAIRNESS` in `app/core/config.py`
- [ ] T003 [P] Add test fixtures `tests/fixtures/allocation.py`: `dadu_release(tier, pools)`, `register_fixture(balanced|sparse)`, `tehsil_risk(score, lead)`, FR17 reserve spy, AlertService spy

---

## Phase 2: Foundational

- [ ] T004 Write migration `alembic/versions/0011_fr11_ranking_fairness.py`: `disaster_fund.ranking_weights`, `fund_release.allocation_saved_at/by`, `fairness_check.acknowledged_at/acknowledgement_note/scenario_id` + unique `fund_release_id`, `allocation.overridden_at/scenario_id`; index `allocation(fund_release_id, status, rank)`
- [ ] T005 Update `app/models/funds.py` and `app/models/beneficiaries.py` with the proposed fields (after foundation merge)
- [ ] T006 [P] Define `RankingWeights`, `Norms`, `FairnessThresholds` Pydantic models with validators and `rules_version()` in `app/services/allocation/rules.py`
- [ ] T007 [P] Define dataclasses `Candidate`, `Factors`, `RankedCandidate`, `AllocationContext`, `FinalizeResult`, `OverrideResult` in `app/services/allocation/context.py`
- [ ] T008 Define schemas in `app/schemas/allocations.py` (`AllocationOut`, `AllocationDetailOut`, `OverrideIn`, `OverrideOut`, `SaveIn`, `SaveOut`, `FairnessOut`, `RulesOut`, `RulesUpdateIn`)
- [ ] T009 Router skeleton with RBAC and DC scoping in `app/api/v1/allocations.py`; register in `app/api/v1/__init__.py`

---

## Phase 3: User Story 1 - Ranked list with reasons (P1) 🎯 MVP

### Tests for User Story 1 (mandatory, write first) ⚠️

- [ ] T010 [P] [US1] Unit tests for each normaliser (poverty passthrough and imputation chain, family cap, urgency decay day 1/7, stock norm, support norm, clamps) in `tests/unit/allocation/test_normalise.py`
- [ ] T011 [P] [US1] Unit tests for `priority_score` worked examples A=81.23, B=29.77, S1=78.50, S2=57.10 and clamp at 0 and 100 in `tests/unit/allocation/test_ranking.py`
- [ ] T012 [P] [US1] Unit tests for `rank`: separate ranks per type; full tie-break chain on constructed ties; 200 seeded shuffles identical; `register_ref` final key in `tests/unit/allocation/test_ranking.py`
- [ ] T013 [P] [US1] Unit tests for `render_allocation_reason`: FUNDED/UNMET_NEED/EXCLUDED/overridden variants, Urdu string starts with Arabic-script text, numbers in the text equal inputs, no phone/CNIC in output in `tests/unit/allocation/test_reasons.py`
- [ ] T014 [P] [US1] Integration test `allocate` on a Warning release: rows created with rank, score, `score_inputs` (recompute score from it), reasons; second call returns same rows and writes no new audit; affected tehsil scoping and `DISTRICT_FALLBACK`; inactive beneficiaries excluded; scenario isolation in `tests/integration/allocation/test_allocate_flow.py`

### Implementation for User Story 1

- [ ] T015 [US1] Implement `build_context(db, fund_release) -> AllocationContext` (fund, event, tier, rules, affected tehsils, tehsil risk/urgency map, register stats, medians) in `app/services/allocation/context.py`
- [ ] T016 [US1] Implement `load_eligible`, `normalise_household`, `normalise_shopkeeper`, `priority_score`, `sort_key`, `rank` in `app/services/allocation/ranking.py`
- [ ] T017 [US1] Implement `render_allocation_reason(inputs) -> tuple[str, str]` with EN and UR templates in `app/services/explain/allocation_templates.py`
- [ ] T018 [US1] Implement `build_reasons(rows, ctx)` calling `ExplainService.explain_allocation` with template fallback in `app/services/allocation/reasons.py`
- [ ] T019 [US1] Implement `AllocationServiceImpl.allocate` (lock, idempotency, FR12 `compute_targets` + `fill_release`, persist, fairness, audit `ALLOCATION_COMPUTED`) in `app/services/allocation/service.py` (depends on T015–T018 and FR12 T010, T016)
- [ ] T020 [US1] Add `GET /fund-releases/{id}/allocations` and `GET /allocations/{id}` in `app/api/v1/allocations.py`
- [ ] T021 [US1] Bind `AllocationServiceImpl` to the `AllocationService` Protocol in the service registry (replace foundation stub) in `app/services/allocation/__init__.py`

**Checkpoint**: Ranked, explained lists exist for every release.

---

## Phase 4: User Story 2 - Fairness check (P1)

### Tests for User Story 2 (mandatory, write first) ⚠️

- [ ] T022 [P] [US2] Unit tests for `run_fairness_check` on in-memory inputs: balanced passes; sparse + ratio < 0.5 + expected ≥ 0.05 fails with `SPARSE_UNDERFUNDED`; underfunded non-sparse → `UNDERFUNDED_INFO` and passes; expected < 0.05 ignored; zero households → sparse; no funded rows → passes with `NO_FUNDED_ROWS` in `tests/unit/allocation/test_fairness.py`
- [ ] T023 [P] [US2] Integration tests: Watch with balanced register auto-saves and calls the FR17 reserve spy once; Warning with sparse fixture stays unsaved, no reserve call, one `fairness_hold` alert enqueued; save without ack → 409 `FAIRNESS_ACK_REQUIRED`; save with ack → saved, two audit entries in `tests/integration/allocation/test_finalize_gate.py`

### Implementation for User Story 2

- [ ] T024 [US2] Implement `run_fairness_check(db, fund_release, rows, ctx) -> FairnessResult` and upsert of `FairnessCheck` in `app/services/allocation/fairness.py`
- [ ] T025 [US2] Implement `AllocationServiceImpl.finalize` (approval gate, rejected gate, fairness gate, ack, replay auto-ack, saved fields, audit, FR17 reserve call, hold alert once) in `app/services/allocation/service.py`
- [ ] T026 [US2] Call `finalize(actor="system")` at the end of `allocate` for Watch/Warning in `app/services/allocation/service.py`
- [ ] T027 [US2] Add `GET /fund-releases/{id}/fairness` and `POST /fund-releases/{id}/allocations/save` in `app/api/v1/allocations.py`

---

## Phase 5: User Story 4 - Action tier gate (P1)

### Tests for User Story 4 (mandatory, write first) ⚠️

- [ ] T028 [P] [US4] Integration tests: Action release in `PENDING_APPROVAL` → `allocate` creates rows, `allocation_saved_at` null, no reserve; `finalize` → 409 `APPROVAL_REQUIRED`; set approval APPROVED → `finalize` saves and reserves; REJECTED → 409 `RELEASE_REJECTED`; replay with `REPLAY_SIM` approval saves; `finalize` twice → `ALREADY_SAVED`, one reserve call in `tests/integration/allocation/test_finalize_gate.py`

### Implementation for User Story 4

- [ ] T029 [US4] Ensure `allocate` never calls `finalize` for tiers with `requires_approval=true`, and `finalize` reads `Approval` with `SELECT … FOR UPDATE` in `app/services/allocation/service.py`

---

## Phase 6: User Story 3 - Overrides (P2)

### Tests for User Story 3 (mandatory, write first) ⚠️

- [ ] T030 [P] [US3] Unit tests for `validate_override_reason` (length, phone pattern, CNIC pattern, Urdu text allowed) in `tests/unit/allocation/test_reasons.py::test_override_reason_*`
- [ ] T031 [P] [US3] Integration tests: override to 0 → `EXCLUDED` and next unmet funded; increase within pool; `AMOUNT_NOT_ROUNDED`; `AMOUNT_ABOVE_LIMIT`; `OVERRIDE_EXCEEDS_POOL` leaves everything unchanged; fairness re-run clears acknowledgement; reset restores rule amount; locked after save; DC/donor 403; one audit entry per override in `tests/integration/allocation/test_override.py`

### Implementation for User Story 3

- [ ] T032 [US3] Implement `validate_override_reason` in `app/services/allocation/overrides.py`
- [ ] T033 [US3] Implement `AllocationServiceImpl.override` and `.reset_override` (lock release, FR12 `override_limit` + `refill_release`, rebuild reasons for changed rows, re-run fairness, audit) in `app/services/allocation/service.py`
- [ ] T034 [US3] Implement `AllocationServiceImpl.recompute` (keep overrides on still-eligible beneficiaries, audit `ALLOCATION_RECOMPUTED`) in `app/services/allocation/service.py`
- [ ] T035 [US3] Add `PATCH`/`DELETE /allocations/{id}/override` and `POST /fund-releases/{id}/allocations/recompute` in `app/api/v1/allocations.py`

---

## Phase 7: User Story 5 - Published rules (P3)

### Tests for User Story 5 ⚠️

- [ ] T036 [P] [US5] Contract tests: `GET /allocation-rules` as each role (donor allowed, no beneficiary fields); `PUT` validation (`WEIGHTS_INVALID` on sum ≠ 1, negative, > 1); reset; audit entry; new `rules_version` used by next allocate only in `tests/contract/test_allocation_rules_api.py`
- [ ] T037 [P] [US5] Contract tests for all allocation routes: shapes, money as strings, error codes, donor 403, DC 404 cross-district in `tests/contract/test_allocations_api.py`

### Implementation for User Story 5

- [ ] T038 [US5] Implement `get_rules(fund)`, `update_district_rules(db, district_id, body, actor)` in `app/services/allocation/rules.py`
- [ ] T039 [US5] Add `GET /allocation-rules` and `PUT /allocation-rules/{district_id}` in `app/api/v1/allocations.py` (embed FR12 `amount_rules(fund)` under `amounts`)

---

## Phase 8: Polish

- [ ] T040 [P] Performance test: 10 000 eligible → `allocate` < 10 s; list p95 < 500 ms in `tests/integration/allocation/test_allocate_flow.py::test_perf_*` (`@pytest.mark.slow`)
- [ ] T041 [P] Urdu review of templates by a native speaker; record reviewer in a comment in `app/services/explain/allocation_templates.py`
- [ ] T042 mypy strict and ruff on `app/services/allocation/`
- [ ] T043 Run the replay `sindh-2022` end to end (with FR5/FR7) and verify the implement.md checklist

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup → Foundational → US1 → (US2 ∥ US4) → US3 → US5 → Polish.
- T019 needs FR12's `compute_targets` and `fill_release`; build against the signatures and FR12's unit tests, in parallel.
- T025 needs FR17's reserve entry point; until it exists use the spy from T003 and a `PaymentReserver` Protocol.
- T033 needs FR12's `override_limit` and `refill_release` (FR12 T018, T019).

### Within each story

- Tests first, failing. Pure functions (ranking, fairness, templates) before `service.py`. Service before routes.

### Parallel Opportunities

- T002, T003; T006, T007.
- All `[P]` test tasks within a story.
- US2 and US4 after US1 (different test files; both touch `service.py`, so merge carefully).
- T036, T037 in parallel with US3 implementation.

```bash
pytest tests/unit/allocation/test_normalise.py tests/unit/allocation/test_ranking.py tests/unit/allocation/test_fairness.py -q
```

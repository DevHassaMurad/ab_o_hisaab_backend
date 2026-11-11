---
description: "Task list for FR22 Outcome Reports (roadmap, behind FR22_OUTCOME)"
---

# Tasks: FR22 Outcome Reports

**Input**: Design documents from `/specs/022-fr22-outcome-reports/`

**Prerequisites**: plan.md, spec.md, clarify.md; foundation `flags.py`, `DonorScope`, scheduler. Roadmap build also needs FR14, FR15, FR17 merged.

**Tests**: The donor no-PII contract test is **mandatory and first** (Principle XI: donor-endpoint aggregation). Other tests are written first for each story.

**Organization**: Part A is the **MVP stub (behind a flag)** and is all the MVP builds. Part B is the **Roadmap build**.

## Format: `[ID] [P?] [Story] Description`

---

# Part A: MVP stub (behind a flag)

## Phase 1: Setup

- [ ] T001 Add flag `FR22_OUTCOME` (default false) to `app/core/flags.py` (coordinate the `FR22_OUTCOME` naming with the foundation owner)
- [ ] T002 [P] Create package `app/services/outcomes/__init__.py` and `app/services/outcomes/service.py`

## Phase 2: User Story 1 - Safe stub (P1)

### Tests (write first) ⚠️

- [ ] T003 [P] [US1] Contract test: `OutcomeStoryOut` and `OutcomeAggregateOut` inherit `DonorScope`; their JSON schemas contain none of `name`, `phone`, `cnic`, `wallet_id`, `beneficiary_id`, `register_ref`, `lat`, `lon`, `tehsil` in `tests/contract/test_outcomes_donor_scope.py`
- [ ] T004 [P] [US1] Integration test: flag off → `OutcomeStoryService.stories()` and `.aggregates()` return `[]`; every `/outcomes/*` route (each role) returns 404 `FEATURE_DISABLED` in `tests/integration/outcomes/test_flag_off.py`

### Implementation

- [ ] T005 [US1] Define `OutcomeStoryOut`, `OutcomeAggregateOut`, `QuestionAggregateOut` (`DonorScope`), `OutcomeReportOut`, `ProgressOut` in `app/schemas/outcomes.py`
- [ ] T006 [US1] Implement `OutcomeStoryService.stories(db, *, district_ids, scenario_id=None)` and `.aggregates(...)` returning `[]` when `flags.is_enabled("FR22_OUTCOME")` is false (roadmap path raises `NotImplementedError` until Part B) in `app/services/outcomes/service.py`
- [ ] T007 [US1] Create `app/api/v1/outcomes.py` with all five routes guarded by a `require_flag("FR22_OUTCOME")` dependency returning 404 `FEATURE_DISABLED`; register the router in `app/api/v1/__init__.py`

**Checkpoint (MVP done for FR22)**: FR19 can call `OutcomeStoryService.stories()` safely; contract test green.

---

# Part B: Roadmap build

## Phase 3: Foundational

- [ ] T008 Write migration `alembic/versions/0022_fr22_outcome_reports.py` with the proposed `OutcomeReport` fields, nullable `received_at`, unique `(beneficiary_id, event_id, scenario_id)`, index `(status, last_message_at)`
- [ ] T009 Update `app/models/beneficiaries.py` `OutcomeReport` model with the proposed fields (after foundation merge)
- [ ] T010 [P] Define `QUESTIONS_V1` (keys, options, codes, template keys per type) in `app/services/outcomes/questions.py`
- [ ] T011 [P] Hand FR14 the seven template keys and texts from plan.md (PR to `014-fr14-whatsapp-alerts` template registry)

## Phase 4: User Story 2 - Survey after payment (P2)

### Tests (write first) ⚠️

- [ ] T012 [P] [US2] Unit tests `parse_answer`: `1`, `1` (Urdu digits), `1` (Arabic-Indic digits), `1 haan`, `  2`, `7` (invalid for 3-option question), `abc`, empty; option ranges per question and type in `tests/unit/outcomes/test_questions_parse.py`
- [ ] T013 [P] [US2] Integration tests with `ReplayClock`: due after 7 days from last PAID; not due if a later payment exists; skipped while a payment notice awaits reply (<48 h); outside send window skipped; one survey per event (job twice); shopkeeper gets `outcome_q1_shopkeeper`; replies advance questions; re-ask once then null; completion queues thanks; duplicate `provider_message_id` ignored; three audit event types written in `tests/integration/outcomes/test_survey_flow.py`

### Implementation

- [ ] T014 [US2] Implement `parse_answer(question, body) -> ParsedAnswer` (reuse FR15 digit normaliser) in `app/services/outcomes/questions.py`
- [ ] T015 [US2] Implement `schedule_due_surveys(db, now) -> TickReport` (candidate query: last PAID per beneficiary per event, delay, no existing report, `phone_valid`, no pending notice, sample rate, window) in `app/services/outcomes/survey.py`
- [ ] T016 [US2] Implement `has_open_survey(db, beneficiary_id)` and `handle_reply(db, *, beneficiary_id, body, provider_message_id) -> bool` in `app/services/outcomes/survey.py`
- [ ] T017 [US2] Register `outcome_survey_tick` in `app/jobs/outcome_survey.py` and add `POST /outcomes/surveys/run` (ADMIN) in `app/api/v1/outcomes.py`
- [ ] T018 [US2] Coordinate FR15 routing hook: FR15 calls `has_open_survey` / `handle_reply` after the payment-notice check (PR note to `015-fr15-receipt-confirmation`)

## Phase 5: User Story 3 - Expiry (P3)

### Tests (write first) ⚠️

- [ ] T019 [P] [US3] Integration test: 72 h without reply → `EXPIRED`, partial answers kept, audit written; late reply returns `False` from `handle_reply` in `tests/integration/outcomes/test_expiry.py`

### Implementation

- [ ] T020 [US3] Implement `expire_stale(db, now) -> int` in `app/services/outcomes/survey.py` and call it first in the job tick

## Phase 6: User Story 4 - Aggregates and stories (P2)

### Tests (write first) ⚠️

- [ ] T021 [P] [US4] Unit tests `aggregate_outcomes`: shares sum to 100 (±0.1); k = 5 suppression of shares and counts; per-question n; null answers excluded from that question's n; simulated flag propagates if any input is simulated in `tests/unit/outcomes/test_aggregate_kanon.py`
- [ ] T022 [P] [US4] Unit tests `render_story`: household/shopkeeper EN and UR, 82.5 → "83%" (half up), top-1 spending label, no story for suppressed cells, no tehsil names in `tests/unit/outcomes/test_stories.py`
- [ ] T023 [P] [US4] Extend contract test: donor responses for real data contain no beneficiary-level fields; donor 403 on `/reports` and `/progress`; donor 403 `FUND_NOT_LINKED` for unlinked district in `tests/contract/test_outcomes_donor_scope.py`

### Implementation

- [ ] T024 [US4] Implement `aggregate_outcomes(db, *, district_ids, event_id, scenario_id, k) -> list[OutcomeAggregateOut]` in `app/services/outcomes/aggregate.py`
- [ ] T025 [US4] Implement `render_story(aggregate) -> OutcomeStoryOut | None` with EN/UR templates in `app/services/outcomes/stories.py`
- [ ] T026 [US4] Replace the stub's roadmap path in `OutcomeStoryService` with real calls in `app/services/outcomes/service.py`
- [ ] T027 [US4] Implement `GET /outcomes/aggregates` and `GET /outcomes/stories` with donor fund scoping via `DonorContribution` in `app/api/v1/outcomes.py`
- [ ] T028 [P] [US4] Write `data/seeds/outcome_simulation.yaml` and `scripts/simulate_outcome_responses.py --scenario <key> --seed <n>` (creates `COMPLETED`, `simulated=true` reports for PAID beneficiaries; audited as `OUTCOME_REPORT_COMPLETED` with `simulated=true`)

## Phase 7: User Story 5 - Officer monitoring (P3)

### Tests ⚠️

- [ ] T029 [P] [US5] Contract tests: `/outcomes/reports` shows `register_ref` and codes, never name/phone; DC scoped to own district; `/outcomes/progress` counts in `tests/contract/test_outcomes_donor_scope.py::test_officer_*`

### Implementation

- [ ] T030 [US5] Implement `GET /outcomes/reports` and `GET /outcomes/progress` in `app/api/v1/outcomes.py`

## Phase 8: Polish

- [ ] T031 [P] Native-speaker review of the Urdu survey and story texts; record reviewer in `app/services/outcomes/questions.py`
- [ ] T032 [P] Performance: tick < 30 s with 50 000 PAID payments (`@pytest.mark.slow`) in `tests/integration/outcomes/test_survey_flow.py::test_perf_tick`
- [ ] T033 Enable `FR22_OUTCOME` in a replay of `sindh-2022`, run the simulation script, verify FR19 shows the stories labelled simulated

---

## Dependencies & Execution Order

### Phase Dependencies

- **Part A** (T001–T007) depends only on the foundation. It is the full MVP scope for FR22.
- **Part B** depends on Part A, FR14 (templates, `AlertService`), FR15 (routing hook, digit normaliser), FR17 (`PAID`, `paid_at`), and the foundation merging the proposed fields.
- Within Part B: Foundational (T008–T011) → US2 → US3 → US4 → US5 → Polish. US4's unit tests (T021, T022) can start right after T010.

### Within each story

- Tests first and failing; pure functions (`parse_answer`, `aggregate_outcomes`, `render_story`) before DB functions; services before routes.

### Parallel Opportunities

- Part A: T003 and T004 in parallel; T005 with T006.
- Part B: T010, T011; T012, T013; T021, T022, T023; T028 alongside T024–T027.

```bash
pytest tests/contract/test_outcomes_donor_scope.py tests/integration/outcomes/test_flag_off.py -q   # Part A
```

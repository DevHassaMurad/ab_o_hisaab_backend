---
description: "Task list for FR18 Payment failure reasons, retry and cancel"
---

# Tasks: FR18 Payment failure reasons, retry and cancel

**Input**: Design documents from `/specs/018-fr18-payment-failure-retry/`

**Prerequisites**: plan.md, spec.md, clarify.md; `017-fr17-payment-tracking` tasks T010 (`transition()`), T013 (`SimulatedRail`) and T018 (`send_payment`/`apply_rail_result`) merged; `020-fr20-audit-log` US1 merged

**Tests**: MANDATORY (Principle XI: payment state machine). Write each test task first and see it fail.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 retry · US2 cancel · US3 access rules · US4 failure summary

---

## Phase 1: Setup

- [ ] T001 Create empty modules `app/services/payments/actions.py`, `app/services/payments/failure_reasons.py`, `app/schemas/payment_actions.py`, `app/api/v1/payment_actions.py`
- [ ] T002 [P] Add settings `PAYMENT_NOTE_MIN_LEN=5`, `PAYMENT_NOTE_MAX_LEN=500` to `app/core/config.py` (`PAYMENT_MAX_RETRIES` comes from FR17)

---

## Phase 2: Foundational (blocking)

- [ ] T003 [P] Write failing tests in `tests/unit/payments/test_failure_reasons.py`: every `failure_reason` enum value has a catalogue entry with `suggested_action_code` and text; `suggested_action(reason, retry_count=3)` returns `CANCEL_RETRIES_USED`
- [ ] T004 [P] Write failing tests in `tests/unit/payments/test_action_rules.py` for pure helpers `check_role(user)`, `check_district(user, payment)` and `validate_note(note)`: donor → `DONOR_AGGREGATE_ONLY`; officer/admin → `DC_ONLY_ACTION`; other-district DC → `DISTRICT_FORBIDDEN`; right DC → ok; `None`, `""`, `"   "`, `"ok"` → `NOTE_REQUIRED`; 501 chars → `NOTE_TOO_LONG`; 5 and 500 chars after trimming → ok; Urdu note accepted
- [ ] T005 Implement `FAILURE_REASONS` and `suggested_action()` in `app/services/payments/failure_reasons.py` (makes T003 pass)
- [ ] T006 Implement `check_role()`, `check_district()`, `validate_note()` and error classes (`DcOnlyAction`, `NoteRequired`, `NoteTooLong`, `RetryLimitReached`, `StalePaymentState`) in `app/services/payments/actions.py` (makes T004 pass)
- [ ] T007 [P] Implement `RetryIn` (`note: str | None`, `expected_retry_count: int`), `CancelIn` (`note: str | None`), `ActionResultOut`, `FailureSummaryOut`, `ReasonRow` in `app/schemas/payment_actions.py` (note optional at schema level so rule order is controlled by the service)

**Checkpoint**: Rules are pure and proven.

---

## Phase 3: User Story 1 - The DC retries a failed payment (P1) 🎯 MVP

**Goal**: Retry with a new key, bounded at 3, result applied.

**Independent Test**: `pytest tests/integration/payments/test_retry.py tests/integration/payments/test_action_concurrency.py`

### Tests for User Story 1 ⚠️

- [ ] T008 [P] [US1] Write failing tests in `tests/integration/payments/test_retry.py`: LIMIT_REACHED payment retried with `expected_retry_count=0` → PAID, `retry_count=1`, key `{alloc}:1`, ledger reserved→disbursed, notice queued; INVALID_WALLET retried without fixing → FAILED again, earlier reason in timeline; wallet fixed in register then retry → PAID with new wallet; 3 failed retries then 4th → `RetryLimitReached`, nothing changes; retry from PROCESSING/PAID/RESERVED/CANCELLED → `InvalidTransition`; stale `expected_retry_count` → `StalePaymentState`; `…999` wallet → response PROCESSING; audit: one `RETRY` entry (actor = DC id, `note_sha256`, no note text) + one `RESULT` entry; replay-scoped payment retried under `use_scenario` writes to that chain
- [ ] T009 [P] [US1] Write failing test in `tests/integration/payments/test_action_concurrency.py`: 10 threads retry the same FAILED payment with `expected_retry_count=0` → exactly 1 succeeds, 9 get `INVALID_TRANSITION` or `STALE_PAYMENT_STATE`; `retry_count == 1`; one rail call

### Implementation for User Story 1

- [ ] T010 [US1] Implement `PaymentActionService.retry(payment_id, *, user, note, expected_retry_count)` in `app/services/payments/actions.py`: tx1 (lock, checks in order, bump count, new key, clear failure fields, `transition(FAILED→PROCESSING, action=RETRY, actor=str(user.id), note=note)`) → commit → `get_rail().send(build_instruction(p))` (no tx) → tx2 `apply_rail_result()`
- [ ] T011 [US1] Implement `POST /payments/{payment_id}/retry` in `app/api/v1/payment_actions.py` (maps errors to problem JSON per plan rules table; returns `ActionResultOut` with `timeline_tail` and recomputed `allowed_actions`)

---

## Phase 4: User Story 2 - The DC cancels and money returns (P1)

**Goal**: Cancel from FAILED or RESERVED with the ledger effect.

**Independent Test**: `pytest tests/integration/payments/test_cancel.py`

### Tests for User Story 2 ⚠️

- [ ] T012 [P] [US2] Write failing tests in `tests/integration/payments/test_cancel.py`: cancel FAILED → CANCELLED, reserved −A, remaining +A, `cancelled_at` set; cancel RESERVED → same and dispatch later skips it; cancel PROCESSING/PAID/CANCELLED → `InvalidTransition`, nothing changes; one `CANCEL` audit entry with `fund_after`; fund identity holds; cancel races dispatch (thread) → either sent or cancelled, never both

### Implementation for User Story 2

- [ ] T013 [US2] Implement `PaymentActionService.cancel(db, payment_id, *, user, note)` in `app/services/payments/actions.py` (one transaction; `transition(… → CANCELLED, action=CANCEL)`)
- [ ] T014 [US2] Implement `POST /payments/{payment_id}/cancel` in `app/api/v1/payment_actions.py` (returns `ActionResultOut` with `fund_after`)

---

## Phase 5: User Story 3 - Only the right person, with a reason (P1)

**Goal**: Role, district, note and scenario rules enforced at the API.

**Independent Test**: `pytest tests/contract/test_payment_actions_api.py`

### Tests for User Story 3 ⚠️

- [ ] T015 [P] [US3] Write failing contract tests in `tests/contract/test_payment_actions_api.py`: matrix of {officer, admin, donor, DC Buner, DC Dadu} × {retry, cancel} on a Dadu payment → 403 `DC_ONLY_ACTION` / 403 `DC_ONLY_ACTION` / 403 `DONOR_AGGREGATE_ONLY` / 403 `DISTRICT_FORBIDDEN` / 200; note missing/blank/short → 422 `NOTE_REQUIRED`, long → 422 `NOTE_TOO_LONG`; check order (a donor with no note gets 403 not 422); replay payment without `X-Scenario-Id` → 404, with it → 200; response schemas; problem JSON shape with `code`

### Implementation for User Story 3

- [ ] T016 [US3] Wire the check order in `app/api/v1/payment_actions.py`: `get_current_user` → `check_role(user)` (donor / non-DC) → `get_scenario` → load payment via `scoped()` (404) → `check_district(user, payment)` → `validate_note()`; status/limit/stale checks stay in the service under the row lock. Register the router in `app/api/v1/__init__.py` **before** FR17's payments router

---

## Phase 6: User Story 4 - Failure reasons at a glance (P2)

**Goal**: Summary by reason with suggested actions.

**Independent Test**: `pytest tests/integration/payments/test_failure_summary.py`

### Tests for User Story 4 ⚠️

- [ ] T017 [P] [US4] Write failing tests in `tests/integration/payments/test_failure_summary.py`: counts/amounts per reason; `retries_exhausted` counts FAILED with `retry_count=3`; totals equal FR17 `list_payments(status=FAILED)` for the same filters; DC scoped to own district; donor 403; empty → zero totals and empty list

### Implementation for User Story 4

- [ ] T018 [US4] Implement `failure_summary(db, filters, user)` in `app/services/payments/actions.py` (one `GROUP BY failure_reason` query + `FILTER (WHERE retry_count >= max)`; reuse FR17 filter builder from `queries.py`)
- [ ] T019 [US4] Implement `GET /payments/failures/summary` in `app/api/v1/payment_actions.py`

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T020 [P] Extend FR17's randomised invariant test with retry/cancel via the real service (`tests/integration/payments/test_fund_invariants.py` — coordinate with FR17 owner; add a new test function only)
- [ ] T021 [P] Verify `GET /audit/verify` is valid after the full FR18 suite (`tests/integration/payments/test_retry.py::test_chain_valid_after_actions`)
- [ ] T022 Run `ruff check app/services/payments/actions.py app/api/v1/payment_actions.py` and `mypy --strict app/services/payments`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup → Foundational** → US1/US2/US3 → US4 → Polish.
- **US1** and **US2** depend on Phase 2 and FR17 T010/T018; they can run in parallel (different functions in `actions.py`: coordinate or do sequentially if one developer).
- **US3** depends on T011 and T014 (it tests the routes).
- **US4** depends on Phase 2 and FR17 `queries.py`.

### Within Each User Story

- Tests first and failing; service → router.

## Parallel opportunities

- T003, T004, T007 in parallel.
- T008, T009, T012, T015, T017 (test files) in parallel.
- T020, T021 in parallel.

```bash
pytest tests/unit/payments/test_action_rules.py tests/unit/payments/test_failure_reasons.py -q
pytest tests/integration/payments -q -k "retry or cancel or failure_summary or concurrency"
pytest tests/contract/test_payment_actions_api.py -q
```

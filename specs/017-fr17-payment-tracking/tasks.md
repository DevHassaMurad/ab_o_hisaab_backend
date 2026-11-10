---
description: "Task list for FR17 Payment tracking"
---

# Tasks: FR17 Payment tracking

**Input**: Design documents from `/specs/017-fr17-payment-tracking/`

**Prerequisites**: plan.md, spec.md, clarify.md; `000-foundation` and `020-fr20-audit-log` (US1 at least) merged

**Tests**: MANDATORY (Principle XI: the payment state machine; donor no-PII on payment routes). Write each test task first and see it fail.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 reserve/dispatch · US2 state machine · US3 views · US4 stuck · US5 simulated rail

---

## Phase 1: Setup

- [ ] T001 Create package `app/services/payments/` with empty modules `__init__.py`, `errors.py`, `state_machine.py`, `ledger.py`, `service.py`, `dispatch.py`, `timers.py`, `queries.py`, `csv_export.py`
- [ ] T002 [P] Add settings to `app/core/config.py`: `SIM_RAIL_WALLET_LIMIT_PKR=40000`, `SIM_RAIL_LIMIT_CLEARS_AFTER_RETRIES=1`, `SIM_RAIL_FAILURE_RATE=0.0`, `SIM_RAIL_SEED="aab-demo"`, `SIM_RAIL_INVALID_SUFFIX="000"`, `SIM_RAIL_PENDING_SUFFIX="999"`, `SIM_RAIL_TIMEOUT_SUFFIX="998"`, `PAYMENT_STUCK_HOURS=24`, `PAYMENT_TIMER_INTERVAL_MIN=15`, `PAYMENT_RECONCILE_AFTER_MIN=10`, `PAYMENT_DISPATCH_MODE="background"`, `PAYMENT_MAX_RETRIES=3`
- [ ] T003 [P] Create `tests/integration/payments/conftest.py` with fixtures: `fund_with_release(tier, n_alloc, amounts, wallets)`, `fake_clock`, `fake_alerts` (records enqueue calls), and an **autouse** `assert_fund_invariants` that runs `check_fund_invariants()` on every fund after each test

---

## Phase 2: Foundational (blocking)

- [ ] T004 [P] Write failing tests in `tests/unit/payments/test_ledger.py`: delta per action (`RESERVE`, `RESULT` paid, `CANCEL`, others zero) on Decimals; `check_fund_invariants` detects each kind of drift; no float ever accepted
- [ ] T005 Extend `app/models/payments.py` per plan "Proposed data-model additions": `Payment.fund_release_id`, `Payment.district_id`, nullable `provider`, indexes; `PaymentEvent.action`, `idempotency_key`, `failure_reason`, `provider_txn_id`, `scenario_id`
- [ ] T006 Write migration `alembic/versions/<rev>_fr17_payments.py` for T005 (columns, enum `payment_event_action`, indexes incl. partial `WHERE status='PROCESSING'`)
- [ ] T007 Implement `errors.py` (`InvalidTransition`, `InsufficientFundBalance`, `ReleaseNotPayable`, mapped to 409 problem codes) in `app/services/payments/errors.py`
- [ ] T008 Implement `lock_fund()`, `apply_delta()` (SQL increments) and `check_fund_invariants()` in `app/services/payments/ledger.py` (makes T004 pass)

**Checkpoint**: Schema and ledger ready.

---

## Phase 3: User Story 2 - Illegal status changes are refused (P1) 🎯 (first, because US1 builds on it)

**Goal**: `transition()` enforces the table atomically.

**Independent Test**: `pytest tests/unit/payments/test_state_machine.py`

### Tests for User Story 2 ⚠️

- [ ] T009 [P] [US2] Write failing parametrised tests in `tests/unit/payments/test_state_machine.py` over all 25 (from, to) pairs: the 6 allowed pairs succeed with the right ledger delta, one `PaymentEvent`, one audit entry (`PAYMENT_STATUS_CHANGED`) and correct field side effects (`processing_since`, `paid_at`, `cancelled_at`, `is_stuck` cleared, failure fields set/cleared); the 19 others raise `InvalidTransition` and leave payment, fund, events and audit unchanged; Paid enqueues exactly one `PAYMENT_NOTICE`; audit payload has no PII and contains `note_sha256` not the note

### Implementation for User Story 2

- [ ] T010 [US2] Implement `TRANSITIONS` table and `transition(db, payment, to_status, *, action, actor, note, **changes)` in `app/services/payments/state_machine.py`: lock payment, validate, lock fund, apply delta, set fields, insert `PaymentEvent`, `audit.record`, enqueue notice on PAID
- [ ] T011 [US2] Map `InvalidTransition` → 409 `INVALID_TRANSITION` (with `from`/`to` in `detail`) via the foundation `Conflict` error in `app/services/payments/errors.py`

**Checkpoint**: The money path is guarded.

---

## Phase 4: User Story 5 - Simulated payment service (P2, needed by US1)

**Goal**: Deterministic `SimulatedRail`.

**Independent Test**: `pytest tests/unit/payments/test_simulated_rail.py`

### Tests for User Story 5 ⚠️

- [ ] T012 [P] [US5] Write failing tests in `tests/unit/payments/test_simulated_rail.py`: rule order (no wallet → `NO_WALLET`; `…000` → `INVALID_WALLET`; `…998` raises `RailTimeout` and `get_status` → `FAILED/PROVIDER_TIMEOUT`; `…999` → `PENDING` and `get_status` → `PENDING`; amount > limit with `retry_count=0` → `LIMIT_REACHED`, with `retry_count=1` → `PAID`); same key twice → identical result and txn id; failure rate 0.05 over 10 000 keys within 3–7 %; rate 0 → never `PROVIDER_ERROR`; txn id prefix `SIMJC-`/`SIMEP-`; `simulated is True`

### Implementation for User Story 5

- [ ] T013 [US5] Implement `RailInstruction`, `RailResult`, `RailTimeout`, `SimulatedRail` and `get_rail()` in `app/integrations/payment_rail.py`
- [ ] T014 [US5] Register `get_rail()` → `SimulatedRail(settings)` in `app/core/deps.py` (one-line provider change)

---

## Phase 5: User Story 1 - Funded allocations become tracked payments (P1) 🎯 MVP

**Goal**: Reserve → send → Paid/Failed with ledger, timeline, audit and notice.

**Independent Test**: `pytest tests/integration/payments/test_reserve.py tests/integration/payments/test_dispatch.py`

### Tests for User Story 1 ⚠️

- [ ] T015 [P] [US1] Write failing tests in `tests/integration/payments/test_reserve.py`: one payment per FUNDED allocation with amount > 0; none for 0 or `UNMET_NEED`; `PENDING_APPROVAL` / `REJECTED` release → `ReleaseNotPayable` and nothing written; insufficient remaining → `InsufficientFundBalance`, nothing reserved (all-or-nothing); re-run is idempotent; key `{allocation_id}:0`; `district_id`, `fund_release_id`, `scenario_id`, `simulated=true` set; provider mapping incl. null for `NONE`
- [ ] T016 [P] [US1] Write failing tests in `tests/integration/payments/test_dispatch.py`: 10 allocations with one `…000` wallet → 9 PAID, 1 FAILED; ledger moves as per table; dispatch in rank order; each payment's rail call happens after the Processing commit (spy asserts no open transaction during `send`); two concurrent `dispatch_release` calls send each payment once; release → `RELEASED`, or `PARTIALLY_RELEASED` when any allocation is `UNMET_NEED`, audited; exactly 9 `PAYMENT_NOTICE` alerts queued (one per Paid, none for Failed); re-run is a no-op

### Implementation for User Story 1

- [ ] T017 [US1] Implement `PaymentService.reserve_for_release(db, release, *, actor)` in `app/services/payments/service.py` (lock fund once, sum, check, create payments + RESERVE transitions)
- [ ] T018 [US1] Implement `PaymentService.send_payment(payment_id, *, actor)` and `apply_rail_result(db, payment, result)` in `app/services/payments/service.py` (tx1 SEND → commit → rail call → tx2 RESULT; `RailTimeout`/`PENDING` → timeline note, stay PROCESSING)
- [ ] T019 [US1] Implement `dispatch_release()`, `schedule_dispatch()` (inline vs background via `PAYMENT_DISPATCH_MODE`) and `finalise_release_status()` in `app/services/payments/dispatch.py` (`FOR UPDATE SKIP LOCKED`, rank order, audit `FUND_RELEASE_STATUS_CHANGED`)
- [ ] T020 [US1] Expose `PaymentService` and `schedule_dispatch` from `app/services/payments/__init__.py` for FR8/FR12 hand-off

**Checkpoint**: UC08 runs end to end with the simulated rail.

---

## Phase 6: User Story 3 - DC and officer views (P1)

**Goal**: List, counts, detail with timeline, CSV, role scoping.

**Independent Test**: `pytest tests/integration/payments/test_queries.py tests/contract/test_payments_api.py`

### Tests for User Story 3 ⚠️

- [ ] T021 [P] [US3] Write failing tests in `tests/integration/payments/test_queries.py`: every filter alone and combined; counts sum to total and match the list total for the same filters; amounts are Decimal sums; scenario scoping (live vs replay); sort orders
- [ ] T022 [P] [US3] Write failing contract tests in `tests/contract/test_payments_api.py`: response schemas; DC auto-scoped and 403 `DISTRICT_FORBIDDEN` for other district; **donor 403 `DONOR_AGGREGATE_ONLY` on all five `/payments` routes**; officer/admin see all; detail timeline ordered by time; `allowed_actions` per role/status/retry_count; CSV header exact, rows = list rows, **no canary name/phone/CNIC/wallet strings** in list, detail or CSV (wallet only masked `****NNNN`); CSV writes one `PAYMENTS_EXPORTED` audit entry; `X-Simulated-Data: true` header present

### Implementation for User Story 3

- [ ] T023 [P] [US3] Implement schemas in `app/schemas/payments.py` (`PaymentOut`, `BeneficiaryRef` with `wallet_masked`, `PaymentDetailOut`, `TimelineEventOut`, `PaymentCountsOut`, `PaymentFilter`, `DispatchIn`)
- [ ] T024 [US3] Implement `list_payments()`, `count_by_status()`, `get_payment_detail()`, `allowed_actions()` in `app/services/payments/queries.py` (DC scoping via `assert_district_access`, `scoped()` for scenario)
- [ ] T025 [US3] Implement `iter_payments_csv()` in `app/services/payments/csv_export.py` (server-side cursor, stdlib `csv`, UTF-8 BOM off)
- [ ] T026 [US3] Implement router `app/api/v1/payments.py` (all five routes; donor guard dependency `deny_donor()` raising `DONOR_AGGREGATE_ONLY`; register in `app/api/v1/__init__.py`, tag `Payments`)

---

## Phase 7: User Story 4 - Stuck payments are flagged (P2)

**Goal**: Reconcile + stuck timer, replay-aware.

**Independent Test**: `pytest tests/integration/payments/test_timers.py`

### Tests for User Story 4 ⚠️

- [ ] T027 [P] [US4] Write failing tests in `tests/integration/payments/test_timers.py` with `fake_clock`: `…999` wallet at +23 h 59 m not stuck, at +24 h 1 m stuck (one event `STUCK_FLAG`, one audit `PAYMENT_STUCK_FLAGGED`); second run no duplicate; `…998` wallet reconciled to FAILED `PROVIDER_TIMEOUT` after 10 min; stuck cleared on leaving Processing; timers run inside `use_scenario(sid)` only touch that scenario; the job is registered with a 15-min interval

### Implementation for User Story 4

- [ ] T028 [US4] Implement `run_payment_timers(now)` in `app/services/payments/timers.py` (reconcile via `rail.get_status`, then stuck flag; each payment in its own short transaction)
- [ ] T029 [US4] Implement `register_payment_timers(scheduler)` in `app/jobs/timers.py` (job id `payments.timers`, interval from settings, live scope)

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T030 [P] Write `tests/integration/payments/test_fund_invariants.py`: a randomised sequence of 500 operations (reserve, send, results, FR18-style retry/cancel through `transition()`) keeps `allocated = reserved + disbursed + remaining` and the per-status sums true at every step
- [ ] T031 [P] Performance check: seed 50 000 payments and assert list/counts < 500 ms (marked `@pytest.mark.slow`) in `tests/integration/payments/test_queries.py::test_perf_50k`
- [ ] T032 [P] Add `scripts/seed_demo_payments.py`: builds a release with wallets that exercise every SimulatedRail rule (for the frontend team)
- [ ] T033 Run `ruff check app/services/payments app/integrations/payment_rail.py app/api/v1/payments.py` and `mypy --strict app/services/payments`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup → Foundational** (T005/T006 before anything touching the DB).
- **US2 (Phase 3)** first: every other story uses `transition()`.
- **US5 (Phase 4)** before US1 (dispatch needs the rail). US5 tests (T012) can be written in parallel with US2.
- **US1 (Phase 5)** after US2 + US5.
- **US3 (Phase 6)** after US1 (needs payments to exist) — the contract tests can be written in parallel with US1.
- **US4 (Phase 7)** after US1.
- **Polish** last.
- **Cross-FR**: FR18 starts after T010 and T018; FR19 after T024.

### Within Each User Story

- Tests first and failing; models → services → routers.

## Parallel opportunities

- T002, T003 in Setup; T004 with T005.
- T009 (state machine tests) and T012 (rail tests) in parallel.
- T015 and T016 in parallel; T021, T022, T023 in parallel; T030, T031, T032 in parallel.

```bash
pytest tests/unit/payments -q                 # US2 + US5 + ledger
pytest tests/integration/payments -q -k "reserve or dispatch"
pytest tests/contract/test_payments_api.py -q
```

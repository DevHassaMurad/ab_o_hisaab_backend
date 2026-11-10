---
description: "Task list for FR16 SMS and field-agent fallback (Roadmap)"
---

# Tasks: FR16 Fall back to SMS or a field agent when there is no reply

**Input**: Design documents from `/specs/016-fr16-sms-field-fallback/`

**Prerequisites**: plan.md, spec.md, clarify.md; FR14 Phase 2 (outbox, worker, sender registry); FR15 Phase 2 and T027 (`receipt_status_for_payments`); FR15 T031 (`simulate_replies`)

**Tests**: Required where they protect a contract (Principle XI): timer rules, idempotency, flag-off behaviour, SMS length. Write first and see them fail.

**Roadmap rule** (authoring guide): Part A is the **MVP stub behind `FR16_FALLBACK`**; Part B is the **Roadmap build**. Only Part A is built in the 32-hour MVP.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 … US6 from spec.md

---

# Part A: MVP stub (behind a flag)

## Phase 1: Setup

- [ ] T001 Add FR16 settings (`FALLBACK_SMS_AFTER_H=6`, `FALLBACK_FIELD_AFTER_H=12`, `FALLBACK_SWEEP_INTERVAL_MIN=5`, `FALLBACK_WINDOW_DAYS=7`, `SMS_MODE=dry_run`, `SMS_MAX_SEGMENTS=3`) to `MessagingSettings` in `app/services/messaging/config.py`
- [ ] T002 [P] Create empty modules `app/services/messaging/fallback.py`, `sms_sender.py`, `field_tasks.py`
- [ ] T003 [P] Add `sms.payment_notice`, `sms.tier_alert.WARNING`, `sms.tier_alert.ACTION`, `sms.line.demo`, `field_task.brief` and the reason texts (spec.md "Message content") to `app/services/messaging/templates/catalogue.yaml`

## Phase 2: Foundational

**⚠️ CRITICAL**: blocks US1–US3

### Tests first

- [ ] T004 [P] Unit test SMS rendering and UCS-2 segment count (`segments(text)`: 70 single / 67 per part for non-GSM text) — every `sms.*` key ≤ 3 segments with sample values (`amount=50,000`, long district names) in `tests/unit/messaging/test_sms_segments.py`
- [ ] T005 [P] Unit test pure rule function `decide(root, receipt_status, payment_status, phone_valid, has_sms, has_open_task, now) -> set[Action]` for the table in implement.md in `tests/unit/messaging/test_fallback_rules.py`

### Implementation

- [ ] T006 Alembic revision for the proposed `FieldAgentTask` fields, the one-open-task partial unique index and `ix_alert_fallback_scan` in `alembic/versions/<rev>_fr16_field_agent_task_fields.py`
- [ ] T007 [P] Register `FIELD_TASK_CREATED`, `FIELD_TASK_DONE` in `app/services/audit/event_types.py`
- [ ] T008 [P] Implement `segments()` and `DryRunSmsSender` (provider id `SIMSMS-<uuid>`, `simulated=True`) and register it with `register_sender("SMS", …)` in `app/services/messaging/sms_sender.py`
- [ ] T009 [P] Implement `FieldAgentSender` (returns ok with `FIELD-<task_id>`, no network) and `create_field_task(db, *, root, payment, reason_code, via_alert)` in `app/services/messaging/field_tasks.py`
- [ ] T010 Implement `decide(...)` (pure) in `app/services/messaging/fallback.py`

---

## Phase 3: User Story 1 - SMS after 6 hours (Priority: P1)

**Goal**: Unanswered notices get one SMS at 6 h.

**Independent Test**: `pytest tests/integration/messaging/test_fallback_sweep.py -k sms`

- [ ] T011 [P] [US1] Integration test with fixed clock: 05:59 nothing; 06:00 one SMS alert (`channel=SMS`, `PAYMENT_NOTICE`, `expects_reply`, `fallback_of_id=root`, dedupe `FALLBACK_SMS:{root}`, `simulated`); reply `1` at 05:00 → nothing; only `ok` → SMS; second sweep → no new SMS; payment `CANCELLED` → nothing; phone invalid → no SMS, in `tests/integration/messaging/test_fallback_sweep.py`
- [ ] T012 [US1] Implement `run_fallback_sweep` (scan query, FR15 status lookup in one batch, `use_scenario` per root, SMS enqueue) in `app/services/messaging/fallback.py`

---

## Phase 4: User Story 2 - Field task after 12 hours (Priority: P1)

**Goal**: Still-silent notices get one open task at 12 h, listed for DC/officer.

**Independent Test**: `pytest tests/integration/messaging/test_fallback_sweep.py -k task tests/integration/messaging/test_field_tasks_api.py`

- [ ] T013 [P] [US2] Integration test: 12:00 no reply → 1 `OPEN` task (`NO_REPLY_12H`) + 1 `FIELD_AGENT` alert (`fallback_of_id` = SMS alert) + `FIELD_TASK_CREATED` audit; reply `2` at 08:00 → no task; repeat sweep → no duplicate; downtime catch-up at 13:00 → SMS and task in one run; replay scenario → task carries `scenario_id`, in `tests/integration/messaging/test_fallback_sweep.py`
- [ ] T014 [P] [US2] Integration/contract test `GET /field-tasks`: shape per plan.md, DC scoped, donor 403, flag off → 404 `FEATURE_DISABLED`, in `tests/integration/messaging/test_field_tasks_api.py`
- [ ] T015 [US2] Extend `run_fallback_sweep` with the 12 h rule in `app/services/messaging/fallback.py`
- [ ] T016 [P] [US2] Schemas `FieldTaskOut` in `app/schemas/field_tasks.py`
- [ ] T017 [US2] Router `GET /field-tasks` and registration in `app/api/v1/__init__.py`, in `app/api/v1/field_tasks.py`

---

## Phase 5: User Story 3 - Off by default (Priority: P1)

**Goal**: Flag off → nothing.

**Independent Test**: `pytest tests/integration/messaging/test_fallback_sweep.py -k flag_off`

- [ ] T018 [P] [US3] Integration test: flag off, 100 overdue notices → 0 alerts, 0 tasks, 0 audit rows, in `tests/integration/messaging/test_fallback_sweep.py`
- [ ] T019 [US3] Add `register_fr16_jobs(scheduler)` (job `fr16.fallback_sweep`, interval from settings; the job checks the flag on every run so it can be toggled without restart) in `app/services/messaging/fallback.py`, and call it from the "FR16" section of `app/jobs/timers.py`
- [ ] T020 [US3] In replays, call FR15 `simulate_replies(db, now=now)` at the start of the sweep in `app/services/messaging/fallback.py`

## Phase 6: Polish (stub)

- [ ] T021 [P] Benchmark the sweep over 50 000 notices (< 10 s) and note in PR
- [ ] T022 Manual: `sindh-2022` replay with `FLAGS=FR16_FALLBACK`; step one day after payments; check SMS then tasks for silent simulated beneficiaries
- [ ] T023 [P] `ruff` and `mypy --strict app/services/messaging/fallback.py app/services/messaging/field_tasks.py`

---

# Part B: Roadmap build

## Phase 7: User Story 4 - Field agent records the outcome (Priority: P2)

- [ ] T024 [P] [US4] Integration test `PATCH /field-tasks/{id}`: `CONFIRMED` → task `DONE`, `FIELD_AGENT` confirmation with `recorded_by`, effective receipt `CONFIRMED`, 2 audit entries; `UNREACHABLE` → no confirmation; done task → 409 `TASK_NOT_OPEN`; other district DC → 403; missing note → 422, in `tests/integration/messaging/test_field_tasks_api.py`
- [ ] T025 [US4] Implement `complete_field_task` in `app/services/messaging/field_tasks.py`
- [ ] T026 [P] [US4] Schema `FieldTaskComplete` in `app/schemas/field_tasks.py`
- [ ] T027 [US4] Route `PATCH /field-tasks/{task_id}` in `app/api/v1/field_tasks.py`

## Phase 8: User Story 5 - SMS replies count (Priority: P2)

- [ ] T028 Choose and verify the Pakistani SMS route (Twilio SMS vs local aggregator; two-way support; sender registration); record the decision in `specs/016-fr16-sms-field-fallback/clarify.md`
- [ ] T029 [P] [US5] Integration test signed inbound SMS `Body=1` → `CONFIRMED` with `channel=SMS` on the SMS alert; duplicate sid → 1 row; DC command over SMS → ignored, in `tests/integration/messaging/test_sms_inbound.py`
- [ ] T030 [US5] Implement `TwilioSmsSender` (or aggregator sender) with segment check, registered when `SMS_MODE=live`, in `app/services/messaging/sms_sender.py`
- [ ] T031 [US5] Add the "FR16" section `POST /webhooks/sms` reusing `require_twilio_signature`, the advisory lock and FR15's beneficiary branch with `channel="SMS"`, in `app/api/v1/webhooks.py`

## Phase 9: User Story 6 - Immediate fallbacks (Priority: P3)

- [ ] T032 [P] [US6] Unit tests for the immediate rules in `decide(...)` (not on WhatsApp → SMS now; invalid number → task now; `NOT_RECEIVED` → task; failed Warning/Action tier alert → safety SMS) in `tests/unit/messaging/test_fallback_rules.py`
- [ ] T033 [US6] Integration test for each immediate rule in `tests/integration/messaging/test_fallback_sweep.py`
- [ ] T034 [US6] Implement immediate rules in `app/services/messaging/fallback.py` (roots include `FAILED` WhatsApp notices and Warning/Action `TIER_ALERT` alerts)

## Phase 10: Polish (roadmap)

- [ ] T035 `FIELD_AGENT` role: hand-off to the foundation owner (enum, `require_role`, route-role contract test)
- [ ] T036 [P] Task SLA reminder to the DC after 24 h open (new template `field_task.overdue`)

---

## Dependencies & Execution Order

### Phase Dependencies

- Part A: Setup → Foundational → US1 → US2 (T015 extends T012) → US3 (T019/T020 wrap the sweep) → Polish
- Part B: only after the MVP demo works; US4 needs Part A; US5 needs T028 (provider decision); US6 needs Part A

### Cross-FR dependencies

- FR14: `enqueue`, worker, `register_sender`, `Alert.expects_reply`, `error_code` classes
- FR15: `receipt_status_for_payments`, `simulate_replies`, `record_confirmation`, beneficiary routing branch
- FR17: payment statuses
- `app/jobs/timers.py` is shared with FR8 and FR17: edit only the FR16 section

### Within each story

- Tests first and failing; pure `decide()` before the sweep; sweep before API

## Parallel opportunities

- T002, T003 in parallel; T004, T005 in parallel; T007, T008, T009 in parallel
- T011 and T013 tests can be written together; T014 and T016 in parallel with the sweep work
- Part B: T024–T027 (tasks API) in parallel with T029–T031 (SMS inbound) once T028 is decided

```bash
Task: "T004 Unit test SMS segments in tests/unit/messaging/test_sms_segments.py"
Task: "T005 Unit test decide() in tests/unit/messaging/test_fallback_rules.py"
Task: "T008 DryRunSmsSender in app/services/messaging/sms_sender.py"
```

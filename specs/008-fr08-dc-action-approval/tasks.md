---
description: "Task list for FR8 DC Approval of Action-Tier Releases"
---

# Tasks: FR8 DC Approval of Action-Tier Releases

**Input**: Design documents from `/specs/008-fr08-dc-action-approval/`

**Prerequisites**: plan.md, spec.md, clarify.md; 000-foundation; FR7 `transition_release`, `run_release_pipeline` and its `ApprovalRequired` guard

**Tests**: MANDATORY (Principle XI: Action-tier approval gating). Write first, see them fail.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Write migration for the proposed Approval fields (`scenario_id`, nullable `channel`/`otp_hash`/`otp_expires_at`, `otp_attempts`, `otp_last_sent_at`, `wa_code_hash`, `wa_code_expires_at`, `wa_code_attempts`, `escalation_reason`, `decision_message_id`) in `alembic/versions/0008_fr08_approval_fields.py` (after the foundation merge)
- [ ] T002 [P] Add approval settings from plan.md "Configuration" to `app/core/config.py` (foundation PR) with `APPROVAL_OTP_DEV_ECHO` forced false in production
- [ ] T003 [P] Add fixtures `pending_action_release(district)`, `dc_user(district, phone)`, `officer_users()`, `FakeClock` in `tests/integration/triggers/conftest.py`

---

## Phase 2: Foundational (codes and gate)

**⚠️ Blocks every story.**

- [ ] T004 [P] Failing unit tests: `generate_code` always returns 6 ASCII digits (leading zeros kept) and uses `secrets`; `hash_code`/`verify_code` round-trip and reject a different code; `normalise_digits` maps `۰-۹` and `٠-٩`, in `tests/unit/triggers/test_approval_codes.py`
- [ ] T005 [P] Failing gate tests: PDMA officer, admin, donor and other-district DC get 403 on approve/reject; no `Allocation` rows exist for a PENDING or ESCALATED approval; `decide()` on a decided approval raises 409, in `tests/integration/triggers/test_approval_gate.py`
- [ ] T006 Implement `generate_code`, `hash_code`, `verify_code`, `normalise_digits` in `app/services/triggers/approval_codes.py`
- [ ] T007 Implement `APPROVAL_TRANSITIONS`, `request_approval` (code, alert, audit, no-DC escalation) and `decide` (lock, approver check, burn codes, audit, FR7 transition + pipeline or reject + officer notice) in `app/services/triggers/approvals.py`

**Checkpoint**: FR7 can call `request_approval`; the gate holds.

---

## Phase 3: User Story 1 - Dashboard approval with OTP (P1) 🎯 MVP

### Tests for User Story 1 ⚠️

- [ ] T008 [P] [US1] Contract tests for list, detail, otp, approve shapes (string money, masked phone, `can_decide`, `forecast_7d` 7 rows) in `tests/contract/test_approvals_contract.py`
- [ ] T009 [P] [US1] Integration tests: detail shows score, explanations, forecast, satellite (when present, empty list otherwise), pools, register counts, deadline, in `tests/integration/triggers/test_approval_dashboard.py`
- [ ] T010 [P] [US1] Integration tests: OTP issue (hash stored, alert with `sensitive_keys=["otp"]`, audit without OTP), cooldown 429, `DC_PHONE_MISSING` 409, in `tests/integration/triggers/test_approval_dashboard.py`
- [ ] T011 [P] [US1] Integration tests: valid OTP approves (channel DASHBOARD, approver, release → ALLOCATING → RELEASED via FR7 with fake allocation); wrong OTP 400 with attempts_left and the counter persisted; 5th failure → 429 `OTP_LOCKED`; expired → 410; not requested → 409, in `tests/integration/triggers/test_approval_dashboard.py`

### Implementation for User Story 1

- [ ] T012 [P] [US1] Implement `build_approval_detail` in `app/services/triggers/approval_view.py`
- [ ] T013 [US1] Implement `issue_dashboard_otp` and `approve_with_otp` (commit failed attempts before raising) in `app/services/triggers/approvals.py`
- [ ] T014 [P] [US1] Create schemas in `app/schemas/approvals.py`
- [ ] T015 [US1] Implement `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/otp`, `POST /approvals/{id}/approve` in `app/api/v1/approvals.py` and register the router

---

## Phase 4: User Story 2 - WhatsApp decision (P1)

### Tests for User Story 2 ⚠️

- [ ] T016 [P] [US2] Parser unit tests for every grammar row in plan.md (English, Urdu keywords, Urdu/Arabic-Indic digits, spaced codes, non-commands → None) in `tests/unit/triggers/test_dc_command_parser.py`
- [ ] T017 [P] [US2] Handler integration tests: right code approves (channel WHATSAPP, `decision_message_id` stored, reply has amount); wrong code decrements and locks at 5; expired code; non-DC sender → `NOT_A_DC`, no change; duplicate `provider_message_id` → `ALREADY_DECIDED`, no change; no open approval → `NO_OPEN_APPROVAL`, in `tests/integration/triggers/test_approval_whatsapp.py`

### Implementation for User Story 2

- [ ] T018 [US2] Implement `parse_dc_command` in `app/services/triggers/dc_commands.py`
- [ ] T019 [US2] Implement `handle_dc_whatsapp_command` and bilingual reply texts in `app/services/triggers/dc_commands.py`

---

## Phase 5: User Story 3 - Reject with reason (P1)

### Tests for User Story 3 ⚠️

- [ ] T020 [P] [US3] Dashboard reject tests: reason stored, release REJECTED, no allocations/payments, FR7 `available_to_release` back to include the 30 M, officers notified; short reason → 422; second decision → 409, in `tests/integration/triggers/test_approval_dashboard.py`
- [ ] T021 [P] [US3] WhatsApp reject tests: `REJECT <reason>` with one open approval; `AMBIGUOUS` with live + replay approvals; `REJECT <code> <reason>` picks the right one; short reason → `REASON_REQUIRED`, in `tests/integration/triggers/test_approval_whatsapp.py`

### Implementation for User Story 3

- [ ] T022 [US3] Implement `reject` and the REJECT branch of the handler in `app/services/triggers/approvals.py` and `app/services/triggers/dc_commands.py`
- [ ] T023 [US3] Add `POST /approvals/{id}/reject` in `app/api/v1/approvals.py`

---

## Phase 6: User Story 4 - Escalation after 2 h (P1)

### Tests for User Story 4 ⚠️

- [ ] T024 [P] [US4] Escalation tests with `FakeClock`: 1 h 59 m → nothing; 2 h 01 m → ESCALATED, `escalated_at`, officer + DC reminder alerts, audit; second run no duplicates; officer approve 403; DC approve after escalation works and notifies officers; no-DC district escalates at request time; replay approvals ignored by the live job, in `tests/integration/triggers/test_approval_escalation.py`
- [ ] T025 [P] [US4] Resend-request tests: new code invalidates the old, attempts reset, cooldown 429, officer allowed, in `tests/integration/triggers/test_approval_escalation.py`

### Implementation for User Story 4

- [ ] T026 [US4] Implement `escalate_overdue_approvals(db, *, now, scenario_id)` and `resend_request` in `app/services/triggers/approvals.py`
- [ ] T027 [US4] Add `job_escalate_overdue_approvals` and its registration in `app/jobs/timers.py`
- [ ] T028 [US4] Add `POST /approvals/{id}/resend-request` and the `escalated` list filter in `app/api/v1/approvals.py`

---

## Phase 7: User Story 5 - Replay simulation (P2)

### Tests for User Story 5 ⚠️

- [ ] T029 [P] [US5] Replay tests: `SIMULATED` mode: `request_approval` leaves it PENDING; `simulate_pending_approvals` then decides it with `REPLAY_SIM`, after Watch/Warning of the same step were allocated,, `simulated=true`, `decided_at = requested_at + 45 min`, and audit; `LIVE_WHATSAPP` mode stays PENDING and a WhatsApp reply decides it; simulating a live approval → 403 `SIMULATION_LIVE_FORBIDDEN`, in `tests/integration/triggers/test_approval_replay.py`

### Implementation for User Story 5

- [ ] T030 [US5] Implement `simulate_replay_decision` and `simulate_pending_approvals` in `app/services/triggers/approvals.py`
- [ ] T031 [US5] Add `POST /approvals/{id}/simulate` (ADMIN) in `app/api/v1/approvals.py`

---

## Phase 8: Polish

- [ ] T032 [P] Register audit types (`APPROVAL_REQUESTED`, `APPROVAL_OTP_ISSUED`, `APPROVAL_CODE_FAILED`, `APPROVAL_DECIDED`, `APPROVAL_ESCALATED`, `APPROVAL_REQUEST_RESENT`) in `app/services/audit/event_types.py`
- [ ] T033 [P] Log-scrubbing test: run the full approve flow with `caplog` and assert no 6-digit code appears in logs, audit payloads or `Alert.message_text`, in `tests/integration/triggers/test_approval_gate.py`
- [ ] T034 [P] End-to-end test with FR7: score 85 → Action pending → WhatsApp approve → allocations exist, in `tests/integration/triggers/test_approval_whatsapp.py`
- [ ] T035 `ruff`, `mypy --strict app/services/triggers`, full FR8 selector

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 → (US2 ‖ US3) → US4 → US5 → Polish.
- US3's WhatsApp part (T021–T022) needs US2's parser (T018).
- US4 needs `decide` (T007) and alert wiring; it is independent of the HTTP routes.
- FR15 can integrate as soon as T019 is merged.

## Parallel opportunities

- T002, T003; T004, T005.
- All tests inside a story phase.
- T012 and T014 alongside T013.
- T032–T034 together.

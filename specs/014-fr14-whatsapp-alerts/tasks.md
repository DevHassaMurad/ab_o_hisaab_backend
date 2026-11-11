---
description: "Task list for FR14 WhatsApp alerts"
---

# Tasks: FR14 WhatsApp alerts in Urdu and English

**Input**: Design documents from `/specs/014-fr14-whatsapp-alerts/`

**Prerequisites**: plan.md, spec.md, clarify.md; `000-foundation` merged; `020-fr20-audit-log` merged (or its stub in place)

**Tests**: Mandatory. Alerts carry approval requests and payment notices, and the constitution's Quality Standards require idempotent webhooks and audited alerts. Write each test first and see it fail.

**Organization**: Tasks are grouped by user story so each story can be built and tested on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 … US6 from spec.md

---

## Phase 1: Setup

**Purpose**: Dependencies, settings and the empty package

- [ ] T001 Add `twilio` (pin current 9.x, verify) and `pyyaml` to `pyproject.toml` dependencies
- [ ] T002 [P] Create `MessagingSettings` (pydantic-settings, env prefix none) with every key in implement.md "Configuration" and its default, in `app/services/messaging/config.py`; add the keys to `.env.example` through a hand-off note to the foundation owner (the `TWILIO_*` keys already exist in foundation `app/core/config.py`; reuse them, do not duplicate)
- [ ] T003 [P] Create package skeleton `app/services/messaging/__init__.py` and empty modules listed in plan.md Project Structure
- [ ] T004 [P] Create test package `tests/unit/messaging/__init__.py` and `tests/integration/messaging/conftest.py` with fixtures: `recording_sender`, `dc_dadu_with_phone`, `pdma_officer_with_phone`, `funded_release_factory`, `paid_payment_factory`, `signed_form(url, params)` helper

---

## Phase 2: Foundational (blocking for all stories)

**Purpose**: Outbox columns, templates, senders, enqueue and the worker core

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests first

- [ ] T005 [P] Unit test catalogue rendering (Urdu block first, blank line, English block; strict missing-variable error `TemplateContextMissing`; `line.demo` appended when `simulated`) in `tests/unit/messaging/test_templates.py`
- [ ] T006 [P] Unit lint test over every catalogue key: renders with sample context; beneficiary blocks ≤ 6 lines and ≤ 60 words per language; blocklist words absent (`tier`, `allocation`, `disbursement`, `beneficiary`); no `{name}`, `{phone}`, `{cnic}`, `{wallet_id}` variables; Urdu block contains Arabic-script characters, in `tests/unit/messaging/test_catalogue_lint.py`
- [ ] T007 [P] Unit test dedupe key builders for every row of plan.md "Dedupe keys" in `tests/unit/messaging/test_dedupe.py`
- [ ] T008 [P] Unit test allowlist gate: `dry_run` never calls Twilio; `twilio` mode calls Twilio only for allowlisted numbers or (flag on and non-simulated beneficiary) in `tests/unit/messaging/test_allowlist.py`
- [ ] T009 [P] Unit test `classify_twilio_error` for each code in plan.md table plus timeout and 5xx in `tests/unit/messaging/test_error_classification.py`
- [ ] T010 Integration test `DbAlertService.enqueue`: creates `QUEUED` alert with rendered text, `language=UR_EN`, priority, `triggered_at`, `district_id`, `expects_reply`; writes one `ALERT_QUEUED` audit entry whose payload has `text_sha256` and no phone/text; second identical call returns the same row and writes no audit; `scenario_id` copied from `current_scenario()`; no-phone recipient stored `FAILED/NO_PHONE` with `ALERT_FAILED` audit, in `tests/integration/messaging/test_enqueue.py`
- [ ] T011 Integration test worker: claims by priority then `queued_at`; success → `SENT`, `sent_at`, `provider_message_id`, `attempt_count=1`, `ALERT_SENT` audit in the alert's scenario chain; transient error → stays `QUEUED` with `next_attempt_at` per backoff; 5th transient failure → `FAILED/PROVIDER_UNAVAILABLE`; permanent error → `FAILED` with code; two concurrent worker runs never send one alert twice (SKIP LOCKED), in `tests/integration/messaging/test_worker.py`

### Implementation

- [ ] T012 Alembic revision adding the proposed `Alert`, `Beneficiary` and `District` columns, the `REPLY_ACK`/`DECISION_NOTICE` enum values, the dedupe unique index and `ix_alert_queue` in `alembic/versions/<rev>_fr14_alert_outbox.py` (coordinate with the foundation owner; if they merge the fields into `0001`, this revision only adds indexes)
- [ ] T013 [P] Register audit event types `ALERT_QUEUED`, `ALERT_SENT`, `ALERT_STATUS_CHANGED`, `ALERT_FAILED`, `BENEFICIARY_PHONE_FLAGGED` (FR20 hand-off: add constants in your own PR) in `app/services/audit/event_types.py`
- [ ] T014 [P] Write the full catalogue (every key in spec.md "Message content catalogue") in `app/services/messaging/templates/catalogue.yaml`
- [ ] T015 [P] Implement catalogue loader, `render(key, ctx, *, simulated) -> RenderedMessage(text, ur, en, purpose, expects_reply, priority, content_sid)`, amount formatting (`15,000`), explanation truncation at 200 chars, in `app/services/messaging/templates.py`
- [ ] T016 [P] Implement dedupe key builders in `app/services/messaging/dedupe.py`
- [ ] T017 [P] Implement `ErrorClass` and `classify_twilio_error` in `app/services/messaging/errors.py`
- [ ] T018 [P] Implement recipient helpers `resolve_phone`, `dc_users_for_district`, `pdma_officers`, `mask_phone`, `district_of(recipient)` in `app/services/messaging/recipients.py`
- [ ] T019 [P] Implement thin Twilio wrapper `get_twilio_client()`, `send_whatsapp(to_e164, body | content_sid+vars, status_callback)` with `TWILIO_HTTP_TIMEOUT_S` in `app/integrations/twilio_client.py`
- [ ] T020 Implement `Sender` Protocol, `SendResult`, `TwilioWhatsAppSender`, `DryRunSender` (returns `SIM-<uuid>`, optional latency), `RecordingSender` (tests), `register_sender`/`get_sender`, and the allowlist gate `choose_sender(alert, recipient)` in `app/services/messaging/senders.py`
- [ ] T021 Implement `DbAlertService.enqueue` (render, resolve phone, `INSERT … ON CONFLICT DO NOTHING RETURNING`, audit, mark session for kick) in `app/services/messaging/alert_service.py`
- [ ] T022 Implement `run_send_worker()` and `_send_one(alert_id)` (per-alert transaction, `FOR UPDATE SKIP LOCKED`, `use_scenario(alert.scenario_id)`, thread pool, backoff, audit) and the SQLAlchemy `after_commit` kick in `app/services/messaging/worker.py`
- [ ] T023 Register jobs `alerts.send_worker` and `alerts.nfr2_watchdog` via `register_job` at startup (call `register_messaging_jobs(scheduler)` from `app/services/messaging/__init__.py`; foundation `app/main.py` calls each FR's register function)
- [ ] T024 Wire `get_alerts()` to return `DbAlertService` in `app/core/deps.py` (one line)

**Checkpoint**: `enqueue` + worker work end to end with the dry-run sender

---

## Phase 3: User Story 1 - DC tier change and approval request (Priority: P1) 🎯 MVP

**Goal**: The DC gets the tier-change message and the Action approval request within 60 s.

**Independent Test**: `pytest tests/integration/messaging/test_notify_helpers.py -k "dc"` and `tests/integration/messaging/test_nfr2_latency.py -k official`

### Tests for User Story 1

- [ ] T025 [P] [US1] Integration test `notify_tier_change(direction="UP")` with Watch+Warning releases in one call → one alert per active DC, `tiers_en="Watch + Warning"`, summed amount, both purposes; Action pending → `dc.tier_change.up_pending`; `DOWN` and `CLOSED` variants; repeated call → no new alerts, in `tests/integration/messaging/test_notify_helpers.py`
- [ ] T026 [P] [US1] Integration test `notify_approval_request(code="482913", issue_no=1)` → text contains `APPROVE 482913` on its own line, expiry in PKT, beneficiary count; `issue_no=2` creates a second alert, in `tests/integration/messaging/test_notify_helpers.py`
- [ ] T027 [P] [US1] Latency test: 2 official alerts queued after 5 000 beneficiary alerts are sent first and within 60 s of `triggered_at`; all 5 002 within 300 s with `DryRunSender(latency_ms=50)` and `ALERT_WORKER_CONCURRENCY=8`, in `tests/integration/messaging/test_nfr2_latency.py`

### Implementation for User Story 1

- [ ] T028 [US1] Implement `notify_tier_change` (UP, UP with Action pending, DOWN, CLOSED; multi-tier coalescing) in `app/services/messaging/notify.py`
- [ ] T029 [US1] Implement `notify_approval_request` (context from `Approval`, `FundRelease`, `RiskIndex`, allocation counts; `{expires_pkt}` in Asia/Karachi) in `app/services/messaging/notify.py`

**Checkpoint**: The "phone buzzes" demo moment works with the dry-run sender

---

## Phase 4: User Story 2 - Payment notice (Priority: P1)

**Goal**: Each Paid payment produces one notice that asks for a 1/2 reply.

**Independent Test**: `pytest tests/integration/messaging/test_notify_helpers.py -k payment`

### Tests for User Story 2

- [ ] T030 [P] [US2] Integration test `notify_payment_paid`: text has `Rs 15,000`, wallet names, `{ref}` = 8 upper-case chars of payment id, demo label for simulated; `expects_reply=true`; `triggered_at=paid_at`; second call → same alert; phone flagged invalid → `FAILED/PHONE_INVALID` without a provider call, in `tests/integration/messaging/test_notify_helpers.py`

### Implementation for User Story 2

- [ ] T031 [US2] Implement `notify_payment_paid` in `app/services/messaging/notify.py`

---

## Phase 5: User Story 3 - Beneficiary tier alerts (Priority: P1)

**Goal**: Every allocated beneficiary gets the right safety message for the highest tier fired.

**Independent Test**: `pytest tests/integration/messaging/test_notify_helpers.py -k tier_alert`

### Tests for User Story 3

- [ ] T032 [P] [US3] Integration test `notify_beneficiary_tier_alerts`: funded household Warning → header + household advice + `amount.wallet`; shopkeeper Watch → shop advice; Action pending → no amount line; `UNMET_NEED` → no amount line; `wallet_provider=NONE` → `amount.no_wallet`; Watch+Warning+Action in one call → one alert per beneficiary (Action); repeat → no new rows, in `tests/integration/messaging/test_notify_helpers.py`

### Implementation for User Story 3

- [ ] T033 [US3] Implement `notify_beneficiary_tier_alerts` with a single query over `Allocation` rows of the given releases (batch insert in chunks of 500) in `app/services/messaging/notify.py`

---

## Phase 6: User Story 4 - Delivery status and invalid numbers (Priority: P2)

**Goal**: Signed status callbacks move alerts forward; invalid numbers flag the beneficiary.

**Independent Test**: `pytest tests/integration/messaging/test_status_callback.py tests/integration/messaging/test_invalid_number.py tests/contract/test_webhook_signature.py`

### Tests for User Story 4

- [ ] T034 [P] [US4] Unit test status rank ordering and FAILED rules in `tests/unit/messaging/test_status_ordering.py`
- [ ] T035 [P] [US4] Contract test: missing or wrong `X-Twilio-Signature` → 403 `INVALID_SIGNATURE`, no DB change; correct signature computed with `RequestValidator` against `PUBLIC_BASE_URL` → 204, in `tests/contract/test_webhook_signature.py`
- [ ] T036 [P] [US4] Integration test callbacks `delivered`, `sent` (ignored), `read`, `read` (duplicate) → final `READ`, 2 audit entries; unknown `MessageSid` → 204, no change; `failed` after `DELIVERED` ignored, in `tests/integration/messaging/test_status_callback.py`
- [ ] T037 [P] [US4] Integration test `failed` + `ErrorCode=21211` on a beneficiary alert → alert `FAILED`, beneficiary `phone_valid=false`, `phone_flag_reason=TWILIO_21211`, `BENEFICIARY_PHONE_FLAGGED` audit; `63015` → no flag; synchronous 21211 at send time behaves the same, in `tests/integration/messaging/test_invalid_number.py`

### Implementation for User Story 4

- [ ] T038 [US4] Implement `require_twilio_signature` (rebuild URL from `PUBLIC_BASE_URL` + path + query; read form; `RequestValidator.validate`; bypass only when `ENV=test` and `TWILIO_VALIDATE_SIGNATURE=false`) in `app/services/messaging/signature.py`
- [ ] T039 [US4] Implement `apply_status_callback(db, form)` and `flag_invalid_phone(db, beneficiary, code)` in `app/services/messaging/status.py`
- [ ] T040 [US4] Create the webhooks router with `POST /webhooks/whatsapp/status` (marked section "FR14") and register it in `app/api/v1/__init__.py` in `app/api/v1/webhooks.py`

---

## Phase 7: User Story 5 - Escalation and decision notices (Priority: P2)

**Goal**: PDMA officers hear about 2 h escalations and DC decisions; the DC gets a reminder.

**Independent Test**: `pytest tests/integration/messaging/test_notify_helpers.py -k "escalation or decision"`

### Tests for User Story 5

- [ ] T041 [P] [US5] Integration test `notify_escalation` → one `escalation.pdma` per active officer and one `escalation.dc_reminder` per DC; with and without a fresh code; repeat → no new rows; no officer phone → `FAILED/NO_RECIPIENT` row, in `tests/integration/messaging/test_notify_helpers.py`
- [ ] T042 [P] [US5] Integration test `notify_decision` for REJECTED (reason in text, "money stays in the fund") and APPROVED, in `tests/integration/messaging/test_notify_helpers.py`

### Implementation for User Story 5

- [ ] T043 [US5] Implement `notify_escalation` and `notify_decision` in `app/services/messaging/notify.py`

---

## Phase 8: User Story 6 - Alerts API, test send, resend (Priority: P3)

**Goal**: Officers can see, measure and re-send messages.

**Independent Test**: `pytest tests/contract/test_alerts_api.py`

### Tests for User Story 6

- [ ] T044 [P] [US6] Contract test `GET /alerts`, `/alerts/{id}`, `/alerts/stats`, `/alerts/templates`: shapes per plan.md, masked phone, DC scoped to own district (403 `DISTRICT_FORBIDDEN` on others), donor 403, `X-Scenario-Id` scoping; `POST /alerts/test` admin only; `POST /alerts/{id}/resend` 409 when not failed, 201 with `resend_of_id`, in `tests/contract/test_alerts_api.py`

### Implementation for User Story 6

- [ ] T045 [P] [US6] Pydantic schemas `AlertOut`, `AlertStats`, `TemplateOut`, `TestSendIn`, `ResendIn` in `app/schemas/alerts.py`
- [ ] T046 [US6] Implement `send_reply` (REPLY_ACK, priority 4, dedupe `ACK:{sid}`) for FR15 in `app/services/messaging/notify.py`
- [ ] T047 [US6] Implement the alerts router (list with filters and pagination, detail, stats with percentile query, templates preview, test send, resend) in `app/api/v1/alerts.py`

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T048 [P] Implement `python -m app.services.messaging.demo_setup` (reads `DEMO_PHONES` JSON env, sets `User.phone`/`phone_hash` for seed DCs and officer, and the hero beneficiary's phone; prints sandbox checklist) in `app/services/messaging/demo_setup.py`
- [ ] T049 [P] Add seed Urdu district names (`name_ur`) to the foundation seed via a hand-off note to the foundation owner (values in implement.md); fallback works without them
- [ ] T050 Run the sandbox manual checklist in implement.md with the 4 demo phones and record results in the PR
- [ ] T051 [P] Performance check: `GET /alerts` p95 < 500 ms with 50 000 alerts (index on `(district_id, queued_at)`), note in PR
- [ ] T052 `ruff check app/services/messaging app/api/v1/alerts.py app/api/v1/webhooks.py` and `mypy --strict app/services/messaging`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none
- **Foundational (Phase 2)**: needs Setup; blocks every story. T012 (migration) before T010/T011 can pass
- **US1, US2, US3 (Phases 3–5)**: need Phase 2; independent of each other (all edit `notify.py`, so serialise the implementation tasks T028/T029, T031, T033 or split by function and merge carefully)
- **US4 (Phase 6)**: needs Phase 2; independent of US1–US3
- **US5 (Phase 7)**: needs Phase 2
- **US6 (Phase 8)**: needs Phase 2; T046 is needed by FR15
- **Polish (Phase 9)**: after the stories you ship

### Cross-FR dependencies

- `020-fr20-audit-log` for real hashing (stub is enough to start)
- `007`, `008`, `017` call the helpers; until they exist, tests call the helpers directly with factories
- `015-fr15-receipt-confirmation` needs T038, T040, T046

### Within each story

- Tests first, see them fail, then implement
- Templates and dedupe before `enqueue`; `enqueue` before helpers; helpers before APIs

## Parallel opportunities

- T002, T003, T004 in parallel
- T005–T009 (unit tests) in parallel; T013–T019 in parallel (different files)
- Once Phase 2 is done: US4 (status callback, `status.py`, `signature.py`, `webhooks.py`) can run in parallel with US1–US3 (`notify.py`)
- All `[P]` test tasks inside a story in parallel

```bash
# After Phase 2:
Task: "T034 Unit test status rank ordering in tests/unit/messaging/test_status_ordering.py"
Task: "T035 Contract test signature in tests/contract/test_webhook_signature.py"
Task: "T025 Integration test notify_tier_change in tests/integration/messaging/test_notify_helpers.py"
```

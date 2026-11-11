---
description: "Task list for FR15 receipt confirmation"
---

# Tasks: FR15 Record a beneficiary's confirmation reply

**Input**: Design documents from `/specs/015-fr15-receipt-confirmation/`

**Prerequisites**: plan.md, spec.md, clarify.md; `000-foundation`, `020-fr20-audit-log` and FR14 Phase 2 plus T038, T040, T046 (signature dependency, webhooks router, `send_reply`)

**Tests**: Mandatory. Confirmations feed the donor "received" figure, and the webhook is the WhatsApp path into Action-tier approval (Principle II). Idempotent webhooks are a constitution Quality Standard. Write each test first and see it fail.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 … US6 from spec.md

---

## Phase 1: Setup

- [ ] T001 Add FR15 settings (`RECEIPT_REPLY_WINDOW_DAYS=7`, `RECEIPT_KEYWORDS_ENABLED=true`, `RECEIPT_YES_KEYWORDS`, `RECEIPT_NO_KEYWORDS`, `RECEIPT_HELP_THROTTLE_MIN=10`, `SIM_REPLY_*`) to `MessagingSettings` in `app/services/messaging/config.py`
- [ ] T002 [P] Create empty modules `app/services/messaging/reply_parser.py`, `inbound.py`, `confirmations.py`, `simulated_replies.py`
- [ ] T003 [P] Write the parser corpus (≥ 40 cases: input, button payload, expected status) in `tests/fixtures/replies_corpus.yaml`
- [ ] T004 [P] Add a stub `handle_dc_whatsapp_command` fixture (records calls, returns a configurable `DcCommandResult`) in `tests/integration/messaging/conftest.py` (FR15 section)

---

## Phase 2: Foundational

**⚠️ CRITICAL**: blocks all stories

### Tests first

- [ ] T005 [P] Unit test `normalise` and `parse_reply` against every corpus case in `tests/unit/messaging/test_reply_parser.py`
- [ ] T006 [P] Unit test `is_command` (`APPROVE 1`, `approve`, `  REJECT reason`, `APPROVED`, `REJECTION`, `1 APPROVE` (Urdu digits)) in `tests/unit/messaging/test_command_detection.py`
- [ ] T007 Integration test `record_confirmation`: inserts row with alert, beneficiary, payment, channel, status, encrypted raw reply, message id, scenario of the alert, `simulated` rule; writes one `RECEIPT_*` audit entry with `reply_sha256` and no raw text; duplicate message id raises `DuplicateInbound`, in `tests/integration/messaging/test_inbound_receipt.py`

### Implementation

- [ ] T008 Alembic revision for the proposed `Confirmation` fields, unique `provider_message_id`, indexes, and `reply_raw` encryption (data migration encrypts existing rows if any) in `alembic/versions/<rev>_fr15_confirmation_fields.py`
- [ ] T009 [P] Register `RECEIPT_NOT_RECEIVED`, `RECEIPT_UNPARSEABLE` in `app/services/audit/event_types.py`
- [ ] T010 [P] Implement `normalise`, `parse_reply`, `is_command`, `ParsedReply` in `app/services/messaging/reply_parser.py`
- [ ] T011 Implement `record_confirmation` in `app/services/messaging/confirmations.py`
- [ ] T012 [P] Add `receipt.*`, `dc.approval_result.*`, `dc.help`, `official.not_authorised` keys (text from spec.md "Reply message catalogue") to `app/services/messaging/templates/catalogue.yaml`

**Checkpoint**: parser and recorder are ready

---

## Phase 3: User Story 1 - Beneficiary confirms with one digit (Priority: P1) 🎯 MVP

**Goal**: `1`/`1` (Urdu digits)/`1` (Arabic-Indic digits) from the beneficiary's phone marks the latest notice Confirmed.

**Independent Test**: `pytest tests/integration/messaging/test_inbound_receipt.py tests/contract/test_inbound_webhook.py -k receipt`

### Tests for User Story 1

- [ ] T013 [P] [US1] Contract test: signed form `Body=1` (Urdu digits) → 200 `text/xml` `<Response/>`; `CONFIRMED` row; `receipt.ack.confirmed` alert queued with dedupe `ACK:{sid}`, in `tests/contract/test_inbound_webhook.py`
- [ ] T014 [P] [US1] Integration tests: `2` → `NOT_RECEIVED` + `receipt.ack.not_received` with `{ref}`; `2` then `1` → two rows, effective `CONFIRMED`; two notices → latest wins; shared phone across two beneficiaries → latest across both; SMS fallback notice answerable; reply's scenario equals alert's scenario and audit lands in that chain, in `tests/integration/messaging/test_inbound_receipt.py`

### Implementation for User Story 1

- [ ] T015 [US1] Implement `InboundMessage` (from form), `find_open_prompt(db, phone_hash, now)` and the beneficiary branch of `route_inbound` in `app/services/messaging/inbound.py`
- [ ] T016 [US1] Add the "FR15" section with `POST /webhooks/whatsapp` (depends on `require_twilio_signature`; advisory lock; returns empty TwiML) to `app/api/v1/webhooks.py`

**Checkpoint**: The demo "reply 1" works with a signed test request

---

## Phase 4: User Story 2 - DC approves or rejects by WhatsApp (Priority: P1)

**Goal**: DC commands reach FR8; the DC gets the rendered outcome.

**Independent Test**: `pytest tests/integration/messaging/test_inbound_dc_routing.py`

### Tests for User Story 2

- [ ] T017 [P] [US2] Integration tests with the FR8 stub: `approve 482913` (Urdu digits) from the DC phone → handler called once with `approve 482913`, the DC user, the sid; each outcome renders `dc.approval_result.<outcome>`; command from a beneficiary phone → not called, `UNPARSEABLE`; from an officer → `official.not_authorised`; DC "hello" → `dc.help`; inactive DC → treated as unknown; handler raises → no confirmation/approval writes, `dc.approval_result.ERROR` queued, response 200; phone that is both DC and beneficiary: `1` → receipt, `APPROVE …` → FR8, in `tests/integration/messaging/test_inbound_dc_routing.py`

### Implementation for User Story 2

- [ ] T018 [US2] Implement the official branch of `route_inbound`, `_result_to_ack(result) -> (template_key, context, scenario_id)` and the error fallback (rollback, new transaction, `ERROR` ack) in `app/services/messaging/inbound.py`
- [ ] T019 [US2] Import `handle_dc_whatsapp_command` lazily from `app/services/triggers/dc_commands.py` with a clear `ImportError` message until FR8 lands, in `app/services/messaging/inbound.py`

---

## Phase 5: User Story 3 - Once only, and only from Twilio (Priority: P1)

**Goal**: Exactly-once effects per `MessageSid`; unsigned requests rejected.

**Independent Test**: `pytest tests/integration/messaging/test_inbound_idempotency.py tests/contract/test_inbound_webhook.py -k signature`

### Tests for User Story 3

- [ ] T020 [P] [US3] Integration test: same sid 3× (2 concurrently via threads with separate sessions) → 1 confirmation, 1 audit entry, 1 ack; duplicate DC command → FR8 stub called once; duplicate "no open notice" → 1 ack, in `tests/integration/messaging/test_inbound_idempotency.py`
- [ ] T021 [P] [US3] Contract test: no signature / wrong signature / signature for another URL → 403 `INVALID_SIGNATURE`, 0 rows in `confirmation` and `alert`, in `tests/contract/test_inbound_webhook.py`

### Implementation for User Story 3

- [ ] T022 [US3] Implement `processed(db, sid)` and the `pg_advisory_xact_lock(hashtext(:sid))` step at the top of `route_inbound` in `app/services/messaging/inbound.py`

---

## Phase 6: User Story 4 - Help for unclear replies, silence for strangers (Priority: P2)

**Goal**: Unparseable replies recorded with throttled help; strangers ignored.

**Independent Test**: `pytest tests/integration/messaging/test_inbound_receipt.py -k "unparseable or unknown or no_open"`

### Tests for User Story 4

- [ ] T023 [P] [US4] Integration tests: `ok` → `UNPARSEABLE` + help; second unclear reply within 10 min → recorded, no help; media-only → `reply_raw="[media]"`; no notice in 7 days → `receipt.no_open_notice`, no confirmation; unknown number → no rows, no alerts; non-PK number → no rows, in `tests/integration/messaging/test_inbound_receipt.py`

### Implementation for User Story 4

- [ ] T024 [US4] Implement help throttle (`recent_ack_exists(db, phone_hash, "receipt.help", minutes)`), media handling and unknown-sender logging in `app/services/messaging/inbound.py`

---

## Phase 7: User Story 5 - Officers and DCs see who confirmed (Priority: P2)

**Goal**: One shared definition of receipt status and counts.

**Independent Test**: `pytest tests/integration/messaging/test_receipt_status.py tests/contract/test_confirmations_api.py`

### Tests for User Story 5

- [ ] T025 [P] [US5] Integration test `receipt_status_for_payments` and `receipt_counts` against the 10-payment fixture (6/1/1/2) and with `beneficiary_type` filter, in `tests/integration/messaging/test_receipt_status.py`
- [ ] T026 [P] [US5] Contract test `GET /confirmations` (filters, pagination, `reply_preview` ≤ 40 chars, DC scoped, donor 403) and `GET /confirmations/summary` (shape, 422 without scope), in `tests/contract/test_confirmations_api.py`

### Implementation for User Story 5

- [ ] T027 [US5] Implement `receipt_status_for_payments` (DISTINCT ON query) and `receipt_counts` in `app/services/messaging/confirmations.py`
- [ ] T028 [P] [US5] Schemas `ConfirmationOut`, `ReceiptSummary` in `app/schemas/confirmations.py`
- [ ] T029 [US5] Router `GET /confirmations`, `GET /confirmations/summary` and registration in `app/api/v1/__init__.py`, in `app/api/v1/confirmations.py`

---

## Phase 8: User Story 6 - Simulated replies in replays (Priority: P3)

**Goal**: Labelled, deterministic simulated replies for dry-run notices.

**Independent Test**: `pytest tests/integration/messaging/test_simulated_replies.py`

### Tests for User Story 6

- [ ] T030 [P] [US6] Integration test: 1 000 dry-run notices in a scenario → counts within ±3% of 85/5/10, all `simulated=true` with `SIMIN-` ids and audit entries; really-sent alert skipped; live alerts skipped unless `SIM_REPLY_LIVE`; second run adds nothing, in `tests/integration/messaging/test_simulated_replies.py`

### Implementation for User Story 6

- [ ] T031 [US6] Implement `simulate_replies(db, now)` and `register_fr15_jobs(scheduler)` in `app/services/messaging/simulated_replies.py`

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T032 [P] Load test: 20 concurrent signed inbound requests, p95 < 2 s; note in PR
- [ ] T033 Sandbox manual check: hero phone replies `1`, `1` (Urdu digits), `2` (Arabic-Indic digits), `ok`, and the DC phone sends `APPROVE <code>` against a real FR8 build; record results in the PR
- [ ] T034 [P] `ruff check app/services/messaging app/api/v1/confirmations.py` and `mypy --strict app/services/messaging`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: needs FR14 Phase 2 merged
- **Foundational (Phase 2)**: needs Setup; blocks all stories. T008 before T007 passes
- **US1 (Phase 3)**: needs Phase 2 and FR14 T038/T040/T046
- **US2 (Phase 4)**: needs US1's `route_inbound` skeleton (T015); real FR8 only for T033
- **US3 (Phase 5)**: needs T015/T016; can run alongside US2
- **US4 (Phase 6)**: needs T015
- **US5 (Phase 7)**: needs Phase 2 only (service + API); can run in parallel with US1–US4
- **US6 (Phase 8)**: needs Phase 2
- **Polish**: last

### Cross-FR dependencies

- `014-fr14-whatsapp-alerts`: signature, router, `send_reply`, outbox fields
- `008-fr08-dc-action-approval`: real handler for T033
- `019-fr19-donor-report` and `017-fr17-payment-tracking` consume T027

### Within each story

- Tests first and failing; parser before router; router before API

## Parallel opportunities

- T002, T003, T004 in parallel; T005, T006 in parallel; T009, T010, T012 in parallel
- US5 (`confirmations.py` service + API) in parallel with US1–US4 (`inbound.py`)
- US6 in parallel with US2–US4

```bash
Task: "T005 Unit test parse_reply in tests/unit/messaging/test_reply_parser.py"
Task: "T006 Unit test is_command in tests/unit/messaging/test_command_detection.py"
Task: "T025 Integration test receipt_counts in tests/integration/messaging/test_receipt_status.py"
```

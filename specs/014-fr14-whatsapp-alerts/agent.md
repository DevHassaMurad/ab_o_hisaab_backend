# Agent Brief: FR14 WhatsApp alerts in Urdu and English

## Owner role
Backend & alerts.

## Mission
Turn every system event that a person must know about (a tier change, an approval request, an escalation, a payment) into a short WhatsApp message, Urdu first and then English, and hand it to Twilio within 5 minutes (NFR2). Track each message through Queued, Sent, Delivered, Read or Failed, flag invalid numbers (UC10-1a), and audit every step. This FR replaces the foundation's logging-only `AlertService` stub with a real DB-backed outbox and send worker.

## Scope
**In (MVP)**
- `AlertService.enqueue(...)` implementation (`DbAlertService`) with deterministic `dedupe_key` idempotency
- The message template catalogue (`catalogue.yaml`): every purpose, Urdu first then English, low-literacy wording, simulated-money label
- Fan-out helpers the other FRs call: `notify_tier_change`, `notify_beneficiary_tier_alerts`, `notify_payment_paid`, `notify_approval_request`, `notify_escalation`, `notify_decision`, `send_reply` (used by FR15)
- The send worker (APScheduler interval job + an "after commit" kick), retries with backoff, priority ordering
- Sender adapters: `TwilioWhatsAppSender` and `DryRunSender`, with a safety allowlist so synthetic numbers never get a real message
- Twilio status callback `POST /api/v1/webhooks/whatsapp/status` with signature validation, monotonic status updates, error-code classification
- Invalid-number flagging on the beneficiary (`phone_valid=false`)
- Read APIs: `GET /alerts`, `GET /alerts/{id}`, `GET /alerts/stats`, `GET /alerts/templates`; admin `POST /alerts/test`; `POST /alerts/{id}/resend`
- Twilio WhatsApp sandbox setup guide and a demo-phone setup helper
- The production note: business-initiated messages outside the 24-hour window need pre-approved WhatsApp templates (Twilio Content API `content_sid`)

**Out**
- Inbound replies, confirmations and DC command routing: `015-fr15-receipt-confirmation`
- SMS fallback and field-agent tasks: `016-fr16-sms-field-fallback` (Roadmap)
- Deciding *when* to alert: `007-fr07-auto-tier-release` (tier changes), `008-fr08-dc-action-approval` (approval request, escalation, decision), `017-fr17-payment-tracking` (Paid). They call the helpers here.
- Outcome survey content and logic: `022-fr22-outcome-reports` (Roadmap). FR14 only reserves the `OUTCOME_SURVEY` template key and refuses it unless flag `FR22_OUTCOME` is on.
- Roadmap: getting WhatsApp templates approved by Meta for a production sender, a WhatsApp Business sender in Pakistan, quick-reply buttons.

## Read first
- `.specify/memory/constitution.md` (Principles III, VI, VII, VIII, IX, X; Quality Standards "Alert latency", "Language", "Idempotency")
- `specs/_authoring-guide.md` (FR14 facts, shared rules)
- `specs/000-foundation/data-model.md` §7 `Alert`, §5 `Beneficiary`, §8 `User`
- `specs/000-foundation/plan.md` (`AlertService` signature, API conventions, webhooks under `/api/v1/webhooks/*`)
- `specs/000-foundation/implement.md` (`normalise_pk_phone`, `phone_hash`, `use_scenario`, `deps.py` providers)
- `specs/020-fr20-audit-log/agent.md` (hand-off notes: no PII or free text in audit payloads)
- This folder: `spec.md`, `clarify.md`, `plan.md`

## Depends on / Blocks
| Direction | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | Models, `get_db`, `require_role`, scheduler, flags, `phone_hash`, `Clock`, `deps.py` |
| Depends on | 020-fr20-audit-log | Every queue, send and status change is audited |
| Soft | 010-fr10-beneficiary-register | Beneficiary phones and `phone_valid`; tests use factories until then |
| Blocks | 007-fr07-auto-tier-release | Calls `notify_tier_change` and `notify_beneficiary_tier_alerts` |
| Blocks | 008-fr08-dc-action-approval | Calls `notify_approval_request`, `notify_escalation`, `notify_decision` |
| Blocks | 017-fr17-payment-tracking | Calls `notify_payment_paid` on Paid (UC08-5) |
| Blocks | 015-fr15-receipt-confirmation | Uses `send_reply`, the sender, the signature dependency and the webhooks router |
| Blocks | 016-fr16-sms-field-fallback | Reuses the outbox, worker and `fallback_of_id` chain |
| Blocks | 022-fr22-outcome-reports | Uses the `OUTCOME_SURVEY` purpose and worker |

## Interfaces
**Provides**
- `AlertService` Protocol from `app/services/interfaces.py`: `enqueue(db, *, recipient, purpose, template_key, context: dict, related: dict) -> Alert`. Implemented by `DbAlertService` in `app/services/messaging/alert_service.py`, wired in `app/core/deps.py:get_alerts()`.
- Helpers in `app/services/messaging/notify.py`:
  - `notify_tier_change(db, *, event, releases: list[FundRelease], risk_index, direction: Literal["UP","DOWN","CLOSED"], triggered_at) -> list[Alert]`
  - `notify_beneficiary_tier_alerts(db, *, event, releases: list[FundRelease], risk_index, triggered_at) -> list[Alert]`
  - `notify_payment_paid(db, *, payment) -> Alert | None`
  - `notify_approval_request(db, *, approval, code: str, expires_at, issue_no: int, triggered_at) -> list[Alert]`
  - `notify_escalation(db, *, approval, triggered_at, code: str | None = None) -> list[Alert]`
  - `notify_decision(db, *, approval) -> list[Alert]`
  - `send_reply(db, *, recipient, template_key, context, inbound_sid, related=None) -> Alert` (REPLY_ACK, used by FR15)
- `require_twilio_signature` FastAPI dependency in `app/services/messaging/signature.py` (used by FR15 and FR16 webhooks)
- `Sender` protocol and `get_sender(channel)` in `app/services/messaging/senders.py` (FR16 registers the SMS sender)
- `classify_twilio_error(code) -> ErrorClass` in `app/services/messaging/errors.py`

**Consumes**
- `AuditService.record` (FR20), `Clock` (foundation / FR5 `ReplayClock`), `current_scenario()` and `use_scenario`, `register_job` / `run_now` from `app/core/scheduler.py`, `flags.is_enabled(...)`, `normalise_pk_phone`, `phone_hash`.

## Files you own
- `app/integrations/twilio_client.py`
- `app/services/messaging/__init__.py`, `config.py`, `alert_service.py`, `notify.py`, `templates.py`, `templates/catalogue.yaml`, `senders.py`, `worker.py`, `status.py`, `errors.py`, `recipients.py`, `signature.py`, `dedupe.py`, `demo_setup.py`
- `app/schemas/alerts.py`
- `app/api/v1/alerts.py`
- `app/api/v1/webhooks.py` (creates the router and the status route; FR15 adds the inbound route and FR16 the SMS route in clearly marked sections)
- `alembic/versions/<rev>_fr14_alert_outbox.py` (only after the foundation owner merges the proposed fields)
- `tests/unit/messaging/test_templates.py`, `test_catalogue_lint.py`, `test_dedupe.py`, `test_error_classification.py`, `test_status_ordering.py`, `test_allowlist.py`
- `tests/integration/messaging/conftest.py`, `test_enqueue.py`, `test_worker.py`, `test_nfr2_latency.py`, `test_status_callback.py`, `test_invalid_number.py`, `test_notify_helpers.py`
- `tests/contract/test_alerts_api.py`, `tests/contract/test_webhook_signature.py`
- One line in `app/core/deps.py`: `get_alerts()` returns `DbAlertService`

## Guardrails
| Principle | How it applies here |
|---|---|
| III Traceability | `enqueue`, send, status change and phone flag each call `AuditService.record` in the same transaction. Payloads hold ids, enums, template keys and SHA-256 of the message text, never phone numbers or text. |
| VI Privacy | Templates never contain name, CNIC, wallet id or phone. The alerts API returns a masked phone (`+92300****567`) to officers and DCs only; DCs see only their district. No donor access. |
| VII Degrade gracefully | Twilio down → retries, then `FAILED` with an error code, visible in stats. A failed DC message never blocks the approval: the dashboard path (FR8) still works. |
| VIII Honest prototype | Every message about simulated money carries the "آزمائشی / DEMO: not real money" line. Dry-run sends set `Alert.simulated=true`. Synthetic numbers never get a real send. |
| IX Config over code | Template text lives in `catalogue.yaml`. Timings, batch sizes, allowlist and Twilio numbers live in settings. No district names in code. |
| X Simplicity | The `alert` table is the queue. No broker. One in-process worker with a small thread pool. |
| XI Tests | Tests first for idempotent enqueue, the worker state transitions, the NFR2 latency budget and the signature check. |
| NFR2 | `sent_at − triggered_at ≤ 300 s`. Officials' messages go first (priority 1). |
| NFR10 | Urdu block first, then English. A lint test caps line count and word count. |

## Definition of Done
- [ ] All tests listed above written first, seen failing, now green
- [ ] `get_alerts()` returns `DbAlertService`; the foundation stub is no longer used
- [ ] Every template key in `catalogue.yaml` renders in Urdu then English with sample context; lint test passes (no missing variables, Urdu first, ≤ 6 lines per language for beneficiary templates)
- [ ] A burst of 5 000 queued beneficiary alerts plus 2 DC alerts is fully sent within 300 s with the dry-run sender (simulated 50 ms latency); DC alerts within 60 s (test)
- [ ] Re-running the same trigger produces zero new alerts (dedupe test)
- [ ] Status callbacks: out-of-order `delivered` then `sent` leaves `DELIVERED`; a bad signature returns 403 and changes nothing (tests)
- [ ] Error 21211 on a beneficiary sets `phone_valid=false` and writes `BENEFICIARY_PHONE_FLAGGED` (test)
- [ ] Real send works end to end on the Twilio sandbox for the 4 demo phones (manual, checklist in implement.md)
- [ ] `ruff` and `mypy --strict app/services/messaging` clean

## Hand-off notes
- **007**: after a tier fires and allocation is saved, call `notify_tier_change(direction="UP")` once with all releases fired in that evaluation, then `notify_beneficiary_tier_alerts`. For Action (pending approval) the beneficiary alert is safety-only. Pass `triggered_at` = the moment the tier fired (`FundRelease.created_at`). On a tier fall call `direction="DOWN"`; on event close `direction="CLOSED"`.
- **008**: call `notify_approval_request` with the plain 6-digit code right after you hash it; never store the code anywhere else. Bump `issue_no` when you reissue a code. Call `notify_escalation` from your 2 h timer and `notify_decision` after APPROVED or REJECTED. FR15 renders your `DcCommandResult` back to the DC, so do not send that reply yourself.
- **017**: call `notify_payment_paid(db, payment=p)` in the same transaction that sets `PAID`. It is idempotent per payment.
- **015**: use `send_reply(...)` for every acknowledgement so it is queued, audited and deduped by the inbound `MessageSid`. Mount your inbound route on the router in `app/api/v1/webhooks.py` and reuse `require_twilio_signature`.
- **016**: register `SmsSender` with `register_sender(Channel.SMS, ...)`. Fallback alerts set `fallback_of_id`. `Alert.expects_reply` tells you which alerts need chasing.
- **022**: enqueue with `purpose="OUTCOME_SURVEY"`, `template_key="outcome_survey.q1"`. Enqueue raises `FeatureDisabled` unless `FR22_OUTCOME` is on.
- **005**: reset must delete or re-key the scenario's `alert` rows, otherwise `dedupe_key` blocks re-sends on the next run (see clarify.md open item 6).

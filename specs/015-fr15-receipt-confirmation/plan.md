# Implementation Plan: FR15 Record a beneficiary's confirmation reply

**Branch**: `015-fr15-receipt-confirmation` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/015-fr15-receipt-confirmation/spec.md`

## Summary

Add `POST /api/v1/webhooks/whatsapp` to the webhooks router created by FR14. Each request is signature-checked, serialised per `MessageSid` with a transaction-scoped advisory lock, and routed: DC commands to FR8's `handle_dc_whatsapp_command`, receipt replies to a parser and `record_confirmation`, everything else to help or silence. Every effect (confirmation + audit + acknowledgement alert) commits in one transaction, which also makes the message id the idempotency record. A receipt-status service gives FR17 and FR19 one definition of "received". A replay-only job writes labelled simulated replies for dry-run notices.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI (form parsing via `python-multipart`), SQLAlchemy 2.x, Alembic, `twilio` (`RequestValidator`, via FR14), PyYAML (corpus fixture), structlog

**Storage**: PostgreSQL 16 (`confirmation`, `alert`, `beneficiary`, `user`)

**Testing**: pytest, httpx TestClient, a signed-form helper (FR14 fixture), a stub FR8 handler, a YAML parser corpus

**Target Platform**: Linux container reachable by Twilio at `PUBLIC_BASE_URL`

**Project Type**: web-service (webhook + REST + one scheduled job)

**Performance Goals**: Webhook p95 < 2 s, p99 < 5 s at 20 concurrent requests. Receipt summary p95 < 500 ms for 50 000 payments (NFR1)

**Constraints**: Exactly-once effect per `MessageSid`. Only DC phones reach FR8. No raw reply text in audit. Empty TwiML response

**Scale/Scope**: Up to a few thousand replies per district per event; a handful of DC commands

## Constitution Check

| Principle | How satisfied | ✅/⚠️ |
|---|---|---|
| II Human authority | Routing gate: only `role = DC`, `is_active`, matching `phone_hash` reaches FR8; officers get `official.not_authorised`; FR15 never writes `Approval` | ✅ |
| III Traceability | `record_confirmation` writes the row and `RECEIPT_*` audit entry together; ack alerts are audited by FR14 | ✅ |
| IV Explainable | Each confirmation keeps the raw reply (encrypted), parsed status and message id | ✅ |
| VI Privacy | Lookup by `phone_hash`; raw reply encrypted (proposed); API restricted to officer/admin/own-district DC; FR19 gets counts only | ✅ |
| VII Degrade gracefully | FR8 failure → rollback + dashboard-fallback reply in a new transaction; parsing failure → `UNPARSEABLE` + help | ✅ |
| VIII Honest prototype | `simulated` on confirmations; `SIMIN-` ids for generated replies; never simulate replies to really-sent alerts | ✅ |
| IX Config over code | Window, keywords, throttle, mix, delays in settings | ✅ |
| X Simplicity | No inbound-message table: confirmation and ack rows double as the processed-message record | ✅ |
| XI Tests | Parser corpus, idempotency (incl. concurrency), routing precedence and signature tests written first | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Advisory lock on `hashtext(MessageSid)` + existence check of `Confirmation.provider_message_id` or `Alert.dedupe_key='ACK:{sid}'` | Concurrent duplicates serialise; the "processed" marker commits with the effect | A separate `InboundMessage` table (new entity, guide forbids); relying on Twilio not to duplicate |
| Append-only confirmations; effective status = latest valid | Every reply is evidence; corrections are natural; one unique key per reply | Upsert one row per alert (a late retry of an old message could revert a newer answer) |
| Empty TwiML + queued acks | Every outbound message is an audited `Alert` with delivery status | TwiML `<Message>` replies (not tracked, not audited) |
| Explicit digit translation table + NFKC + mark stripping | NFKC does not fold `1` (Urdu digits)/`1` (Arabic-Indic digits) to `1`; RLM marks are common in Urdu keyboards | Regex `\d` alone (Python matches Unicode digits, but `int()` and comparisons then vary; explicit is clearer) |
| First-token rule with "no other digit token" | Accepts "1 shukriya"; rejects "12", "1 2" | Whole-message equality only (too strict for real users) |
| Route commands before receipts | The same demo phone may be both DC and hero beneficiary | Separate numbers (not possible on one sandbox) |
| Call FR8 inside FR15's transaction | Decision, ack and idempotency marker commit together | Fire-and-forget to FR8 (could lose the ack or double-apply) |
| Simulated replies via a replay-only job keyed on `SIM-` alerts | Realistic demo numbers, clearly labelled, never touches real phones | Hard-coded "received" numbers in FR19 |

## Data model slice

| Entity | Fields read | Fields written |
|---|---|---|
| `Confirmation` (§7) | all | all, plus proposed fields |
| `Alert` (§7) | `id`, `purpose`, `expects_reply`, `to_phone_hash`, `beneficiary_id`, `related_payment_id`, `sent_at`, `queued_at`, `scenario_id`, `simulated`, `provider_message_id`, `dedupe_key`, `district_id` | (ack alerts via FR14 `send_reply`) |
| `Beneficiary` (§5) | `id`, `phone_hash`, `type`, `tehsil_id` | — |
| `User` (§8) | `id`, `phone_hash`, `role`, `district_id`, `is_active` | — |
| `Payment` (§6) | `id`, `status`, `simulated`, `allocation_id` | — |
| `Allocation`, `FundRelease` (§4–5) | for summary filters by release, district, beneficiary type | — |
| `AuditLog` (§8) | — | via `AuditService.record` |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Change | Type | Notes |
|---|---|---|---|
| Confirmation | `provider_message_id` | str, nullable, **unique** | Inbound `MessageSid` (or `SIMIN-{alert_id}`); null for field-agent entries |
| Confirmation | `scenario_id` | FK ReplayScenario, nullable | Shared rule 2; copied from the answered alert |
| Confirmation | `simulated` | bool 🧪, default false | Simulated reply, or reply about a simulated payment |
| Confirmation | `recorded_by` | FK User, nullable | Set when staff record an outcome (FR16 field agent) |
| Confirmation | `reply_raw` | change to 🔒 encrypted | Free text may contain PII |
| Confirmation | index | `(payment_id, received_at DESC)` and `(alert_id)` | Effective-status queries |
| Relationship | `Alert 1─0..1 Confirmation` → `Alert 1─* Confirmation` | — | Append-only; effective = latest `CONFIRMED`/`NOT_RECEIVED` |
| AuditLog event types | `RECEIPT_CONFIRMED` (exists), `RECEIPT_NOT_RECEIVED`, `RECEIPT_UNPARSEABLE` | — | Register in `app/services/audit/event_types.py` |

FR15 also relies on FR14's proposed `Alert.expects_reply`, `Alert.to_phone_hash`, `Alert.dedupe_key`, `Alert.district_id` and the `REPLY_ACK` purpose.

## Routing algorithm

```text
validate signature (403 on failure)
sid := MessageSid; from := From without "whatsapp:"
BEGIN
  pg_advisory_xact_lock(hashtext(sid))
  if processed(sid): COMMIT; return 200            -- confirmation or ACK alert exists
  try e164 := normalise_pk_phone(from); h := phone_hash(e164)
  except ValueError: log inbound.non_pk_number; COMMIT; return 200   -- foundation hashes PK mobiles only
  text := normalise(Body)
  user := active User with phone_hash = h (DC preferred)
  if is_command(text):
      if user and user.role = DC   -> FR8 handle_dc_whatsapp_command; send_reply(dc.passthrough, text=result.reply_text)
      elif user (officer/admin)    -> send_reply(official.not_authorised)
      else                         -> fall through to beneficiary flow
  bens := Beneficiaries with phone_hash = h
  if bens:
      alert := latest open reply-expecting alert for bens within window
      if alert is OUTCOME_SURVEY and FR22_OUTCOME on -> FR22 handler
      elif alert: parsed := parse_reply(Body, ButtonPayload); record_confirmation(...); ack
      else: send_reply(receipt.no_open_notice)
  elif user: send_reply(dc.help)
  else: log inbound.unknown_sender
COMMIT; return 200 <Response/>
on FR8 exception: ROLLBACK; new txn: send_reply(dc.approval_result.ERROR); return 200
```

## API contracts

OpenAPI tag: **Communication**. Errors use problem JSON.

### `POST /api/v1/webhooks/whatsapp` (Twilio; no JWT)

`application/x-www-form-urlencoded`, header `X-Twilio-Signature`. Fields used (**verify** names): `MessageSid`, `From`, `To`, `Body`, `NumMedia`, `ButtonPayload`, `ButtonText`, `ProfileName` (ignored; PII), `WaId` (ignored).

```text
MessageSid=SM9a1c...&From=whatsapp%3A%2B923001234567&To=whatsapp%3A%2B14155238886&Body=%DB%B1&NumMedia=0
```

Response `200`, `Content-Type: text/xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?><Response/>
```

| Status | Code | When |
|---|---|---|
| 200 | — | Processed, duplicate, unknown sender, FR8 failure (DC told to use dashboard) |
| 400 | `MISSING_FIELD` | No `MessageSid` or `From` |
| 403 | `INVALID_SIGNATURE` | Signature missing or wrong |
| 500 | `INTERNAL` | Database unavailable (logged; Twilio may show an error) |

### `GET /api/v1/confirmations`

Roles: `PDMA_OFFICER`, `ADMIN`, `DC` (own district via `Alert.district_id`). Query: `status`, `channel`, `district_id`, `fund_release_id`, `payment_id`, `beneficiary_id`, `from`, `to`, `simulated`, `page`, `page_size`. Respects `X-Scenario-Id`.

```json
{
  "items": [
    {
      "id": "0c5f6a8e-1d1b-4c1e-8f0e-1b9b4b0f2a77",
      "alert_id": "6a3c0b1e-5d1f-4a57-9a43-0f6f7c1b9a10",
      "payment_id": "9e2b4c11-7a0d-4f6a-b3a5-5c2d9d0e8f31",
      "beneficiary_id": "1b7d4f0a-2c55-4e8b-9f0e-4b2f8f1f6d21",
      "status": "CONFIRMED",
      "channel": "WHATSAPP",
      "reply_preview": "\u06f1",
      "received_at": "2026-11-10T06:05:41Z",
      "provider_message_id": "SM9a1c…",
      "recorded_by": null,
      "simulated": true,
      "scenario_id": null
    }
  ],
  "total": 1, "page": 1, "page_size": 50
}
```

`reply_preview` is the decrypted raw reply cut to 40 characters. Errors: 403 `ROLE_FORBIDDEN`, 403 `DISTRICT_FORBIDDEN`.

### `GET /api/v1/confirmations/summary`

Roles: `PDMA_OFFICER`, `ADMIN`, `DC` (own district). Query: one of `fund_release_id`, `district_id`; optional `beneficiary_type`.

```json
{
  "paid": 10,
  "notified": 10,
  "confirmed": 6,
  "not_received": 1,
  "unparseable_only": 1,
  "no_reply": 2,
  "not_notified": 0,
  "confirmation_rate_pct": "60.00",
  "simulated": true
}
```

Definitions: `paid` = payments with status `PAID` in scope. `notified` = paid payments with at least one `PAYMENT_NOTICE` alert not in `FAILED` (any channel). Each notified payment falls in exactly one of `confirmed`, `not_received`, `unparseable_only`, `no_reply` by effective status. `confirmation_rate_pct = confirmed / notified × 100`, Decimal, 2 places. Errors: 422 `SCOPE_REQUIRED`.

### Service interfaces (for FR17, FR19, FR16)

```python
class ReceiptStatus(StrEnum):
    CONFIRMED = "CONFIRMED"; NOT_RECEIVED = "NOT_RECEIVED"
    UNPARSEABLE_ONLY = "UNPARSEABLE_ONLY"; NO_REPLY = "NO_REPLY"; NOT_NOTIFIED = "NOT_NOTIFIED"

def receipt_status_for_payments(db, payment_ids: Sequence[UUID]) -> dict[UUID, ReceiptStatus]: ...
def receipt_counts(db, *, district_id: UUID | None = None, fund_release_id: UUID | None = None,
                   beneficiary_type: str | None = None) -> ReceiptCounts: ...
def record_confirmation(db, *, alert: Alert, parsed: ParsedReply, channel: str,
                        provider_message_id: str | None, reply_raw: str, simulated: bool,
                        recorded_by: UUID | None = None) -> Confirmation: ...
```

## Scheduled jobs

| Job id | Schedule | What it does |
|---|---|---|
| `confirmations.simulate_replies` | Every 30 s (`SIM_REPLY_INTERVAL_S`) | For alerts with `purpose=PAYMENT_NOTICE`, `simulated=true`, `provider_message_id LIKE 'SIM-%'`, `scenario_id IS NOT NULL` (or any scenario when `SIM_REPLY_LIVE=true`), sent at least `SIM_REPLY_DELAY_S` ago, with no confirmation: pick an outcome from `sha256(alert_id)` and the mix; write a confirmation with `provider_message_id='SIMIN-{alert_id}'`, `simulated=true`, plus audit |

## Project Structure

### Documentation (this feature)

```text
specs/015-fr15-receipt-confirmation/
├── agent.md
├── spec.md
├── clarify.md
├── plan.md
├── tasks.md
└── implement.md
```

### Source Code (repository root)

```text
app/
├── services/messaging/
│   ├── reply_parser.py            # normalise(), parse_reply(), is_command(), ParsedReply
│   ├── inbound.py                 # InboundMessage, route_inbound(), find_open_prompt(), _result_to_ack()
│   ├── confirmations.py           # record_confirmation(), receipt_status_for_payments(), receipt_counts()
│   ├── simulated_replies.py       # simulate_replies() job, register_fr15_jobs()
│   └── templates/catalogue.yaml   # + receipt.*, dc.approval_result.*, dc.help, official.not_authorised
├── schemas/confirmations.py       # ConfirmationOut, ReceiptSummary
├── api/v1/confirmations.py        # GET /confirmations, GET /confirmations/summary
└── api/v1/webhooks.py             # + "FR15" section: POST /webhooks/whatsapp
alembic/versions/<rev>_fr15_confirmation_fields.py
tests/
├── fixtures/replies_corpus.yaml
├── unit/messaging/test_reply_parser.py, test_command_detection.py
├── integration/messaging/test_inbound_receipt.py, test_inbound_dc_routing.py,
│   test_inbound_idempotency.py, test_receipt_status.py, test_simulated_replies.py
└── contract/test_inbound_webhook.py, test_confirmations_api.py
```

**Structure Decision**: Single project at the repository root. FR15 shares the `messaging` package and the webhooks router with FR14 (same owner role); each FR edits only its marked section of shared files.

## Complexity Tracking

None.

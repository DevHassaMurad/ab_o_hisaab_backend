# Implementation Plan: FR14 WhatsApp alerts in Urdu and English

**Branch**: `014-fr14-whatsapp-alerts` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/014-fr14-whatsapp-alerts/spec.md`

## Summary

Implement the foundation's `AlertService.enqueue` as a **transactional outbox**: each call renders a catalogue template (Urdu first, then English), stores an `Alert` row in `QUEUED` with a deterministic `dedupe_key`, and writes an `ALERT_QUEUED` audit entry, all in the caller's transaction. An in-process send worker drains the outbox in priority order through a `Sender` (Twilio WhatsApp, or dry-run) within the NFR2 budget, retries transient errors, and audits each result. A signed Twilio status callback moves alerts forward to `DELIVERED`, `READ` or `FAILED` and flags invalid beneficiary numbers (UC10-1a). Helper functions give FR7, FR8 and FR17 one call per event.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, APScheduler, `twilio` (Python helper library, pin the current 9.x release; **verify** version), PyYAML, structlog

**Storage**: PostgreSQL 16. The `alert` table is the queue (partial index on `status='QUEUED'`)

**Testing**: pytest, pytest-asyncio, httpx TestClient, factory fixtures, a `RecordingSender` fake, `freezegun`-style control through the foundation `Clock`

**Target Platform**: Linux container; Twilio must reach `PUBLIC_BASE_URL` over HTTPS (ngrok for local dev)

**Project Type**: web-service (REST + webhooks + scheduled jobs)

**Performance Goals**: `sent_at − triggered_at ≤ 300 s` for all alerts, ≤ 60 s for officials (NFR2). `GET /alerts` p95 < 500 ms (NFR1). Worker throughput ≥ 50 messages/s with the dry-run sender

**Constraints**: Urdu first (NFR10). No PII in message text or audit payloads (NFR6, NFR8). No real send to synthetic numbers (NFR13). Idempotent enqueue and callbacks

**Scale/Scope**: Pilot districts hold up to about 5 000 beneficiaries each; up to 50 000 alerts per replay

## Constitution Check

| Principle | How satisfied | ✅/⚠️ |
|---|---|---|
| I Anticipate | Tier alerts go out at fire time; Action safety advice is not held for approval | ✅ |
| II Human authority | FR14 only carries the approval request; it never approves. Officers are told "only the DC can approve" | ✅ |
| III Traceability | `ALERT_QUEUED`, `ALERT_SENT`, `ALERT_STATUS_CHANGED`, `ALERT_FAILED`, `BENEFICIARY_PHONE_FLAGGED` recorded in the same transaction as each change | ✅ |
| IV Explainable | Approval request includes the stored template explanation (D2) | ✅ |
| V Fair | Every allocated beneficiary (funded or not) gets the same safety advice | ✅ |
| VI Privacy | No PII in templates; masked phone in API; DC scoped by `Alert.district_id`; no donor access; phone decrypted only inside the sender | ✅ |
| VII Degrade gracefully | Retries then visible `FAILED` with error class; stats show NFR2 breaches; dashboard approval path unaffected | ✅ |
| VIII Honest prototype | `line.demo` on simulated money; dry-run sends `simulated=true`; allowlist gate | ✅ |
| IX Config over code | Catalogue YAML; settings for timings, numbers, allowlist, content SIDs | ✅ |
| X Simplicity | DB outbox + APScheduler + a small thread pool; no broker | ✅ |
| XI Tests | Tests first: dedupe, worker transitions, latency budget, signature, status ordering, invalid-number flag | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| DB outbox (`alert` rows in `QUEUED`) drained by an APScheduler job plus an after-commit kick | Alert and business change commit together; nothing is lost if the process restarts; fits Principle X | Celery/RQ + Redis (new infra); sending inside the caller's transaction (slow, and a rollback would leave a sent message) |
| Claim rows with `SELECT … FOR UPDATE SKIP LOCKED`, one short transaction per alert | Safe if two workers ever run (e.g. `--reload`); a slow provider call never blocks other rows | A `SENDING` status (needs an enum change and a stuck-row sweeper) |
| Twilio Python library (`twilio.rest.Client`, `twilio.request_validator.RequestValidator`) | Official signature validation and typed errors (`TwilioRestException.code`) | Hand-rolled HMAC (easy to get the URL/param rules wrong) |
| Per-message `status_callback` parameter | Works for sandbox, a production sender and a Messaging Service alike | Relying only on the sandbox's console setting |
| Free-form `body` in the sandbox; `content_sid` + `content_variables` when `TWILIO_CONTENT_SIDS` maps the key | Production business-initiated WhatsApp messages need pre-approved templates; the same catalogue key drives both | Two catalogues |
| Templates in YAML rendered with `str.format_map` over a strict dict | Text is data (Principle IX); missing variables fail loudly in tests | Jinja2 (more power than needed, harder to lint word counts) |
| Deterministic `dedupe_key` + unique index + `INSERT … ON CONFLICT DO NOTHING` | Race-free idempotency across re-runs and concurrent callers | Check-then-insert (races) |
| Thread pool (`ALERT_WORKER_CONCURRENCY=8`) with one DB session per thread | The Twilio SDK is synchronous; 8 threads × ~200 ms ≈ 40 msg/s live, far more in dry-run | asyncio rewrite of the SDK calls |
| Priority column (1 officials, 2 beneficiary tier alerts, 3 payment notices, 4 acks, 5 other) | Officials' approval request must never wait behind 5 000 beneficiary messages | FIFO only |
| `MESSAGING_MODE=dry_run` default + allowlist | Synthetic register numbers may belong to real strangers | Trusting test data |
| Sender registry by channel (`WHATSAPP`, `SMS`) | FR16 plugs its SMS sender in without touching the worker | `if channel == …` in the worker |

## Data model slice

| Entity | Fields read | Fields written |
|---|---|---|
| `Alert` (§7) | all | all, including the proposed fields below |
| `Beneficiary` (§5) | `phone` 🔒, `phone_hash`, `phone_valid`, `type`, `wallet_provider`, `tehsil_id`, `simulated` | `phone_valid` (UC10-1a), proposed `phone_flagged_at`, `phone_flag_reason` |
| `User` (§8) | `phone` 🔒, `phone_hash`, `role`, `district_id`, `is_active`, `full_name` (never in text) | — |
| `District` / `Tehsil` (§1) | `name`, proposed `name_ur` | — |
| `TriggerEvent`, `FundRelease`, `TriggerTier` (§4) | tier, amounts, `release_pct`, `purpose`, `status`, `simulated`, `created_at` | — |
| `RiskIndex` (§3) | `score`, `peak_lead_day`, `explanation_en`, `explanation_ur` | — |
| `Allocation` (§5) | `status`, `final_amount_pkr`, `beneficiary_id` | — |
| `Payment` (§6) | `id`, `amount_pkr`, `paid_at`, `simulated`, `beneficiary_id` | — |
| `Approval` (§4) | `id`, `requested_at`, `decision`, `reason`, `fund_release_id` | — |
| `AuditLog` (§8) | — | via `AuditService.record` |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

For the foundation owner to merge into `000-foundation/data-model.md`. Until merged, FR14 keeps these columns in its own Alembic revision.

| Entity | Field | Type | Notes |
|---|---|---|---|
| Alert | `dedupe_key` | str, not null | Unique with scenario: `UNIQUE (dedupe_key, COALESCE(scenario_id, '00000000-0000-0000-0000-000000000000'))` |
| Alert | `priority` | smallint, default 5 | 1 officials … 5 other |
| Alert | `triggered_at` | timestamptz, nullable | NFR2 anchor (`sent_at − triggered_at`) |
| Alert | `attempt_count` | int, default 0 | Send attempts so far |
| Alert | `next_attempt_at` | timestamptz, nullable | Backoff; worker picks rows where it is ≤ now |
| Alert | `error_code` | str, nullable | Twilio code as text, or `NO_PHONE`, `PHONE_INVALID`, `NO_RECIPIENT`, `PROVIDER_UNAVAILABLE`, `TEMPLATE_REQUIRED` |
| Alert | `error_detail` | text, nullable | Provider message, no PII |
| Alert | `delivered_at`, `read_at`, `failed_at` | timestamptz, nullable | Status timeline |
| Alert | `expects_reply` | bool, default false | True for `PAYMENT_NOTICE`, `OUTCOME_SURVEY` (FR15/FR16 use it) |
| Alert | `to_phone_hash` | str, nullable, indexed | Hash of the number actually used; FR15 matches replies with it |
| Alert | `district_id` | FK District, nullable, indexed | DC scoping without joins |
| Alert | `content_sid` | str, nullable | WhatsApp template used (production) |
| Alert | `resend_of_id` | FK Alert, nullable | Manual re-send link (US6) |
| Alert | `note` | text, nullable | Operator note on a manual re-send; never in audit payloads |
| Alert | `simulated` | bool 🧪, default false | Dry-run send or simulated subject (NFR13) |
| Alert | `purpose` enum | + `REPLY_ACK`, `DECISION_NOTICE` | Acks (FR15) and PDMA decision notices (UC07-3a) |
| Alert | index | `ix_alert_queue (priority, queued_at) WHERE status = 'QUEUED'` | Worker scan |
| Beneficiary | `phone_flagged_at` | timestamptz, nullable | Set with `phone_valid=false` (UC10-1a) |
| Beneficiary | `phone_flag_reason` | str, nullable | e.g. `TWILIO_21211` |
| District | `name_ur` | str, nullable | Urdu name for message text; seed values in implement.md |

## Dedupe keys

| Purpose / template | `dedupe_key` |
|---|---|
| Beneficiary tier alert | `TIER:{event_id}:{highest_tier}:B:{beneficiary_id}` |
| DC tier change up | `TIERUP:{event_id}:{highest_tier}:U:{user_id}` |
| DC tier change down | `TIERDOWN:{event_id}:{risk_index_id}:U:{user_id}` |
| DC event closed | `CLOSED:{event_id}:U:{user_id}` |
| Payment notice | `PAY:{payment_id}` |
| Approval request | `APPROVAL:{approval_id}:{issue_no}:U:{user_id}` |
| Escalation (officer / DC reminder) | `ESC:{approval_id}:U:{user_id}` |
| Decision notice | `DECISION:{approval_id}:{decision}:U:{user_id}` |
| Reply ack (FR15) | `ACK:{inbound_message_sid}` |
| Test send | `TEST:{uuid4}` (never deduped) |
| Re-send | `RESEND:{original_alert_id}:{n}` |
| SMS fallback (FR16) | `FALLBACK_SMS:{original_alert_id}` |
| Field-agent hand-off (FR16) | `FALLBACK_FIELD:{original_alert_id}` |

A caller may pass `related["dedupe_key"]` to override.

## Status mapping (Twilio → Alert)

| Twilio `MessageStatus` (**verify** list) | Alert status | Rank |
|---|---|---|
| `queued`, `accepted`, `scheduled`, `sending` | no change (stays `SENT` once accepted) | — |
| `sent` | `SENT` | 1 |
| `delivered` | `DELIVERED` | 2 |
| `read` | `READ` | 3 |
| `undelivered`, `failed` | `FAILED` (only from `QUEUED`/`SENT`) | terminal |

## Error classification (`app/services/messaging/errors.py`)

All codes **verify** against Twilio's error dictionary before the demo.

| Code | Meaning | Class | Effect |
|---|---|---|---|
| 21211 | Invalid `To` number | `INVALID_NUMBER` | `FAILED`; flag beneficiary |
| 21614 | `To` is not a mobile number | `INVALID_NUMBER` | `FAILED`; flag beneficiary |
| 30005 | Unknown destination handset | `INVALID_NUMBER` | `FAILED`; flag beneficiary |
| 30006 | Landline or unreachable carrier | `INVALID_NUMBER` | `FAILED`; flag beneficiary |
| 63003 | Channel could not find the `To` address (not on WhatsApp) | `NOT_ON_CHANNEL` | `FAILED`; no flag; FR16 may SMS |
| 63015 | Sandbox: recipient has not joined | `CONFIG` | `FAILED`; no flag; admin warning |
| 63016 | Outside the 24 h window; template required | `TEMPLATE_REQUIRED` | `FAILED`; no flag; admin warning |
| 63018, 20429 | Rate limit | `TRANSIENT` | Retry with backoff |
| HTTP 5xx, timeout, connection error | Provider unavailable | `TRANSIENT` | Retry with backoff |
| 30003, 30007, 30008, others | Unreachable, filtered, unknown | `PERMANENT` | `FAILED`; no flag |

## API contracts

All under `/api/v1`, problem-JSON errors per foundation conventions. OpenAPI tag: **Communication**.

### `POST /webhooks/whatsapp/status` (Twilio; no JWT)

Twilio status callback. `application/x-www-form-urlencoded`. Header `X-Twilio-Signature` required.

Request (form fields, **verify** names):

```text
MessageSid=SM1f0e...&MessageStatus=delivered&ErrorCode=&AccountSid=AC...&From=whatsapp%3A%2B14155238886&To=whatsapp%3A%2B923001234567&ChannelPrefix=whatsapp
```

Responses:

| Status | When |
|---|---|
| 204 | Processed, duplicate, older status, or unknown `MessageSid` |
| 400 `MISSING_FIELD` | No `MessageSid` or `MessageStatus` |
| 403 `INVALID_SIGNATURE` | Signature missing or wrong |

### `GET /alerts`

Roles: `PDMA_OFFICER`, `ADMIN`, `DC` (own district only). Query: `purpose`, `status`, `channel`, `recipient_type`, `district_id`, `fund_release_id`, `payment_id`, `approval_id`, `from`, `to`, `page`, `page_size` (≤ 200). Respects `X-Scenario-Id`.

```json
{
  "items": [
    {
      "id": "6a3c0b1e-5d1f-4a57-9a43-0f6f7c1b9a10",
      "recipient_type": "BENEFICIARY",
      "beneficiary_id": "1b7d4f0a-2c55-4e8b-9f0e-4b2f8f1f6d21",
      "user_id": null,
      "recipient_phone_masked": "+92300****567",
      "district_id": "2f9c...",
      "purpose": "PAYMENT_NOTICE",
      "channel": "WHATSAPP",
      "language": "UR_EN",
      "template_key": "payment_notice",
      "message_text": "[Urdu text]",
      "status": "DELIVERED",
      "error_code": null,
      "provider_message_id": "SM1f0e…",
      "triggered_at": "2026-11-10T06:02:11Z",
      "queued_at": "2026-11-10T06:02:11Z",
      "sent_at": "2026-11-10T06:02:14Z",
      "delivered_at": "2026-11-10T06:02:19Z",
      "read_at": null,
      "latency_s": 3,
      "attempt_count": 1,
      "expects_reply": true,
      "related_payment_id": "9e2b…",
      "fund_release_id": "c1d0…",
      "approval_id": null,
      "fallback_of_id": null,
      "simulated": true,
      "scenario_id": null
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 50
}
```

Errors: 403 `ROLE_FORBIDDEN`, 403 `DISTRICT_FORBIDDEN`.

### `GET /alerts/{alert_id}`

Same roles and item shape. 404 `ALERT_NOT_FOUND`; 403 `DISTRICT_FORBIDDEN`.

### `GET /alerts/stats`

Roles: `PDMA_OFFICER`, `ADMIN`, `DC` (own district). Query: `fund_release_id` or `district_id`, `from`, `to`.

```json
{
  "total": 1206,
  "by_status": {"QUEUED": 0, "SENT": 40, "DELIVERED": 1100, "READ": 50, "FAILED": 16},
  "by_purpose": {"TIER_ALERT": 602, "PAYMENT_NOTICE": 600, "APPROVAL_REQUEST": 2, "ESCALATION": 0, "REPLY_ACK": 2},
  "failed_by_class": {"INVALID_NUMBER": 9, "NOT_ON_CHANNEL": 5, "CONFIG": 2},
  "latency_s": {"p50": 4, "p95": 41, "max": 88},
  "nfr2_budget_s": 300,
  "nfr2_breaches": 0,
  "simulated": true
}
```

### `GET /alerts/templates`

Roles: `PDMA_OFFICER`, `ADMIN`.

```json
{
  "items": [
    {
      "key": "payment_notice",
      "purpose": "PAYMENT_NOTICE",
      "audience": "BENEFICIARY",
      "expects_reply": true,
      "variables": ["amount", "wallet_ur", "wallet_en", "ref"],
      "content_sid_configured": false,
      "sample": "[Urdu text]"
    }
  ]
}
```

### `POST /alerts/test`

Role: `ADMIN`. Sends `system.test` to a staff user (checks sandbox join and the 24 h window).

```json
{"user_id": "5b0e…"}
```

Response 202 with the alert item. Errors: 404 `USER_NOT_FOUND`; 422 `USER_HAS_NO_PHONE`.

### `POST /alerts/{alert_id}/resend`

Roles: `PDMA_OFFICER`, `DC` (own district). Body `{"note": "Phone corrected by DC office"}` (required). The note is stored on the new alert's proposed `note` field and only its SHA-256 goes into the audit payload. Creates a new `QUEUED` alert with `resend_of_id`, re-rendered against current data.

Response 201 with the new alert. Errors: 409 `ALERT_NOT_FAILED`; 422 `NOTE_REQUIRED`; 422 `PHONE_STILL_INVALID`.

## Scheduled jobs

| Job id | Schedule | What it does |
|---|---|---|
| `alerts.send_worker` | Every `ALERT_WORKER_INTERVAL_S` (10 s), `max_instances=1`, `coalesce=True`; also `run_now` after any commit that queued alerts | Claims up to `ALERT_WORKER_BATCH` (200) due alerts by priority and sends them through the thread pool |
| `alerts.nfr2_watchdog` | Every 60 s | Logs a warning (structlog `alert.nfr2_risk`) for alerts still `QUEUED` more than 240 s after `triggered_at`; surfaced in `/alerts/stats` |

## Twilio WhatsApp sandbox setup (demo)

1. Twilio Console → Messaging → Try it out → Send a WhatsApp message. Note the sandbox number (usually `whatsapp:+14155238886`, **verify**) and the join phrase (`join <two-words>`).
2. Each real demo phone (DC Dadu, DC Buner, PDMA officer, hero shopkeeper) sends the join phrase to the sandbox number from WhatsApp. Sandbox membership expires after a few days (**verify**, about 72 h); re-join the day before the demo.
3. Sandbox settings: "When a message comes in" → `POST {PUBLIC_BASE_URL}/api/v1/webhooks/whatsapp` (FR15). "Status callback URL" → `POST {PUBLIC_BASE_URL}/api/v1/webhooks/whatsapp/status` (FR14 also passes it per message).
4. Local dev: `ngrok http 8000`; set `PUBLIC_BASE_URL` to the https ngrok URL **exactly** (signature validation uses it).
5. Env: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM=whatsapp:+14155238886`, `MESSAGING_MODE=twilio`, `MESSAGING_LIVE_ALLOWLIST=+92300…,+92301…`.
6. Run `python -m app.services.messaging.demo_setup` to write the demo phones onto the seed users and the hero beneficiary from `DEMO_PHONES` (never commit numbers).
7. 24-hour window: free-form business-initiated messages only reach a user who messaged the sender in the last 24 h (**verify** how strictly the sandbox applies this). On demo morning each phone sends "hi" to the sandbox. `POST /alerts/test` confirms each phone.

**Production note**: outside the 24 h customer-service window, WhatsApp only accepts **pre-approved templates**. Each catalogue key becomes a Twilio Content template (category UTILITY; Urdu and English variants or one bilingual body), submitted for WhatsApp approval, and its `HX…` SID goes into `TWILIO_CONTENT_SIDS`. Template variables may not contain newlines or long runs of spaces (**verify**), so multi-line blocks become fixed template text with single-line variables. A Pakistani business sender needs Meta business verification; plan weeks, not days.

## Project Structure

### Documentation (this feature)

```text
specs/014-fr14-whatsapp-alerts/
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
├── integrations/twilio_client.py          # get_twilio_client(), send_whatsapp(), send_sms() thin wrappers
├── services/messaging/
│   ├── __init__.py                        # exports DbAlertService, notify helpers, register_messaging_jobs
│   ├── config.py                          # MessagingSettings (worker, allowlist, content SIDs, budgets)
│   ├── alert_service.py                   # DbAlertService.enqueue (AlertService Protocol)
│   ├── notify.py                          # notify_tier_change, notify_beneficiary_tier_alerts, notify_payment_paid,
│   │                                      # notify_approval_request, notify_escalation, notify_decision, send_reply
│   ├── templates.py                       # Catalogue loader, render(key, ctx) -> RenderedMessage, lint helpers
│   ├── templates/catalogue.yaml           # all text (FR14 keys; FR15 receipt.* and dc.approval_result.*; FR16 sms.*)
│   ├── senders.py                         # Sender Protocol, TwilioWhatsAppSender, DryRunSender, RecordingSender, registry
│   ├── worker.py                          # run_send_worker(), _send_one(alert_id), backoff, kick-after-commit hook
│   ├── status.py                          # apply_status_callback(db, form) -> None
│   ├── errors.py                          # ErrorClass, classify_twilio_error()
│   ├── recipients.py                      # resolve_phone(), dc_users_for_district(), pdma_officers(), mask_phone()
│   ├── signature.py                       # require_twilio_signature dependency
│   ├── dedupe.py                          # dedupe key builders
│   └── demo_setup.py                      # python -m … writes DEMO_PHONES onto seed users and hero beneficiary
├── schemas/alerts.py                      # AlertOut, AlertStats, TemplateOut, TestSendIn, ResendIn
├── api/v1/alerts.py                       # GET /alerts, /alerts/{id}, /alerts/stats, /alerts/templates; POST test, resend
└── api/v1/webhooks.py                     # router; POST /webhooks/whatsapp/status (FR15 and FR16 add routes)
alembic/versions/<rev>_fr14_alert_outbox.py
tests/
├── unit/messaging/test_templates.py, test_catalogue_lint.py, test_dedupe.py,
│   test_error_classification.py, test_status_ordering.py, test_allowlist.py
├── integration/messaging/conftest.py, test_enqueue.py, test_worker.py, test_nfr2_latency.py,
│   test_status_callback.py, test_invalid_number.py, test_notify_helpers.py
└── contract/test_alerts_api.py, test_webhook_signature.py
```

**Structure Decision**: Single project at the repository root, laid out as in the constitution. Messaging logic lives in `app/services/messaging/`; the only provider-specific code is in `app/integrations/twilio_client.py` and `senders.py`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Thread pool inside the worker | 5 000 sequential Twilio calls at ~200 ms each take ~17 min, breaking NFR2 | Sequential send loop |

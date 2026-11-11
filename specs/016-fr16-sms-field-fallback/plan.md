# Implementation Plan: FR16 Fall back to SMS or a field agent when there is no reply

**Branch**: `016-fr16-sms-field-fallback` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/016-fr16-sms-field-fallback/spec.md`

## Summary

A single idempotent sweep, registered in `app/jobs/timers.py` and gated by `FR16_FALLBACK`, scans reply-expecting WhatsApp payment notices. Using FR15's effective receipt status, it queues an SMS fallback alert at `sent_at + 6 h` (through FR14's outbox, `fallback_of_id` set) and opens a `FieldAgentTask` plus a `FIELD_AGENT` hand-off alert at `sent_at + 12 h`. The MVP stub sends SMS through a dry-run sender and offers a read-only task list. The roadmap build adds a real SMS route, inbound SMS replies, task completion and immediate fallbacks by failure class.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.x, Alembic, APScheduler; roadmap: `twilio` SMS or a local aggregator's HTTP API via `httpx`

**Storage**: PostgreSQL 16 (`alert`, `confirmation`, `field_agent_task`, `payment`)

**Testing**: pytest with the foundation `Clock` set to fixed times; FR14 `RecordingSender`; FR15 fixtures

**Target Platform**: Linux container

**Project Type**: web-service (scheduled job + REST; roadmap webhook)

**Performance Goals**: Sweep < 10 s for 50 000 notices; list endpoint p95 < 500 ms

**Constraints**: Flag-gated; idempotent; Urdu SMS ≤ 3 UCS-2 parts; no PII in SMS/brief text or audit payloads

**Scale/Scope**: At most a few thousand open notices per event

## Constitution Check

| Principle | How satisfied | ✅/⚠️ |
|---|---|---|
| I Anticipate | Chasing unreachable people keeps the money-to-person loop short | ✅ |
| III Traceability | SMS alerts audited via FR14 (`ALERT_QUEUED` with `fallback_of_id`); `FIELD_TASK_CREATED`, `FIELD_TASK_DONE` in the same transaction | ✅ |
| VI Privacy | Brief and SMS carry ref, tehsil, amount only; task API under RBAC; DC scoped by `district_id` | ✅ |
| VII Degrade gracefully | This is the fallback path; the sweep catches up after downtime | ✅ |
| VIII Honest prototype | Dry-run SMS (`simulated=true`); tasks for simulated payments `simulated=true` | ✅ |
| IX Config over code | `FALLBACK_SMS_AFTER_H=6`, `FALLBACK_FIELD_AFTER_H=12`, interval, window in settings | ✅ |
| X Simplicity | One sweep job; roadmap behind a flag; MVP stub is minimal | ✅ |
| XI Tests | Fake-clock rule tests and flag-off test first | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Periodic sweep over indexed queries | Restart-safe, idempotent, catches up; matches the constitution's "APScheduler … SMS fallback" | One APScheduler `date` job per alert (lost on restart unless a job store is added) |
| Both deadlines from the WhatsApp `sent_at` | Matches UC10's single clock | 12 h after the SMS (would be 18 h total) |
| SMS alert = `PAYMENT_NOTICE`, `channel=SMS`, `expects_reply=true`, `fallback_of_id=root` | FR15 matches replies to it with no change; stats per channel come free | A separate SMS table |
| Field hand-off = `Alert(channel=FIELD_AGENT)` + `FieldAgentTask` | Completes the documented alert chain; the task holds workflow state | Task only (breaks the `fallback_of` chain) |
| One open task per payment (partial unique index) | Idempotent under concurrent sweeps | Check-then-insert |
| Dry-run SMS in the MVP stub | No PK two-way SMS route is ready; honest labelling | Twilio SMS to synthetic numbers (could reach strangers) |
| Run `simulate_replies` before evaluating in replays | Prevents chasing simulated beneficiaries who would have replied | Ordering by luck of job timing |
| SMS text cap 201 chars (3 × 67 UCS-2) | Cost and readability on basic phones | WhatsApp-length text over SMS (6–10 parts) |

## Data model slice

| Entity | Fields read | Fields written |
|---|---|---|
| `Alert` (§7 + FR14 proposals) | `purpose`, `channel`, `expects_reply`, `status`, `sent_at`, `error_code`, `fallback_of_id`, `related_payment_id`, `beneficiary_id`, `scenario_id`, `simulated`, `district_id` | new SMS and `FIELD_AGENT` alerts via `enqueue` |
| `Confirmation` (§7 + FR15 proposals) | via `receipt_status_for_payments` | roadmap: via `record_confirmation` (`FIELD_AGENT`, `SMS`) |
| `FieldAgentTask` (§7) | all | all, plus proposed fields |
| `Payment` (§6) | `id`, `status`, `amount_pkr`, `simulated` | — |
| `Beneficiary` (§5) | `phone_valid`, `tehsil_id`, `wallet_provider` | — |
| `Tehsil` (§1) | `name` | — |
| `AuditLog` (§8) | — | via `AuditService.record` |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Field | Type | Notes |
|---|---|---|---|
| FieldAgentTask | `scenario_id` | FK ReplayScenario, nullable | Shared rule 2 |
| FieldAgentTask | `district_id` | FK District | DC scoping |
| FieldAgentTask | `source_alert_id` | FK Alert | The root WhatsApp notice |
| FieldAgentTask | `alert_id` | FK Alert, nullable | The `FIELD_AGENT` hand-off alert |
| FieldAgentTask | `reason_code` | enum `NO_REPLY_12H`, `INVALID_PHONE`, `NOT_RECEIVED` | `reason` (str) keeps the English text |
| FieldAgentTask | `due_at` | timestamptz, nullable | Roadmap SLA |
| FieldAgentTask | `outcome` | enum `CONFIRMED`, `NOT_RECEIVED`, `UNREACHABLE`, nullable | Set on completion |
| FieldAgentTask | `note` | text 🔒, nullable | Free text from staff |
| FieldAgentTask | `done_at` | timestamptz, nullable | |
| FieldAgentTask | `done_by` | FK User, nullable | |
| FieldAgentTask | `assigned_to` | FK User, nullable | Roadmap |
| FieldAgentTask | `simulated` | bool 🧪 | Copied from the payment |
| FieldAgentTask | index | `UNIQUE (payment_id, COALESCE(scenario_id, '0…0')) WHERE status = 'OPEN'` | One open task per payment |
| Alert | index | `ix_alert_fallback_scan (purpose, channel, sent_at) WHERE expects_reply AND fallback_of_id IS NULL` | Sweep scan |
| User | role enum | + `FIELD_AGENT` (roadmap only) | Not in the MVP stub |
| AuditLog event types | `FIELD_TASK_CREATED`, `FIELD_TASK_DONE` | — | Register in `event_types.py` |

## Sweep algorithm

```text
if not flag FR16_FALLBACK: return
now := clock.now()
if a replay scenario is active: simulate_replies(db, now)          -- FR15
roots := WhatsApp PAYMENT_NOTICE alerts with expects_reply, fallback_of_id IS NULL,
         status IN (SENT, DELIVERED, READ), sent_at >= now - window, sent_at <= now - 6h
status := receipt_status_for_payments(roots.payment_ids)           -- FR15
for root in roots where status[root.payment] in (NO_REPLY, UNPARSEABLE_ONLY)
                    and payment.status = PAID:
    with use_scenario(root.scenario_id):
        sms := existing SMS alert with fallback_of_id = root.id
        if sms is None and beneficiary.phone_valid:
            sms := enqueue(SMS, sms.payment_notice, fallback_of_id=root.id,
                           dedupe_key="FALLBACK_SMS:{root.id}", triggered_at=root.sent_at + 6h)
        if root.sent_at <= now - 12h and no OPEN task for payment:
            fa := enqueue(FIELD_AGENT, field_task.brief, fallback_of_id=(sms or root).id,
                          dedupe_key="FALLBACK_FIELD:{root.id}")
            insert FieldAgentTask(reason_code=NO_REPLY_12H, source_alert_id=root.id, alert_id=fa.id, ...)
            audit FIELD_TASK_CREATED
-- roadmap: immediate rules by root.error_code class, NOT_RECEIVED tasks, tier-alert SMS
```

## API contracts

OpenAPI tag: **Communication**.

### `GET /api/v1/field-tasks` (MVP stub)

Roles: `PDMA_OFFICER`, `ADMIN`, `DC` (own district). Query: `status`, `reason_code`, `district_id`, `fund_release_id`, `from`, `to`, `page`, `page_size`. Respects `X-Scenario-Id`.

```json
{
  "items": [
    {
      "id": "f3b1c0de-3b9e-4a77-8d7a-2f0a6c1e9b55",
      "payment_id": "9e2b4c11-7a0d-4f6a-b3a5-5c2d9d0e8f31",
      "payment_ref": "9E2B4C11",
      "beneficiary_id": "1b7d4f0a-2c55-4e8b-9f0e-4b2f8f1f6d21",
      "district_id": "2f9c…",
      "tehsil": "Johi",
      "amount_pkr": "15000.00",
      "status": "OPEN",
      "reason_code": "NO_REPLY_12H",
      "reason": "no reply after 12h",
      "brief": "[Urdu text]",
      "source_alert_id": "6a3c…",
      "created_at": "2026-11-10T12:00:03Z",
      "outcome": null,
      "done_at": null,
      "simulated": true,
      "scenario_id": null
    }
  ],
  "total": 1, "page": 1, "page_size": 50
}
```

Errors: 403 `ROLE_FORBIDDEN`, 403 `DISTRICT_FORBIDDEN`, 404 `FEATURE_DISABLED` when the flag is off (the route returns 404 so the frontend hides it).

### `PATCH /api/v1/field-tasks/{task_id}` (roadmap build)

Roles: `PDMA_OFFICER`, `DC` (own district).

```json
{"outcome": "CONFIRMED", "note": "Visited; cashed at JazzCash agent in Johi bazaar"}
```

Response 200 with the task (status `DONE`, `outcome`, `done_at`, `done_by`). Side effects: `CONFIRMED`/`NOT_RECEIVED` → `record_confirmation(channel="FIELD_AGENT", alert=task.alert, recorded_by=user)`; audit `FIELD_TASK_DONE` (payload: task id, outcome, `note_sha256`). Errors: 409 `TASK_NOT_OPEN`, 422 `NOTE_REQUIRED`, 422 `OUTCOME_INVALID`, 403 `DISTRICT_FORBIDDEN`.

### `POST /api/v1/webhooks/sms` (roadmap build; provider-signed)

Twilio form (if Twilio SMS is used): `MessageSid`, `From`, `To`, `Body` (**verify**). Same signature dependency as FR14; same advisory-lock idempotency as FR15. Routing: beneficiary receipt flow only (DC commands stay WhatsApp-only), `channel="SMS"`. Response: empty TwiML. Errors: 403 `INVALID_SIGNATURE`.

## Scheduled jobs

| Job id | Schedule | What it does |
|---|---|---|
| `fr16.fallback_sweep` | Every `FALLBACK_SWEEP_INTERVAL_MIN` (5 min), `max_instances=1`, `coalesce=True`; registered in `app/jobs/timers.py`; returns at once when the flag is off | Runs `run_fallback_sweep(db, now=clock.now())` |

## Project Structure

### Documentation (this feature)

```text
specs/016-fr16-sms-field-fallback/
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
│   ├── fallback.py                  # run_fallback_sweep(), SweepResult, register_fr16_jobs()
│   ├── sms_sender.py                # DryRunSmsSender (stub), TwilioSmsSender (roadmap), segment counter
│   ├── field_tasks.py               # create_field_task(), FieldAgentSender, complete_field_task() (roadmap)
│   └── templates/catalogue.yaml     # + sms.*, field_task.*
├── jobs/timers.py                   # + "FR16" section: register_fr16_jobs(scheduler)
├── schemas/field_tasks.py           # FieldTaskOut, FieldTaskComplete (roadmap)
├── api/v1/field_tasks.py            # GET /field-tasks; PATCH (roadmap)
└── api/v1/webhooks.py               # + "FR16" section: POST /webhooks/sms (roadmap)
alembic/versions/<rev>_fr16_field_agent_task_fields.py
tests/
├── unit/messaging/test_fallback_rules.py, test_sms_segments.py
└── integration/messaging/test_fallback_sweep.py, test_field_tasks_api.py, test_sms_inbound.py
```

**Structure Decision**: Single project. FR16 extends the shared `messaging` package and the shared timer and webhook modules only in marked sections.

## Complexity Tracking

None.

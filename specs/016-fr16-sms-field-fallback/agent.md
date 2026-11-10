# Agent Brief: FR16 Fall back to SMS or a field agent when there is no reply

## Owner role
Backend & alerts.

## Mission
Make sure no beneficiary is silently lost. If a payment notice gets no valid reply within 6 hours, send a short SMS. If there is still no reply after 12 hours, open a field-agent task so a person goes to check (UC10 alternates 2a and 2b). Everything sits behind flag `FR16_FALLBACK`, reuses FR14's outbox and FR15's parser, and is audited.

## Scope
**MVP stub (behind `FR16_FALLBACK`, default off)**
- `fallback_sweep` timer (every 5 min) registered from `app/jobs/timers.py`; a no-op when the flag is off
- 6 h rule: enqueue an SMS `PAYMENT_NOTICE` alert (`channel=SMS`, `fallback_of_id` = the WhatsApp notice) through FR14, sent by the **dry-run** SMS sender
- 12 h rule: create a `FieldAgentTask` (`reason_code=NO_REPLY_12H`) plus a `FIELD_AGENT` channel alert that records the hand-off
- Stop rules: any valid reply (FR15 effective status `CONFIRMED`/`NOT_RECEIVED`), payment `CANCELLED`, or the notice older than the reply window
- `GET /api/v1/field-tasks` (read-only list)
- SMS template texts in the catalogue (`sms.*`, `field_task.brief`)

**Roadmap build**
- Real SMS sender (`TwilioSmsSender`, or a Pakistani aggregator / short code), segment-aware
- `POST /api/v1/webhooks/sms` for SMS replies (reuses FR15 `parse_reply` and `record_confirmation`, `channel=SMS`)
- `PATCH /api/v1/field-tasks/{id}`: complete a task with an outcome (`CONFIRMED`, `NOT_RECEIVED`, `UNREACHABLE`) recorded as a `FIELD_AGENT` confirmation
- Immediate fallbacks: WhatsApp `FAILED` with `NOT_ON_CHANNEL`/`PERMANENT` → SMS at once; `INVALID_NUMBER` → field task at once; FR15 `NOT_RECEIVED` → field task at once
- SMS copies of Warning and Action tier alerts when WhatsApp fails
- A `FIELD_AGENT` role and assignment (`assigned_to`), with a mobile-friendly task view in the frontend

**Out**
- WhatsApp sending and status callbacks (`014-fr14-whatsapp-alerts`); WhatsApp replies (`015-fr15-receipt-confirmation`); payment retry/cancel (`018-fr18-payment-failure-retry`)

## Read first
- `.specify/memory/constitution.md` (Principles III, VII, VIII, IX, X; Technology Stack "APScheduler … SMS fallback")
- `specs/_authoring-guide.md` (FR16 facts; Roadmap rule for tasks/implement)
- `specs/000-foundation/data-model.md` §7 `Alert`, `Confirmation`, `FieldAgentTask`
- `specs/014-fr14-whatsapp-alerts/plan.md` (outbox, `fallback_of_id`, senders registry, dedupe keys)
- `specs/015-fr15-receipt-confirmation/plan.md` (`receipt_status_for_payments`, `record_confirmation`, `parse_reply`)
- This folder: `spec.md`, `clarify.md`, `plan.md`

## Depends on / Blocks
| Direction | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | Flags, scheduler, `Clock`, `use_scenario` |
| Depends on | 020-fr20-audit-log | Tasks and SMS alerts are audited |
| Depends on | 014-fr14-whatsapp-alerts | `enqueue`, worker, sender registry, `Alert.expects_reply`, `error_code` classes |
| Depends on | 015-fr15-receipt-confirmation | Effective receipt status; parser and recorder for SMS and field outcomes |
| Depends on | 017-fr17-payment-tracking | Payment status (`CANCELLED` stops the chain) |
| Shares | 008, 017 | `app/jobs/timers.py` also holds the 2 h escalation (FR8) and 24 h stuck-payment (FR17) timers; each FR edits only its own marked section |
| Blocks | 019-fr19-donor-report | Optional: field-agent confirmations count as "received" |
| Blocks | 022-fr22-outcome-reports | Field agents may later collect outcome reports (`OutcomeReport.channel=FIELD_AGENT`) |

## Interfaces
**Provides**
- `run_fallback_sweep(db, *, now) -> SweepResult` in `app/services/messaging/fallback.py`
- `register_fr16_jobs(scheduler)` called from `app/jobs/timers.py`
- `SmsSender` registered with FR14's `register_sender("SMS", …)` (dry-run in the MVP stub); `FieldAgentSender` registered for `"FIELD_AGENT"` (records the hand-off, no network call)
- `complete_field_task(db, *, task_id, user, outcome, note) -> FieldAgentTask` (roadmap)

**Consumes**
- `AlertService.enqueue` (FR14), `receipt_status_for_payments` / `record_confirmation` / `parse_reply` (FR15), `AuditService.record` (FR20), `flags.is_enabled("FR16_FALLBACK")`, `Clock`, `use_scenario`

## Files you own
- `app/services/messaging/fallback.py`, `sms_sender.py`, `field_tasks.py`
- The "FR16" section of `app/jobs/timers.py` (one `register_fr16_jobs` call and nothing else)
- The `sms.*` and `field_task.*` sections of `app/services/messaging/templates/catalogue.yaml`
- `app/api/v1/field_tasks.py`, `app/schemas/field_tasks.py`
- The "FR16" section of `app/api/v1/webhooks.py` (roadmap: `POST /webhooks/sms`)
- `alembic/versions/<rev>_fr16_field_agent_task_fields.py`
- `tests/unit/messaging/test_fallback_rules.py`, `test_sms_segments.py`
- `tests/integration/messaging/test_fallback_sweep.py`, `test_field_tasks_api.py`, `test_sms_inbound.py` (roadmap)

## Guardrails
| Principle | How it applies here |
|---|---|
| III Traceability | SMS alerts are audited by FR14 (`ALERT_QUEUED` with `fallback_of_id`); `FIELD_TASK_CREATED` and `FIELD_TASK_DONE` are written in the same transaction as the task change |
| VI Privacy | Field-task brief and SMS text carry only the payment reference, tehsil and amount; names and phones are seen in the dashboard under role checks only |
| VII Degrade gracefully | This FR **is** the degrade path for unreachable beneficiaries; the sweep is idempotent and catches up after downtime |
| VIII Honest prototype | MVP stub SMS is dry-run (`simulated=true`); tasks for simulated payments are `simulated=true` |
| IX Config over code | 6 h, 12 h, sweep interval, window, segment cap are settings |
| X Simplicity | One periodic sweep over indexed queries; no per-alert timers |
| XI Tests | Rule tests with a fake clock first; flag-off test proves nothing happens |

## Definition of Done
**MVP stub**
- [ ] With the flag off, the sweep does nothing (test)
- [ ] With the flag on and a fake clock: no reply at 6 h → 1 SMS alert; at 12 h → 1 task and 1 `FIELD_AGENT` alert; a reply at 5 h → nothing; a reply at 7 h → SMS but no task; 3 sweeps → no duplicates (tests)
- [ ] SMS templates fit in 3 UCS-2 segments (≤ 201 characters) (test)
- [ ] `GET /field-tasks` lists open tasks, DC scoped, donor 403 (test)
- [ ] In a `sindh-2022` replay with the flag on, silent simulated beneficiaries show SMS then field tasks on the next day step (manual)

**Roadmap build**
- [ ] Real SMS provider chosen and verified for Pakistani numbers; inbound SMS webhook signed and idempotent
- [ ] Task completion with outcome writes a `FIELD_AGENT` confirmation and audit entries
- [ ] Immediate fallbacks on WhatsApp failure and on `NOT_RECEIVED`

## Hand-off notes
- **014**: keep `Alert.expects_reply`, `fallback_of_id` and the `NOT_ON_CHANNEL`/`INVALID_NUMBER` error classes stable; FR16 keys off them.
- **015**: SMS alerts are `PAYMENT_NOTICE` with `expects_reply=true`, so a WhatsApp reply after the SMS still lands on the latest open notice.
- **008 / 017**: in `app/jobs/timers.py`, add your own `register_*` call in your own section. FR16 adds only `register_fr16_jobs(scheduler)`.
- **005**: with the flag on in a replay, the sweep first runs FR15's `simulate_replies` so simulated beneficiaries are not chased by mistake.
- **019**: field-agent `CONFIRMED` outcomes count as "received" through FR15's effective status (any channel).

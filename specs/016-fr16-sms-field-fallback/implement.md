# Implementation Guide: FR16 SMS and field-agent fallback (Roadmap)

FR16 is a roadmap FR (Principle X). Build **Part A (MVP stub behind `FR16_FALLBACK`)** in the hackathon only if FR14 and FR15 are done and the demo is stable. Part B is the roadmap build.

---

## Part A: MVP stub (behind a flag)

### Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T001–T003 | Settings, modules, SMS and brief texts |
| 2 | T004–T005 (failing) | Segment and rule contracts |
| 3 | T006–T010 | Migration, event types, dry-run SMS sender, field-agent sender, `decide()` |
| 4 | T011–T012 | 6 h SMS rule |
| 5 | T013–T017 | 12 h task rule and the task list |
| 6 | T018–T020 | Flag gate, job registration, replay ordering |
| 7 | T021–T023 | Benchmark, replay check, lint |

### Key algorithms

#### Decision table (`decide`, pure)

| Condition (root WhatsApp payment notice) | Action |
|---|---|
| Flag off | none |
| Payment not `PAID`, or effective receipt `CONFIRMED`/`NOT_RECEIVED`, or `sent_at` older than window | none |
| `now ≥ sent_at + 6 h`, no SMS for root, beneficiary `phone_valid` | `QUEUE_SMS` |
| `now ≥ sent_at + 12 h`, no open task for payment | `OPEN_TASK` (reason `NO_REPLY_12H`) |
| (roadmap) root `FAILED`, class `NOT_ON_CHANNEL` or `PERMANENT`, no SMS | `QUEUE_SMS` now |
| (roadmap) root `FAILED`, class `INVALID_NUMBER`, no open task | `OPEN_TASK` (reason `INVALID_PHONE`) now |
| (roadmap) effective receipt `NOT_RECEIVED`, no open task | `OPEN_TASK` (reason `NOT_RECEIVED`) |

```python
class Action(StrEnum):
    QUEUE_SMS = "QUEUE_SMS"; OPEN_TASK = "OPEN_TASK"

def decide(*, sent_at: datetime | None, root_status: str, error_class: str | None,
           receipt: ReceiptStatus, payment_status: str, phone_valid: bool,
           has_sms: bool, has_open_task: bool, now: datetime, s: MessagingSettings,
           roadmap: bool = False) -> set[Action]:
    if payment_status != "PAID" or receipt == ReceiptStatus.CONFIRMED:
        return set()
    if receipt == ReceiptStatus.NOT_RECEIVED:                 # a valid reply stops the timed chain
        return {Action.OPEN_TASK} if roadmap and not has_open_task else set()
    out: set[Action] = set()
    if sent_at is not None and root_status in ("SENT", "DELIVERED", "READ"):
        if sent_at < now - timedelta(days=s.FALLBACK_WINDOW_DAYS):
            return set()
        if now >= sent_at + timedelta(hours=s.FALLBACK_SMS_AFTER_H) and not has_sms and phone_valid:
            out.add(Action.QUEUE_SMS)
        if now >= sent_at + timedelta(hours=s.FALLBACK_FIELD_AFTER_H) and not has_open_task:
            out.add(Action.OPEN_TASK)
    elif roadmap and root_status == "FAILED":
        if error_class in ("NOT_ON_CHANNEL", "PERMANENT") and not has_sms and phone_valid:
            out.add(Action.QUEUE_SMS)
        if error_class == "INVALID_NUMBER" and not has_open_task:
            out.add(Action.OPEN_TASK)
    return out
```

#### Sweep (`fallback.py`)

```python
def run_fallback_sweep(db: Session, *, now: datetime) -> SweepResult:
    if not flags.is_enabled("FR16_FALLBACK"):
        return SweepResult.disabled()
    s = messaging_settings()
    if current_scenario() is not None:
        simulate_replies(db, now=now)                          # FR15: let simulated repliers reply first
    since = now - timedelta(days=s.FALLBACK_WINDOW_DAYS)
    roots = db.scalars(
        select(Alert).where(
            Alert.purpose == "PAYMENT_NOTICE", Alert.channel == "WHATSAPP",
            Alert.expects_reply.is_(True), Alert.fallback_of_id.is_(None),
            Alert.status.in_(("SENT", "DELIVERED", "READ")),
            Alert.sent_at >= since,
            Alert.sent_at <= now - timedelta(hours=s.FALLBACK_SMS_AFTER_H))
    ).all()
    receipts = receipt_status_for_payments(db, [r.related_payment_id for r in roots])   # FR15, one query
    payments = load_payments(db, roots); bens = load_beneficiaries(db, roots)
    sms_by_root = existing_children(db, roots, channel="SMS")
    open_tasks = open_task_payment_ids(db, roots)
    result = SweepResult()
    for root in roots:
        acts = decide(sent_at=root.sent_at, root_status=root.status, error_class=None,
                      receipt=receipts[root.related_payment_id],
                      payment_status=payments[root.related_payment_id].status,
                      phone_valid=bens[root.beneficiary_id].phone_valid,
                      has_sms=root.id in sms_by_root,
                      has_open_task=root.related_payment_id in open_tasks, now=now, s=s)
        if not acts:
            continue
        with use_scenario(root.scenario_id):
            sms = sms_by_root.get(root.id)
            if Action.QUEUE_SMS in acts:
                sms = alerts.enqueue(db, recipient=bens[root.beneficiary_id], purpose="PAYMENT_NOTICE",
                                     template_key="sms.payment_notice",
                                     context=payment_ctx(payments[root.related_payment_id], bens[root.beneficiary_id]),
                                     related={"channel": "SMS", "payment_id": root.related_payment_id,
                                              "fallback_of_id": root.id, "district_id": root.district_id,
                                              "dedupe_key": f"FALLBACK_SMS:{root.id}",
                                              "triggered_at": root.sent_at + timedelta(hours=s.FALLBACK_SMS_AFTER_H),
                                              "simulated": root.simulated})
                result.sms += 1
            if Action.OPEN_TASK in acts:
                create_field_task(db, root=root, payment=payments[root.related_payment_id],
                                  reason_code="NO_REPLY_12H", via_alert=sms or root)
                result.tasks += 1
        db.commit()                                            # one short transaction per root
    return result
```

`create_field_task` enqueues the `FIELD_AGENT` alert (template `field_task.brief`, dedupe `FALLBACK_FIELD:{root.id}`, `fallback_of_id = via_alert.id`), inserts the task with `ON CONFLICT DO NOTHING` on the one-open-task index, and writes `FIELD_TASK_CREATED` (payload: task id, payment id, reason code, simulated).

Scenario handling: the scheduler runs the sweep in live context; roots from replays carry their own `scenario_id` and the loop sets it with `use_scenario`. For the replay `simulate_replies` call, iterate the distinct `scenario_id`s among candidate roots and call it inside each.

#### SMS segments (`sms_sender.py`)

```python
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")

def segments(text: str) -> int:
    if all(ch in GSM7 for ch in text):
        return 1 if len(text) <= 160 else math.ceil(len(text) / 153)
    n = len(text.encode("utf-16-le")) // 2                    # UCS-2 code units
    return 1 if n <= 70 else math.ceil(n / 67)
```

### Configuration (stub)

| Key | Default | Notes |
|---|---|---|
| Flag `FR16_FALLBACK` | off | `FLAGS=FR16_FALLBACK` to enable |
| `FALLBACK_SMS_AFTER_H` | 6 | UC10-2a |
| `FALLBACK_FIELD_AFTER_H` | 12 | UC10-2b |
| `FALLBACK_SWEEP_INTERVAL_MIN` | 5 | |
| `FALLBACK_WINDOW_DAYS` | 7 | Same as FR15's reply window |
| `SMS_MODE` | `dry_run` | Stub never sends real SMS |
| `SMS_MAX_SEGMENTS` | 3 | Lint and runtime check |

### Commands (stub)

```bash
alembic upgrade head
pytest -q tests/unit/messaging/test_sms_segments.py tests/unit/messaging/test_fallback_rules.py
pytest -q tests/integration/messaging/test_fallback_sweep.py tests/integration/messaging/test_field_tasks_api.py
FLAGS=FR16_FALLBACK uvicorn app.main:create_app --factory
```

### Verification checklist (stub)

| Acceptance | How |
|---|---|
| US1-1..5 | `test_fallback_sweep.py -k sms` |
| US2-1..4 | `test_fallback_sweep.py -k task` |
| US2-5 | `test_field_tasks_api.py::test_scoping` |
| US3-1 | `test_fallback_sweep.py::test_flag_off` |
| SC-001, SC-002 | fixed-clock tests at 05:59 / 06:00 / 11:59 / 12:00 |
| SC-003 | `test_fallback_sweep.py::test_idempotent_three_runs` |
| SC-004 | `test_sms_segments.py` |
| SC-005 | `test_flag_off` |
| SC-006 | FR20 verify after the suite |
| SC-007 | T021 benchmark note |

### Demo notes

- Not part of the 3-minute demo (flag off). For Q&A ("what if they have no WhatsApp?"), run a rehearsal replay with `FLAGS=FR16_FALLBACK`: step one day past the payments and show the ~10% silent simulated beneficiaries moving to "SMS sent" and then to open field tasks, all labelled simulated.

### Pitfalls

- **Sweep before simulated replies** in replays would SMS everyone; always call `simulate_replies` first.
- **Do not chase `QUEUED`** notices: the worker may just be slow. Timed rules need `sent_at`.
- **Shared `timers.py`**: add one call in your section; do not reorder FR8/FR17 jobs.
- **Commit per root**: keeps the FR20 chain lock short and lets a failure on one root skip only that root.
- **`receipt_status_for_payments` batching**: call it once per sweep, never per root.
- **Window boundary**: use the same window as FR15, or a late reply could land on a notice FR16 already gave up on.

---

## Part B: Roadmap build

### Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T024–T027 | Task completion with outcome → `FIELD_AGENT` confirmation |
| 2 | T028 | SMS route decision for Pakistan |
| 3 | T029–T031 | Real SMS sender and inbound SMS webhook |
| 4 | T032–T034 | Immediate fallbacks by failure class, `NOT_RECEIVED` tasks, tier-alert SMS |
| 5 | T035–T036 | `FIELD_AGENT` role, task SLA reminder |

### Key algorithms

#### Complete a task (`field_tasks.py`)

```python
def complete_field_task(db, *, task_id: UUID, user: User, outcome: str, note: str) -> FieldAgentTask:
    task = db.scalars(select(FieldAgentTask).where(FieldAgentTask.id == task_id).with_for_update()).one_or_none()
    if task is None: raise NotFound(code="TASK_NOT_FOUND")
    assert_district_access(user, task.district_id)
    if task.status != "OPEN": raise Conflict(code="TASK_NOT_OPEN")
    if not note.strip(): raise Unprocessable(code="NOTE_REQUIRED")
    with use_scenario(task.scenario_id):
        task.status, task.outcome, task.note = "DONE", outcome, note
        task.done_at, task.done_by = clock.now(), user.id
        if outcome in ("CONFIRMED", "NOT_RECEIVED"):
            record_confirmation(db, alert=db.get(Alert, task.alert_id), parsed=ParsedReply(outcome, outcome),
                                channel="FIELD_AGENT", provider_message_id=None, reply_raw=note,
                                simulated=task.simulated, recorded_by=user.id)
        audit.record(db, event_type="FIELD_TASK_DONE", entity_type="FieldAgentTask", entity_id=task.id,
                     actor=str(user.id), payload={"outcome": outcome, "payment_id": str(task.payment_id),
                                                  "note_sha256": sha256(note.encode()).hexdigest()})
    return task
```

#### Inbound SMS

Reuse FR15: `require_twilio_signature` → advisory lock on `MessageSid` → `processed()` → beneficiary branch of `route_inbound` with `channel="SMS"`. DC commands are not accepted over SMS (D3 names WhatsApp only).

### Configuration (roadmap)

| Key | Default | Notes |
|---|---|---|
| `SMS_MODE` | `live` | Enables the real sender |
| `TWILIO_SMS_FROM` or `SMS_AGGREGATOR_URL`, `SMS_AGGREGATOR_KEY`, `SMS_SENDER_ID` | — | Per the T028 decision (**verify** PK rules) |
| `FIELD_TASK_SLA_H` | 24 | Overdue reminder |

### Commands (roadmap)

```bash
pytest -q tests/integration/messaging/test_field_tasks_api.py -k patch
pytest -q tests/integration/messaging/test_sms_inbound.py
```

### Verification checklist (roadmap)

| Acceptance | How |
|---|---|
| US4-1..4 | `test_field_tasks_api.py -k patch` |
| US5-1..2 | `test_sms_inbound.py` |
| US6-1..4 | `test_fallback_rules.py -k immediate`, `test_fallback_sweep.py -k immediate` |
| SC-008 | `test_sms_inbound.py::test_duplicate`, receipt summary after completion |

### Pitfalls (roadmap)

- **Pakistani SMS rules**: international long codes may be filtered or one-way, and alphanumeric sender IDs cannot receive replies (**verify**). Do not promise "reply by SMS" in the text until the route supports it.
- **Cost**: each Urdu SMS is up to 3 billed parts; never SMS tier alerts at Watch.
- **Field-agent notes** are free text: encrypt them (proposed 🔒) and keep them out of audit payloads.

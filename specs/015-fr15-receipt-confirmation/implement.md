# Implementation Guide: FR15 Receipt confirmation

## Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T001–T004 | Settings, modules, corpus, FR8 stub |
| 2 | T005–T007 (failing) | Parser, command detection and recorder contracts |
| 3 | T008–T012 | Migration, event types, parser, recorder, reply templates |
| 4 | T013–T016 | Beneficiary `1`/`2` end to end through the webhook (demo-critical) |
| 5 | T017–T019 | DC command routing to FR8 |
| 6 | T020–T022 | Exactly-once per `MessageSid`, signature tests |
| 7 | T023–T024 | Help, throttle, strangers |
| 8 | T025–T029 | Receipt status service and APIs (unblocks FR19) |
| 9 | T030–T031 | Simulated replies for replays |
| 10 | T032–T034 | Load, sandbox check, lint |

## Key algorithms

### Reply parser (`reply_parser.py`)

```python
_DIGITS = str.maketrans("\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669", "01234567890123456789")
_MARKS = re.compile(r"[​-‏‪-‮⁦-⁩﻿]")
_TRAIL = " .\u06d4!\u061f?\u060c,"
_DIGIT_TOKEN = re.compile(r"\d+")
_COMMAND = re.compile(r"^(approve|reject)\b", re.IGNORECASE)

@dataclass(frozen=True)
class ParsedReply:
    status: Literal["CONFIRMED", "NOT_RECEIVED", "UNPARSEABLE"]
    normalised: str

def normalise(text: str | None) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = _MARKS.sub("", t).translate(_DIGITS)
    t = " ".join(t.split())                       # collapse whitespace, incl. NBSP
    return t.strip(_TRAIL).strip()

def parse_reply(body: str | None, button_payload: str | None = None) -> ParsedReply:
    if button_payload in ("RECEIPT_YES", "RECEIPT_NO"):
        return ParsedReply("CONFIRMED" if button_payload == "RECEIPT_YES" else "NOT_RECEIVED", button_payload)
    t = normalise(body)
    low = t.lower()
    tokens = low.split(" ") if low else []
    digit_tokens = _DIGIT_TOKEN.findall(low)
    if tokens and tokens[0] in ("1", "2") and len(digit_tokens) == 1:
        return ParsedReply("CONFIRMED" if tokens[0] == "1" else "NOT_RECEIVED", t)
    if settings.RECEIPT_KEYWORDS_ENABLED:
        if low in settings.RECEIPT_YES_KEYWORDS:
            return ParsedReply("CONFIRMED", t)
        if low in settings.RECEIPT_NO_KEYWORDS:
            return ParsedReply("NOT_RECEIVED", t)
    return ParsedReply("UNPARSEABLE", t)

def is_command(text: str | None) -> bool:
    return bool(_COMMAND.match(normalise(text)))
```

Note `1.` → `normalise` strips the trailing dot → `1`. `1 2` → two digit tokens → unparseable. `12` (Urdu digits) → `12` → first token is `12` → unparseable.

### Routing (`inbound.py`)

```python
def route_inbound(db: Session, msg: InboundMessage) -> InboundOutcome:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:sid))"), {"sid": msg.sid})
    if processed(db, msg.sid):
        return InboundOutcome.DUPLICATE
    try:
        h = phone_hash(msg.from_number)                       # normalises; raises for non-PK
    except ValueError:
        log.info("inbound.non_pk_number"); return InboundOutcome.IGNORED
    official = find_official(db, h)                          # active User; DC preferred
    if is_command(msg.body):
        if official and official.role == Role.DC:
            return _route_dc_command(db, official, msg)
        if official:
            send_reply(db, recipient=official, template_key="official.not_authorised",
                       context={}, inbound_sid=msg.sid)
            return InboundOutcome.OFFICIAL_NOT_AUTHORISED
    bens = db.scalars(select(Beneficiary).where(Beneficiary.phone_hash == h)).all()
    if bens:
        return _route_beneficiary(db, bens, h, msg)
    if official:
        send_reply(db, recipient=official, template_key="dc.help", context={}, inbound_sid=msg.sid)
        return InboundOutcome.HELP
    log.info("inbound.unknown_sender", phone_hash=h[:12])
    return InboundOutcome.IGNORED

def _route_dc_command(db, user: User, msg: InboundMessage) -> InboundOutcome:
    # FR8 contract (008-fr08-dc-action-approval/plan.md): FR8 parses the command itself
    # (parse_dc_command) and returns the bilingual reply_text, so FR15 only forwards it.
    from app.services.triggers.dc_commands import handle_dc_whatsapp_command   # FR8
    result = handle_dc_whatsapp_command(
        db, sender_phone_hash=user.phone_hash, body=msg.body,
        provider_message_id=msg.sid, received_at=clock.now(),
    )
    key, ctx, scenario_id = _result_to_ack(result)
    with use_scenario(scenario_id):
        send_reply(db, recipient=user, template_key=key, context=ctx, inbound_sid=msg.sid,
                   related={"approval_id": result.approval_id})
    return InboundOutcome.DC_COMMAND

def _result_to_ack(result: "DcCommandResult") -> tuple[str, dict, UUID | None]:
    # "dc.passthrough" renders context["text"] verbatim (FR14 catalogue); FR8 owns the wording.
    ctx = {"text": result.reply_text,
           "link": dashboard_link("approvals", result.approval_id) if result.approval_id else None}
    return "dc.passthrough", ctx, result.scenario_id

def _route_beneficiary(db, bens, h, msg) -> InboundOutcome:
    alert = find_open_prompt(db, [b.id for b in bens], h, now=clock.now())
    if alert is None:
        send_reply(db, recipient=bens[0], template_key="receipt.no_open_notice", context={}, inbound_sid=msg.sid)
        return InboundOutcome.NO_OPEN_NOTICE
    with use_scenario(alert.scenario_id):
        if alert.purpose == "OUTCOME_SURVEY" and flags.is_enabled("FR22_OUTCOME"):
            from app.services.outcomes.survey import handle_survey_reply             # FR22
            handle_survey_reply(db, alert=alert, beneficiary=beneficiary_of(alert, bens),
                                parsed=parse_reply(msg.body, msg.button_payload), provider_message_id=msg.sid)
            return InboundOutcome.SURVEY
        parsed = parse_reply(msg.body, msg.button_payload)
        raw = msg.body if msg.body else "[media]" if msg.num_media else ""
        record_confirmation(db, alert=alert, parsed=parsed, channel="WHATSAPP",
                            provider_message_id=msg.sid, reply_raw=raw,
                            simulated=alert.simulated or payment_is_simulated(db, alert))
        ben = beneficiary_of(alert, bens)
        if parsed.status == "UNPARSEABLE":
            if not recent_ack_exists(db, h, "receipt.help", settings.RECEIPT_HELP_THROTTLE_MIN):
                send_reply(db, recipient=ben, template_key="receipt.help", context={}, inbound_sid=msg.sid)
        else:
            key = "receipt.ack.confirmed" if parsed.status == "CONFIRMED" else "receipt.ack.not_received"
            send_reply(db, recipient=ben, template_key=key,
                       context={"ref": short_ref(alert.related_payment_id)}, inbound_sid=msg.sid)
    return InboundOutcome.RECEIPT

def find_open_prompt(db, beneficiary_ids, h, now) -> Alert | None:
    since = now - timedelta(days=settings.RECEIPT_REPLY_WINDOW_DAYS)
    ts = func.coalesce(Alert.sent_at, Alert.queued_at)
    return db.scalars(
        select(Alert)
        .where(Alert.beneficiary_id.in_(beneficiary_ids), Alert.expects_reply.is_(True),
               Alert.status.notin_(("QUEUED", "FAILED")), ts >= since,
               or_(Alert.to_phone_hash == h, Alert.to_phone_hash.is_(None)))
        .order_by(ts.desc(), Alert.id.desc()).limit(1)
    ).first()
```

`processed(db, sid)` = `EXISTS(confirmation.provider_message_id = sid) OR EXISTS(alert.dedupe_key = 'ACK:' || sid)`.

Note: `find_open_prompt` skips `QUEUED` (not yet sent) and `FAILED` (never reached the person) alerts. When FR16 sends an SMS for a failed WhatsApp notice, the SMS alert is itself a reply-expecting `PAYMENT_NOTICE`, so the person can still answer it.

### Recorder (`confirmations.py`)

```python
EVENT = {"CONFIRMED": "RECEIPT_CONFIRMED", "NOT_RECEIVED": "RECEIPT_NOT_RECEIVED",
         "UNPARSEABLE": "RECEIPT_UNPARSEABLE"}

def record_confirmation(db, *, alert, parsed, channel, provider_message_id, reply_raw,
                        simulated, recorded_by=None) -> Confirmation:
    c = Confirmation(alert_id=alert.id, beneficiary_id=alert.beneficiary_id,
                     payment_id=alert.related_payment_id, reply_raw=reply_raw, channel=channel,
                     status=parsed.status, received_at=clock.now(),
                     provider_message_id=provider_message_id, scenario_id=alert.scenario_id,
                     simulated=simulated, recorded_by=recorded_by)
    db.add(c)
    try:
        db.flush()
    except IntegrityError as e:                      # unique provider_message_id
        raise DuplicateInbound(provider_message_id) from e
    audit.record(db, event_type=EVENT[parsed.status], entity_type="Confirmation", entity_id=c.id,
                 actor=str(recorded_by) if recorded_by else "beneficiary",
                 payload={"alert_id": str(alert.id), "payment_id": str_or_none(alert.related_payment_id),
                          "status": parsed.status, "channel": channel,
                          "reply_sha256": sha256(reply_raw.encode()).hexdigest(),
                          "provider_message_id": provider_message_id, "simulated": simulated})
    return c
```

### Receipt status (`confirmations.py`)

```sql
-- effective status per payment
SELECT DISTINCT ON (c.payment_id) c.payment_id, c.status
FROM confirmation c
WHERE c.payment_id = ANY(:ids) AND c.status IN ('CONFIRMED', 'NOT_RECEIVED')
ORDER BY c.payment_id, c.received_at DESC, c.id DESC;
```

Then: payments with only `UNPARSEABLE` rows → `UNPARSEABLE_ONLY`; payments with a non-failed `PAYMENT_NOTICE` alert but no rows → `NO_REPLY`; otherwise `NOT_NOTIFIED`. `receipt_counts` runs the same logic as one SQL query with CTEs over `payment` (status `PAID`) joined to `allocation` → `fund_release` → `disaster_fund.district_id` and `beneficiary.type`, scoped by `current_scenario()`.

### Simulated replies (`simulated_replies.py`)

```python
def _pick(alert_id: UUID, mix: tuple[float, float]) -> str | None:
    x = int(sha256(alert_id.bytes).hexdigest()[:8], 16) / 0xFFFFFFFF
    confirm, not_received = mix                       # default (0.85, 0.05)
    if x < confirm: return "1"
    if x < confirm + not_received: return "2"
    return None                                        # stays silent (FR16 can chase it)

def simulate_replies(db, *, now) -> int:
    rows = db.scalars(select(Alert).where(
        Alert.purpose == "PAYMENT_NOTICE", Alert.simulated.is_(True),
        Alert.provider_message_id.like("SIM-%"),
        Alert.sent_at <= now - timedelta(seconds=settings.SIM_REPLY_DELAY_S),
        ~exists().where(Confirmation.alert_id == Alert.id),
        Alert.scenario_id.is_not(None) if not settings.SIM_REPLY_LIVE else true(),
    ).limit(1000)).all()
    n = 0
    for a in rows:
        body = _pick(a.id, settings.SIM_REPLY_MIX)
        if body is None:
            continue
        with use_scenario(a.scenario_id):
            record_confirmation(db, alert=a, parsed=parse_reply(body), channel="WHATSAPP",
                                provider_message_id=f"SIMIN-{a.id}", reply_raw=body, simulated=True)
        n += 1
    return n
```

Silent alerts are re-scanned each run; cap the scan with `sent_at >= now - 2 × window` to keep it cheap.

## Configuration

| Key | Default | Notes |
|---|---|---|
| `RECEIPT_REPLY_WINDOW_DAYS` | 7 | Open-notice window |
| `RECEIPT_KEYWORDS_ENABLED` | `true` | Exact whole-message match only |
| `RECEIPT_YES_KEYWORDS` | Urdu words from the translation file, plus the Roman Urdu list in spec.md | Lower-cased, normalised |
| `RECEIPT_NO_KEYWORDS` | Urdu words from the translation file, plus the Roman Urdu list in spec.md | |
| `RECEIPT_HELP_THROTTLE_MIN` | 10 | One help reply per phone per window |
| `SIM_REPLY_INTERVAL_S` | 30 | Job interval |
| `SIM_REPLY_DELAY_S` | 20 | Wait after `sent_at` before simulating |
| `SIM_REPLY_MIX` | `0.85,0.05` | Confirmed, not received; rest silent |
| `SIM_REPLY_LIVE` | `false` | Simulate for live dry-run alerts too |
| Twilio / signature keys | see FR14 | `TWILIO_AUTH_TOKEN`, `PUBLIC_BASE_URL`, `TWILIO_VALIDATE_SIGNATURE` |
| Flag `FR22_OUTCOME` | off | Survey routing |

## Commands

```bash
alembic upgrade head
pytest -q tests/unit/messaging/test_reply_parser.py tests/unit/messaging/test_command_detection.py
pytest -q tests/integration/messaging -k "inbound or receipt or simulated"
pytest -q tests/contract/test_inbound_webhook.py tests/contract/test_confirmations_api.py
# Manual signed request (dev), using the FR14 helper:
python -c "from tests.integration.messaging.conftest import print_signed_curl; print_signed_curl('/api/v1/webhooks/whatsapp', {'MessageSid':'SMtest1','From':'whatsapp:+923001234567','Body':'\u06f1'})"
```

## Verification checklist

| Acceptance | How |
|---|---|
| US1-1 | `test_inbound_webhook.py::test_reply_1_confirms` |
| US1-2 | `test_reply_parser.py` (corpus) + `test_inbound_receipt.py::test_variants` |
| US1-3 | `test_inbound_receipt.py::test_reply_2_not_received` |
| US1-4 | `test_inbound_receipt.py::test_latest_valid_wins` |
| US1-5 | `test_inbound_receipt.py::test_latest_notice` |
| US2-1..7 | `test_inbound_dc_routing.py` |
| US3-1..2 | `test_inbound_idempotency.py` |
| US3-3 | `test_inbound_webhook.py::test_bad_signature` |
| US4-1..5 | `test_inbound_receipt.py -k "unparseable or no_open or unknown or media"` |
| US5-1 | `test_receipt_status.py::test_counts_fixture` |
| US5-2..3 | `test_confirmations_api.py` |
| US6-1..3 | `test_simulated_replies.py` |
| SC-003 | T032 load note |
| SC-004 | `test_inbound_receipt.py::test_audit_payload` + FR20 verify |
| SC-006 | Manual: hero phone replies `1`; dashboard payment row shows "Confirmed" on next poll |
| SC-008 | `test_receipt_status.py::test_same_as_fr19` (once FR19 lands) |

## Demo notes

- After the DC approves (live `APPROVE <code>` from the DC phone, routed here to FR8) and payments go Paid, the hero shopkeeper's phone gets the payment notice. The presenter replies `1` (Urdu digits) on an Urdu keyboard; the dashboard flips the receipt to Confirmed and the audit log shows `RECEIPT_CONFIRMED`.
- The replay's other beneficiaries get labelled simulated replies, so the donor report shows "received" as a realistic share, with the simulated badge.
- Backup if the venue network fails: FR5's `REPLAY_SIM` approval and the dashboard OTP still work; FR15 is not on that path.

## Pitfalls

- **Signature with form bodies**: read the form once in the dependency and pass the dict on; Starlette's request body cannot be read twice.
- **Same phone, two roles**: always test commands before digits. A DC who is also the hero beneficiary must still be able to reply `1`.
- **NFKC does not fold Arabic-script digits**: keep the explicit translation table.
- **Python `str.isdigit()` is true for `1` (Urdu digits)**: never use it to decide ASCII-ness; normalise first.
- **Advisory lock scope**: use `pg_advisory_xact_lock` (released at commit/rollback), not the session-level lock.
- **FR8 errors**: rollback first, then open a **new** transaction for the `ERROR` ack, otherwise the ack is rolled back too.
- **Old open notices**: a reply with no new notice could confirm a week-old payment. The 7-day window limits this; tune it with the PDMA.
- **Ack loops**: never reply to messages from the sandbox number itself or from `To == From`; ignore them.
- **PII**: never log `Body` or `ProfileName`; log `phone_hash[:12]` and the sid only.

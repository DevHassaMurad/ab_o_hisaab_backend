# Implementation Guide: FR14 WhatsApp alerts

## Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T001–T004 | Dependencies, settings, package and fixtures |
| 2 | T005–T011 (tests, failing) | Contract for templates, dedupe, allowlist, errors, enqueue, worker |
| 3 | T012–T024 | Outbox columns, catalogue, senders, `enqueue`, worker, jobs, `get_alerts()` → **unblocks 007, 008, 017, 015** |
| 4 | T025–T029 | DC tier change + approval request (demo-critical) |
| 5 | T030–T033 | Payment notice and beneficiary tier alerts |
| 6 | T034–T040 | Status callbacks, signature, invalid-number flag |
| 7 | T041–T043 | Escalation and decision notices |
| 8 | T044–T047 | Alerts API, `send_reply` for FR15 |
| 9 | T048–T052 | Demo setup, sandbox rehearsal, perf, lint |

## Key algorithms

### Catalogue format (`app/services/messaging/templates/catalogue.yaml`)

```yaml
version: 1
shared:
  line.demo: {ur: "(آزمائشی پیغام، یہ اصل رقم نہیں ہے)", en: "(DEMO message, this is not real money)"}
  wallet.JAZZCASH: {ur: "جاز کیش", en: "JazzCash"}
  wallet.EASYPAISA: {ur: "ایزی پیسہ", en: "Easypaisa"}
  tier.WATCH: {ur: "نگرانی", en: "Watch"}
  tier.WARNING: {ur: "خبردار", en: "Warning"}
  tier.ACTION: {ur: "فوری خطرہ", en: "Action"}
templates:
  payment_notice:
    purpose: PAYMENT_NOTICE
    audience: BENEFICIARY
    expects_reply: true
    priority: 3
    demo_line: true
    variables: [amount, wallet_ur, wallet_en, ref]
    ur: |-
      آب و حساب: {amount} روپے آپ کے {wallet_ur} اکاؤنٹ میں بھیج دیے گئے ہیں۔
      رقم {wallet_ur} ایجنٹ کی دکان یا ایپ سے نکالیں۔
      یہ رقم مفت ہے۔ کسی کو فیس یا اپنا پن نہ دیں۔
      رقم مل گئی تو 1 بھیجیں۔ نہیں ملی تو 2 بھیجیں۔
      حوالہ: {ref}
    en: |-
      Aab o Hisaab: Rs {amount} has been sent to your {wallet_en} account.
      Cash it at a {wallet_en} agent shop or in the app.
      This money is free. Never pay a fee or share your PIN.
      Got the money? Reply 1. Not received? Reply 2.
      Ref: {ref}
  tier_alert.WARNING.HOUSEHOLD:
    purpose: TIER_ALERT
    audience: BENEFICIARY
    priority: 2
    demo_line: true
    compose: [header.WARNING, advice.WARNING.HOUSEHOLD, "@amount_line"]
  # … every other key from spec.md "Message content catalogue"
```

`compose` lists block keys; `@amount_line` is resolved at render time to `amount.wallet`, `amount.no_wallet` or nothing.

### Rendering (`templates.py`)

```python
class _Strict(dict):
    def __missing__(self, key: str) -> str:
        raise TemplateContextMissing(key)

def fmt_pkr(value: Decimal) -> str:
    return f"{value.quantize(Decimal('1')):,}"          # 15000.00 -> "15,000"

def render(key: str, ctx: dict, *, simulated: bool) -> RenderedMessage:
    t = CATALOGUE.templates[key]                        # KeyError -> TemplateUnknown
    ur_lines, en_lines = _blocks(t, ctx)                # header/advice/amount or ur/en text
    if simulated and t.demo_line:
        ur_lines.append(CATALOGUE.shared["line.demo"].ur)
        en_lines.append(CATALOGUE.shared["line.demo"].en)
    s = _Strict(ctx)
    ur = "\n".join(line.format_map(s) for line in ur_lines if line)
    en = "\n".join(line.format_map(s) for line in en_lines if line)
    return RenderedMessage(
        text=f"{ur}\n\n{en}", ur=ur, en=en, purpose=t.purpose,
        expects_reply=t.expects_reply, priority=t.priority,
        content_sid=settings.TWILIO_CONTENT_SIDS.get(key),
    )
```

### Enqueue (`alert_service.py`)

```python
class DbAlertService:  # implements AlertService
    def enqueue(self, db: Session, *, recipient: Beneficiary | User, purpose: str,
                template_key: str, context: dict, related: dict) -> Alert:
        if purpose == "OUTCOME_SURVEY" and not flags.is_enabled("FR22_OUTCOME"):
            raise FeatureDisabled("FR22_OUTCOME")
        simulated_subject = bool(related.get("simulated") or getattr(recipient, "simulated", False))
        msg = render(template_key, context, simulated=simulated_subject)
        phone, phone_error = resolve_phone(recipient)          # (e164 | None, "NO_PHONE"/"PHONE_INVALID"/None)
        now = clock.now()
        values = dict(
            recipient_type=recipient_type_of(recipient),
            beneficiary_id=recipient.id if isinstance(recipient, Beneficiary) else None,
            user_id=recipient.id if isinstance(recipient, User) else None,
            district_id=related.get("district_id") or district_of(db, recipient),
            purpose=purpose, channel=related.get("channel", "WHATSAPP"), language="UR_EN",
            template_key=template_key, message_text=msg.text, content_sid=msg.content_sid,
            status="FAILED" if phone_error else "QUEUED",
            error_code=phone_error, failed_at=now if phone_error else None,
            priority=msg.priority, expects_reply=msg.expects_reply,
            to_phone_hash=phone_hash(phone) if phone else None,
            related_payment_id=related.get("payment_id"),
            fund_release_id=related.get("fund_release_id"),
            approval_id=related.get("approval_id"),
            fallback_of_id=related.get("fallback_of_id"),
            triggered_at=related.get("triggered_at", now),
            queued_at=now, next_attempt_at=now,
            dedupe_key=related.get("dedupe_key") or default_key(purpose, template_key, recipient, related),
            simulated=simulated_subject, scenario_id=current_scenario(),
        )
        stmt = (pg_insert(Alert).values(**values)
                .on_conflict_do_nothing(index_elements=[Alert.dedupe_key, coalesce_scenario_expr])
                .returning(Alert.id))
        new_id = db.execute(stmt).scalar_one_or_none()
        if new_id is None:                                  # duplicate: idempotent, no audit
            return db.scalars(select(Alert).where(Alert.dedupe_key == values["dedupe_key"],
                                                  scenario_eq(current_scenario()))).one()
        alert = db.get(Alert, new_id)
        audit.record(db, event_type="ALERT_FAILED" if phone_error else "ALERT_QUEUED",
                     entity_type="Alert", entity_id=alert.id, actor=related.get("actor", "system"),
                     payload={"purpose": purpose, "template_key": template_key, "channel": alert.channel,
                              "recipient_type": alert.recipient_type, "priority": alert.priority,
                              "text_sha256": sha256(msg.text.encode()).hexdigest(),
                              "error_code": phone_error, "simulated": alert.simulated,
                              "related_payment_id": str_or_none(alert.related_payment_id),
                              "fund_release_id": str_or_none(alert.fund_release_id),
                              "approval_id": str_or_none(alert.approval_id)})
        db.info["alerts_queued"] = True                     # after_commit hook kicks the worker
        return alert
```

### Worker (`worker.py`)

```python
BACKOFF_S = [10, 30, 60, 120]          # settings.ALERT_RETRY_BACKOFF_S

def run_send_worker() -> int:
    with SessionLocal() as db:
        ids = db.scalars(
            select(Alert.id)
            .where(Alert.status == "QUEUED", Alert.next_attempt_at <= clock.now())
            .order_by(Alert.priority, Alert.queued_at)
            .limit(settings.ALERT_WORKER_BATCH)
        ).all()
    with ThreadPoolExecutor(max_workers=settings.ALERT_WORKER_CONCURRENCY) as pool:
        list(pool.map(_send_one, ids))
    return len(ids)

def _send_one(alert_id: UUID) -> None:
    with SessionLocal() as db, db.begin():
        alert = db.scalars(select(Alert).where(Alert.id == alert_id, Alert.status == "QUEUED")
                           .with_for_update(skip_locked=True)).one_or_none()
        if alert is None:
            return                                          # another worker has it or it is done
        with use_scenario(alert.scenario_id):               # audit goes to the right chain
            recipient = load_recipient(db, alert)
            phone = resolve_phone(recipient)[0]
            sender = choose_sender(alert, recipient, phone)  # Twilio, DryRun (allowlist gate)
            alert.attempt_count += 1
            result = sender.send(alert, phone)              # never raises; returns SendResult
            now = clock.now()
            if result.ok:
                alert.status, alert.sent_at = "SENT", now
                alert.provider_message_id = result.provider_message_id
                alert.simulated = alert.simulated or result.simulated
                audit.record(db, event_type="ALERT_SENT", entity_type="Alert", entity_id=alert.id,
                             actor="system", payload={"provider_message_id": result.provider_message_id,
                                                      "attempt": alert.attempt_count,
                                                      "latency_s": int((now - alert.triggered_at).total_seconds()),
                                                      "simulated": alert.simulated})
                return
            cls = classify_twilio_error(result.error_code)
            if cls is ErrorClass.TRANSIENT and alert.attempt_count < settings.ALERT_MAX_ATTEMPTS:
                alert.next_attempt_at = now + timedelta(seconds=BACKOFF_S[alert.attempt_count - 1])
                return                                      # stays QUEUED; no audit (no state change)
            mark_failed(db, alert, code=result.error_code or "PROVIDER_UNAVAILABLE",
                        detail=result.error_detail, cls=cls)   # audits ALERT_FAILED, flags phone if INVALID_NUMBER

@event.listens_for(Session, "after_commit")
def _kick(session: Session) -> None:
    if session.info.pop("alerts_queued", False):
        scheduler.run_now("alerts.send_worker")           # non-blocking; max_instances=1 coalesces
```

### Senders and the allowlist gate (`senders.py`)

```python
def choose_sender(alert: Alert, recipient, phone: str) -> Sender:
    if settings.MESSAGING_MODE != "twilio":
        return get_sender("DRY_RUN")
    if phone in settings.MESSAGING_LIVE_ALLOWLIST:
        return get_sender(alert.channel)                    # real WhatsApp / SMS
    if (settings.MESSAGING_SEND_TO_REAL_BENEFICIARIES and not getattr(recipient, "simulated", True)
            and alert.scenario_id is None):
        return get_sender(alert.channel)
    return get_sender("DRY_RUN")                            # synthetic or replay recipients

class TwilioWhatsAppSender:
    def send(self, alert: Alert, phone: str) -> SendResult:
        try:
            kwargs = dict(from_=settings.TWILIO_WHATSAPP_FROM, to=f"whatsapp:{phone}",
                          status_callback=f"{settings.PUBLIC_BASE_URL}/api/v1/webhooks/whatsapp/status")
            if alert.content_sid:
                kwargs |= dict(content_sid=alert.content_sid,
                               content_variables=json.dumps(content_vars_for(alert)))
            else:
                kwargs["body"] = alert.message_text
            m = twilio_client().messages.create(**kwargs)
            return SendResult(ok=True, provider_message_id=m.sid)
        except TwilioRestException as e:
            return SendResult(ok=False, error_code=str(e.code or e.status), error_detail=e.msg[:500])
        except (httpx.TimeoutException, requests.exceptions.RequestException) as e:
            return SendResult(ok=False, error_code="TIMEOUT", error_detail=type(e).__name__)
```

### Status callback (`status.py`)

```python
RANK = {"QUEUED": 0, "SENT": 1, "DELIVERED": 2, "READ": 3}
MAP = {"sent": "SENT", "delivered": "DELIVERED", "read": "READ",
       "undelivered": "FAILED", "failed": "FAILED"}

def apply_status_callback(db: Session, form: Mapping[str, str]) -> None:
    sid, raw = form.get("MessageSid"), (form.get("MessageStatus") or "").lower()
    new = MAP.get(raw)
    if not new:
        return                                              # queued/sending/accepted: ignore
    alert = db.scalars(select(Alert).where(Alert.provider_message_id == sid)
                       .with_for_update()).one_or_none()
    if alert is None:
        return                                              # unknown (other app, dry-run): 204
    with use_scenario(alert.scenario_id):
        old = alert.status
        if new == "FAILED":
            if old not in ("QUEUED", "SENT"):
                return
            mark_failed(db, alert, code=form.get("ErrorCode") or "UNDELIVERED",
                        detail=form.get("ErrorMessage"), cls=classify_twilio_error(form.get("ErrorCode")))
            return
        if old == "FAILED" or RANK[new] <= RANK.get(old, 0):
            return                                          # duplicate or out of order
        alert.status = new
        setattr(alert, {"SENT": "sent_at", "DELIVERED": "delivered_at", "READ": "read_at"}[new], clock.now())
        audit.record(db, event_type="ALERT_STATUS_CHANGED", entity_type="Alert", entity_id=alert.id,
                     actor="twilio", payload={"from": old, "to": new, "provider_message_id": sid})
```

### Signature dependency (`signature.py`)

```python
async def require_twilio_signature(request: Request) -> dict[str, str]:
    form = dict((await request.form()).items())
    if settings.ENV == "test" and not settings.TWILIO_VALIDATE_SIGNATURE:
        return form
    url = settings.PUBLIC_BASE_URL.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    sig = request.headers.get("X-Twilio-Signature", "")
    if not RequestValidator(settings.TWILIO_AUTH_TOKEN).validate(url, form, sig):
        raise Forbidden(code="INVALID_SIGNATURE")
    return form
```

### Tier-change helper (`notify.py`, sketch)

```python
def notify_tier_change(db, *, event, releases, risk_index, direction, triggered_at) -> list[Alert]:
    district = event.district
    dcs = dc_users_for_district(db, district.id)
    if direction == "UP":
        top = max(releases, key=lambda r: TIER_ORDER[r.tier])
        auto = [r for r in releases if r.status != "PENDING_APPROVAL"]
        pending = [r for r in releases if r.status == "PENDING_APPROVAL"]
        key = "dc.tier_change.up_pending" if pending else "dc.tier_change.up"
        ctx = base_ctx(district, risk_index) | released_ctx(auto) | pending_ctx(pending)
        dk = lambda u: f"TIERUP:{event.id}:{top.tier}:U:{u.id}"
    elif direction == "DOWN":
        key, ctx = "dc.tier_change.down", base_ctx(district, risk_index) | tier_ctx(risk_index.tier)
        dk = lambda u: f"TIERDOWN:{event.id}:{risk_index.id}:U:{u.id}"
    else:
        key, ctx = "dc.event_closed", base_ctx(district, risk_index) | {"days": event.fund.event_close_days}
        dk = lambda u: f"CLOSED:{event.id}:U:{u.id}"
    if not dcs:
        return [record_no_recipient(db, purpose="TIER_ALERT", template_key=key, district=district)]
    return [alerts.enqueue(db, recipient=u, purpose="TIER_ALERT", template_key=key, context=ctx,
                           related={"fund_release_id": top.id if direction == "UP" else None,
                                    "district_id": district.id, "triggered_at": triggered_at,
                                    "dedupe_key": dk(u), "simulated": any(r.simulated for r in releases)})
            for u in dcs]
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | — | Foundation config. Auth token also signs webhooks |
| `TWILIO_WHATSAPP_FROM` | `whatsapp:+14155238886` | Sandbox number (**verify** in Console) |
| `TWILIO_CONTENT_SIDS` | `{}` | JSON map `template_key → HX… content SID` for production templates |
| `TWILIO_HTTP_TIMEOUT_S` | 10 | Per send call |
| `TWILIO_VALIDATE_SIGNATURE` | `true` | May be `false` only with `ENV=test` |
| `PUBLIC_BASE_URL` | — | Exact https URL Twilio calls (ngrok/Render). Used for signature and callbacks |
| `DASHBOARD_BASE_URL` | `http://localhost:5173` | `{link}` in official messages |
| `MESSAGING_MODE` | `dry_run` | `dry_run` or `twilio` |
| `MESSAGING_LIVE_ALLOWLIST` | `""` | Comma list of E.164 numbers that get real sends |
| `MESSAGING_SEND_TO_REAL_BENEFICIARIES` | `false` | Production only; never true in the demo |
| `ALERT_WORKER_INTERVAL_S` | 10 | Poll interval (the after-commit kick makes it faster) |
| `ALERT_WORKER_BATCH` | 200 | Rows per run |
| `ALERT_WORKER_CONCURRENCY` | 8 | Threads |
| `ALERT_MAX_ATTEMPTS` | 5 | |
| `ALERT_RETRY_BACKOFF_S` | `10,30,60,120` | Total ≈ 220 s, inside NFR2 |
| `ALERT_NFR2_BUDGET_S` | 300 | Stats and watchdog |
| `ALERT_OFFICIAL_BUDGET_S` | 60 | Target for priority 1 |
| `DRY_RUN_LATENCY_MS` | 0 | Tests set 50 to mimic the provider |
| `DEMO_PHONES` | — | JSON `{"dc.dadu@sindh.demo": "+923…", "officer@pdma-sindh.demo": "+923…", "hero_beneficiary": "+923…"}`; never committed |
| Flag `FR22_OUTCOME` | off | Enables `OUTCOME_SURVEY` enqueue |

**Seed Urdu district names** (hand to the foundation owner for `data/seeds/districts.yaml` once `name_ur` is merged): Dadu دادو · Larkana لاڑکانہ · Qambar Shahdadkot قمبر شہدادکوٹ · Jacobabad جیکب آباد · Khairpur خیرپور · Sanghar سانگھڑ · Mirpurkhas میرپور خاص · Buner بونیر · Swat سوات · Shangla شانگلہ · Charsadda چارسدہ · Nowshera نوشہرہ.

## Commands

```bash
alembic upgrade head
pytest -q tests/unit/messaging
pytest -q tests/integration/messaging -k "enqueue or worker"
pytest -q tests/integration/messaging/test_nfr2_latency.py
pytest -q tests/contract/test_alerts_api.py tests/contract/test_webhook_signature.py
python -m app.services.messaging.demo_setup          # writes DEMO_PHONES, prints sandbox checklist
ngrok http 8000                                      # then set PUBLIC_BASE_URL
curl -X POST "$API/api/v1/alerts/test" -H "Authorization: Bearer $ADMIN" -d '{"user_id":"<dc id>"}'
ruff check app/services/messaging && mypy --strict app/services/messaging
```

## Verification checklist

| Acceptance | How |
|---|---|
| US1-1 (multi-tier, one DC message) | `test_notify_helpers.py::test_tier_change_up_coalesces` |
| US1-2 (approval request text) | `test_notify_helpers.py::test_approval_request_text` |
| US1-3 (≤ 60 s) | `test_nfr2_latency.py::test_official_first`; manual on sandbox (SC-002) |
| US1-4 (no phone) | `test_enqueue.py::test_no_phone_failed` |
| US1-5 (tier down) | `test_notify_helpers.py::test_tier_change_down` |
| US2-1..3 | `test_notify_helpers.py -k payment` |
| US3-1..5 | `test_notify_helpers.py -k tier_alert` |
| US4-1..2, 5 | `test_status_callback.py` |
| US4-3 | `test_webhook_signature.py` |
| US4-4 | `test_invalid_number.py` |
| US5-1..3 | `test_notify_helpers.py -k "escalation or decision"` |
| US6-1..4 | `test_alerts_api.py` |
| SC-001 | `test_nfr2_latency.py::test_burst_5000` |
| SC-003 | `test_catalogue_lint.py` |
| SC-004 | `test_enqueue.py::test_idempotent`, helper repeat tests |
| SC-005 | FR20 `verify` after the integration suite (`test_worker.py::test_chain_verifies`) |
| SC-006 | `test_templates.py::test_demo_line` |
| SC-007 | `test_allowlist.py` |
| SC-010 | `test_enqueue.py::test_audit_payload_has_no_pii`, `test_alerts_api.py::test_donor_forbidden` |
| Sandbox manual | (1) each demo phone joined; (2) `POST /alerts/test` arrives on each; (3) status reaches `DELIVERED`/`READ` via callback; (4) Urdu renders right-to-left with the `APPROVE` line intact; (5) wrong signature from `curl` → 403 |

## Demo notes

- Replay `sindh-2022` (FR5). When Dadu crosses Watch and Warning, the DC phone buzzes once with both tiers. At Action, the presenter shows the approval request on the DC phone; the code is on its own line.
- The hero shopkeeper's phone gets the Warning tier alert (shop advice) and, after approval and Paid, the payment notice with the demo label. FR15 takes the "1" reply live.
- The dashboard can show `GET /alerts/stats?fund_release_id=…`: hundreds of dry-run alerts, p95 latency in seconds, 0 NFR2 breaches. Every dry-run alert is labelled simulated.
- Only the 4 allowlisted phones get real messages. Everyone else in the synthetic register is dry-run.

## Pitfalls

- **Signature URL mismatch**: behind ngrok or a proxy, `request.url` is `http://localhost…`. Always rebuild from `PUBLIC_BASE_URL`, keep the query string, and pass the **form** params (not JSON).
- **Sandbox expiry and the 24 h window**: re-join and send "hi" from every demo phone on demo morning; otherwise you get 63015/63016 (**verify**) and nothing arrives.
- **Bidi text**: a Latin command inside an Urdu sentence can reorder on screen. Keep `APPROVE {code}` and `REJECT …` on their own lines.
- **Message length**: Twilio's body limit is 1 600 characters (**verify**). Keep explanation text truncated at 200 characters.
- **Scheduler under `--reload`**: two schedulers mean two workers. `SKIP LOCKED` keeps it safe, but follow the foundation's single-start guard.
- **Audit chain lock**: the per-alert transaction holds the FR20 chain lock until commit. Keep the Twilio call short (timeout 10 s) and never send inside the caller's transaction.
- **Replay dedupe**: if FR5 does not clear the scenario's alerts on reset, the next run sends nothing (clarify.md open item 6).
- **PII guard**: FR20's `pii_guard` may reject keys like `beneficiary_id` or `phone`. Audit payloads use the alert as the entity and carry `recipient_type`, never the phone or text.
- **Twilio library timeout**: set the HTTP client timeout explicitly; the default can hang far longer than the NFR2 budget.

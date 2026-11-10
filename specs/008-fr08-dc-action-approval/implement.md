# Implement Guide: FR8 DC Approval of Action-Tier Releases

## Execution order

| Step | Tasks | Output |
|---|---|---|
| 1 | T001–T003 | Migration, config, fixtures |
| 2 | T004–T005 (red) → T006–T007 (green) | Codes, `request_approval`, `decide`, gate |
| 3 | T008–T011 → T012–T015 | Dashboard OTP approval + detail view |
| 4 | T016–T017 → T018–T019 | WhatsApp parser + handler (FR15 unblocked) |
| 5 | T020–T021 → T022–T023 | Reject paths |
| 6 | T024–T025 → T026–T028 | Escalation job, resend |
| 7 | T029 → T030–T031 | Replay simulation |
| 8 | T032–T035 | Registry, scrubbing, end-to-end, lint |

## Key algorithms

### Codes (`app/services/triggers/approval_codes.py`)

```python
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"

def hash_code(code: str) -> str:
    return bcrypt_ctx.hash(code)                       # passlib CryptContext(schemes=["bcrypt"])

def verify_code(code: str, code_hash: str | None) -> bool:
    return bool(code_hash) and bcrypt_ctx.verify(code, code_hash)

def normalise_digits(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_DIGITS)
```

### Request (`app/services/triggers/approvals.py`)

```python
def request_approval(db: Session, release: FundRelease) -> Approval:
    now = clock.now()
    code = generate_code()
    appr = Approval(fund_release_id=release.id, requested_at=now, decision="PENDING",
                    wa_code_hash=hash_code(code), wa_code_expires_at=now + timedelta(hours=cfg.APPROVAL_WA_CODE_TTL_H),
                    otp_attempts=0, wa_code_attempts=0, simulated=False, scenario_id=release.scenario_id)
    db.add(appr); db.flush()
    dc = find_district_dc(db, release.event.district_id)          # active User role=DC
    audit.record(db, event_type="APPROVAL_REQUESTED", entity_type="Approval", entity_id=appr.id, actor="system",
                 payload={"release_id": str(release.id), "district_id": str(release.event.district_id),
                          "amount_pkr": str(release.amount_pkr), "score": release.risk_index.score,
                          "deadline_at": (now + ESCALATE_AFTER).isoformat(),
                          "dc_user_id": str(dc.id) if dc else None,
                          "wa_code_expires_at": appr.wa_code_expires_at.isoformat()})
    if dc is None:
        _escalate(db, appr, now, reason="NO_DC_ASSIGNED")
    else:
        alerts.enqueue(db, recipient=dc, purpose="APPROVAL_REQUEST", template_key="dc_approval_request",
                       context={"district": release.event.district.name, "score": release.risk_index.score,
                                "amount_pkr": str(release.amount_pkr), "purpose": release.purpose,
                                "explanation_ur": release.risk_index.explanation_ur,
                                "explanation_en": release.risk_index.explanation_en,
                                "code": code, "deadline_at": (now + ESCALATE_AFTER).isoformat(),
                                "link": f"{cfg.DASHBOARD_BASE_URL}/approvals/{appr.id}"},
                       sensitive_keys=["code"],
                       related={"approval_id": appr.id, "fund_release_id": release.id})
    # No inline simulation: deciding here would allocate Action before the Watch/Warning
    # releases fired in the same evaluate() call. FR5 calls simulate_pending_approvals() after evaluate.
    return appr
```

Example request message (FR14 renders; Urdu first):
```
ایکشن الرٹ: دادو، خطرہ 86/100۔ PKR 30,000,000 (نقد امداد) کی منظوری درکار ہے۔
منظوری: APPROVE 482913   مسترد: REJECT <وجہ>
ACTION tier: Dadu, risk 86/100. Approve PKR 30,000,000 (Cash support)?
Reply APPROVE 482913 or REJECT <reason>. Escalates to PDMA at 05:00 PKT if unanswered.
```

### Decide (single path for all channels)

```python
def decide(db, approval, *, decision, channel, approver: User | None, reason=None,
           simulated=False, message_id=None, decided_at=None) -> DecisionResult:
    appr = db.scalar(select(Approval).where(Approval.id == approval.id).with_for_update())
    if decision not in APPROVAL_TRANSITIONS.get(appr.decision, set()) or decision == "ESCALATED":
        raise Conflict("APPROVAL_ALREADY_DECIDED")
    release = appr.fund_release
    if not simulated:                                                   # Principle II
        if approver is None or approver.role != "DC" or not approver.is_active \
                or approver.district_id != release.event.district_id:
            raise Forbidden("APPROVER_NOT_DC")
    if decision == "REJECTED":
        reason = (reason or "").strip()
        if not cfg.APPROVAL_REJECT_REASON_MIN_CHARS <= len(reason) <= 1000:
            raise Unprocessable("REJECT_REASON_REQUIRED")
    was_escalated = appr.decision == "ESCALATED"
    appr.decision, appr.channel, appr.reason = decision, channel, reason
    appr.approver_id = approver.id if approver else None
    appr.decided_at = decided_at or clock.now()
    appr.simulated, appr.decision_message_id = simulated, message_id
    appr.otp_hash = appr.wa_code_hash = None                            # burn both codes
    audit.record(db, event_type="APPROVAL_DECIDED", entity_type="Approval", entity_id=appr.id,
                 actor=f"replay-sim:{appr.approver_id}" if simulated else str(approver.id),
                 payload={"release_id": str(release.id), "decision": decision, "channel": channel,
                          "approver_id": str(appr.approver_id) if appr.approver_id else None,
                          "reason": reason, "simulated": simulated, "was_escalated": was_escalated,
                          "minutes_to_decide": int((appr.decided_at - appr.requested_at).total_seconds() // 60),
                          "message_id": message_id})
    actor = str(appr.approver_id or "system")
    if decision == "APPROVED":
        transition_release(db, release, "APPROVED", actor, f"approved via {channel}")
        run_release_pipeline(db, release, actor=actor)                  # FR7: ALLOCATING → …
        if was_escalated:
            _notify_officers(db, appr, "approval_decided_officer")
    else:
        transition_release(db, release, "REJECTED", actor, reason)      # money stays in the fund
        _notify_officers(db, appr, "approval_rejected_officer")
    return DecisionResult(approval=appr, release=release)
```

### Dashboard OTP

```python
def issue_dashboard_otp(db, approval_id, user) -> OtpIssued:
    appr = _load_for_dc(db, approval_id, user, for_update=True)        # 403 / 404 / 409 decided
    now = clock.now()
    if appr.otp_last_sent_at and (now - appr.otp_last_sent_at).total_seconds() < cfg.APPROVAL_OTP_RESEND_COOLDOWN_S:
        raise TooManyRequests("OTP_RESEND_TOO_SOON")
    if not user.phone:
        raise Conflict("DC_PHONE_MISSING")
    otp = generate_code()
    appr.otp_hash, appr.otp_expires_at = hash_code(otp), now + timedelta(minutes=cfg.APPROVAL_OTP_TTL_MIN)
    appr.otp_attempts, appr.otp_last_sent_at = 0, now
    alerts.enqueue(db, recipient=user, purpose="APPROVAL_REQUEST", template_key="dc_approval_otp",
                   context={"otp": otp, "amount_pkr": str(appr.fund_release.amount_pkr),
                            "expires_in_min": cfg.APPROVAL_OTP_TTL_MIN},
                   sensitive_keys=["otp"], related={"approval_id": appr.id})
    audit.record(db, event_type="APPROVAL_OTP_ISSUED", entity_type="Approval", entity_id=appr.id,
                 actor=str(user.id), payload={"user_id": str(user.id),
                                              "expires_at": appr.otp_expires_at.isoformat(), "channel": "WHATSAPP"})
    return OtpIssued(appr.otp_expires_at, mask_phone(user.phone), cfg.APPROVAL_OTP_MAX_ATTEMPTS,
                     dev_otp=otp if cfg.APPROVAL_OTP_DEV_ECHO and cfg.ENV != "production" else None)

def approve_with_otp(db, approval_id, user, otp) -> DecisionResult:
    appr = _load_for_dc(db, approval_id, user, for_update=True)
    if appr.otp_hash is None:
        raise Conflict("OTP_NOT_REQUESTED")
    if appr.otp_attempts >= cfg.APPROVAL_OTP_MAX_ATTEMPTS:
        raise TooManyRequests("OTP_LOCKED")
    if clock.now() > appr.otp_expires_at:
        raise Gone("OTP_EXPIRED")
    if not verify_code(normalise_digits(otp).strip(), appr.otp_hash):
        appr.otp_attempts += 1
        left = cfg.APPROVAL_OTP_MAX_ATTEMPTS - appr.otp_attempts
        audit.record(db, event_type="APPROVAL_CODE_FAILED", entity_type="Approval", entity_id=appr.id,
                     actor=str(user.id), payload={"kind": "OTP", "attempt": appr.otp_attempts,
                                                  "attempts_left": left, "locked": left == 0})
        db.commit()                                         # persist the attempt before failing
        raise (TooManyRequests("OTP_LOCKED") if left == 0 else BadRequest("OTP_INVALID", attempts_left=left))
    return decide(db, appr, decision="APPROVED", channel="DASHBOARD", approver=user)
```

### WhatsApp handler (`app/services/triggers/dc_commands.py`)

```python
_APPROVE = {"approve", "منظور"}
_REJECT = {"reject", "مسترد"}

def parse_dc_command(body: str) -> DcCommand | None:
    text = " ".join(normalise_digits(body).split())
    if not text:
        return None
    head, _, rest = text.partition(" ")
    verb = head.casefold()
    if verb in _APPROVE:
        digits = rest.replace(" ", "")
        return DcCommand(DcVerb.APPROVE, digits if re.fullmatch(r"\d{6}", digits) else None, None)
    if verb in _REJECT:
        m = re.match(r"^(\d{3}\s?\d{3})\b\s*(.*)$", rest)
        code, reason = (m.group(1).replace(" ", ""), m.group(2)) if m else (None, rest)
        return DcCommand(DcVerb.REJECT, code, reason.strip() or None)
    return None

def handle_dc_whatsapp_command(db, *, sender_phone_hash, body, provider_message_id, received_at) -> DcCommandResult:
    cmd = parse_dc_command(body)
    dc = db.scalar(select(User).where(User.phone_hash == sender_phone_hash, User.role == "DC", User.is_active))
    if cmd is None or dc is None:
        return _result(DcCommandOutcome.NOT_A_DC, None, replies.not_authorised())
    done = db.scalar(select(Approval).where(Approval.decision_message_id == provider_message_id))
    if done:
        return _result(DcCommandOutcome.ALREADY_DECIDED, done.id, replies.already_decided(done))
    open_ = open_approvals_for_district(db, dc.district_id)          # PENDING/ESCALATED, all scenarios, FOR UPDATE
    if not open_:
        return _result(DcCommandOutcome.NO_OPEN_APPROVAL, None, replies.none_open())

    target = None
    if cmd.code:
        target = next((a for a in open_ if verify_code(cmd.code, a.wa_code_hash)), None)
        if target is None:
            return _fail_code(db, dc, open_)                         # attempts += 1 on each, audit, lock at 5
        if target.wa_code_attempts >= cfg.APPROVAL_WA_CODE_MAX_ATTEMPTS:
            return _result(DcCommandOutcome.CODE_LOCKED, target.id, replies.locked())
        if received_at > target.wa_code_expires_at:
            return _result(DcCommandOutcome.CODE_EXPIRED, target.id, replies.expired())
    elif cmd.verb is DcVerb.APPROVE:
        return _result(DcCommandOutcome.CODE_INVALID, None, replies.usage())
    elif len(open_) == 1:
        target = open_[0]                                            # REJECT without code
    else:
        return _result(DcCommandOutcome.AMBIGUOUS, None, replies.include_code())

    with use_scenario(target.scenario_id):                     # audit goes to the right chain
        if cmd.verb is DcVerb.APPROVE:
            res = decide(db, target, decision="APPROVED", channel="WHATSAPP", approver=dc,
                         message_id=provider_message_id, decided_at=received_at)
            return _result(DcCommandOutcome.APPROVED, target.id, replies.approved(res.release), changed=True)
        if not cmd.reason or len(cmd.reason) < cfg.APPROVAL_REJECT_REASON_MIN_CHARS:
            return _result(DcCommandOutcome.REASON_REQUIRED, target.id, replies.reason_required())
        decide(db, target, decision="REJECTED", channel="WHATSAPP", approver=dc, reason=cmd.reason,
               message_id=provider_message_id, decided_at=received_at)
        return _result(DcCommandOutcome.REJECTED, target.id, replies.rejected(target), changed=True)
```

Wrong-code attempts are flushed in the caller's transaction (FR15 commits after it returns), so they persist.

### Escalation

```python
ESCALATE_AFTER = timedelta(minutes=cfg.APPROVAL_ESCALATE_AFTER_MIN)

def escalate_overdue_approvals(db, *, now: datetime, scenario_id: UUID | None) -> list[Approval]:
    due = db.scalars(select(Approval)
                     .where(Approval.decision == "PENDING",
                            Approval.requested_at <= now - ESCALATE_AFTER,
                            scenario_filter(Approval, scenario_id))
                     .with_for_update(skip_locked=True)).all()
    with use_scenario(scenario_id):
        for appr in due:
            _escalate(db, appr, now, reason="NO_RESPONSE_2H")
    return list(due)

def _escalate(db, appr, now, reason) -> None:
    appr.decision, appr.escalated_at, appr.escalation_reason = "ESCALATED", now, reason
    officers = active_users(db, role="PDMA_OFFICER")
    for o in officers:
        alerts.enqueue(db, recipient=o, purpose="ESCALATION", template_key="approval_escalated_officer",
                       context=_summary(appr), related={"approval_id": appr.id,
                                                        "fund_release_id": appr.fund_release_id})
    if (dc := find_district_dc(db, appr.fund_release.event.district_id)) is not None:
        alerts.enqueue(db, recipient=dc, purpose="ESCALATION", template_key="approval_reminder_dc",
                       context=_summary(appr), related={"approval_id": appr.id})
    audit.record(db, event_type="APPROVAL_ESCALATED", entity_type="Approval", entity_id=appr.id, actor="system",
                 payload={"reason": reason, "requested_at": appr.requested_at.isoformat(),
                          "escalated_at": now.isoformat(), "notified_officer_ids": [str(o.id) for o in officers]})
```

### Replay simulation

```python
def simulate_replay_decision(db, approval, *, decision="APPROVED", reason=None) -> DecisionResult:
    if approval.scenario_id is None:
        raise Forbidden("SIMULATION_LIVE_FORBIDDEN")                 # Principle II
    dc = find_district_dc(db, approval.fund_release.event.district_id)
    return decide(db, approval, decision=decision, channel="REPLAY_SIM", approver=dc,
                  reason=reason or ("Simulated rejection" if decision == "REJECTED" else None),
                  simulated=True,
                  decided_at=approval.requested_at + timedelta(minutes=cfg.REPLAY_APPROVAL_DELAY_MIN))

def simulate_pending_approvals(db, *, scenario_id: UUID) -> list[DecisionResult]:
    """FR5 calls this once per replay step, after the trigger step, when REPLAY_APPROVAL_MODE=SIMULATED."""
    if cfg.REPLAY_APPROVAL_MODE != "SIMULATED":
        return []
    pending = db.scalars(select(Approval).where(Approval.scenario_id == scenario_id,
                                                Approval.decision.in_(("PENDING", "ESCALATED")))
                         .order_by(Approval.requested_at)).all()
    with use_scenario(scenario_id):
        return [simulate_replay_decision(db, a, decision="APPROVED") for a in pending]
```

Replay step order (owned by FR5): ingest → forecast → `TriggerEngine.evaluate` (fires tiers; Watch/Warning pipelines run) → `simulate_pending_approvals` → `escalate_overdue_approvals(now=replay_now, scenario_id)`.

## Configuration

See plan.md "Configuration". `.env.example` additions:
```
APPROVAL_ESCALATE_AFTER_MIN=120
APPROVAL_ESCALATION_JOB_EVERY_MIN=5
APPROVAL_OTP_TTL_MIN=10
APPROVAL_OTP_MAX_ATTEMPTS=5
APPROVAL_OTP_RESEND_COOLDOWN_S=60
APPROVAL_WA_CODE_TTL_H=24
APPROVAL_WA_CODE_MAX_ATTEMPTS=5
APPROVAL_REJECT_REASON_MIN_CHARS=5
APPROVAL_OTP_DEV_ECHO=false
REPLAY_APPROVAL_MODE=SIMULATED
REPLAY_APPROVAL_DELAY_MIN=45
DASHBOARD_BASE_URL=http://localhost:5173
```

## Commands

```bash
alembic upgrade head
pytest tests/unit/triggers/test_approval_codes.py tests/unit/triggers/test_dc_command_parser.py -q
pytest tests/integration/triggers -k approval -q
pytest tests/contract/test_approvals_contract.py -q
# manual WhatsApp check against FR15's webhook in dev (signature validation disabled only with TWILIO_VALIDATE=false)
curl -X POST localhost:8000/api/v1/webhooks/whatsapp -d "From=whatsapp:+923001234567" -d "Body=APPROVE 482913" -d "MessageSid=SM123"
```

## Verification checklist

| Scenario | Test / step |
|---|---|
| US1-1 detail contents | `test_approval_dashboard.py::test_detail_contents` |
| US1-2 OTP issued, hash only | `test_approval_dashboard.py::test_issue_otp` |
| US1-3 approve with OTP → pipeline | `test_approval_dashboard.py::test_approve_with_valid_otp` |
| US1-4 attempts and lock | `test_approval_dashboard.py::test_wrong_otp_attempts_and_lock` |
| US1-5 expired | `test_approval_dashboard.py::test_expired_otp` |
| US2-1…5 WhatsApp | `test_approval_whatsapp.py`, `test_dc_command_parser.py` |
| US3-1…4 reject | `test_approval_dashboard.py::test_reject*`, `test_approval_whatsapp.py::test_reject*` |
| US4-1…5 escalation | `test_approval_escalation.py` |
| US5-1…3 replay | `test_approval_replay.py` |
| SC-001 gate | `test_approval_gate.py` + FR7 `test_action_never_allocated_without_approval` |
| Codes never leak | `test_approval_gate.py::test_no_code_in_logs_audit_or_alerts` |
| Live phone (manual) | Demo rehearsal: DC phone joined to Twilio sandbox replies `APPROVE <code>`; dashboard flips to Approved within seconds |

## Demo notes

This is the climax of the 3-minute demo. In the Sindh 2022 replay, Dadu crosses 80 and the Action card shows "Pending approval: PKR 30,000,000". With `REPLAY_APPROVAL_MODE=LIVE_WHATSAPP`, the DC's phone on stage buzzes with the Urdu-first request. The presenter replies `APPROVE 482913`, the WhatsApp confirmation arrives, and the dashboard shows Approved (channel WhatsApp), then allocations and payments start. Backup: if the network fails, the operator switches to `SIMULATED`, and the card shows "Simulated DC approval" with the simulated badge. Never present it as real.

## Pitfalls

- Commit failed-attempt counters before raising; FastAPI's exception path would otherwise roll them back and allow unlimited guesses.
- Do not pass the OTP or code into `log.*`, audit payloads or exception messages.
- The live escalation job must filter `scenario_id IS NULL`; replay rows have historical `requested_at` values and would all escalate at once.
- Use `with_for_update(skip_locked=True)` in the job so it never blocks a DC deciding at the same moment.
- `decide()` must run FR7's pipeline in the same transaction; if the pipeline fails it records `pipeline_error` but the approval still stands.
- A DC can have two open approvals (live + replay demo). `REJECT` without a code must not guess.
- bcrypt verify is ~0.25 s; verify against at most the DC's open approvals, never a table scan.

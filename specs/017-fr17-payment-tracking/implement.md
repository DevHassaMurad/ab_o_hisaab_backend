# Implementation Guide: FR17 Payment tracking

## Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T001–T003 | Package, settings, fixtures with the autouse invariant check |
| 2 | T004–T008 | Schema additions + ledger proven |
| 3 | T009 → T010–T011 | State machine proven against all 25 pairs |
| 4 | T012 → T013–T014 | Deterministic `SimulatedRail` |
| 5 | T015–T016 → T017–T020 | Reserve and dispatch (UC08) — **hand-off to FR8, FR12, FR18** |
| 6 | T021–T022 → T023–T026 | Views, counts, CSV, role scoping |
| 7 | T027 → T028–T029 | Reconcile + stuck timer |
| 8 | T030–T033 | Property test, perf, demo seed, lint |

## Key algorithms

### Transition table and choke point (`app/services/payments/state_machine.py`)

```python
from decimal import Decimal
from hashlib import sha256
from app.models.payments import Payment, PaymentEvent, PaymentStatus as S, PaymentAction as A
from app.services.payments.ledger import lock_fund, apply_delta
from app.services.payments.errors import InvalidTransition

# (from, to) -> (allowed actions, ledger delta as (reserved, disbursed, remaining) multipliers of amount)
TRANSITIONS: dict[tuple[S | None, S], tuple[set[A], tuple[int, int, int]]] = {
    (None,         S.RESERVED):   ({A.RESERVE},  (+1,  0, -1)),
    (S.RESERVED,   S.PROCESSING): ({A.SEND},     ( 0,  0,  0)),
    (S.PROCESSING, S.PAID):       ({A.RESULT},   (-1, +1,  0)),
    (S.PROCESSING, S.FAILED):     ({A.RESULT},   ( 0,  0,  0)),
    (S.FAILED,     S.PROCESSING): ({A.RETRY},    ( 0,  0,  0)),
    (S.FAILED,     S.CANCELLED):  ({A.CANCEL},   (-1,  0, +1)),
    (S.RESERVED,   S.CANCELLED):  ({A.CANCEL},   (-1,  0, +1)),
}

def transition(db, payment: Payment, to: S, *, action: A, actor: str,
               note: str = "", **changes) -> PaymentEvent:
    frm = payment.status  # None only for a brand-new payment (RESERVE)
    if frm is not None:
        db.refresh(payment, with_for_update=True)          # lock + re-read
        frm = payment.status
    rule = TRANSITIONS.get((frm, to))
    if rule is None or action not in rule[0]:
        raise InvalidTransition(frm, to)

    release = db.get(FundRelease, payment.fund_release_id)  # works before the payment is flushed
    fund = lock_fund(db, release.fund_id)                   # SELECT ... FOR UPDATE (re-entrant in one tx)
    apply_delta(db, fund, payment.amount_pkr, rule[1])

    now = get_clock().now()
    payment.status = to
    for k, v in changes.items():
        setattr(payment, k, v)
    if to == S.PROCESSING:
        payment.processing_since, payment.is_stuck = now, False
    elif to in (S.PAID, S.FAILED, S.CANCELLED):
        payment.is_stuck = False
    if to == S.PAID:
        payment.paid_at = now
    if to == S.CANCELLED:
        payment.cancelled_at = now

    ev = PaymentEvent(payment_id=payment.id, from_status=frm, to_status=to, action=action,
                      actor=actor, note=note, at=now, idempotency_key=payment.idempotency_key,
                      failure_reason=payment.failure_reason, provider_txn_id=payment.provider_txn_id,
                      scenario_id=payment.scenario_id)
    db.add(ev); db.flush()

    get_audit().record(db, event_type="PAYMENT_STATUS_CHANGED", entity_type="payment",
        entity_id=payment.id, actor=actor, payload={
            "payment_id": payment.id, "allocation_id": payment.allocation_id,
            "fund_release_id": payment.fund_release_id, "district_id": payment.district_id,
            "from": frm, "to": to, "action": action, "amount_pkr": payment.amount_pkr,
            "retry_count": payment.retry_count, "idempotency_key": payment.idempotency_key,
            "provider": payment.provider, "provider_txn_id": payment.provider_txn_id,
            "failure_reason": payment.failure_reason, "payment_event_id": ev.id,
            "note_sha256": sha256(note.encode()).hexdigest() if note else None,
            "simulated": payment.simulated,
            "fund_after": {"reserved_pkr": fund.reserved_pkr, "disbursed_pkr": fund.disbursed_pkr,
                           "remaining_pkr": fund.remaining_pkr}})

    if to == S.PAID:
        get_alerts().enqueue(db, recipient={"beneficiary_id": payment.beneficiary_id},
            purpose="PAYMENT_NOTICE", template_key="payment_notice",
            context={"amount_pkr": payment.amount_pkr, "provider": payment.provider,
                     "tier": release.tier},
            related={"payment_id": payment.id})
    return ev
```

Lock order is always **payment → fund → audit advisory lock**. Never lock the fund first.

### Ledger (`app/services/payments/ledger.py`)

```python
def lock_fund(db, fund_id) -> DisasterFund:
    return db.execute(select(DisasterFund).where(DisasterFund.id == fund_id)
                      .with_for_update()).scalar_one()

def apply_delta(db, fund, amount: Decimal, mult: tuple[int, int, int]) -> None:
    r, d, m = (amount * k for k in mult)
    if fund.remaining_pkr + m < 0:
        raise InsufficientFundBalance(fund.id, needed=amount, remaining=fund.remaining_pkr)
    db.execute(update(DisasterFund).where(DisasterFund.id == fund.id).values(
        reserved_pkr=DisasterFund.reserved_pkr + r,
        disbursed_pkr=DisasterFund.disbursed_pkr + d,
        remaining_pkr=DisasterFund.remaining_pkr + m))       # version NOT bumped (FR6 owns it)
    db.refresh(fund)

def check_fund_invariants(db, fund_id) -> list[str]:
    f = db.get(DisasterFund, fund_id)
    sums = dict(db.execute(select(Payment.status, func.coalesce(func.sum(Payment.amount_pkr), 0))
                .where(Payment.fund_release.has(fund_id=fund_id)).group_by(Payment.status)).all())
    held = sum(sums.get(s, 0) for s in (S.RESERVED, S.PROCESSING, S.FAILED))
    errs = []
    if f.allocated_pkr != f.reserved_pkr + f.disbursed_pkr + f.remaining_pkr: errs.append("identity")
    if f.reserved_pkr != held: errs.append("reserved")
    if f.disbursed_pkr != sums.get(S.PAID, 0): errs.append("disbursed")
    return errs
```

### SimulatedRail (`app/integrations/payment_rail.py`)

```python
class SimulatedRail:
    simulated = True

    def __init__(self, s: Settings):
        self.limit = Decimal(s.SIM_RAIL_WALLET_LIMIT_PKR)
        self.limit_clears_after = s.SIM_RAIL_LIMIT_CLEARS_AFTER_RETRIES
        self.rate, self.seed = s.SIM_RAIL_FAILURE_RATE, s.SIM_RAIL_SEED
        self.sfx_invalid, self.sfx_pending, self.sfx_timeout = (
            s.SIM_RAIL_INVALID_SUFFIX, s.SIM_RAIL_PENDING_SUFFIX, s.SIM_RAIL_TIMEOUT_SUFFIX)

    def _u(self, key: str) -> float:
        return int(sha256(f"{self.seed}:{key}".encode()).hexdigest()[:8], 16) / 2**32

    def _txn(self, provider: str, key: str) -> str:
        prefix = "SIMJC" if provider == "JAZZCASH" else "SIMEP"
        return f"{prefix}-{sha256(key.encode()).hexdigest()[:12]}"

    def send(self, i: RailInstruction) -> RailResult:
        _SENT[i.idempotency_key] = i                     # lets get_status() answer later
        if i.wallet_provider == "NONE" or not i.wallet_id:
            return RailResult("FAILED", None, "NO_WALLET", "Simulated: beneficiary has no wallet")
        if i.wallet_id.endswith(self.sfx_invalid):
            return RailResult("FAILED", None, "INVALID_WALLET", "Simulated: wallet not found")
        if i.wallet_id.endswith(self.sfx_timeout):
            raise RailTimeout(i.idempotency_key)
        if i.wallet_id.endswith(self.sfx_pending):
            return RailResult("PENDING", None, None, "Simulated: provider has not confirmed")
        if i.amount_pkr > self.limit and i.retry_count < self.limit_clears_after:
            return RailResult("FAILED", None, "LIMIT_REACHED",
                              f"Simulated: amount above wallet limit {self.limit:.2f}")
        if self._u(i.idempotency_key) < self.rate:
            return RailResult("FAILED", None, "PROVIDER_ERROR", "Simulated: provider error")
        return RailResult("PAID", self._txn(i.wallet_provider, i.idempotency_key), None)

    def get_status(self, key: str) -> RailResult:
        # Outcomes are a pure function of the instruction, so asking again gives the same answer.
        inst = _SENT.get(key)
        if inst is None or (inst.wallet_id or "").endswith(self.sfx_timeout):
            return RailResult("FAILED", None, "PROVIDER_TIMEOUT", "Simulated: instruction never landed")
        return self.send(inst)       # pending stays pending, others resolve as on send

_SENT: dict[str, RailInstruction] = {}
```

`_SENT` is a small in-process dict `{idempotency_key: RailInstruction}` filled in `send()` (before the rules run). After a restart it is empty, so reconcile returns `PROVIDER_TIMEOUT` — the safe answer for a simulated rail. The real rail implementation will query the provider instead.

### Reserve (`app/services/payments/service.py`)

```python
PAYABLE = {ReleaseStatus.APPROVED, ReleaseStatus.ALLOCATING}

def reserve_for_release(self, db, release: FundRelease, *, actor: str) -> list[Payment]:
    if release.status not in PAYABLE:
        raise ReleaseNotPayable(release.id, release.status)
    fund = lock_fund(db, release.fund_id)
    todo = db.scalars(select(Allocation).where(
        Allocation.fund_release_id == release.id, Allocation.status == "FUNDED",
        Allocation.final_amount_pkr > 0,
        ~exists().where(Payment.allocation_id == Allocation.id)).order_by(Allocation.rank, Allocation.id)).all()
    total = sum((a.final_amount_pkr for a in todo), Decimal("0"))
    if total > fund.remaining_pkr:
        raise InsufficientFundBalance(fund.id, needed=total, remaining=fund.remaining_pkr)
    out = []
    for a in todo:
        b = a.beneficiary
        p = Payment(id=uuid4(), allocation_id=a.id, beneficiary_id=b.id, fund_release_id=release.id,
                    district_id=fund.district_id, amount_pkr=a.final_amount_pkr, status=None,
                    retry_count=0, idempotency_key=f"{a.id}:0", provider=PROVIDER_MAP.get(b.wallet_provider),
                    is_stuck=False, simulated=True, scenario_id=current_scenario())
        db.add(p)                    # not flushed yet: status is set by transition() before the flush
        transition(db, p, S.RESERVED, action=A.RESERVE, actor=actor,
                   note=f"Reserved for {release.tier} release")
        out.append(p)
    return out
```

Releases whose status is `APPROVED` (Action after FR8) or `ALLOCATING` (Watch/Warning) are payable. Agree the exact enum values with `007-fr07-auto-tier-release`.

### Send (two short transactions around the rail call)

```python
def send_payment(self, payment_id, *, actor="system") -> Payment:
    with SessionLocal.begin() as db:                                   # tx1
        p = db.execute(select(Payment).where(Payment.id == payment_id)
                       .with_for_update(skip_locked=True)).scalar_one_or_none()
        if p is None or p.status not in (S.RESERVED,):                  # someone else has it, or already sent
            return p
        transition(db, p, S.PROCESSING, action=A.SEND, actor=actor, note=f"Sent to {p.provider}")
        instr = build_instruction(p)                                    # decrypts wallet in memory only
    try:
        result = get_rail().send(instr)                                 # no transaction open
    except RailTimeout:
        result = RailResult("PENDING", None, None, "Provider timeout; awaiting reconciliation")
    with SessionLocal.begin() as db:                                    # tx2
        p = db.get(Payment, payment_id, with_for_update=True)
        return self.apply_rail_result(db, p, result, actor=actor)

def apply_rail_result(self, db, p, r: RailResult, *, actor="system") -> Payment:
    if p.status != S.PROCESSING:
        return p                                                        # late/duplicate answer: ignore
    if r.status == "PAID":
        transition(db, p, S.PAID, action=A.RESULT, actor=actor, note="Provider confirmed",
                   provider_txn_id=r.txn_id, failure_reason=None, failure_detail=None)
    elif r.status == "FAILED":
        transition(db, p, S.FAILED, action=A.RESULT, actor=actor, note=r.failure_reason,
                   failure_reason=r.failure_reason, failure_detail=r.detail)
    else:                                                               # PENDING: timeline only
        db.add(PaymentEvent(payment_id=p.id, from_status=S.PROCESSING, to_status=S.PROCESSING,
                            action=A.RECONCILE, actor=actor, note=r.detail, at=get_clock().now(),
                            idempotency_key=p.idempotency_key, scenario_id=p.scenario_id))
    return p
```

`send_payment` must be called with the scenario context set (`use_scenario(release.scenario_id)`), so audit entries land in the right chain.

### Dispatch and release status (`app/services/payments/dispatch.py`)

```python
def dispatch_release(release_id) -> DispatchSummary:
    with SessionLocal() as db:
        rel = db.get(FundRelease, release_id)
        ids = db.scalars(select(Payment.id).join(Allocation)
              .where(Payment.fund_release_id == release_id, Payment.status == S.RESERVED)
              .order_by(Allocation.rank, Allocation.id)).all()
    with use_scenario(rel.scenario_id):
        for pid in ids:
            payment_service.send_payment(pid)
        with SessionLocal.begin() as db:
            finalise_release_status(db, release_id)
    return DispatchSummary(...)

def finalise_release_status(db, release_id):
    rel = db.get(FundRelease, release_id, with_for_update=True)
    if db.scalar(select(func.count()).where(Payment.fund_release_id == release_id,
                                            Payment.status == S.RESERVED)):
        return                                                          # still sending
    unmet = db.scalar(select(func.count()).where(Allocation.fund_release_id == release_id,
                                                 Allocation.status == "UNMET_NEED"))
    new = ReleaseStatus.PARTIALLY_RELEASED if unmet else ReleaseStatus.RELEASED
    if rel.status != new:
        old, rel.status, rel.released_at = rel.status, new, get_clock().now()
        get_audit().record(db, event_type="FUND_RELEASE_STATUS_CHANGED", entity_type="fund_release",
                           entity_id=rel.id, actor="system",
                           payload={"from": old, "to": new, "unmet_count": unmet})

def schedule_dispatch(release_id):
    if settings.PAYMENT_DISPATCH_MODE == "inline":
        dispatch_release(release_id)
    else:
        scheduler.run_now("payments.dispatch", args=[release_id])
```

### Timers (`app/services/payments/timers.py`)

```python
def run_payment_timers(now: datetime) -> TimerSummary:
    recon_cut = now - timedelta(minutes=settings.PAYMENT_RECONCILE_AFTER_MIN)
    stuck_cut = now - timedelta(hours=settings.PAYMENT_STUCK_HOURS)
    with SessionLocal() as db:
        recon_ids = db.scalars(scoped(select(Payment.id).where(
            Payment.status == S.PROCESSING, Payment.processing_since < recon_cut), Payment)).all()
    for pid in recon_ids:                                   # rail call outside tx
        key = ...  # read p.idempotency_key
        r = get_rail().get_status(key)
        if r.status != "PENDING":
            with SessionLocal.begin() as db:
                payment_service.apply_rail_result(db, db.get(Payment, pid, with_for_update=True), r)
    with SessionLocal.begin() as db:
        for p in db.scalars(scoped(select(Payment).where(
                Payment.status == S.PROCESSING, Payment.is_stuck.is_(False),
                Payment.processing_since < stuck_cut), Payment).with_for_update(skip_locked=True)):
            p.is_stuck = True
            ev = PaymentEvent(payment_id=p.id, from_status=S.PROCESSING, to_status=S.PROCESSING,
                              action=A.STUCK_FLAG, actor="system", at=now,
                              note=f"In Processing for more than {settings.PAYMENT_STUCK_HOURS} h",
                              scenario_id=p.scenario_id)
            db.add(ev); db.flush()
            get_audit().record(db, event_type="PAYMENT_STUCK_FLAGGED", entity_type="payment",
                               entity_id=p.id, actor="system",
                               payload={"payment_id": p.id, "district_id": p.district_id,
                                        "processing_since": p.processing_since, "payment_event_id": ev.id})
```

Strictly greater than 24 h: `processing_since < now − 24h`. One transaction for the stuck batch is fine (no I/O inside); if there are thousands, chunk by 500.

### Masking and queries (`app/services/payments/queries.py`)

```python
def mask_wallet(w: str | None) -> str | None:
    return None if not w else "****" + w[-4:]

def allowed_actions(user, p) -> list[str]:
    if user.role != Role.DC or user.district_id != p.district_id:
        return []
    acts = []
    if p.status == S.FAILED and p.retry_count < settings.PAYMENT_MAX_RETRIES:
        acts.append("RETRY")
    if p.status in (S.FAILED, S.RESERVED):
        acts.append("CANCEL")
    return acts
```

Counts: one `GROUP BY status` query with `count(*)`, `sum(amount_pkr)` and `count(*) FILTER (WHERE is_stuck)` over the filtered set; join `Beneficiary` only when `tehsil_id` or `beneficiary_type` filters are present.

## Configuration

| Key | Default | Notes |
|---|---|---|
| `SIM_RAIL_WALLET_LIMIT_PKR` | `40000` | Above this → `LIMIT_REACHED` on early attempts |
| `SIM_RAIL_LIMIT_CLEARS_AFTER_RETRIES` | `1` | Limit "resets" from this retry count on |
| `SIM_RAIL_FAILURE_RATE` | `0.0` (tests) / `0.05` (demo `.env`) | Seeded, per key |
| `SIM_RAIL_SEED` | `aab-demo` | Change to get a different but repeatable failure set |
| `SIM_RAIL_INVALID_SUFFIX` / `PENDING_SUFFIX` / `TIMEOUT_SUFFIX` | `000` / `999` / `998` | Wallet id suffix rules |
| `PAYMENT_STUCK_HOURS` | `24` | UC09-1a |
| `PAYMENT_TIMER_INTERVAL_MIN` | `15` | |
| `PAYMENT_RECONCILE_AFTER_MIN` | `10` | |
| `PAYMENT_DISPATCH_MODE` | `background` (live) / `inline` (tests, replay) | |
| `PAYMENT_MAX_RETRIES` | `3` | Shared with FR18 |

## Commands

```bash
alembic revision -m "fr17 payments"          # hand-edit enums and partial index
alembic upgrade head
pytest -q tests/unit/payments
pytest -q tests/integration/payments
pytest -q tests/contract/test_payments_api.py
pytest -q -m slow tests/integration/payments/test_queries.py::test_perf_50k
python scripts/seed_demo_payments.py --district PK605 --n 200
```

## Verification checklist

| Acceptance | How |
|---|---|
| US1-1, US1-6 | `test_reserve.py` |
| US1-2, US1-3, US1-4, US1-7 | `test_dispatch.py` |
| US1-5 | `test_reserve.py::test_pending_approval_not_payable` |
| US2-1/2/3, SC-001 | `test_state_machine.py` (25 pairs) |
| US3-1/2 | `test_queries.py` |
| US3-3, US3-4, US3-5, US3-6, SC-007 | `tests/contract/test_payments_api.py` |
| US4-1/2/3 | `test_timers.py` |
| US4-4 | `test_timers.py::test_scenario_scoped` + FR5 replay backtest shows stuck flag on day 2 for a `…999` wallet |
| US5-1/2/3, SC-008 | `test_simulated_rail.py` |
| SC-002 | audit entry count assertions in `test_state_machine.py` + `GET /audit/verify` valid after suite |
| SC-003 | autouse invariant fixture + `test_fund_invariants.py` |
| SC-004 | `test_state_machine.py::test_paid_enqueues_notice_same_tx` |
| SC-005 | `test_perf_50k` (manual note in PR) |

## Demo notes

- In the Sindh 2022 replay, the Watch release pays a few hundred synthetic households and shopkeepers in seconds; the Payments screen counts tick from Reserved to Paid.
- The synthetic register (FR10) should include a handful of wallets ending `000` (invalid), one shopkeeper above 40 000 PKR (limit; succeeds on retry in FR18), and one `999` (pending → stuck after the replay advances a day). This gives the DC screen one failure of each kind.
- Every payment row and the CSV carry `simulated=true`; the frontend shows the SIMULATED ribbon from `X-Simulated-Data`.

## Pitfalls

- Never call `get_rail().send()` inside a transaction that has recorded audit entries: the audit chain lock would be held during I/O and block every other writer.
- Always set the scenario context before dispatch and timers; otherwise replay payments write to the live audit chain.
- Do not bump `DisasterFund.version` on balance changes (FR6's optimistic lock).
- `Decimal * int` is fine; never mix in floats (e.g. a JSON-loaded float amount). Pydantic money fields must be `Decimal` with `max_digits=14, decimal_places=2`.
- Respect lock order payment → fund → audit to avoid deadlocks with FR18.
- `with_for_update(skip_locked=True)` returns nothing when another worker has the row; treat it as "already handled", not as an error.
- The `_SENT` registry is per process; with several Uvicorn workers, a reconcile may run in a different process and conclude `PROVIDER_TIMEOUT`. That is acceptable for the simulation; run one worker for the demo.
- Decrypt wallet ids only into the `RailInstruction`; never log the instruction.

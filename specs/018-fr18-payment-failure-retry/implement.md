# Implementation Guide: FR18 Payment failure reasons, retry and cancel

## Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T001–T002 | Modules and settings |
| 2 | T003–T004 → T005–T007 | Failure catalogue and pure rules proven |
| 3 | T008–T009 → T010–T011 | Retry (the demo moment) |
| 4 | T012 → T013–T014 | Cancel with ledger return |
| 5 | T015 → T016 | Access/note/scenario rules at the API |
| 6 | T017 → T018–T019 | Failure summary |
| 7 | T020–T022 | Invariants, chain check, lint |

## Key algorithms

### Failure catalogue (`app/services/payments/failure_reasons.py`)

```python
FAILURE_REASONS: dict[str, tuple[str, str]] = {
    "INVALID_WALLET":   ("FIX_WALLET_THEN_RETRY", "Correct the wallet in the beneficiary register, then retry; or cancel."),
    "NO_WALLET":        ("ADD_WALLET_OR_CANCEL",  "Add a wallet in the register, then retry; or cancel."),
    "LIMIT_REACHED":    ("RETRY_LATER",           "Retry later; the wallet limit may have reset."),
    "PROVIDER_TIMEOUT": ("RETRY",                 "Retry; the earlier instruction did not land."),
    "PROVIDER_ERROR":   ("RETRY",                 "Retry."),
}
RETRIES_USED = ("CANCEL_RETRIES_USED", "All retries used. Cancel to return the money to the fund.")

def suggested_action(reason: str, retry_count: int) -> tuple[str, str]:
    if retry_count >= settings.PAYMENT_MAX_RETRIES:
        return RETRIES_USED
    return FAILURE_REASONS[reason]
```

### Rules (`app/services/payments/actions.py`)

```python
def check_role(user: User) -> None:
    if user.role == Role.DONOR:
        raise Forbidden(code="DONOR_AGGREGATE_ONLY")
    if user.role != Role.DC:
        raise Forbidden(code="DC_ONLY_ACTION")

def check_district(user: User, p: Payment) -> None:
    if user.district_id != p.district_id:
        raise Forbidden(code="DISTRICT_FORBIDDEN")

def validate_note(note: str | None) -> str:
    n = (note or "").strip()
    if len(n) < settings.PAYMENT_NOTE_MIN_LEN:
        raise Unprocessable(code="NOTE_REQUIRED", detail=f"A note of at least {settings.PAYMENT_NOTE_MIN_LEN} characters is required.")
    if len(n) > settings.PAYMENT_NOTE_MAX_LEN:
        raise Unprocessable(code="NOTE_TOO_LONG", detail=f"Keep the note under {settings.PAYMENT_NOTE_MAX_LEN} characters.")
    return n
```

### Retry

```python
class PaymentActionService:
    def retry(self, payment_id: UUID, *, user: User, note: str, expected_retry_count: int) -> Payment:
        actor = str(user.id)
        # tx1: validate under lock and move FAILED -> PROCESSING with a new key
        with SessionLocal.begin() as db:
            p = db.execute(scoped(select(Payment).where(Payment.id == payment_id), Payment)
                           .with_for_update()).scalar_one_or_none()
            if p is None:
                raise NotFound(code="PAYMENT_NOT_FOUND")
            check_district(user, p)
            if p.status != S.FAILED:
                raise InvalidTransition(p.status, S.PROCESSING)
            if p.retry_count >= settings.PAYMENT_MAX_RETRIES:
                raise RetryLimitReached(p.id, p.retry_count)
            if p.retry_count != expected_retry_count:
                raise StalePaymentState(p.id, expected=expected_retry_count, actual=p.retry_count)
            new_count = p.retry_count + 1
            transition(db, p, S.PROCESSING, action=A.RETRY, actor=actor, note=note,
                       retry_count=new_count,
                       idempotency_key=f"{p.allocation_id}:{new_count}",
                       failure_reason=None, failure_detail=None)
            instr = build_instruction(p)              # FR17 helper: decrypts the CURRENT wallet
        # no transaction open: talk to the rail
        try:
            result = get_rail().send(instr)
        except RailTimeout:
            result = RailResult("PENDING", None, None, "Provider timeout; awaiting reconciliation")
        # tx2: apply the answer (FR17)
        with SessionLocal.begin() as db:
            p = db.get(Payment, payment_id, with_for_update=True)
            payment_service.apply_rail_result(db, p, result, actor="system")
            db.refresh(p)
            return p
```

Notes:
- `transition()` writes the `PaymentEvent` **after** applying `changes`, so the RETRY event carries the new key; the previous key is on the earlier events.
- The ordering of `if` checks after the lock is the normative order from plan.md (status → limit → stale).
- The route has already run `check_role()` and `validate_note()` before calling `retry()`.

### Cancel

```python
    def cancel(self, db, payment_id: UUID, *, user: User, note: str) -> Payment:
        p = db.execute(scoped(select(Payment).where(Payment.id == payment_id), Payment)
                       .with_for_update()).scalar_one_or_none()
        if p is None:
            raise NotFound(code="PAYMENT_NOT_FOUND")
        check_district(user, p)
        if p.status not in (S.FAILED, S.RESERVED):
            raise InvalidTransition(p.status, S.CANCELLED)
        transition(db, p, S.CANCELLED, action=A.CANCEL, actor=str(user.id), note=note)
        return p          # router commits (get_db dependency) and returns fund_after
```

The ledger effect (reserved −A, remaining +A) comes from FR17's `TRANSITIONS` table; FR18 never touches fund columns.

### Router (`app/api/v1/payment_actions.py`)

```python
router = APIRouter(prefix="/payments", tags=["Payments"])

@router.post("/{payment_id}/retry", response_model=ActionResultOut)
def retry(payment_id: UUID, body: RetryIn, user=Depends(get_current_user),
          _sid=Depends(get_scenario)):
    check_role(user)
    note = validate_note(body.note)
    p = actions.retry(payment_id, user=user, note=note, expected_retry_count=body.expected_retry_count)
    return to_action_result(p, user)

@router.post("/{payment_id}/cancel", response_model=ActionResultOut)
def cancel(payment_id: UUID, body: CancelIn, user=Depends(get_current_user),
           _sid=Depends(get_scenario), db=Depends(get_db)):
    check_role(user)
    note = validate_note(body.note)
    p = actions.cancel(db, payment_id, user=user, note=note)
    db.commit()
    return to_action_result(p, user, include_fund=True)

@router.get("/failures/summary", response_model=FailureSummaryOut)
def summary(f: PaymentFilter = Depends(), user=Depends(require_role(Role.PDMA_OFFICER, Role.DC, Role.ADMIN)),
            db=Depends(get_db)):
    return failure_summary(db, f, user)
```

Note: with this order a missing payment is checked after the role, so a donor always gets 403 and never learns whether a payment id exists. `RetryIn.note` is `str | None` so the API, not Pydantic, decides the 422 code and the check order holds. The donor role is excluded from `require_role` on the summary; add a `deny_donor` guard first so donors get `DONOR_AGGREGATE_ONLY` rather than the generic `ROLE_FORBIDDEN`.

### Failure summary query

```python
def failure_summary(db, f: PaymentFilter, user: User) -> FailureSummaryOut:
    q = build_payment_filter(select(Payment), f, user)        # FR17 queries.py helper (DC scoping inside)
    q = q.where(Payment.status == S.FAILED)
    rows = db.execute(select(Payment.failure_reason, func.count(), func.sum(Payment.amount_pkr),
                             func.count().filter(Payment.retry_count >= settings.PAYMENT_MAX_RETRIES))
                      .select_from(q.subquery()).group_by(Payment.failure_reason)).all()
    ...
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `PAYMENT_MAX_RETRIES` | `3` | Owned by FR17 settings, shared |
| `PAYMENT_NOTE_MIN_LEN` | `5` | After trimming |
| `PAYMENT_NOTE_MAX_LEN` | `500` | |
| `SIM_RAIL_LIMIT_CLEARS_AFTER_RETRIES` | `1` | FR17; makes a `LIMIT_REACHED` retry succeed in the demo |

## Commands

```bash
pytest -q tests/unit/payments/test_action_rules.py tests/unit/payments/test_failure_reasons.py
pytest -q tests/integration/payments/test_retry.py tests/integration/payments/test_cancel.py
pytest -q tests/integration/payments/test_action_concurrency.py
pytest -q tests/contract/test_payment_actions_api.py
curl -X POST localhost:8000/api/v1/payments/$PID/retry -H "Authorization: Bearer $DC_TOKEN" \
     -H "Content-Type: application/json" -d '{"note":"Limit reset, retrying","expected_retry_count":0}'
```

## Verification checklist

| Acceptance | How |
|---|---|
| US1-1, US1-3, US1-4 | `test_retry.py` |
| US1-2 | `test_retry.py::test_fourth_retry_refused` |
| US1-5, SC-004 | `test_action_concurrency.py` |
| US1-6 | `test_retry.py::test_retry_from_non_failed_refused` |
| US2-1, US2-2, US2-3, US2-4, SC-003 | `test_cancel.py` (+ FR17 autouse invariant fixture) |
| US3-1..5, SC-001 | `tests/contract/test_payment_actions_api.py` matrix |
| US4-1, US4-2 | `test_failure_summary.py` |
| SC-002 | audit count assertions in `test_retry.py`/`test_cancel.py`; `test_chain_valid_after_actions` |
| SC-005 | timing assertion in `test_retry.py::test_retry_under_1s` (simulated rail) |
| SC-006 | `test_retry.py::test_note_not_in_audit_payload` |

## Demo notes

- During the Sindh 2022 replay, after the Warning release, the DC (phone or dashboard) opens Payments → Failed (2). One shopkeeper shows `LIMIT_REACHED`. The DC types "Limit reset, retrying" and taps Retry: it turns Paid and the beneficiary's WhatsApp payment notice goes out (FR14). About 20 seconds of the 3-minute demo.
- Optionally cancel a `NO_WALLET` household with a note, and show the fund's remaining balance going up by that amount, and the audit entry in the log.
- Keep the demo `.env` with `SIM_RAIL_LIMIT_CLEARS_AFTER_RETRIES=1`, otherwise the retry fails again on stage.

## Pitfalls

- Do **not** call the rail inside tx1; commit first. The audit chain lock is held until commit.
- Register `payment_actions.router` before FR17's router, or `GET /payments/failures/summary` is routed to `GET /payments/{payment_id}` and fails UUID parsing (422).
- Pydantic would return a generic 422 for a missing `note` if it were required in the schema; keep it optional and validate in `validate_note()` so codes and check order match the spec.
- Never write the note into the audit payload; `transition()` already stores `note_sha256`.
- Lock order is payment → fund → audit (same as FR17); never lock the fund first in FR18 code.
- The `expected_retry_count` check must run under the row lock, or two requests can both pass it.

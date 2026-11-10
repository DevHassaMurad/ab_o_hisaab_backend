# Implementation Plan: FR17 Payment tracking

**Branch**: `017-fr17-payment-tracking` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/017-fr17-payment-tracking/spec.md`

## Summary

One choke-point function, `transition()`, owns every `Payment.status` change: it validates against the transition table, locks the fund row, applies the ledger delta, writes a `PaymentEvent`, records the audit entry and (on Paid) queues the payment notice, all in the caller's transaction. `PaymentService` reserves payments for a payable release, dispatches them in rank order through the `PaymentRail` (rail calls happen between transactions), and a 15-minute timer reconciles timeouts and flags payments stuck for more than 24 h. Read endpoints give list, detail with timeline, counts and CSV, scoped by role and scenario.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, APScheduler (foundation wrapper), stdlib `csv`, `hashlib`, `decimal`

**Storage**: PostgreSQL 16 (`payment`, `payment_event`, `disaster_fund`, `fund_release`, `allocation`, `beneficiary`)

**Testing**: pytest, httpx TestClient, foundation factories, a frozen/fake `Clock`

**Target Platform**: Linux container; Windows/macOS dev

**Project Type**: web-service

**Performance Goals**: list/counts p95 < 500 ms at 50 000 payments (NFR1); dispatch ≥ 100 payments/s with the simulated rail; payment notice queued in the same transaction as Paid (NFR2)

**Constraints**: `Decimal`/`NUMERIC(14,2)` only; no rail call inside a transaction; READ COMMITTED; idempotent reserve and dispatch

**Scale/Scope**: up to 50 000 beneficiaries per district register; a release typically pays hundreds to a few thousand

## Constitution Check

| Principle | How satisfied | ✅/⚠️ |
|---|---|---|
| II Human authority | No payment is created for a `PENDING_APPROVAL` release; Action payments are reserved only when FR8 approves | ✅ |
| III Traceability | `transition()` writes `PaymentEvent` + `AuditService.record("PAYMENT_STATUS_CHANGED")` in the same transaction as the ledger update; stuck flags and release status changes are audited too | ✅ |
| VI Privacy | Donor 403 on all `/payments` routes; masked wallet; CSV without name/phone/CNIC/wallet; audit payloads hold ids and `note_sha256` only | ✅ |
| VII Degrade gracefully | Rail timeouts stay Processing and are reconciled; stuck flag surfaces silent failures | ✅ |
| VIII Honest prototype | `SimulatedRail` only; `simulated=true`, provider `SIMULATED_*`, `X-Simulated-Data` header | ✅ |
| IX Config over code | Wallet limit, failure rate, seed, stuck hours, timer interval, dispatch mode are settings | ✅ |
| X Simplicity | In-process scheduler; no queue broker; one service module | ✅ |
| XI Test the money paths | State machine, ledger and rail tests written first | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Single `transition()` choke point with a static table | One place to test; FR18 reuses it | Status setters spread across services |
| Lock order: payment row (`FOR UPDATE`) then fund row (`FOR UPDATE`) then audit advisory lock | Fixed order avoids deadlocks | Optimistic `version` on fund (would collide with FR6 settings edits) |
| SQL-side increments on fund balances | Exact and race-free under the row lock | Read-modify-write in Python |
| Rail call between two transactions | Never hold fund/audit locks during I/O | One long transaction around the rail call |
| Timeout ⇒ stay Processing + reconcile via `get_status` | A timeout is ambiguous; avoids double pay on retry | Mark Failed immediately |
| Deterministic `SimulatedRail` (suffix rules + `sha256(seed:key)` draw) | Repeatable demos and backtests; idempotent by construction | `random.random()` |
| `FOR UPDATE SKIP LOCKED` when dispatching | Two dispatchers never send the same payment | Advisory lock per release |
| Denormalise `district_id`, `fund_release_id` onto `Payment` | Fast DC scoping and counts without 3 joins | Join through allocation → release → fund on every read |
| Stream CSV with a server-side cursor | Constant memory at 50 000 rows | Build the file in memory |

## Data model slice

| Entity (data-model.md) | Fields used | R/W |
|---|---|---|
| `Payment` §6 | all | RW |
| `PaymentEvent` §6 | all | W (R for timeline) |
| `DisasterFund` §4 | `allocated_pkr`, `reserved_pkr`, `disbursed_pkr`, `remaining_pkr`, `district_id` | RW (balances only) |
| `FundRelease` §4 | `id`, `event_id`, `fund_id`, `tier`, `status`, `released_at`, `simulated`, `scenario_id` | R, W (`status`, `released_at`) |
| `Allocation` §5 | `id`, `fund_release_id`, `beneficiary_id`, `rank`, `final_amount_pkr`, `status` | R |
| `Beneficiary` §5 | `id`, `type`, `tehsil_id`, `name` 🔒, `wallet_provider`, `wallet_id` 🔒 | R |
| `Alert` §7 | created via `AlertService.enqueue` | W (through FR14) |
| `AuditLog` §8 | via `AuditService.record` | W |

### Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Change | Why |
|---|---|---|
| `DisasterFund` | **Correct** the note on `reserved_pkr`: "Sum of `Payment` rows in Reserved, Processing **or Failed**" | Failed money is held for retry until cancelled (FR18) |
| `Payment` | **Add** `fund_release_id: FK FundRelease` (indexed) | Filter/count per release without joins; set at reserve |
| `Payment` | **Add** `district_id: FK District` (indexed) | DC scoping and counts (NFR1) |
| `Payment` | **Change** `provider` to nullable | Null when the beneficiary's `wallet_provider = NONE` |
| `Payment` | **Add** index `(scenario_id, district_id, status)` and partial index `WHERE status = 'PROCESSING'` | Counts and the stuck timer |
| `PaymentEvent` | **Add** `action: enum RESERVE, SEND, RESULT, RETRY, CANCEL, STUCK_FLAG, RECONCILE` | Timeline readability; FR18 filters on it |
| `PaymentEvent` | **Add** `idempotency_key: str, nullable`, `failure_reason: enum, nullable`, `provider_txn_id: str, nullable` | Keeps the history of each attempt (the `Payment` row only holds the latest) |
| `PaymentEvent` | **Add** `scenario_id: FK, nullable` | Shared rule 2: every operational write carries `scenario_id` |

## Transition table (normative)

| # | From | Action | To | Who | Ledger effect (A = amount) | Other effects | Audit `event_type` |
|---|---|---|---|---|---|---|---|
| 0 | — | `RESERVE` | RESERVED | system | reserved +A, remaining −A | `idempotency_key={alloc}:0`, `retry_count=0` | `PAYMENT_STATUS_CHANGED` |
| 1 | RESERVED | `SEND` | PROCESSING | system | — | `processing_since=now`, `is_stuck=false` | `PAYMENT_STATUS_CHANGED` |
| 2 | PROCESSING | `RESULT` ok | PAID | system | reserved −A, disbursed +A | `paid_at`, `provider_txn_id`, `is_stuck=false`, enqueue `PAYMENT_NOTICE` | `PAYMENT_STATUS_CHANGED` |
| 3 | PROCESSING | `RESULT` fail | FAILED | system | — | `failure_reason`, `failure_detail`, `is_stuck=false` | `PAYMENT_STATUS_CHANGED` |
| 4 | FAILED | `RETRY` | PROCESSING | DC (FR18) | — | `retry_count+1` (≤ 3), new key, `processing_since=now`, clear failure fields | `PAYMENT_STATUS_CHANGED` |
| 5 | FAILED | `CANCEL` | CANCELLED | DC (FR18) | reserved −A, remaining +A | `cancelled_at` | `PAYMENT_STATUS_CHANGED` |
| 6 | RESERVED | `CANCEL` | CANCELLED | DC (FR18) | reserved −A, remaining +A | `cancelled_at` | `PAYMENT_STATUS_CHANGED` |
| — | PROCESSING | `STUCK_FLAG` | (no change) | system | — | `is_stuck=true` | `PAYMENT_STUCK_FLAGGED` |
| — | PROCESSING | `RECONCILE` pending | (no change) | system | — | timeline note only | — |
| ✗ | any other pair | — | — | — | — | 409 `INVALID_TRANSITION` | — |

Terminal: PAID, CANCELLED. Rows 4–6 are executed by FR18 through the same `transition()`.

Audit payload for `PAYMENT_STATUS_CHANGED`:
```json
{"payment_id": "…", "allocation_id": "…", "fund_release_id": "…", "district_id": "…",
 "from": "PROCESSING", "to": "PAID", "action": "RESULT", "amount_pkr": "7000.00",
 "retry_count": 0, "idempotency_key": "…:0", "provider": "SIMULATED_JAZZCASH",
 "provider_txn_id": "SIMJC-4be1c9a0d2f3", "failure_reason": null,
 "payment_event_id": "…", "note_sha256": null, "simulated": true,
 "fund_after": {"reserved_pkr": "…", "disbursed_pkr": "…", "remaining_pkr": "…"}}
```

## PaymentRail contract (`app/integrations/payment_rail.py`)

```python
@dataclass(frozen=True)
class RailInstruction:
    payment_id: UUID
    idempotency_key: str
    amount_pkr: Decimal
    wallet_provider: Literal["JAZZCASH", "EASYPAISA", "NONE"]
    wallet_id: str | None          # decrypted, never logged
    retry_count: int

@dataclass(frozen=True)
class RailResult:
    status: Literal["PAID", "FAILED", "PENDING"]
    txn_id: str | None
    failure_reason: FailureReason | None
    detail: str | None = None

class RailTimeout(Exception): ...

class PaymentRail(Protocol):
    simulated: bool
    def send(self, instruction: RailInstruction) -> RailResult: ...      # may raise RailTimeout
    def get_status(self, idempotency_key: str) -> RailResult: ...
```

## API contracts

Base `/api/v1`, tag `Payments`. `X-Scenario-Id` scopes all reads. Money as string decimals. Problem JSON errors.

### `GET /payments` (`PDMA_OFFICER`, `DC`, `ADMIN`; `DONOR` → 403)

Query: `district_id`, `tehsil_id`, `beneficiary_type` (`HOUSEHOLD|SHOPKEEPER`), `status` (repeatable), `fund_release_id`, `event_id`, `is_stuck` (bool), `failure_reason`, `sort` (`-created_at` default, `amount_pkr`, `-amount_pkr`, `status`), `page`, `page_size` (≤ 200).

200:
```json
{
  "items": [{
    "id": "8c1f…", "allocation_id": "a91e…", "fund_release_id": "f22b…", "district_id": "d01…",
    "tier": "WARNING",
    "beneficiary": {"id": "b77…", "type": "SHOPKEEPER", "tehsil_id": "t12…", "tehsil_name": "Johi",
                    "display_name": "Ghulam R.", "wallet_masked": "****4821", "wallet_provider": "JAZZCASH"},
    "amount_pkr": "20000.00", "status": "FAILED", "failure_reason": "LIMIT_REACHED",
    "failure_detail": "Simulated: amount above wallet limit 40000.00",
    "retry_count": 0, "is_stuck": false, "provider": "SIMULATED_JAZZCASH", "provider_txn_id": null,
    "processing_since": null, "paid_at": null, "cancelled_at": null,
    "created_at": "2026-11-10T06:05:12.004Z", "simulated": true
  }],
  "total": 412, "page": 1, "page_size": 50
}
```
Errors: 403 `DONOR_AGGREGATE_ONLY` (donor), 403 `DISTRICT_FORBIDDEN` (DC asks for another district), 422 `INVALID_FILTER`.

### `GET /payments/counts` (`PDMA_OFFICER`, `DC`, `ADMIN`; `DONOR` → 403)

Same filters except paging/sort.
```json
{
  "by_status": {"RESERVED": 12, "PROCESSING": 30, "PAID": 340, "FAILED": 22, "CANCELLED": 8},
  "amount_by_status_pkr": {"RESERVED": "84000.00", "PROCESSING": "210000.00", "PAID": "2380000.00",
                           "FAILED": "154000.00", "CANCELLED": "56000.00"},
  "stuck": 3, "total": 412, "total_amount_pkr": "2884000.00", "simulated": true
}
```

### `GET /payments/{payment_id}` (`PDMA_OFFICER`, `DC` own district, `ADMIN`)

200: the list item plus
```json
{
  "timeline": [
    {"at": "2026-11-10T06:05:12.004Z", "from_status": null, "to_status": "RESERVED", "action": "RESERVE", "actor": "system", "note": "Reserved for WARNING release", "idempotency_key": "a91e…:0"},
    {"at": "2026-11-10T06:05:12.410Z", "from_status": "RESERVED", "to_status": "PROCESSING", "action": "SEND", "actor": "system", "note": "Sent to SIMULATED_JAZZCASH"},
    {"at": "2026-11-10T06:05:12.415Z", "from_status": "PROCESSING", "to_status": "FAILED", "action": "RESULT", "actor": "system", "note": "LIMIT_REACHED", "failure_reason": "LIMIT_REACHED"}
  ],
  "allowed_actions": ["RETRY", "CANCEL"]
}
```
`allowed_actions` is computed for the caller (FR18 rules: DC of that district only; RETRY only if FAILED and `retry_count < 3`; CANCEL if FAILED or RESERVED). Errors: 404 `PAYMENT_NOT_FOUND` (also when the payment is in another scenario), 403 as above.

### `GET /payments/export.csv` (`PDMA_OFFICER`, `DC`, `ADMIN`)

Same filters as the list (no paging). `text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="payments_{district}_{yyyymmddThhmmZ}.csv"`. Columns:

`payment_id,district_pcode,tehsil_pcode,tehsil_name,beneficiary_ref,beneficiary_type,tier,amount_pkr,status,failure_reason,retry_count,is_stuck,provider,provider_txn_id,processing_since,paid_at,cancelled_at,created_at,simulated`

`beneficiary_ref` = first 8 chars of the beneficiary UUID. Side effect: audit `PAYMENTS_EXPORTED` `{filters, row_count}`.

### `POST /payments/dispatch` (`PDMA_OFFICER`, `ADMIN`)

Request `{"fund_release_id": "f22b…"}`. Re-runs dispatch for any payments of that release still in RESERVED (recovery tool). 202 `{"queued": 12}`. Errors: 404 `RELEASE_NOT_FOUND`, 409 `RELEASE_NOT_PAYABLE` (status `PENDING_APPROVAL` or `REJECTED`).

### Error codes (FR17)

| HTTP | code | When |
|---|---|---|
| 409 | `INVALID_TRANSITION` | Pair not in the transition table |
| 409 | `INSUFFICIENT_FUND_BALANCE` | Reserve would make `remaining_pkr < 0` |
| 409 | `RELEASE_NOT_PAYABLE` | Reserve/dispatch on a non-payable release |
| 403 | `DONOR_AGGREGATE_ONLY` | Donor on any `/payments` route |
| 403 | `DISTRICT_FORBIDDEN` | DC outside own district |
| 404 | `PAYMENT_NOT_FOUND`, `RELEASE_NOT_FOUND` | |
| 422 | `INVALID_FILTER` | Bad enum/uuid/page size |

## Scheduled jobs

| Job id | Trigger | What it does |
|---|---|---|
| `payments.timers` | every `PAYMENT_TIMER_INTERVAL_MIN` (15) min, registered by `register_payment_timers()` in `app/jobs/timers.py` | `run_payment_timers(clock.now())` for the live scope: (1) reconcile Processing payments older than `PAYMENT_RECONCILE_AFTER_MIN` (10) via `rail.get_status`; (2) flag `is_stuck` where `now − processing_since > PAYMENT_STUCK_HOURS` (24) and not yet stuck |
| `payments.dispatch` | on demand via `scheduler.run_now("payments.dispatch", release_id)` | `dispatch_release(release_id)` when `PAYMENT_DISPATCH_MODE=background` |

Replay: FR5 calls `run_payment_timers(clock.now())` inside `use_scenario(sid)` after each replay step.

## Project Structure

### Documentation (this feature)

```text
specs/017-fr17-payment-tracking/
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
├── models/payments.py                   # extend: fund_release_id, district_id, nullable provider; PaymentEvent.action/... ; indexes
├── schemas/payments.py                  # PaymentOut, BeneficiaryRef, PaymentDetailOut, TimelineEventOut, PaymentCountsOut, PaymentFilter, DispatchIn
├── api/v1/payments.py                   # GET /payments, /payments/counts, /payments/{id}, /payments/export.csv, POST /payments/dispatch
├── integrations/payment_rail.py         # RailInstruction, RailResult, RailTimeout, SimulatedRail, get_rail()
├── services/payments/
│   ├── __init__.py
│   ├── errors.py                        # InvalidTransition, InsufficientFundBalance, ReleaseNotPayable
│   ├── state_machine.py                 # TRANSITIONS table, transition()
│   ├── ledger.py                        # lock_fund(), apply_delta(), check_fund_invariants()
│   ├── service.py                       # PaymentService: reserve_for_release, send_payment, apply_rail_result
│   ├── dispatch.py                      # dispatch_release(), schedule_dispatch(), finalise_release_status()
│   ├── timers.py                        # run_payment_timers(): reconcile + stuck flag
│   ├── queries.py                       # list_payments(), count_by_status(), get_payment_detail(), allowed_actions()
│   └── csv_export.py                    # iter_payments_csv()
└── jobs/timers.py                       # register_payment_timers(scheduler)  (only this function)
alembic/versions/<rev>_fr17_payments.py
tests/
├── unit/payments/{test_state_machine,test_ledger,test_simulated_rail}.py
├── integration/payments/{conftest,test_reserve,test_dispatch,test_timers,test_queries,test_fund_invariants}.py
└── contract/test_payments_api.py
```

**Structure Decision**: single project per the constitution. FR18 adds `app/services/payments/actions.py` and `app/api/v1/payment_actions.py` beside these files without editing them (except `allowed_actions()` which FR18 may extend).

## Complexity Tracking

None.

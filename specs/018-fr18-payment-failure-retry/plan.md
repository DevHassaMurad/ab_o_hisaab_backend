# Implementation Plan: FR18 Payment failure reasons, retry and cancel

**Branch**: `018-fr18-payment-failure-retry` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/018-fr18-payment-failure-retry/spec.md`

## Summary

Two DC-only commands on top of FR17's state machine. **Retry**: validate (role, district, note, status, limit, expected count), then in one transaction bump `retry_count`, set the new idempotency key, clear failure fields and call `transition(FAILED → PROCESSING, action=RETRY)`; commit; call the rail with a freshly built instruction; apply the result in a second transaction. **Cancel**: validate, then one transaction `transition(FAILED|RESERVED → CANCELLED, action=CANCEL)`, which returns the money to `remaining_pkr`. A failure summary endpoint aggregates failed payments by reason with suggested actions.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x (no new packages)

**Storage**: PostgreSQL 16 (`payment`, `payment_event`, `disaster_fund`, `beneficiary`, `audit_log`)

**Testing**: pytest, httpx TestClient, FR17 fixtures (`fund_with_release`, `fake_clock`, autouse fund invariant check), thread-based concurrency test

**Target Platform**: Linux container; Windows/macOS dev

**Project Type**: web-service

**Performance Goals**: retry < 1 s end to end with the simulated rail; summary p95 < 500 ms at 50 000 payments (NFR1)

**Constraints**: no rail call inside a transaction; lock order payment → fund → audit (same as FR17); `Decimal` money

**Scale/Scope**: tens to hundreds of failed payments per release

## Constitution Check

| Principle | How satisfied | ✅/⚠️ |
|---|---|---|
| II Human authority | Only the DC of the district can re-send or cancel money; no automatic retries | ✅ |
| III Traceability | Every action is a `transition()` call: `PaymentEvent` + audit in the same transaction; retry result audited separately | ✅ |
| VI Privacy | Note only on `PaymentEvent`; audit has `note_sha256`; donors 403 | ✅ |
| VIII Honest prototype | Retries go to `SimulatedRail`; responses keep `simulated=true` | ✅ |
| IX Config over code | `PAYMENT_MAX_RETRIES`, note length bounds are settings | ✅ |
| X Simplicity | Two endpoints + one summary; reuses FR17 entirely | ✅ |
| XI Test the money paths | Rule matrix, ledger on cancel and concurrency tests written first | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Reuse FR17 `transition()` for both actions | One state machine, one test matrix | A separate status setter in FR18 |
| `expected_retry_count` in the retry body | Prevents double-click double retries without a request-key table | `Idempotency-Key` header store (new table); no protection |
| Increment `retry_count` and set the new key **before** the rail call, in the RETRY transaction | The key used is recorded before any money can move; a crash leaves a Processing payment that reconcile can resolve by that key | Incrementing after the result (a crash could reuse a key or lose the count) |
| Cancel allowed from Reserved too | Lets the DC stop a wrong payment before it is sent | Failed only (would force a pointless send) |
| No cancel from Processing | Money may be in flight | Allowing it (double spend risk) |
| Failure catalogue as code constants with stable codes | Small, versioned with the code, testable | DB table (overkill for 5 values) |

## Data model slice

| Entity (data-model.md) | Fields | R/W |
|---|---|---|
| `Payment` §6 | `status`, `failure_reason`, `failure_detail`, `retry_count`, `idempotency_key`, `processing_since`, `cancelled_at`, `district_id`*, `amount_pkr`, `scenario_id` | RW |
| `PaymentEvent` §6 | `from_status`, `to_status`, `actor`, `note`, `at`, `action`*, `idempotency_key`*, `failure_reason`* | W |
| `DisasterFund` §4 | `reserved_pkr`, `remaining_pkr` | W (via FR17 ledger) |
| `Beneficiary` §5 | `wallet_provider`, `wallet_id` 🔒 | R (at retry) |
| `User` §8 | `role`, `district_id` | R |

\* Added by `017-fr17-payment-tracking` (see its "Proposed data-model additions").

### Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

None beyond those proposed in `017-fr17-payment-tracking/plan.md` (`Payment.district_id`, `PaymentEvent.action`, `PaymentEvent.idempotency_key`, `PaymentEvent.failure_reason`). FR18 depends on them.

## Rules (normative)

| Check (in this order) | Failure → HTTP / code |
|---|---|
| Authenticated | 401 |
| Role is `DONOR` | 403 `DONOR_AGGREGATE_ONLY` |
| Role is not `DC` (officer, admin) | 403 `DC_ONLY_ACTION` |
| Payment exists in the current scenario scope | 404 `PAYMENT_NOT_FOUND` |
| `user.district_id == payment.district_id` | 403 `DISTRICT_FORBIDDEN` |
| Note present, 5 ≤ len(trimmed) ≤ 500 | 422 `NOTE_REQUIRED` / `NOTE_TOO_LONG` |
| Retry: status is `FAILED`; Cancel: status in `FAILED`, `RESERVED` | 409 `INVALID_TRANSITION` |
| Retry: `retry_count < PAYMENT_MAX_RETRIES` (3) | 409 `RETRY_LIMIT_REACHED` |
| Retry: `expected_retry_count == retry_count` | 409 `STALE_PAYMENT_STATE` |

Status, limit and stale checks run **after** taking the payment row lock.

## API contracts

Base `/api/v1`, tag `Payments`, router `app/api/v1/payment_actions.py` mounted at `/payments`. `X-Scenario-Id` scopes the payment lookup.

### `POST /payments/{payment_id}/retry` (`DC` of that district)

Request:
```json
{"note": "Provider says limit reset today; retrying", "expected_retry_count": 0}
```
200 (after the rail answered):
```json
{
  "id": "8c1f…", "status": "PAID", "retry_count": 1, "idempotency_key": "a91e…:1",
  "amount_pkr": "45000.00", "failure_reason": null, "failure_detail": null,
  "provider": "SIMULATED_JAZZCASH", "provider_txn_id": "SIMJC-0be41a9d7c21",
  "is_stuck": false, "simulated": true,
  "allowed_actions": [],
  "timeline_tail": [
    {"at": "2026-11-10T07:41:02.110Z", "from_status": "FAILED", "to_status": "PROCESSING", "action": "RETRY",
     "actor": "4d2c…(dc.dadu)", "note": "Provider says limit reset today; retrying", "idempotency_key": "a91e…:1"},
    {"at": "2026-11-10T07:41:02.160Z", "from_status": "PROCESSING", "to_status": "PAID", "action": "RESULT",
     "actor": "system", "note": "Provider confirmed"}
  ]
}
```
Errors: see the rules table. Example 409:
```json
{"type": "about:blank", "title": "Conflict", "status": 409, "code": "RETRY_LIMIT_REACHED",
 "detail": "Payment 8c1f… has used 3 of 3 retries. Cancel it instead."}
```

### `POST /payments/{payment_id}/cancel` (`DC` of that district)

Request:
```json
{"note": "No wallet; family moved to Dadu camp, support given in kind"}
```
200:
```json
{
  "id": "77aa…", "status": "CANCELLED", "amount_pkr": "7000.00", "cancelled_at": "2026-11-10T07:45:10.020Z",
  "retry_count": 0, "failure_reason": "NO_WALLET", "simulated": true, "allowed_actions": [],
  "fund_after": {"reserved_pkr": "193000.00", "disbursed_pkr": "2380000.00", "remaining_pkr": "47427000.00"}
}
```
Errors: rules table (no limit/stale checks).

### `GET /payments/failures/summary` (`DC` own district, `PDMA_OFFICER`, `ADMIN`; `DONOR` → 403)

Query: FR17 list filters (`district_id`, `tehsil_id`, `beneficiary_type`, `fund_release_id`, `event_id`).

200:
```json
{
  "total_failed": 6, "total_failed_amount_pkr": "118000.00", "retries_exhausted": 1,
  "by_reason": [
    {"reason": "INVALID_WALLET", "count": 3, "amount_pkr": "21000.00", "suggested_action_code": "FIX_WALLET_THEN_RETRY",
     "suggested_action": "Correct the wallet in the beneficiary register, then retry; or cancel."},
    {"reason": "LIMIT_REACHED", "count": 2, "amount_pkr": "90000.00", "suggested_action_code": "RETRY_LATER",
     "suggested_action": "Retry later; the wallet limit may have reset."},
    {"reason": "NO_WALLET", "count": 1, "amount_pkr": "7000.00", "suggested_action_code": "ADD_WALLET_OR_CANCEL",
     "suggested_action": "Add a wallet in the register, then retry; or cancel."}
  ],
  "simulated": true
}
```

## Audit entries

Through FR17 `transition()`: `PAYMENT_STATUS_CHANGED` with `action` `RETRY` or `CANCEL`, `actor` = DC user id, `note_sha256`, `payment_event_id`, `retry_count`, `idempotency_key` (the new one; the previous key is on the earlier `PaymentEvent` and audit entry), `fund_after`. The retry's result adds a second `PAYMENT_STATUS_CHANGED` (`action=RESULT`, actor `system`).

## Scheduled jobs

None (stuck/reconcile is FR17's timer).

## Project Structure

### Documentation (this feature)

```text
specs/018-fr18-payment-failure-retry/
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
├── schemas/payment_actions.py           # RetryIn, CancelIn, ActionResultOut, FailureSummaryOut, ReasonRow
├── api/v1/payment_actions.py            # POST /payments/{id}/retry, POST /payments/{id}/cancel, GET /payments/failures/summary
└── services/payments/
    ├── actions.py                       # PaymentActionService.retry/cancel, check_role(), check_district(), validate_note(), failure_summary()
    └── failure_reasons.py               # FAILURE_REASONS catalogue, suggested_action()
tests/
├── unit/payments/{test_action_rules,test_failure_reasons}.py
├── integration/payments/{test_retry,test_cancel,test_failure_summary,test_action_concurrency}.py
└── contract/test_payment_actions_api.py
```

**Structure Decision**: single project per the constitution; a separate router module avoids edit conflicts with FR17's `app/api/v1/payments.py`. Register `payment_actions.router` **before** FR17's router in `app/api/v1/__init__.py` so `/payments/failures/summary` is not captured by `/payments/{payment_id}`.

## Complexity Tracking

None.

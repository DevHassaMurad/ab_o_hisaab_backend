# Agent Brief: FR18 Payment failure reasons, retry and cancel

## Owner role
Backend & alerts.

## Mission
Give the Deputy Commissioner of a district the tools to fix failed payments: see why each one failed, retry it (at most 3 times, each with a new idempotency key) or cancel it so the money goes back to the district fund. Every action needs a written note, goes through the FR17 state machine, and is audited in the same transaction.

## Scope
**In (MVP)**
- `POST /api/v1/payments/{id}/retry` and `POST /api/v1/payments/{id}/cancel` (DC of the payment's district only, note required)
- Retry rules: only from Failed, `retry_count < 3`, optimistic check on `expected_retry_count`, new key `{allocation_id}:{retry_count}`, wallet re-read from the register at send time
- Cancel rules: from Failed or Reserved; ledger returns the amount to `remaining_pkr`
- `GET /api/v1/payments/failures/summary`: failed counts and amounts by reason, retries exhausted, suggested action per reason
- Failure-reason catalogue with suggested actions (`app/services/payments/failure_reasons.py`)

**Out**
- The state machine, ledger, rail and timeline: `017-fr17-payment-tracking` (FR18 calls `transition()` and `send_payment` helpers from there).
- Editing a beneficiary's wallet: `010-fr10-beneficiary-register` (officer). FR18 only re-reads it.
- Re-allocating cancelled money to Unmet-need beneficiaries: not in the MVP (see clarify.md).
- Automatic retries without a human: not in the MVP.
- WhatsApp commands for retry/cancel: roadmap.

## Read first
- `.specify/memory/constitution.md` (Principles II, III, VI, XI)
- `specs/_authoring-guide.md` (FR18 facts)
- `specs/000-foundation/data-model.md` §6 `Payment`, `PaymentEvent`; §4 `DisasterFund`
- `specs/017-fr17-payment-tracking/plan.md` (transition table, `transition()`, ledger, rail) and `implement.md`
- `specs/020-fr20-audit-log/agent.md` (audit hand-off rules)
- This folder: `spec.md`, `clarify.md`, `plan.md`

## Depends on / Blocks
- **Depends on**: `000-foundation`, `020-fr20-audit-log`, `017-fr17-payment-tracking` (`transition()`, `PaymentService.apply_rail_result`, `build_instruction`, `SimulatedRail`).
- **Uses**: `010-fr10-beneficiary-register` (current wallet at retry time).
- **Blocks**: `019-fr19-donor-report` only loosely (cancelled money shows as unused).

## Interfaces
- **Provides**: `PaymentActionService.retry(db_factory, payment_id, *, user, note, expected_retry_count) -> Payment`, `PaymentActionService.cancel(db, payment_id, *, user, note) -> Payment`, `failure_summary(db, filters) -> FailureSummary`, `FAILURE_REASONS` catalogue.
- **Consumes**: `transition()` and `PaymentService` (FR17), `get_rail()` (`PaymentRail`), `AuditService.record` (via `transition()`), `get_current_user`, `current_scenario()`.

## Files you own
- `app/services/payments/actions.py`
- `app/services/payments/failure_reasons.py`
- `app/schemas/payment_actions.py`
- `app/api/v1/payment_actions.py` (mounted under `/payments`, tag `Payments`)
- `tests/unit/payments/test_action_rules.py`, `test_failure_reasons.py`
- `tests/integration/payments/test_retry.py`, `test_cancel.py`, `test_failure_summary.py`, `test_action_concurrency.py`
- `tests/contract/test_payment_actions_api.py`

## Guardrails
- **II**: only the DC of the payment's district can retry or cancel. The PDMA officer, admin and donor cannot (officials hold authority, the field fix sits with the DC per the document's actor table).
- **III**: each action is one `transition()` call: payment, ledger, `PaymentEvent` (with the note) and audit entry in one transaction. The rail call happens after commit.
- **VI**: the note is stored on `PaymentEvent.note`; the audit payload carries only `note_sha256` and the event id. Notes are never sent to donors.
- **VIII**: retries in the MVP go to the `SimulatedRail`; the response keeps `simulated=true`.
- **XI**: tests first for every rule (role, district, note, limit, status, stale count) and for the ledger effect of cancel.

## Definition of Done
- [ ] Retry from Failed with a note by the right DC → Processing → Paid/Failed; `retry_count` +1; new key `{allocation_id}:{n}`; old key kept in the timeline
- [ ] 4th retry refused with 409 `RETRY_LIMIT_REACHED`; cancel still allowed
- [ ] Cancel from Failed or Reserved returns the amount to `remaining_pkr`; cancel from Processing/Paid/Cancelled → 409 `INVALID_TRANSITION`
- [ ] Missing/blank/short note → 422 `NOTE_REQUIRED`; > 500 chars → 422 `NOTE_TOO_LONG`
- [ ] Officer/admin → 403 `DC_ONLY_ACTION`; DC of another district → 403 `DISTRICT_FORBIDDEN`; donor → 403 `DONOR_AGGREGATE_ONLY`
- [ ] Stale `expected_retry_count` → 409 `STALE_PAYMENT_STATE`; two parallel retries → exactly one succeeds
- [ ] Each action = exactly one audit entry for the action itself (plus one for the rail result on retry); chain verifies
- [ ] Failure summary matches the list filtered by `status=FAILED`
- [ ] `pytest tests/unit/payments/test_action_rules.py tests/integration/payments/test_retry.py tests/integration/payments/test_cancel.py tests/contract/test_payment_actions_api.py` green

## Hand-off notes
- Frontend: show Retry/Cancel only when `allowed_actions` (FR17 detail) includes them; always send `expected_retry_count` with a retry; show `suggested_action` from the failure summary next to each reason.
- `010-fr10-beneficiary-register`: a corrected wallet takes effect on the next retry automatically (the instruction is built at send time). No call needed.
- `019-fr19-donor-report`: cancelled amounts are part of `unused_pkr` (they are back in `remaining_pkr`); failed amounts are part of `in_flight_pkr` until retried or cancelled.
- `005-fr05-event-replay`: DC actions work under `X-Scenario-Id`; the audit entry lands in that replay run's chain.

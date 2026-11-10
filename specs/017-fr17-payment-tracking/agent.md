# Agent Brief: FR17 Payment tracking

## Owner role
Backend & alerts.

## Mission
Turn each funded allocation into a tracked `Payment` that moves Reserved → Processing → Paid or Failed (and Cancelled via FR18), updating the district fund's balances in the same transaction as every status change. Ship the `PaymentRail` adapter with a deterministic `SimulatedRail` (JazzCash/Easypaisa-shaped), flag payments stuck in Processing for more than 24 h, and give the DC and PDMA officer list, filter, count-by-status and CSV views.

## Scope
**In (MVP)**
- The single payment state machine (`transition()`), with the full transition table and 409 `INVALID_TRANSITION` for anything else. FR18 uses the same function.
- Fund ledger updates (`reserved_pkr`, `disbursed_pkr`, `remaining_pkr`) under a row lock, in the same transaction as the status change and the audit entry.
- `reserve_for_release()` (called after an allocation is saved for a payable release) and `dispatch_release()` / `send_payment()` (rail call outside the DB transaction).
- `PaymentRail` implementation registry and `SimulatedRail` with deterministic failures, a pending path and a seeded random failure rate.
- The stuck/reconcile timer job (every 15 min): reconcile timeouts, flag `is_stuck` after > 24 h in Processing.
- `PaymentEvent` timeline rows for every transition and flag.
- On Paid: queue the FR14 payment notice (`AlertService.enqueue`, purpose `PAYMENT_NOTICE`) in the same transaction.
- `FundRelease` status → `RELEASED` / `PARTIALLY_RELEASED` after dispatch.
- Endpoints: `GET /payments`, `GET /payments/{id}`, `GET /payments/counts`, `GET /payments/export.csv`, `POST /payments/dispatch` (admin/officer re-run).

**Out**
- Retry and cancel actions, failure summary: `018-fr18-payment-failure-retry` (it calls `transition()` from here).
- Donor aggregate counts endpoint: `019-fr19-donor-report` (reuses `count_by_status()` from here).
- Real JazzCash/Easypaisa integration and provider callbacks: roadmap (the `PaymentRail` seam is ready).
- Allocation ranking/amounts: `011-fr11-beneficiary-ranking`, `012-fr12-amount-rules`.

## Read first
- `.specify/memory/constitution.md` (Principles III, VI, VIII, XI; Quality Standards: money and idempotency)
- `specs/_authoring-guide.md` (FR17 facts)
- `specs/000-foundation/data-model.md` §4 `DisasterFund`, `FundRelease`; §5 `Allocation`, `Beneficiary`; §6 `Payment`, `PaymentEvent`
- `specs/000-foundation/plan.md` and `implement.md` (interfaces, `assert_district_access`, `scoped()`, `deps.get_rail()`)
- `specs/020-fr20-audit-log/agent.md` (how to call `AuditService.record`)
- This folder: `spec.md`, `clarify.md`, `plan.md`

## Depends on / Blocks
- **Depends on**: `000-foundation`; `020-fr20-audit-log` (real audit; the stub is acceptable for early dev); `012-fr12-amount-rules` (FUNDED allocations with `final_amount_pkr`); `010-fr10-beneficiary-register` (wallet provider/id).
- **Soft dependencies**: `014-fr14-whatsapp-alerts` (`AlertService.enqueue`; the foundation stub logs only), `007-fr07-auto-tier-release` and `008-fr08-dc-action-approval` (they call `reserve_for_release` / `dispatch_release`), `005-fr05-event-replay` (calls `run_payment_timers(now)` on each replay step).
- **Blocks**: `018-fr18-payment-failure-retry`, `019-fr19-donor-report` (disbursed/in-flight metrics, payment counts), `015-fr15-receipt-confirmation` (needs Paid payments and their notices), `022-fr22-outcome-reports`.

## Interfaces
- **Provides**
  - `PaymentRail` implementations: `SimulatedRail` in `app/integrations/payment_rail.py`, returned by `deps.get_rail()`. Protocol: `send(instruction) -> RailResult`, plus `get_status(idempotency_key) -> RailResult` (FR17 extends the foundation Protocol, which it owns).
  - `PaymentService` (`app/services/payments/service.py`): `reserve_for_release(db, fund_release, *, actor) -> list[Payment]`, `dispatch_release(fund_release_id) -> DispatchSummary`, `send_payment(payment_id, *, actor) -> Payment`, `run_payment_timers(now) -> TimerSummary`.
  - `transition(db, payment, to_status, *, action, actor, note, **changes) -> PaymentEvent` (`app/services/payments/state_machine.py`) — the only function allowed to change `Payment.status`.
  - Queries: `list_payments(...)`, `count_by_status(...)` (`app/services/payments/queries.py`).
- **Consumes**: `AuditService.record` (FR20), `AlertService.enqueue` (FR14), `Clock.now()`, `current_scenario()`, `assert_district_access`, `Beneficiary` decrypted wallet fields (FR10).

## Files you own
- `app/models/payments.py` (extend foundation models with the proposed columns only)
- `app/schemas/payments.py`
- `app/services/payments/__init__.py`, `state_machine.py`, `ledger.py`, `service.py`, `dispatch.py`, `timers.py`, `queries.py`, `csv_export.py`, `errors.py`
- `app/integrations/payment_rail.py`
- `app/api/v1/payments.py`
- `app/jobs/timers.py`: only the function `register_payment_timers(scheduler)` (shared file; other FRs own other functions)
- `alembic/versions/<rev>_fr17_payments.py`
- `tests/unit/payments/test_state_machine.py`, `test_ledger.py`, `test_simulated_rail.py`
- `tests/integration/payments/conftest.py`, `test_reserve.py`, `test_dispatch.py`, `test_timers.py`, `test_queries.py`, `test_fund_invariants.py`
- `tests/contract/test_payments_api.py`

## Guardrails
- **III**: every status change and every stuck flag writes one `PaymentEvent` and one audit entry in the same transaction as the ledger update. Never call the rail while holding a transaction that recorded audit entries.
- **VI**: list/detail show a masked wallet (`****1234`) and the beneficiary display name only to DC (own district) and officer/admin. CSV has no name, phone, CNIC or wallet. Donors get 403 `DONOR_AGGREGATE_ONLY` on every `/payments` route.
- **VIII**: `simulated=true` on every payment in the MVP; `provider` is `SIMULATED_*`; responses carry `simulated`.
- **IX**: wallet limit, failure rate, stuck threshold and timer interval are settings.
- **XI**: tests first for the state machine, the ledger maths and the SimulatedRail rules.
- **Money**: `Decimal` only, `NUMERIC(14,2)`; never floats.

## Definition of Done
- [ ] Transition table tests: every allowed transition succeeds; every other pair returns `INVALID_TRANSITION` (409 at the API)
- [ ] Ledger invariant `allocated = reserved + disbursed + remaining` holds after every test (autouse checker) and `reserved = Σ amount in {RESERVED, PROCESSING, FAILED}`, `disbursed = Σ PAID`
- [ ] SimulatedRail rules are deterministic for the same idempotency key; the seeded random failure rate is within ±2 % of the setting over 10 000 keys
- [ ] Stuck flag set at > 24 h (not at exactly 24 h) and cleared when the payment leaves Processing
- [ ] Paid queues exactly one `PAYMENT_NOTICE` alert in the same transaction
- [ ] List filters (district, tehsil, beneficiary type, status, release, event, stuck, failure reason), counts and CSV match each other on the same filters
- [ ] DC sees only their district; donor gets 403 on all `/payments` routes
- [ ] Re-running `reserve_for_release` and `dispatch_release` is idempotent
- [ ] `pytest tests/unit/payments tests/integration/payments tests/contract/test_payments_api.py` green; `mypy --strict app/services/payments` clean

## Hand-off notes
- `012-fr12-amount-rules`: after saving allocations for a release whose status is not `PENDING_APPROVAL`, call `payment_service.reserve_for_release(db, release, actor=...)` in the same transaction, then after commit call `schedule_dispatch(release.id)`.
- `008-fr08-dc-action-approval`: on `APPROVED`, call the same two functions. On `REJECTED`, do nothing here: no payments exist yet, the money stays in the fund.
- `014-fr14-whatsapp-alerts`: the alert is enqueued with `related={"payment_id": ...}` and context `{amount_pkr, wallet_provider, tier}`; render the "how to collect" text from the provider.
- `018-fr18-payment-failure-retry`: use `transition()` and `send_payment()`; never set `status` directly.
- `019-fr19-donor-report`: use `count_by_status()` and the ledger fields; `in_flight = reserved_pkr`.
- `005-fr05-event-replay`: call `run_payment_timers(clock.now())` once per replay step, after the pipeline; set `PAYMENT_DISPATCH_MODE=inline` for replays.

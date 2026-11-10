# Feature Specification: FR17 Payment tracking

**Feature Branch**: `017-fr17-payment-tracking`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: FR17 (project document, Payments): "Track each payment: Reserved, Processing, Paid, Failed, Cancelled"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Funded allocations become tracked payments (Priority: P1)

When a release is payable (Watch or Warning automatically, or Action after the DC approves), each funded beneficiary on the saved allocation list gets one payment. The amount is reserved against the district fund at once, then sent to the payment service. The payment service either confirms (Paid) or refuses with a reason (Failed). Every step updates the fund balance and is recorded.

**Why this priority**: This is UC08, the moment money actually moves. Without it nothing downstream (alerts, confirmations, donor report) has anything to show.

**Independent Test**: Create a release with 10 funded allocations (one with a wallet ending `000`), reserve and dispatch. Expect 9 Paid, 1 Failed `INVALID_WALLET`; the fund shows the 9 amounts as disbursed, the failed amount still reserved, and the rest remaining; 10 Reserved→Processing and 10 result events in each payment timeline and in the audit log.

**Acceptance Scenarios**:

1. **Given** a saved allocation list for a payable release, **When** reservation runs, **Then** each funded allocation with an amount above zero gets exactly one payment in Reserved, and the fund's reserved total rises and remaining falls by the same amount, in one step.
2. **Given** reserved payments, **When** dispatch runs, **Then** each moves to Processing with a start time, and the payment service is called once per payment with a unique idempotency key.
3. **Given** the payment service confirms, **When** the result is applied, **Then** the payment becomes Paid with the provider's transaction id and paid time, the amount moves from reserved to disbursed, and one payment notice is queued for the beneficiary.
4. **Given** the payment service refuses (invalid wallet, limit reached, no wallet, provider error), **When** the result is applied, **Then** the payment becomes Failed with the reason and detail, and the amount stays reserved so it can be retried or cancelled (FR18).
5. **Given** the release is an Action tier still waiting for approval, **When** allocations are saved, **Then** no payments are created until the DC approves.
6. **Given** reservation or dispatch runs twice for the same release, **When** it runs again, **Then** no duplicate payments, reservations or sends happen.
7. **Given** all payments of a release have left Reserved, **When** dispatch finishes, **Then** the release is marked Released (or Partially released if any beneficiary was marked Unmet need).

---

### User Story 2 - Illegal status changes are refused (Priority: P1)

The payment status can only move along the allowed paths. Anything else (for example Paid back to Processing, or cancelling a payment while it is in flight) is refused and nothing changes.

**Why this priority**: Principle XI makes the payment state machine a mandatory-test money path; a wrong transition could double-pay or lose money.

**Independent Test**: For every (from, to) pair of the five statuses, attempt the transition; only the seven allowed pairs succeed, all others are refused with "invalid transition" and leave status, balances, timeline and audit untouched.

**Acceptance Scenarios**:

1. **Given** a Paid or Cancelled payment, **When** any change is attempted, **Then** it is refused with a conflict error.
2. **Given** a Processing payment, **When** someone tries to cancel it, **Then** it is refused (money is in flight).
3. **Given** a Reserved payment, **When** something tries to mark it Paid directly, **Then** it is refused (it must be sent first).

---

### User Story 3 - The DC and officer see where every payment is (Priority: P1)

The DC (for their own district) and the PDMA officer open Payments and see counts by status, filter by tehsil, beneficiary type or status, open one payment to see its timeline and failure reason, and export the filtered list as CSV.

**Why this priority**: UC09 steps 1, 2, 3 and 5: "nobody can see where it got stuck" is one of the four problems the product solves.

**Independent Test**: With 50 seeded payments across two districts, sign in as the Dadu DC: counts cover Dadu only and add up to the Dadu total; filtering by `status=FAILED` returns only failed Dadu payments; the CSV has the same rows as the filtered list; opening one payment shows its full timeline.

**Acceptance Scenarios**:

1. **Given** payments in several statuses, **When** the user opens counts, **Then** they see counts and amounts for Reserved, Processing, Paid, Failed and Cancelled, plus the number stuck, and the counts add up to the total.
2. **Given** filters for tehsil, beneficiary type and status, **When** the user applies them, **Then** the list and the counts reflect exactly those filters.
3. **Given** one payment, **When** the DC opens it, **Then** they see its amount, status, failure reason, retry count, masked wallet, provider transaction id and the ordered timeline of every status change with actor and note.
4. **Given** a filtered list, **When** the user exports CSV, **Then** the file contains the same rows, no names, phones, CNICs or wallet numbers, and a simulated flag column.
5. **Given** a DC of another district, **When** they open a Dadu payment, **Then** they get "forbidden".
6. **Given** a donor, **When** they call any individual payment view, **Then** they get "forbidden" with a pointer to the aggregate donor report.

---

### User Story 4 - Stuck payments are flagged (Priority: P2)

A payment that stays in Processing for more than 24 hours is flagged as stuck, shown in the counts and the timeline, and the flag clears when it finally resolves.

**Why this priority**: UC09 alt 1a. Real rails sometimes go silent; the DC must see it without hunting.

**Independent Test**: Put a payment in Processing (simulated "pending" wallet), advance the clock 23 h 59 min, run the timer (not stuck), advance to 24 h 1 min, run it (stuck, one timeline event, one audit entry). Run again: no duplicate event.

**Acceptance Scenarios**:

1. **Given** a payment in Processing for more than 24 h, **When** the timer runs, **Then** it is flagged stuck once, with a timeline entry and an audit entry.
2. **Given** a stuck payment, **When** it later becomes Paid or Failed, **Then** the stuck flag clears.
3. **Given** a payment whose send timed out, **When** the timer reconciles it with the payment service, **Then** it becomes Paid or Failed per the provider's answer, or stays Processing if still pending.
4. **Given** a replay, **When** each replay day is stepped, **Then** the timer runs against the replay clock, so stuck flags appear on the replay timeline.

---

### User Story 5 - The simulated payment service behaves predictably (Priority: P2)

In the prototype, money moves through a simulated JazzCash/Easypaisa service that is clearly labelled simulated and fails in predictable ways, so demos and tests are repeatable.

**Why this priority**: NFR13 (honest prototype) and repeatable backtests.

**Independent Test**: Send fixed instructions: wallet ending `000` → invalid wallet; amount above the wallet limit on first attempt → limit reached; no wallet → no wallet; wallet ending `999` → stays pending; wallet ending `998` → timeout then failed on reconcile; any other → paid. Same key twice → same answer.

**Acceptance Scenarios**:

1. **Given** the same instruction sent twice with the same key, **When** the simulated service answers, **Then** both answers are identical (idempotent).
2. **Given** a random failure rate of 5 % and a fixed seed, **When** 10 000 distinct keys are sent, **Then** about 5 % fail with "provider error", and the same keys always give the same outcome.
3. **Given** any simulated payment, **When** it is shown anywhere, **Then** it is labelled simulated.

---

### Edge Cases

- Allocation with a final amount of 0 (FR12 allows a 0 tier amount): no payment is created.
- Allocation marked Unmet need: no payment; the release is Partially released.
- Fund remaining is lower than the sum to reserve (should not happen if FR12 respects the pool): the whole reservation is refused with "insufficient fund balance" and nothing is reserved.
- Beneficiary has no wallet (`NONE`): the payment is still created, then fails with `NO_WALLET` so the DC sees it and can cancel.
- Beneficiary's wallet is corrected in the register after a failure: the next send (FR18 retry) uses the new wallet.
- Two dispatchers pick the same payment: only one sends it (row lock, skip locked).
- The process crashes after marking Processing but before calling the rail: the timer reconciles via the provider's status for that idempotency key.
- A fund settings edit (FR6) lowers the fund amount below reserved + disbursed: FR6 must refuse it; FR17's invariant check would flag it.
- Live and replay payments never mix; each list is scoped by scenario.
- A beneficiary appears in several tiers of one event: each tier's allocation is a separate payment (FR12 decides top-up amounts).

## Requirements *(mandatory)*

### Functional Requirements

- **FR17-001**: The system MUST create exactly one payment per funded allocation with a positive final amount, in status Reserved, when the allocation list is saved for a payable release (Watch, Warning, or Action after approval).
- **FR17-002**: The system MUST allow only these status changes: Reserved→Processing (send), Processing→Paid (success), Processing→Failed (failure), Failed→Processing (retry, FR18), Failed→Cancelled (cancel, FR18), Reserved→Cancelled (cancel, FR18). Every other change MUST be refused with an "invalid transition" conflict and change nothing.
- **FR17-003**: Each status change MUST, in one atomic step: update the payment, update the fund balances, add a timeline event, and write one audit entry.
- **FR17-004**: Fund balances MUST follow: reserve → reserved +A, remaining −A; paid → reserved −A, disbursed +A; cancel → reserved −A, remaining +A; send, fail and retry → no change. At all times allocated = reserved + disbursed + remaining, reserved = sum of Reserved, Processing and Failed payments, disbursed = sum of Paid payments.
- **FR17-005**: Each send attempt MUST carry an idempotency key `{allocation_id}:{retry_count}`, unique across all attempts.
- **FR17-006**: The payment service call MUST happen outside the database step that records the status change, so a slow provider never holds money or audit locks.
- **FR17-007**: Failure MUST store a reason from: invalid wallet, limit reached, provider timeout, provider error, no wallet; plus a free-text provider detail.
- **FR17-008**: When a payment becomes Paid, the system MUST queue one payment notice for the beneficiary (FR14) in the same step.
- **FR17-009**: A payment in Processing for more than 24 hours MUST be flagged stuck by a timer that runs every 15 minutes (configurable), with a timeline and audit entry; the flag MUST clear when the payment leaves Processing.
- **FR17-010**: Sends that time out MUST stay in Processing and be reconciled by asking the provider for the status of that idempotency key.
- **FR17-011**: The simulated payment service MUST be deterministic per idempotency key and support: no wallet, invalid wallet (wallet id ending `000`), limit reached (amount above a configurable wallet limit on the first attempt), pending (ending `999`), timeout (ending `998`), and a configurable seeded random provider-error rate.
- **FR17-012**: Every MVP payment MUST be labelled simulated, with a simulated provider name.
- **FR17-013**: Users MUST be able to list payments with filters: district, tehsil, beneficiary type, status, release, trigger event, stuck, failure reason; paged and sortable by created time, amount or status.
- **FR17-014**: Users MUST be able to get counts and amounts by status (plus stuck count) for the same filters.
- **FR17-015**: Users MUST be able to open one payment with its timeline (from, to, actor, note, time) and failure reason.
- **FR17-016**: Users MUST be able to export the filtered list as CSV without beneficiary PII.
- **FR17-017**: The DC MUST only see payments in their own district; the officer and admin see all; donors MUST be refused on every individual payment view.
- **FR17-018**: Reservation and dispatch MUST be idempotent when re-run for the same release.
- **FR17-019**: After dispatch, the release MUST be marked Released when all its payments left Reserved and no allocation is Unmet need, otherwise Partially released, with an audit entry.
- **FR17-020**: Every payment, timeline event and audit entry MUST carry the scenario id of the run that created it.

### Key Entities

- **Payment** (data-model.md §6): one transfer, holds the state machine.
- **PaymentEvent** (§6): the timeline.
- **DisasterFund** (§4): `allocated_pkr`, `reserved_pkr`, `disbursed_pkr`, `remaining_pkr`.
- **FundRelease** (§4): the tranche; its status moves to Released / Partially released.
- **Allocation** (§5): source of amount and rank; `FUNDED` vs `UNMET_NEED`.
- **Beneficiary** (§5): wallet provider and id (encrypted), type, tehsil.
- **Alert** (§7): the payment notice queued on Paid.
- **AuditLog** (§8).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (Principle XI): 100 % of the 25 (from, to) status pairs are covered by tests; exactly 6 succeed, 19 are refused (plus the creation path into Reserved).
- **SC-002** (Principle III, NFR7): every status change produces exactly one timeline event and one audit entry; the chain verifies after the full test suite.
- **SC-003**: the fund ledger identity holds after every integration test (0 violations).
- **SC-004** (NFR2): the payment notice is queued in the same transaction as Paid, i.e. within seconds and always under 5 minutes.
- **SC-005** (NFR1): list and counts for 50 000 payments return p95 < 500 ms; CSV of 50 000 rows streams in < 10 s.
- **SC-006** (UC09-1a): a payment is flagged stuck no later than 24 h + 15 min after entering Processing, and never before 24 h.
- **SC-007** (NFR8, NFR6): donors get 403 on 100 % of `/payments` routes; CSV and list contain 0 phone/CNIC/wallet values (contract test).
- **SC-008** (NFR13): 100 % of MVP payments and responses carry `simulated=true`.

## Assumptions

- Only the simulated rail exists in the MVP; it answers synchronously except for the pending and timeout paths.
- A payment amount equals the allocation's final amount; partial payments are not supported.
- One fund per district per scenario; fund rows are locked, not versioned, for balance changes.
- The DC's district is on their user record; the officer sees all districts in the pilot.
- Wallet daily/monthly limits are modelled by one configurable per-transfer limit.

## Traceability

| Item | How this FR covers it |
|---|---|
| UC08 steps 1–5 | Reserve, send, paid, ledger + audit, payment notice |
| UC08 alt 1a | Unmet-need allocations get no payment; release Partially released |
| UC08 alt 3a | Failed with reason (handled further in FR18) |
| UC09 steps 1, 2, 3, 5 | Counts, filters, detail with timeline, CSV |
| UC09 alt 1a | Stuck flag > 24 h |
| UC09 alt 3a | Donor 403 on individual views (aggregates in FR19) |
| NFR1, NFR2, NFR6, NFR7, NFR8, NFR13 | See success criteria |
| D4 | Releases are per tier; payments per allocation within a tier |
| D5 | `Payment` holds the state machine; `PaymentEvent` the timeline |
| Principles III, VI, VIII, IX, XI | See plan.md Constitution Check |

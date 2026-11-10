# Feature Specification: FR18 Payment failure reasons, retry and cancel

**Feature Branch**: `018-fr18-payment-failure-retry`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: FR18 (project document, Payments): "Record failure reasons and allow retry or cancel"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The DC retries a failed payment (Priority: P1)

The DC opens a failed payment in their district, reads the reason (for example "limit reached"), writes a short note and presses Retry. The payment goes back to Processing with a fresh instruction to the payment service and ends Paid or Failed again. A payment can be retried at most 3 times.

**Why this priority**: UC09 step 4 and UC08 alt 3a. "An approved payment is not money received" is a core problem; retry is the fix.

**Independent Test**: A shopkeeper payment of 45 000 PKR fails with "limit reached". The Dadu DC retries with note "Provider says limit reset today". The payment becomes Paid; retry count is 1; the timeline shows Failed → Processing (actor: the DC, with the note) → Paid; the audit log has two new entries; the fund shows 45 000 moved from reserved to disbursed.

**Acceptance Scenarios**:

1. **Given** a Failed payment in the DC's district with retry count 0–2, **When** the DC retries with a valid note and the current retry count, **Then** the retry count rises by 1, a new idempotency key is used, the payment goes to Processing and then to the payment service's result.
2. **Given** a Failed payment with retry count 3, **When** the DC retries, **Then** it is refused with "retry limit reached" and nothing changes; Cancel remains available.
3. **Given** the beneficiary's wallet was corrected in the register after the failure, **When** the DC retries, **Then** the new wallet is used.
4. **Given** a retry where the payment service fails again, **When** the result arrives, **Then** the payment is Failed with the new reason, and the earlier reason stays visible in the timeline.
5. **Given** two retry requests for the same payment at the same time (double click), **When** both arrive, **Then** exactly one is processed and the other is refused as stale; only one retry is counted.
6. **Given** a payment that is Processing, Paid, Reserved or Cancelled, **When** the DC tries to retry, **Then** it is refused as an invalid transition.

---

### User Story 2 - The DC cancels a payment and the money returns to the fund (Priority: P1)

When a payment cannot be fixed (no wallet, wrong person, duplicate), the DC cancels it with a note. The amount goes back to the district fund's remaining balance.

**Why this priority**: UC09 step 4 and the "fixes failed payments" role of the DC. Without cancel, failed money would be locked forever.

**Independent Test**: A household payment of 7 000 PKR failed with "no wallet". The DC cancels with note "No wallet; family relocated to camp, will be paid in kind". Status Cancelled; fund reserved −7 000, remaining +7 000; one timeline event, one audit entry.

**Acceptance Scenarios**:

1. **Given** a Failed payment, **When** the DC cancels with a valid note, **Then** it becomes Cancelled, the amount moves from reserved to remaining in the same step, and the action is recorded with the note.
2. **Given** a Reserved payment not yet sent, **When** the DC cancels it, **Then** the same happens and it is never sent.
3. **Given** a Processing, Paid or Cancelled payment, **When** the DC tries to cancel, **Then** it is refused as an invalid transition and nothing changes.
4. **Given** a cancelled payment, **When** anyone looks at the fund, **Then** the identity allocated = reserved + disbursed + remaining still holds.

---

### User Story 3 - Only the right person, with a reason, can act (Priority: P1)

Retry and cancel are reserved for the DC of the payment's district, and every action must carry a written note.

**Why this priority**: Separation of duties ("what stops misuse of funds?") and Principle III.

**Independent Test**: Try retry and cancel as the PDMA officer, the admin, the donor, the Buner DC (on a Dadu payment) and the Dadu DC with an empty note; all are refused with the right codes; nothing changes.

**Acceptance Scenarios**:

1. **Given** the PDMA officer or admin, **When** they retry or cancel, **Then** they are refused ("DC only").
2. **Given** a DC of another district, **When** they retry or cancel, **Then** they are refused ("district forbidden").
3. **Given** a donor, **When** they call either action, **Then** they are refused ("aggregate only").
4. **Given** the right DC with a missing, blank or under-5-character note, **When** they act, **Then** the request is refused ("note required"); over 500 characters is refused ("note too long").
5. **Given** a replay scenario, **When** the DC acts on a replay payment with the scenario selected, **Then** it works and is recorded in that replay's audit chain; acting on it without the scenario selected returns "not found".

---

### User Story 4 - The DC sees failure reasons at a glance (Priority: P2)

The DC sees how many payments failed for each reason, how much money that represents, how many have used all retries, and a suggested next step for each reason.

**Why this priority**: UC09 goal: "know where every payment is and why any are delayed or failed".

**Independent Test**: With 3 invalid wallet, 2 limit reached and 1 no wallet failures (one of them with 3 retries used), the summary shows those counts and amounts, `retries_exhausted=1`, and a suggested action per reason; totals match the list filtered by Failed.

**Acceptance Scenarios**:

1. **Given** failed payments, **When** the DC opens the summary, **Then** they see count and amount per reason, totals, retries exhausted, and a suggested action per reason.
2. **Given** filters for release or tehsil, **When** applied, **Then** the summary reflects exactly those filters.

---

### Edge Cases

- The DC retries at the moment the stuck/reconcile timer resolves the same payment: row locks serialise; one wins, the other gets "stale" or "invalid transition".
- The DC cancels a Reserved payment while dispatch is about to send it: whoever locks the row first wins; dispatch skips locked rows and re-checks status.
- Retry when the payment service times out: the payment stays Processing (FR17 reconcile), the retry still counted.
- Note contains a phone number (e.g. a corrected wallet): stored on the timeline only (visible to DC/officer), never in the audit payload or donor views.
- A DC is deactivated after a payment fails: the new DC of that district can act (district match is checked at request time).
- All 3 retries fail: only cancel remains; the summary counts it under "retries exhausted".
- Retry on a beneficiary with wallet provider NONE: allowed but will fail again with "no wallet"; the suggested action says to add a wallet or cancel.

## Requirements *(mandatory)*

### Functional Requirements

- **FR18-001**: The system MUST store a failure reason (invalid wallet, limit reached, provider timeout, provider error, no wallet) and provider detail on every Failed payment, and keep each attempt's reason in the timeline.
- **FR18-002**: The DC of the payment's district MUST be able to retry a Failed payment with a note.
- **FR18-003**: A payment MUST NOT be retried more than 3 times (retry count 0→3); a further retry MUST be refused with "retry limit reached".
- **FR18-004**: Each retry MUST use a new idempotency key `{allocation_id}:{retry_count}` and MUST read the beneficiary's current wallet at send time.
- **FR18-005**: A retry request MUST include the retry count the DC saw; if it differs from the current one, the request MUST be refused as stale.
- **FR18-006**: The DC of the payment's district MUST be able to cancel a Failed or Reserved payment with a note; the amount MUST return to the fund's remaining balance in the same step.
- **FR18-007**: Retry and cancel MUST go through the FR17 state machine; any other source status MUST be refused as an invalid transition.
- **FR18-008**: Every retry and cancel MUST require a note of 5 to 500 characters after trimming.
- **FR18-009**: Every retry and cancel MUST be recorded in the payment timeline (actor, note, time) and in the audit log, in the same step as the status change.
- **FR18-010**: The PDMA officer, admin and donor MUST NOT be able to retry or cancel; a DC of another district MUST be refused.
- **FR18-011**: The system MUST provide a failure summary: count and amount per reason, total failed, retries exhausted, and a suggested action per reason, with the FR17 list filters.
- **FR18-012**: Actions on replay payments MUST be scoped to the selected scenario and recorded in its audit chain.
- **FR18-013**: The response to a retry MUST return the payment's state after the payment service answered (Paid, Failed, or still Processing).

### Key Entities

- **Payment** (data-model.md §6): `status`, `failure_reason`, `failure_detail`, `retry_count`, `idempotency_key`, `cancelled_at`.
- **PaymentEvent** (§6): the action record with actor and note (FR17 adds `action`, `idempotency_key`, `failure_reason`).
- **DisasterFund** (§4): `reserved_pkr`, `remaining_pkr` on cancel.
- **Beneficiary** (§5): current wallet at retry time.
- **User** (§8): role and `district_id` for the access rule.
- **AuditLog** (§8).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (Principle XI): 100 % of rule combinations (5 roles/district cases × 5 statuses × note valid/invalid × retry count 0–3) are covered by tests and give the expected result.
- **SC-002** (Principle III, NFR7): each retry or cancel produces exactly one action audit entry (plus one result entry for retries) and the chain verifies.
- **SC-003**: after any cancel, the fund identity allocated = reserved + disbursed + remaining holds (0 violations).
- **SC-004**: 0 double-counted retries under 10 parallel identical retry requests.
- **SC-005** (NFR1): a retry with the simulated rail returns in < 1 s; the failure summary p95 < 500 ms at 50 000 payments.
- **SC-006** (NFR8): 0 note texts in audit payloads or donor responses.

## Assumptions

- Only the simulated payment service exists; retries resolve within the request except for the pending/timeout wallets.
- The DC's district is fixed on their user record.
- Cancelled money is not re-allocated automatically; it becomes unused fund balance.
- Retry limit 3 is a setting shared with FR17 (`PAYMENT_MAX_RETRIES`).

## Traceability

| Item | How this FR covers it |
|---|---|
| UC08 alt 3a | Failure reason recorded and handled here |
| UC09 step 3 | Failure reason and timeline (with FR17 detail view) |
| UC09 step 4 | Retry or cancel, logged |
| UC09 postcondition | "Failed payments are retried or cancelled, with every action logged" |
| UC09 alt 3a | Donor refused on actions |
| NFR6, NFR7, NFR8 | Role rules, audit, no notes to donors |
| NFR12 | Officials, not the system, decide to re-send money |
| D5 | Uses `Payment` state machine and `PaymentEvent` |
| Principles II, III, VI, XI | See plan.md Constitution Check |

# Feature Specification: FR8 DC Approval of Action-Tier Releases

**Feature Branch**: `008-fr08-dc-action-approval`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: FR8 (project document, Functional requirements): "Require Deputy Commissioner approval before releasing Action tier funds"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - DC approves on the dashboard with a one-time PIN (Priority: P1)

When a district reaches the Action tier, the DC receives an approval request on the dashboard and on WhatsApp. The DC opens it and sees the risk score, the plain-language explanation, the 7-day forecast, the satellite flood map (if any) and the total amount. They press Approve, receive a 6-digit one-time PIN on WhatsApp, enter it, and the release starts: allocation, payment and alerts.

**Why this priority**: This is UC07's main flow and the core of Principle II.

**Independent Test**: Create an Action release pending approval for Dadu; as the Dadu DC, request a PIN, approve with it; check the approval record, the release moving to allocation and the audit entries.

**Acceptance Scenarios**:

1. **Given** an Action release of PKR 30,000,000 pending approval, **When** the DC opens the request, **Then** they see the score, explanation (Urdu and English), the 7-day forecast, satellite maps if available, the amount and the household/shopkeeper pools, and the time left before escalation.
2. **Given** the DC presses Approve, **When** they request a PIN, **Then** a 6-digit PIN valid for 10 minutes is sent to their WhatsApp number and only its hash is stored.
3. **Given** a valid PIN, **When** the DC submits it, **Then** the approval is recorded with channel Dashboard, the DC as approver and the time, the release moves to allocation, and one audit entry is written.
4. **Given** a wrong PIN, **When** submitted, **Then** it is refused, the attempts left are shown, and after 5 wrong attempts the PIN is locked until a new one is requested.
5. **Given** the PIN is older than 10 minutes, **When** submitted, **Then** it is refused as expired.

---

### User Story 2 - DC decides by replying on WhatsApp (Priority: P1)

The approval request on WhatsApp includes an approval code. The DC replies `APPROVE 482913` to approve, or `REJECT <reason>` to reject. The system confirms the outcome back on WhatsApp.

**Why this priority**: The live demo shows the DC approving from a phone on stage (document Q13, Q17).

**Independent Test**: Call the WhatsApp command handler with the DC's phone hash and `APPROVE <code>`; check the approval with channel WhatsApp and the reply text.

**Acceptance Scenarios**:

1. **Given** a pending approval, **When** the DC replies `APPROVE <code>` with the right code, **Then** it is approved with channel WhatsApp and the DC gets a confirmation with the amount.
2. **Given** the DC replies with a wrong code, **When** handled, **Then** nothing is approved and the reply says how many attempts are left; after 5 wrong codes the WhatsApp code is locked and the DC is pointed to the dashboard.
3. **Given** the reply uses Urdu digits (`APPROVE 482913` (Urdu digits)) or the Urdu words for approve and reject, **When** handled, **Then** it is understood the same way.
4. **Given** the same WhatsApp message is delivered twice, **When** handled, **Then** the second delivery changes nothing.
5. **Given** a message from a number that is not a registered DC, **When** handled, **Then** nothing changes.

---

### User Story 3 - DC rejects with a reason (Priority: P1)

The DC can reject the release with a written reason, on the dashboard or by `REJECT <reason>` on WhatsApp. The reason is logged, the money stays in the district fund, and the PDMA officer is notified (UC07-3a).

**Why this priority**: Human authority includes saying no; the rejection path must be as traceable as approval.

**Independent Test**: Reject a pending approval; check the stored reason, the release marked rejected, the fund still holding the 30 M, and an officer notification.

**Acceptance Scenarios**:

1. **Given** a pending approval, **When** the DC rejects with "River levels falling, camps already open", **Then** the approval is Rejected with that reason, the release is Rejected, no allocation or payment exists, and the PDMA officer is notified.
2. **Given** a rejection with no reason or fewer than 5 characters, **When** submitted, **Then** it is refused and nothing changes.
3. **Given** `REJECT <reason>` on WhatsApp and the DC has two pending approvals (live and replay), **When** handled, **Then** the DC is asked to include the code, and nothing changes.
4. **Given** a decided approval, **When** anyone tries to decide it again on any channel, **Then** it is refused as already decided.

---

### User Story 4 - Escalation after 2 hours without a decision (Priority: P1)

If the DC does not decide within 2 hours, the request escalates to the PDMA officer: it is flagged on their dashboard and they get a WhatsApp notice. The officer still cannot approve; only the DC can (D6).

**Why this priority**: UC07-1a; without it a silent DC blocks the money with nobody aware.

**Independent Test**: Advance the clock 2 h 1 min and run the escalation job; check the escalated flag, the officer notice and that the DC can still approve.

**Acceptance Scenarios**:

1. **Given** a pending approval requested at 10:00, **When** the escalation check runs at 12:01, **Then** it is marked Escalated with the time, every active PDMA officer gets a notice, and the DC gets a reminder.
2. **Given** an escalated approval, **When** the PDMA officer tries to approve, **Then** it is refused.
3. **Given** an escalated approval, **When** the DC approves with a valid PIN or code, **Then** it is approved and the officer is told it was decided.
4. **Given** a district has no active DC, **When** an approval is requested, **Then** it escalates immediately with the reason "no DC assigned".
5. **Given** the check runs again, **When** the approval is already escalated, **Then** no duplicate notices are sent.

---

### User Story 5 - Replay simulates the DC decision, labelled (Priority: P2)

In a replay, the DC decision is simulated and clearly labelled, unless the presenter chooses a real WhatsApp approval for the live demo.

**Why this priority**: Backtests must run unattended (NFR3), and the demo must never pass a simulated approval off as real (NFR13).

**Independent Test**: Fire Action in a replay scenario with simulation on; check an approval with channel Replay-simulated and the simulated flag, followed by allocation.

**Acceptance Scenarios**:

1. **Given** replay mode is "simulated", **When** Action fires in a replay, **Then** an approval record is created and decided as Approved with channel Replay-simulated, simulated = true, and the configured delay as its decision time.
2. **Given** replay mode is "live WhatsApp", **When** Action fires in a replay, **Then** the request goes to the real DC phone and waits for their reply.
3. **Given** a live (non-replay) approval, **When** anything tries to simulate its decision, **Then** it is refused.

### Edge Cases

- **DC has no phone on record**: WhatsApp request and PIN cannot be sent; requesting a PIN returns "DC phone missing"; the approval escalates on schedule; an admin must add the phone.
- **PIN requested twice within 60 seconds**: refused as too soon; a new PIN replaces the old one and resets attempts.
- **WhatsApp code older than 24 hours**: refused as expired; the DC (or officer) can ask for the request to be resent with a new code.
- **DC decides on both channels at once**: the first decision wins; the second is told it is already decided.
- **Approval succeeds but allocation fails**: the approval stands; the release stays in allocation with the error and can be resumed (FR7).
- **Trigger episode would close while approval pending**: it stays open (FR7).
- **The DC is transferred** (user deactivated) while a request is pending: the next escalation check escalates it; the new DC sees it on the dashboard.
- **Score falls after the request**: the request stays valid; the detail view shows the latest score next to the score that fired it, and the DC decides.

## Requirements *(mandatory)*

### Functional Requirements

- **FR08-001**: The system MUST create exactly one approval request for each Action release, at the moment it fires, and notify the DC on the dashboard and on WhatsApp.
- **FR08-002**: The system MUST NOT allocate or pay an Action release until an Approved decision by a DC of that district is recorded.
- **FR08-003**: Only a DC assigned to the release's district MUST be able to approve or reject (except simulated replay decisions).
- **FR08-004**: The approval detail MUST show the risk score, the explanation in Urdu and English, the 7-day forecast, satellite flood maps if any, the total amount and pools, what this episode has already released, and the escalation deadline.
- **FR08-005**: Dashboard approval MUST require a 6-digit one-time PIN delivered to the DC's WhatsApp, valid 10 minutes, with at most 5 attempts.
- **FR08-006**: WhatsApp approval MUST require the reply `APPROVE <code>` with the code sent in that request; WhatsApp rejection MUST accept `REJECT <reason>`, with an optional code.
- **FR08-007**: Both channels MUST write the same single approval record, with the channel recorded (D3).
- **FR08-008**: A rejection MUST record a reason of at least 5 characters, leave the money in the fund and notify the PDMA officer.
- **FR08-009**: With no decision 2 hours after the request, the request MUST escalate to the PDMA officer (dashboard flag + WhatsApp notice) and the DC MUST still be able to decide; the officer MUST NOT be able to approve (D6).
- **FR08-010**: An approval MUST start the release pipeline (allocation, payments, alerts) immediately.
- **FR08-011**: A decided approval MUST NOT be changed.
- **FR08-012**: PINs and codes MUST be stored only as hashes and MUST NOT appear in logs, audit entries or stored message text.
- **FR08-013**: Every request, PIN issue, failed code, decision and escalation MUST be audited in the same transaction.
- **FR08-014**: Replays MUST either simulate the decision (labelled simulated, channel Replay-simulated) or wait for a real WhatsApp decision, as configured; live approvals MUST never be simulated.
- **FR08-015**: Repeated delivery of the same WhatsApp message MUST NOT change anything.
- **FR08-016**: The DC MUST see only approvals of their district; officers and admins can view all; donors none.

### Key Entities

- **Approval**: one per Action release; decision, channel, approver, reason, PIN and code hashes, timings, escalation, simulated.
- **FundRelease**: the Action tranche being approved.
- **RiskIndex**, **FloodForecast**, **SatelliteFloodMap**: shown in the detail.
- **User**: DC (approver), PDMA officer (escalation target).
- **Alert**: request, PIN, reminder, escalation, decision notices.
- **AuditLog**.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 Action releases are allocated or paid without an approval record (all tests and backtests, NFR12).
- **SC-002**: The approval request is queued within 5 minutes of the Action firing; in tests, in the same transaction (NFR2).
- **SC-003**: 100% of unanswered requests escalate between 2 h 00 min and 2 h 05 min after the request.
- **SC-004**: A DC can approve on WhatsApp in a single reply, and the confirmation is queued within 5 seconds of the reply being received.
- **SC-005**: Brute-forcing a PIN or code succeeds with probability ≤ 5 in 1,000,000 per issued code.
- **SC-006**: Every decision and escalation appears in the audit log and the chain verifies (NFR7).
- **SC-007**: In replays, 100% of simulated approvals carry `simulated = true` and channel Replay-simulated (NFR13).

## Assumptions

- Each district has one active DC user with a WhatsApp number registered in the Twilio sandbox for the demo.
- DC messages can be English-first (officials), but templates still include Urdu (FR14 decides layout).
- The "PDMA Director General" in UC07-1a is replaced by the PDMA officer in the MVP (D6).
- UC07's precondition "the allocation list from UC06 is ready" is met by showing the pools and the register size; the ranked list is built after approval (see clarify.md).

## Traceability

| Item | Covered by |
|---|---|
| UC07 main flow 1–4 | US1, US2, FR08-001…007, FR08-010 |
| UC07-3a reject | US3, FR08-008 |
| UC07-1a no response in 2 h | US4, FR08-009 |
| UC08 trigger "DC approval" | FR08-010 |
| NFR2 | SC-002 |
| NFR6 | FR08-003, FR08-016 |
| NFR7 | FR08-013, SC-006 |
| NFR10 | Urdu explanation in detail; Urdu keywords and digits accepted |
| NFR12 | FR08-002, SC-001 |
| NFR13 | FR08-014, SC-007 |
| D3 two channels, one record | FR08-005…007 |
| D6 escalation to PDMA officer | FR08-009 |

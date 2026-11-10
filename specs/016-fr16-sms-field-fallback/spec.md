# Feature Specification: FR16 Fall back to SMS or a field agent when there is no reply

**Feature Branch**: `016-fr16-sms-field-fallback`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: Roadmap (MVP stub behind flag `FR16_FALLBACK`)

**Input**: "FR16 Fall back to SMS or a field agent when there is no reply" (Area: Communication, Priority: Roadmap)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - SMS after 6 hours of silence (Priority: P1)

A beneficiary got the WhatsApp payment notice but has not replied after 6 hours: maybe they have no data, their phone is basic, or they missed it. The system sends a short SMS in Urdu and English with the amount and "reply 1 or 2" (UC10 alternate 2a).

**Why this priority**: Many low-income households in Sindh and KP use basic phones or have no mobile data during floods. SMS reaches them.

**Independent Test**: With the flag on and a controllable clock, create a WhatsApp payment notice sent at 00:00 with no reply; run the sweep at 05:59 (nothing) and at 06:00 (one SMS alert linked to the notice).

**Acceptance Scenarios**:

1. **Given** a payment notice sent at 00:00 UTC and no reply, **When** the sweep runs at 06:00 or later, **Then** one SMS alert is queued with `fallback_of` pointing to the WhatsApp notice, purpose payment notice, expecting a reply.
2. **Given** the beneficiary replied `1` at 05:00, **When** the sweep runs at 06:00, **Then** no SMS is queued.
3. **Given** only unclear replies (e.g. "ok"), **When** the sweep runs at 06:00, **Then** the SMS is queued (unclear replies do not count as an answer).
4. **Given** the SMS was already queued, **When** the sweep runs again, **Then** no second SMS is queued.
5. **Given** the payment was cancelled at 03:00, **When** the sweep runs at 06:00, **Then** nothing is queued.

---

### User Story 2 - Field-agent task after 12 hours (Priority: P1)

If there is still no valid reply 12 hours after the WhatsApp notice, the system opens a field-agent task for that payment so someone visits or calls the beneficiary (UC10 alternate 2b). The DC and PDMA officer see open tasks on a list.

**Why this priority**: The last line of "every rupee arrives": a human checks.

**Independent Test**: Continue the US1 setup to 12:00 with no reply; run the sweep; check one open task with reason "no reply after 12h" and one field-agent channel alert recording the hand-off.

**Acceptance Scenarios**:

1. **Given** no valid reply by 12:00, **When** the sweep runs at 12:00 or later, **Then** one open task is created for that beneficiary and payment with reason `NO_REPLY_12H`, and an audit entry is written.
2. **Given** the beneficiary replied `2` to the SMS at 08:00, **When** the sweep runs at 12:00, **Then** no "no reply" task is created.
3. **Given** an open task already exists for the payment, **When** the sweep runs again, **Then** no second task is created.
4. **Given** the sweep was down from 05:00 to 13:00, **When** it runs at 13:00, **Then** it queues the SMS and creates the task in the same run (catch-up).
5. **Given** the DC of Dadu, **When** they list field tasks, **Then** they see only Dadu's tasks; a donor gets 403.

---

### User Story 3 - Feature is off by default (Priority: P1)

FR16 is a roadmap feature. With the flag off, nothing in this spec runs, so the MVP demo is unaffected.

**Why this priority**: Principle X: roadmap features must not destabilise the 3-minute demo.

**Independent Test**: With the flag off, run the sweep over overdue notices; check zero SMS alerts and zero tasks.

**Acceptance Scenarios**:

1. **Given** the flag is off, **When** the sweep runs over 100 overdue notices, **Then** nothing is created and nothing is audited.

---

### User Story 4 - Field agent records the outcome (Priority: P2, roadmap build)

The field agent (in the MVP, the DC office or PDMA officer on their behalf) closes a task with an outcome: received, not received, or could not reach. Received / not received are stored as confirmations through the field-agent channel and count in the receipt numbers.

**Why this priority**: Closes the loop for the donor report's "received" figure.

**Independent Test**: Complete an open task with `CONFIRMED` and a note; check the task is done, a field-agent confirmation exists, the payment's effective receipt status is Confirmed, and two audit entries were written.

**Acceptance Scenarios**:

1. **Given** an open task, **When** the DC of that district completes it with outcome received, **Then** the task is done with outcome, time and user, and a confirmation with channel field agent is recorded.
2. **Given** an open task, **When** it is completed with "could not reach", **Then** the task is done and no confirmation is recorded.
3. **Given** a done task, **When** someone tries to complete it again, **Then** they get a conflict error.
4. **Given** a DC of another district, **When** they try to complete it, **Then** they get 403.

---

### User Story 5 - SMS replies count (Priority: P2, roadmap build)

A beneficiary replies `1` or `۲` to the SMS. The reply is parsed exactly like a WhatsApp reply and recorded with channel SMS.

**Why this priority**: An SMS that cannot be answered only half-solves the problem.

**Independent Test**: Post a signed inbound SMS `Body=1` from the beneficiary's number; check a `CONFIRMED` confirmation with channel SMS on the SMS alert.

**Acceptance Scenarios**:

1. **Given** an SMS fallback was sent, **When** the beneficiary replies by SMS `1`, **Then** a confirmation with channel SMS is recorded and the field-task rule no longer fires.
2. **Given** the same SMS message id arrives twice, **When** processed, **Then** one confirmation exists.

---

### User Story 6 - Immediate fallbacks (Priority: P3, roadmap build)

Waiting 6 hours is pointless if WhatsApp already failed. If the WhatsApp notice failed because the person is not on WhatsApp, the SMS goes at once. If the number is invalid, the field task opens at once. If the beneficiary replies "not received", a field task opens at once to check. For Warning and Action tier alerts that fail on WhatsApp, a short safety SMS goes at once.

**Why this priority**: Faster help, but the timed path already covers these people eventually.

**Independent Test**: Mark a WhatsApp notice `FAILED` with class `NOT_ON_CHANNEL`; run the sweep; check an SMS was queued without waiting.

**Acceptance Scenarios**:

1. **Given** a WhatsApp notice failed as "not on WhatsApp", **When** the sweep runs, **Then** an SMS is queued at once.
2. **Given** a notice failed with an invalid number, **When** the sweep runs, **Then** a task with reason `INVALID_PHONE` is created at once and no SMS is sent.
3. **Given** a `NOT_RECEIVED` reply, **When** it is recorded, **Then** a task with reason `NOT_RECEIVED` is created.
4. **Given** an Action tier alert failed on WhatsApp, **When** the sweep runs, **Then** a safety SMS is queued.

---

### Edge Cases

- **Clock**: deadlines are measured from the WhatsApp notice's `sent_at`, using the system clock (replay clock in replays). A replay day step can pass both deadlines at once; the sweep then does SMS and task in one run.
- **Replay with simulated replies**: the sweep first lets FR15's simulated-reply job run, so simulated beneficiaries who "reply" are not chased.
- **Notice never sent** (`QUEUED` or `FAILED` before sending): the timed rules do not apply; the roadmap immediate rules do.
- **Multiple notices for one beneficiary**: each payment's notice is chased on its own; a reply to the latest notice does not stop the chase of an older one (see FR15 open item 3).
- **Notice older than the 7-day reply window**: never chased.
- **Phone flagged invalid after the WhatsApp notice**: no SMS; go to the task.
- **SMS to Urdu**: Urdu needs Unicode SMS (70 characters per single SMS, 67 per part of a long one). Texts are kept to at most 3 parts.
- **SMS sender in Pakistan**: two-way SMS through an international number may not work in Pakistan; the MVP stub sends dry-run only (see clarify).
- **Payment Paid but later found Failed** (not expected by FR17's state machine): chain stops when the payment is not `PAID`.
- **Field agent visits and finds the money was received after the task opened**: completing with `CONFIRMED` is still correct.

## Message content

SMS texts are short, Urdu first, then English, and must fit 3 Unicode SMS parts (≤ 201 characters including variables at typical lengths).

| Key | Urdu | English |
|---|---|---|
| `sms.payment_notice` | آب و حساب: {amount} روپے {wallet_ur} میں بھیجے گئے۔ ملے تو 1، نہ ملے تو 2 بھیجیں۔ | Rs {amount} sent to your {wallet_en}. Reply 1 if received, 2 if not. Ref {ref} |
| `sms.tier_alert.WARNING` | آب و حساب: {district_ur} میں سیلاب کا بڑا خطرہ۔ اونچی جگہ جانے کی تیاری کریں۔ | High flood risk in {district}. Get ready to move to high ground. |
| `sms.tier_alert.ACTION` | آب و حساب: {district_ur} میں سیلاب آنے والا ہے۔ ابھی اونچی جگہ جائیں۔ | Flood coming to {district}. Go to high ground now. |
| `sms.line.demo` | (آزمائشی) | (DEMO) |

Rendered as one SMS: Urdu line, newline, English line, then `sms.line.demo` when simulated.

Field-agent brief (`field_task.brief`, stored as the `FIELD_AGENT` alert text and shown on the task):

| Urdu | English |
|---|---|
| فیلڈ کام: حوالہ {ref}، تحصیل {tehsil}۔ مستحق سے پوچھیں کہ کیا {amount} روپے مل گئے۔ وجہ: {reason_ur}۔ | Field task: ref {ref}, tehsil {tehsil}. Ask the beneficiary if Rs {amount} was received. Reason: {reason_en}. |

| `reason_code` | `reason_ur` | `reason_en` |
|---|---|---|
| `NO_REPLY_12H` | 12 گھنٹے سے کوئی جواب نہیں | no reply after 12h |
| `INVALID_PHONE` | فون نمبر درست نہیں | invalid phone number |
| `NOT_RECEIVED` | مستحق کے مطابق رقم نہیں ملی | beneficiary reports not received |

## Requirements *(mandatory)*

### Functional Requirements

- **FR16-001**: All behaviour in this spec MUST run only when the `FR16_FALLBACK` flag is on.
- **FR16-002**: A periodic sweep (default every 5 minutes) MUST evaluate every reply-expecting WhatsApp payment notice sent within the reply window.
- **FR16-003**: When a notice has had no valid reply (effective receipt status not `CONFIRMED`/`NOT_RECEIVED`) for 6 hours after it was sent, the system MUST queue exactly one SMS payment notice linked to it (UC10-2a).
- **FR16-004**: When a notice has had no valid reply for 12 hours after the WhatsApp notice was sent, the system MUST create exactly one open field-agent task for that beneficiary and payment, with reason `NO_REPLY_12H`, and a field-agent channel alert linked to the chain (UC10-2b).
- **FR16-005**: The chain MUST stop when a valid reply arrives on any channel, the payment is no longer `PAID`, or the notice leaves the reply window.
- **FR16-006**: The sweep MUST be idempotent and catch up after downtime; repeated runs never create duplicates.
- **FR16-007**: Every SMS alert, task creation and task completion MUST be audited in the same transaction (Principle III).
- **FR16-008**: SMS texts MUST be Urdu first then English, low-literacy, and fit in 3 Unicode SMS parts.
- **FR16-009**: SMS alerts and tasks MUST carry the scenario of the original notice; in the MVP stub, SMS is dry-run only and marked simulated.
- **FR16-010**: Officers and admins MUST be able to list field tasks (filters: status, reason, district, release, date); DCs only their district; donors never.
- **FR16-011** (roadmap): Staff MUST be able to complete a task with an outcome (`CONFIRMED`, `NOT_RECEIVED`, `UNREACHABLE`) and a note; received / not received create a field-agent confirmation.
- **FR16-012** (roadmap): Inbound SMS replies MUST be signature-checked, idempotent by message id, parsed like WhatsApp replies, and recorded with channel SMS.
- **FR16-013** (roadmap): WhatsApp failure classes MUST trigger immediate fallbacks: not on WhatsApp → SMS; invalid number → task; `NOT_RECEIVED` reply → task; failed Warning/Action tier alert → safety SMS.
- **FR16-014**: The 6 h and 12 h thresholds, the sweep interval and the window MUST be configuration, not code.

### Key Entities

- **Alert** (data-model §7): the WhatsApp notice (root), the SMS fallback (`channel=SMS`, `fallback_of_id`), and the field-agent hand-off (`channel=FIELD_AGENT`).
- **FieldAgentTask** (§7): one open task per payment; FR16 proposes reason code, outcome, scenario, links and completion fields (plan.md).
- **Confirmation** (§7): field-agent and SMS outcomes (via FR15's recorder).
- **Payment** (§6), **Beneficiary** (§5), **User** (§8), **AuditLog** (§8).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the flag on, 100% of unanswered notices get an SMS by `sent_at + 6 h + sweep interval` (≤ 6 h 05 min) and a task by `sent_at + 12 h + sweep interval` (fake-clock test).
- **SC-002**: 0 SMS and 0 tasks for notices with a valid reply before the deadline.
- **SC-003**: 3 consecutive sweeps produce identical counts (idempotent).
- **SC-004** (NFR10): 100% of SMS templates render in ≤ 201 characters with sample values and Urdu first.
- **SC-005**: With the flag off, 0 SMS, 0 tasks, 0 audit entries from FR16.
- **SC-006** (NFR7): 100% of FR16 state changes audited; chain verifies.
- **SC-007**: A sweep over 50 000 notices finishes in < 10 s.
- **SC-008** (roadmap): Inbound SMS duplicates produce 1 confirmation; task completion reflects in the receipt summary immediately.

## Assumptions

- FR14 sets `expects_reply`, `sent_at`, `fallback_of_id`, `error_code`; FR15 provides the effective receipt status.
- The deadlines use the WhatsApp notice's `sent_at` (not the SMS's) for both 6 h and 12 h, matching UC10's "no reply within 6 hours … still no reply after 12 hours".
- Field agents are staff acting through the DC office in the MVP; a dedicated role is roadmap.
- A Pakistani SMS route (local aggregator or approved sender) is a roadmap procurement item.

## Traceability

| Source | Covered by |
|---|---|
| UC10 alternate 2a (no reply within 6 hours: send an SMS) | US1, FR16-003 |
| UC10 alternate 2b (still no reply after 12 hours: field agent task) | US2, FR16-004 |
| UC10 postcondition (receipt Confirmed, or a fallback is started) | FR16-003, FR16-004 |
| UC10 secondary actor (WhatsApp API, SMS as fallback) | US1, US5 |
| Document Q13 (WhatsApp …; SMS as fallback) | US1 |
| UC10 alternate 1a (invalid number) — roadmap immediate task | US6 |
| NFR7 | FR16-007 |
| NFR9 / Principle VII (degrade gracefully) | whole feature |
| NFR10 | FR16-008 |
| NFR13 | FR16-009 |
| Principle X (roadmap behind a flag) | FR16-001, US3 |

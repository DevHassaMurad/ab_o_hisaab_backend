# Feature Specification: FR22 Outcome Reports

**Feature Branch**: `022-fr22-outcome-reports`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: Roadmap

**Input**: "FR22 Collect beneficiary outcome reports after support" (Area: Reporting, Priority: Roadmap)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Safe stub so the donor report never breaks (Priority: P1, MVP stub)

With the `FR22_OUTCOME` flag off, the donor report asks for outcome stories and gets an empty list, so it shows its curated simulated stories. Outcome routes say the feature is disabled.

**Why this priority**: The MVP must not depend on a roadmap feature, and FR19 needs a stable call.

**Independent Test**: Flag off: call the service and every `/outcomes` route.

**Acceptance Scenarios**:

1. **Given** the flag is off, **When** FR19 asks for stories for Dadu and Buner, **Then** it gets an empty list without error.
2. **Given** the flag is off, **When** any `/outcomes` route is called, **Then** the response is 404 with code `FEATURE_DISABLED`.
3. **Given** any flag state, **When** the donor-facing outcome schemas are inspected, **Then** they contain no beneficiary-level fields (contract test).

---

### User Story 2 - Short Urdu-first survey after payment (Priority: P2, roadmap)

Seven days after a beneficiary's last payment in a flood event, they receive a WhatsApp message in Urdu and English with three questions, one at a time, answered with a single digit.

**Why this priority**: The core of FR22: evidence that anticipatory cash helped.

**Independent Test**: Create a PAID payment 7 days ago, run the survey job, reply to each question from the test phone.

**Acceptance Scenarios**:

1. **Given** household H's last payment in the event was PAID 7 days ago and H has no open payment notice, **When** the survey job runs inside the send window, **Then** one survey starts (intro plus question 1) and an audit entry `OUTCOME_SURVEY_SENT` is written.
2. **Given** H replies `1` (Urdu digits) to question 1, **When** the reply arrives, **Then** it is stored as `evacuated = yes` and question 2 is sent.
3. **Given** H replies `hello` to question 2, **When** the reply arrives, **Then** H gets one re-ask; a second invalid reply stores no answer for question 2 and moves on to question 3.
4. **Given** H answers question 3, **When** the reply arrives, **Then** the report is `COMPLETED`, a thank-you message is queued, and `OUTCOME_REPORT_COMPLETED` is audited.
5. **Given** H already has a survey for this event, **When** the job runs again, **Then** no second survey is sent.
6. **Given** a shopkeeper, **When** their survey starts, **Then** question 1 asks whether they protected their stock (all / some / none) instead of whether the family moved.

---

### User Story 3 - Unfinished surveys expire (Priority: P3, roadmap)

A survey with no reply for 72 hours expires; answers already given are kept.

**Why this priority**: Keeps routing clean for later payment notices; partial data still counts.

**Independent Test**: Start a survey, answer question 1, advance the clock 72 hours, run the job.

**Acceptance Scenarios**:

1. **Given** a survey last messaged 72 hours ago, **When** the job runs, **Then** its status is `EXPIRED`, its partial answers are kept, and `OUTCOME_REPORT_EXPIRED` is audited.
2. **Given** an expired survey, **When** the beneficiary replies `1`, **Then** the reply is not treated as a survey answer.

---

### User Story 4 - Anonymised aggregates and stories for donors (Priority: P2, roadmap)

Donors and officials see, per district and beneficiary type, how many answered, the share who moved to safety or protected stock, what the money was spent on, and how much it helped, plus short story cards in Urdu and English. Groups with fewer than 5 respondents are hidden.

**Why this priority**: The reason FR22 exists: it feeds UC11-4 with real outcomes instead of curated ones.

**Independent Test**: Seed 40 completed household reports in Dadu and 3 in Buner; request aggregates and stories as a donor.

**Acceptance Scenarios**:

1. **Given** 40 completed household reports in Dadu, 33 with "moved to safety", **When** a donor requests aggregates, **Then** Dadu households show `n=40`, `evacuated_pct=82.5`, and spending shares that add up to 100.
2. **Given** only 3 completed reports for Buner shopkeepers, **When** aggregates are requested, **Then** that cell is returned as `suppressed=true` with no percentages.
3. **Given** Dadu's aggregates, **When** stories are requested, **Then** a story reads like "In Dadu, 33 of 40 households who answered (83%) moved their families to safety before the water came. Most spent the money on food." in Urdu first and English, with no names, phones or tehsil names.
4. **Given** reports built from simulated replay responses, **When** stories are returned, **Then** each story has `simulated=true`.
5. **Given** a donor asks for individual reports, **When** they call the officer list route, **Then** the response is 403.

---

### User Story 5 - Officer monitoring (Priority: P3, roadmap)

The PDMA officer sees survey progress per district: sent, in progress, completed, expired, response rate, without names or phones.

**Independent Test**: Request the reports list as officer and as DC of another district.

**Acceptance Scenarios**:

1. **Given** surveys in Dadu, **When** the officer lists reports, **Then** each item shows `register_ref`, type, status, question reached and answer codes, but no name or phone.
2. **Given** the Buner DC, **When** they list Dadu reports, **Then** they get an empty result (scoped to own district).

---

### Edge Cases

- Beneficiary with `phone_valid=false`: no survey.
- Simulated beneficiary not on the demo allowlist: survey row created only by the simulation script; no WhatsApp send (FR14 rule).
- Beneficiary with a payment notice still awaiting a `1`/`2` reply: survey waits until that notice is answered or 48 hours pass.
- Several payments in one event (top-ups): one survey per beneficiary per event, timed from the last PAID payment.
- A payment later cancelled: if no PAID payment remains in the event, no survey.
- Event still open with more tiers possible: the survey still waits 7 days after the last PAID payment; a new payment in that window pushes the survey back.
- Reply arrives as a WhatsApp button payload or with extra text ("1 haan"): the first digit is used.
- Send window: surveys only start between 09:00 and 19:00 Asia/Karachi.
- Replays: everything carries `scenario_id`; replay surveys are never sent to real phones except allowlisted demo personas.
- Flag switched off while surveys are open: replies are ignored (FR15 falls through to `UNPARSEABLE`), open surveys are left as they are and expire normally when the flag is back on.

## Requirements *(mandatory)*

### Functional Requirements

- **FR22-001**: System MUST provide outcome story and aggregate functions that return empty results, without error, when the `FR22_OUTCOME` flag is off.
- **FR22-002**: System MUST answer every outcome route with "feature disabled" when the flag is off.
- **FR22-003**: When the flag is on, System MUST start at most one survey per beneficiary per trigger event, a configurable number of days (default 7) after the beneficiary's last PAID payment in that event, only inside the send window, only for valid phones, and only when no payment notice is awaiting a reply.
- **FR22-004**: System MUST send questions one at a time, Urdu first then English, answerable with a single digit, using the beneficiary-type-specific first question.
- **FR22-005**: System MUST accept Western, Urdu (`0–9` (Urdu digits)) and Arabic-Indic (`0–9` (Arabic-Indic digits)) digits, use the first digit in the reply, re-ask once on an invalid reply, and record no answer after a second invalid reply.
- **FR22-006**: System MUST store answers as coded values with the raw reply and time, and mark the report completed after the last question.
- **FR22-007**: System MUST expire surveys with no reply for a configurable period (default 72 hours) and keep partial answers.
- **FR22-008**: System MUST NOT collect free text, names, locations or photos in the survey.
- **FR22-009**: System MUST aggregate completed reports per district, beneficiary type and event, and suppress any group with fewer than k respondents (default 5).
- **FR22-010**: System MUST render outcome stories from aggregates in Urdu and English with no personal details and no sub-district names.
- **FR22-011**: Donor-facing aggregate and story responses MUST contain aggregates only; individual reports MUST be denied to donors.
- **FR22-012**: System MUST label aggregates and stories built from simulated data as simulated.
- **FR22-013**: System MUST audit survey start, completion and expiry in the same transaction, without answer text or PII.
- **FR22-014**: System MUST let the officer and the DC (own district) see survey progress without names or phones.
- **FR22-015**: System MUST support generating simulated responses for replay scenarios, labelled simulated.

### Key Entities

- **OutcomeReport** (§5): one per beneficiary per event; answers, channel, received time; proposed status, progress and scoping fields (plan.md).
- **Alert** (§7): survey messages with purpose `OUTCOME_SURVEY`.
- **Payment** (§6): PAID payments start the survey clock.
- **TriggerEvent** (§4), **Beneficiary** (§5), **AuditLog** (§8).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the flag off, 0 errors in FR19 and 100% of `/outcomes` routes return `FEATURE_DISABLED`.
- **SC-002**: 0 beneficiary-level fields in donor outcome responses; 0 groups with fewer than 5 respondents shown (NFR8).
- **SC-003**: 100% of survey messages are Urdu first with single-digit answers (NFR10).
- **SC-004**: Each beneficiary receives at most 1 survey per event (measured over the replay scenarios).
- **SC-005**: Surveys are queued within 1 hour of becoming due inside the send window.
- **SC-006**: Pilot target: response rate ≥ 40% within 72 hours (tracked, not a gate).
- **SC-007**: 100% of stories from simulated data carry `simulated=true` (NFR13).

## Assumptions

- WhatsApp is the only survey channel (FR16 fallback is for receipts).
- Three questions are short enough for low-literacy users; voice is out of scope.
- In production, outbound survey messages use pre-approved WhatsApp templates; the sandbox is used for the demo.
- District-level aggregation is detailed enough for donors and avoids re-identification.

## Traceability

| Item | How FR22 covers it |
|---|---|
| UC11-4 | Outcome stories with no personal details |
| UC10 (pattern) | Same WhatsApp reply-with-a-digit interaction |
| NFR8 | Aggregates only, k-anonymity, donor contract test |
| NFR10 | Urdu first, numeric replies |
| NFR13 | Simulated labels |
| NFR7 | Audit entries |
| Principle X | Roadmap behind flag, minimal stub |
| Data model | `OutcomeReport` (data-model.md §5). No D1–D6 decision applies directly; D4 defines the "event" a survey is tied to |

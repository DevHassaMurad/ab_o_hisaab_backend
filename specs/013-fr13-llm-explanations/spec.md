# Feature Specification: FR13 LLM Explanations

**Feature Branch**: `013-fr13-llm-explanations`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: Roadmap

**Input**: "Explain every score and amount in plain language using the LLM" (FR13, Beneficiaries, Roadmap)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Clearer risk explanations in English and Urdu (Priority: P1)

After the daily scores are saved, the system asks a language model to rephrase each district's template explanation into clearer English and simpler Urdu. Officials see the rephrased text, marked as AI-rephrased, and can always see the template (UC04-5, D2).

**Why this priority**: Explanations are read by officials who decide on large releases; clearer Urdu helps DCs and field staff.

**Independent Test**: Turn the flag on with a fake model that returns a valid rephrasing; run the step; the district's risk index holds the rephrased text with status Accepted, and the template is unchanged.

**Acceptance Scenarios**:

1. **Given** the flag is on and a district has a template explanation, **When** the explanation step runs, **Then** an English and an Urdu rephrasing are stored with the model name, prompt version and status Accepted.
2. **Given** a rephrasing is accepted, **When** an official views the district, **Then** the rephrased text is shown labelled AI-rephrased, and the template is available.
3. **Given** the result was fallback, stale or based on a proxy forecast, **When** the rephrasing is shown, **Then** the same notices as in the template appear at the end, unchanged.

---

### User Story 2 - Guardrails: the model may reword but not change facts (Priority: P1)

Every rephrasing is checked before it is stored for display. Any number not present in the inputs, a missing score, a different tier, the wrong language, excessive length or forbidden content rejects it, and the template is shown instead.

**Why this priority**: A wrong number in an explanation of a money release is worse than no rephrasing at all (NFR4, NFR12).

**Independent Test**: Feed the checker outputs with an invented number, a changed tier, Urdu digits matching the inputs, and English text in the Urdu slot; check accept/reject for each.

**Acceptance Scenarios**:

1. **Given** inputs with score 82 and rain 184 mm, **When** the rephrasing says "190 mm", **Then** it is rejected with reason "number not in inputs" and the template is shown.
2. **Given** the rephrasing writes the score in Urdu digits (۸۲), **When** checked, **Then** it is accepted.
3. **Given** the inputs' tier is Warning, **When** the rephrasing says Action, **Then** it is rejected.
4. **Given** the Urdu rephrasing is mostly English, **When** checked, **Then** it is rejected.
5. **Given** the rephrasing omits the score, **When** checked, **Then** it is rejected.

---

### User Story 3 - Clearer allocation reasons (Priority: P2)

When an officer opens a beneficiary's allocation, the system rephrases the template reason for its rank and amount (UC06-5), under the same guardrails, and stores it on first view.

**Why this priority**: Amount explanations matter for fairness questions, but there are thousands of allocations, so they are done on demand.

**Independent Test**: Open an allocation with the flag on and a fake model; the reason is rephrased and stored; a second view makes no new model call.

**Acceptance Scenarios**:

1. **Given** an allocation with amount PKR 15 000 and rank 12, **When** the officer opens it, **Then** a rephrased reason containing 15000 (in any accepted format) is shown and stored.
2. **Given** the rephrasing contains an amount not in the inputs, **When** checked, **Then** the template reason is shown.
3. **Given** a stored rephrasing, **When** the allocation is opened again with the same inputs, **Then** no new model call is made.

---

### User Story 4 - Never slow down or block the money path (Priority: P1)

The language model runs after scores and triggers are committed, with strict timeouts and a time budget. Any timeout, refusal, error or missing configuration leaves the template in place and records why.

**Why this priority**: Principle I: money must not wait for prose.

**Independent Test**: With a fake model that sleeps past the timeout, the forecast and trigger steps finish on time and every explanation status is Error (timeout).

**Acceptance Scenarios**:

1. **Given** the model times out, **When** the step runs, **Then** the template stays, the status is Error with reason timeout, and tier releases are unaffected.
2. **Given** the step's time budget runs out, **When** items remain, **Then** they keep the template and are retried on the next run or by an admin.
3. **Given** the flag is on but no API key is configured, **When** the step runs, **Then** no call is attempted and the status says the key is missing.

---

### User Story 5 - Admin control, monitoring and audit (Priority: P3)

An admin can regenerate one explanation and see acceptance, rejection and error counts by reason. Each evaluated rephrasing is recorded in the audit log with a hash of the text.

**Why this priority**: Operational visibility; not needed for the first useful version.

**Independent Test**: Regenerate one risk explanation and check the new status and audit entry; read the status endpoint counts.

**Acceptance Scenarios**:

1. **Given** the flag is on, **When** the admin regenerates an explanation, **Then** a new attempt is made and its result recorded.
2. **Given** the flag is off, **When** the admin regenerates, **Then** the request is refused.
3. **Given** a day of runs, **When** the admin opens the status, **Then** counts of accepted, rejected (by reason) and errors are shown.

---

### User Story 6 - Flag off: identical behaviour to the MVP (Priority: P1, MVP stub)

With `FR13_LLM` off, the system makes no model calls and all explanation endpoints return the template with the status Disabled.

**Why this priority**: D2: templates are the MVP; the roadmap feature must be safe to ship dark.

**Independent Test**: With the flag off, run the pipeline and call the endpoints; no outbound call is made (client spy) and status is Disabled.

**Acceptance Scenarios**:

1. **Given** the flag is off, **When** the pipeline runs, **Then** the explanation step does nothing.
2. **Given** the flag is off, **When** an explanation is read, **Then** the template is returned as the display text with status Disabled.

### Edge Cases

- The model returns the template unchanged: accepted (it passes all checks) and stored; display looks the same.
- The model writes "two days" instead of "2 days": number words are converted and checked like digits.
- The model writes "15,000" or "15 ہزار" for 15000: separators and thousand/lakh words are normalised before the check.
- A date written as "24 August" when the input has 2022-08-24: day, month number, month name and year are allowed tokens.
- Percent factors: the input stores 0.91 and the template shows 91; both 0.91 and 91 are allowed.
- The model refuses (stop reason refusal) or stops at the token limit: template, status Error with the stop reason.
- Inputs change after a rephrasing (re-run): the stored rephrasing's input hash no longer matches; it is regenerated on the next run.
- Replay scenarios: rephrasing is off by default in replays so backtests stay deterministic and offline.
- Prompt injection: inputs are system-generated numbers and seeded district names; they are passed as data, and output checks apply regardless.

## Requirements *(mandatory)*

### Functional Requirements

- **FR13-001**: The feature MUST be controlled by the flag `FR13_LLM`; with it off, no model call is made and behaviour equals the MVP.
- **FR13-002**: The template explanation MUST always be generated and stored first; the rephrasing MUST be stored separately.
- **FR13-003**: The system MUST rephrase each district risk explanation in English and Urdu after the day's scores and trigger evaluation are committed.
- **FR13-004**: The system MUST rephrase an allocation reason on first view when the flag is on, and store it.
- **FR13-005**: Every number in a rephrasing (digits in any script, number words, scaled amounts) MUST appear in the inputs or the template; otherwise the rephrasing MUST be rejected.
- **FR13-006**: A rephrasing MUST contain the score (risk) or the final amount (allocation), MUST name the same tier as the inputs and no other tier, MUST be in the requested language, MUST NOT exceed the length limit, and MUST NOT contain links, contact details or formatting markup.
- **FR13-007**: Fallback, stale and proxy notices MUST be appended to the rephrasing by the system, verbatim from the template.
- **FR13-008**: Only whitelisted, non-identifying fields MUST be sent to the model; beneficiary names, phone numbers, CNIC, wallet ids and coordinates MUST NOT be sent.
- **FR13-009**: Each model call MUST have a timeout; the step MUST have an overall time budget; on timeout, refusal, error or missing configuration the template MUST be shown and the reason recorded.
- **FR13-010**: Identical inputs MUST NOT trigger a new model call (result cache by input hash).
- **FR13-011**: Each rephrasing MUST record the model, prompt version, status (Accepted, Rejected, Error, Disabled) and rejection or error reasons.
- **FR13-012**: Read endpoints MUST return the template, the rephrasing (if any) with its status, and which one is displayed; displayed rephrasings MUST be labelled AI-rephrased.
- **FR13-013**: An admin MUST be able to regenerate one explanation and view acceptance statistics.
- **FR13-014**: Each evaluated rephrasing MUST write one audit entry with the text hash, model, prompt version and result.
- **FR13-015**: Rephrasing MUST be off in replay scenarios unless explicitly enabled.
- **FR13-016**: Beneficiary-facing WhatsApp messages MUST NOT use rephrased text.

### Key Entities

- **RiskIndex**: template explanations (FR3) and rephrased explanations with status, model, prompt version, input hash.
- **Allocation**: template reasons (FR11) and rephrased reasons with status.
- **AuditLog**: `EXPLANATION_LLM_EVALUATED` entries.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 displayed rephrasings contain a number absent from the inputs (enforced by the checker; verified on the eval set and by a nightly re-check of stored texts).
- **SC-002**: On the evaluation set, ≥ 90% of English and ≥ 80% of Urdu rephrasings are accepted.
- **SC-003**: In a native-speaker review of 20 Urdu rephrasings, 100% are factually identical to the template and ≥ 80% are judged easier to read (NFR10).
- **SC-004**: Turning the flag on adds 0 seconds to the time between a score crossing a threshold and the release (NFR2, Principle I).
- **SC-005**: The daily rephrasing step for all pilot districts finishes within 120 seconds.
- **SC-006**: With the flag off, the system makes 0 outbound model calls.

## Assumptions

- An Anthropic API key is available for the roadmap build; data-processing terms are checked before production use with real beneficiary data.
- Templates (FR3, FR11) are correct; FR13 only improves wording.
- Officials prefer simpler Urdu over literal translation; a native speaker reviews prompts and samples.
- Volumes: about 70 district explanations × 2 languages per day; allocation reasons only when opened.

## Traceability

| Item | How FR13 covers it |
|---|---|
| UC04 step 5 ("LLM writes a short plain language explanation") | US1 (under D2: template first, LLM rewrite) |
| UC06 step 5 (reason for each amount) | US3 |
| NFR4 | FR13-002, FR13-005, FR13-011 |
| NFR10 | Urdu rephrasing, SC-003 |
| NFR12 | LLM cannot change any number or tier |
| NFR13 | AI-rephrased label; notices preserved |
| NFR6 / NFR8 | FR13-008 |
| D2 | Whole feature: template always stored and the fallback, LLM behind a flag |

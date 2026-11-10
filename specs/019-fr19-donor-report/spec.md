# Feature Specification: FR19 Donor report

**Feature Branch**: `019-fr19-donor-report`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: FR19 (project document, Reporting): "Show donors amounts allocated, disbursed, received and unused"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The donor sees where the money is (Priority: P1)

A donor signs in and opens the report. They see, for the funds they contributed to: how much was allocated to anticipatory action, how much was released by the tiers, how much was paid out, how much beneficiaries confirmed receiving, how much is still in flight, and how much is unused.

**Why this priority**: UC11 steps 1–2. "Donors cannot see how much was allocated, received and spent" is one of the four problems the product exists to solve.

**Independent Test**: Seed one fund (allocated 50 000 000 PKR) with a Watch release (10 %), 100 payments: 90 Paid (60 confirmed, 5 not received, 25 no reply), 6 Failed, 4 Cancelled. The summary returns exactly the numbers computed by hand from the definitions below, and the identities hold.

**Acceptance Scenarios**:

1. **Given** a donor linked to the Dadu and Buner funds, **When** they open the report, **Then** they see allocated, released, disbursed, received, in flight and unused totals across those two funds only, as of now.
2. **Given** some payments are Paid but unconfirmed, **When** the report is shown, **Then** received counts only payments whose latest confirmation is "received", and "awaiting confirmation" and "reported not received" are shown separately.
3. **Given** an Action release is waiting for the DC, **When** the report is shown, **Then** its amount is shown as "awaiting approval" and is not counted as released.
4. **Given** a payment was cancelled, **When** the report is shown, **Then** its amount is counted in unused (it returned to the fund), not in disbursed or in flight.
5. **Given** a replay is selected, **When** the donor opens the report, **Then** the numbers come only from that replay and the report is labelled simulated.

---

### User Story 2 - The donor sees the breakdowns and payment counts (Priority: P1)

The donor sees the same metrics by district and by beneficiary type (households, shopkeepers), and by tier, plus how many payments are in each status, without ever seeing an individual.

**Why this priority**: UC11 step 3 and UC09 alt 3a ("donors see only totals").

**Independent Test**: With payments in two districts and both types, the district rows add up to the totals, the type rows add up to the totals for every metric where type applies, and payment counts by status add up to the number of payments.

**Acceptance Scenarios**:

1. **Given** two districts, **When** the donor views by district, **Then** each district shows every metric and the rows sum to the totals.
2. **Given** households and shopkeepers, **When** the donor views by type, **Then** each type shows released pool, disbursed, received, in flight and unmet need, and the rows sum to the totals.
3. **Given** a breakdown cell covering fewer than 5 beneficiaries, **When** the report is shown, **Then** that cell's amounts and counts are hidden and marked "fewer than 5", and a second cell is hidden if needed so the hidden one cannot be worked out by subtraction.
4. **Given** payments in all statuses, **When** the donor views payment counts, **Then** they see counts and amounts per status and the number stuck, per district and in total.

---

### User Story 3 - Individual data is never shown to a donor (Priority: P1)

If a donor asks for beneficiary-level or payment-level data, the request is refused and they are pointed to the aggregate report. No donor response, including the PDF, ever contains a name, phone, CNIC, wallet, coordinates or an individual record id.

**Why this priority**: NFR8 and Principle VI; Principle XI makes the donor no-PII test mandatory.

**Independent Test**: Seed beneficiaries with unique canary values. As a donor, call every reachable route and download the PDF; none of the canary values or individual ids appear. Call the individual-data routes; all return 403.

**Acceptance Scenarios**:

1. **Given** a donor, **When** they request beneficiary or payment lists or a single payment (through the report routes or the operational routes), **Then** they get 403 with "aggregate only" and a pointer to the report.
2. **Given** any donor-facing response, **When** it is checked, **Then** it contains no field from the PII deny-list and no individual record ids.
3. **Given** a new route is added that a donor can reach, **When** the test suite runs, **Then** the route sweep test fails until the route is explicitly allowlisted and proven aggregate-only.
4. **Given** a donor asks for a district their contribution is not linked to, **When** they request it, **Then** they get 403.

---

### User Story 4 - The donor verifies the log and downloads a PDF (Priority: P2)

From the report, the donor verifies the audit hash chain with one click and downloads the report as a PDF. The PDF carries the verify result, the chain head and a fingerprint of the report, and the download is recorded in the audit log.

**Why this priority**: UC11 steps 5–6 and the postcondition "verified, downloadable report".

**Independent Test**: Download the PDF; it opens, shows the SIMULATED banner, the metrics, the breakdowns, the stories and "Audit chain: VALID, N entries, head …"; the audit log has one new `DONOR_REPORT_GENERATED` entry whose report hash matches the hash printed in the PDF footer.

**Acceptance Scenarios**:

1. **Given** the report screen, **When** the donor clicks verify, **Then** the chain is recomputed and the result (valid, count, head, first broken entry if any) is shown.
2. **Given** the report screen, **When** the donor downloads the PDF, **Then** they get a file with the same numbers as the screen, the verify result at generation time and the report fingerprint, within 5 seconds.
3. **Given** the chain is broken, **When** the PDF is generated, **Then** the PDF shows the failure prominently rather than hiding it.

---

### User Story 5 - The donor reads anonymised outcome stories (Priority: P3)

The report shows short stories of what the support made possible, with no personal details. In the MVP these are curated, illustrative stories clearly labelled simulated; once outcome reports exist (FR22) they are built from aggregated survey answers.

**Why this priority**: UC11 step 4; it is the human side of the report but not needed for the numbers.

**Independent Test**: With the FR22 flag off, the stories endpoint returns the curated stories for the donor's districts, each labelled simulated and source "curated"; none contains digits that look like phones or CNICs, or any register name.

**Acceptance Scenarios**:

1. **Given** the MVP, **When** the donor views stories, **Then** they see curated stories for their districts, in English and Urdu, each labelled "Illustrative story (simulated)".
2. **Given** FR22 is enabled and enough outcome reports exist, **When** stories are shown, **Then** they are aggregated from at least 5 reports each and labelled as such.
3. **Given** a story file containing a phone-like number, **When** it is loaded, **Then** loading fails and the story is never served.

---

### Edge Cases

- Donor with no `DonorContribution`: 403 `NO_LINKED_FUNDS` (not an empty report), so misconfiguration is visible.
- No releases yet (quiet year, e.g. the 2021 control replay): released, disbursed, received are 0; unused equals allocated; stories may be empty. The report says "No tier was triggered".
- No confirmations yet (FR15 not live): received is 0 and awaiting confirmation equals disbursed.
- A payment has several confirmations (e.g. "2" then "1"): the latest one counts.
- A beneficiary received payments in several tiers: "beneficiaries reached" counts them once; amounts add up.
- Fund allocated amount was edited after payments (FR6): the report uses the current `allocated_pkr`; the identity still holds because FR17 keeps the ledger in sync.
- Rounding: all money is exact Decimal; no percentages are rounded before summing; displayed percentages are rounded to one decimal.
- Officer or admin opens the donor report (preview): same aggregate output, all districts, no extra fields.
- PDF requested during heavy activity: the numbers and the verify are each consistent snapshots; the report fingerprint makes the exact content checkable.

## Requirements *(mandatory)*

### Functional Requirements

Metric definitions (normative; scope S = the donor's funds; all amounts in PKR; "release counted" = release status Approved, Allocating, Released or Partially released):

- **FR19-001 Allocated**: sum of `allocated_pkr` of the funds in S.
- **FR19-002 Released**: sum of release amounts of counted releases in S.
- **FR19-003 Awaiting approval**: sum of release amounts in S whose status is Pending approval.
- **FR19-004 Disbursed**: sum of payment amounts in S with status Paid.
- **FR19-005 Received**: sum of payment amounts in S with status Paid whose latest confirmation (ignoring unparseable replies) is Confirmed.
- **FR19-006 Reported not received**: same, latest confirmation Not received. **Awaiting confirmation** = disbursed − received − reported not received.
- **FR19-007 In flight**: sum of payment amounts in S with status Reserved, Processing or Failed (equals the funds' reserved balance).
- **FR19-008 Unused**: allocated − disbursed − in flight (equals the funds' remaining balance). It includes tiers never triggered, release money not assigned to anyone, and cancelled payments.
- **FR19-009 Unmet need**: sum of computed amounts of allocations in S marked Unmet need, with their count (people the released money could not cover).
- **FR19-010 Beneficiaries reached**: count of distinct beneficiaries with at least one Paid payment in S; **confirmed beneficiaries** likewise for received.
- **FR19-011**: The system MUST show these rates: disbursement rate = disbursed ÷ released; confirmation rate = received ÷ disbursed (null when the divisor is 0).
- **FR19-012**: The system MUST show the metrics by district, by beneficiary type (released = the household or shopkeeper pool of counted releases; allocated and unused are not split by type), by district × type, and by tier (released, disbursed, received).
- **FR19-013**: The system MUST show aggregate payment counts and amounts by status (and the stuck count), in total and per district.
- **FR19-014**: The system MUST hide any breakdown cell covering fewer than 5 beneficiaries (configurable) and apply secondary hiding so it cannot be derived from totals.
- **FR19-015**: The scope MUST be the funds linked to the donor through their contributions; for a replay, the same districts' replay funds. Officers and admins MAY preview any scope. DCs and other roles MUST be refused.
- **FR19-016**: Requests for individual beneficiary or payment data by a donor MUST return 403 "aggregate only" with a pointer to the report.
- **FR19-017**: Donor responses MUST NOT contain names, phones, CNICs, wallets, coordinates, notes, tehsil-level rows or the ids of beneficiaries, allocations, payments, confirmations or alerts.
- **FR19-018**: The system MUST show anonymised outcome stories: curated and labelled simulated in the MVP; aggregated from at least 5 outcome reports when FR22 is enabled.
- **FR19-019**: The report MUST link to the one-click audit verify (FR20) for the same scope (live or replay).
- **FR19-020**: The system MUST produce a PDF of the report with the same numbers, the verify result at generation time, the chain head, a SHA-256 fingerprint of the report content, the as-of time and a simulated banner when applicable.
- **FR19-021**: Each PDF generation MUST write one audit entry with the fingerprint, scope and chain head.
- **FR19-022**: Every response MUST carry `as_of`, the scope (districts), whether it is live or a replay, and `simulated`.

### Key Entities

- **DonorContribution** (data-model.md §8): links a donor to funds; defines the scope.
- **DisasterFund** (§4): allocated, reserved, disbursed, remaining.
- **FundRelease** (§4): tier, amount, pools, status.
- **Allocation** (§5): Unmet need amounts.
- **Payment** (§6): status and amounts.
- **Confirmation** (§7): received / not received.
- **Beneficiary** (§5): type only (for the type breakdown).
- **OutcomeReport** (§5): source of aggregated stories when FR22 is on.
- **AuditLog** (§8): verify and the report-generated entry.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (NFR8, Principle XI): 0 PII values and 0 individual record ids in any donor response or PDF across the canary test; 100 % of non-allowlisted routes return 403 to the donor.
- **SC-002**: Every metric matches its hand-computed value on the reference fixture (exact Decimal equality), and the identities hold in 100 % of tests.
- **SC-003** (NFR1): summary p95 < 500 ms at 50 000 payments; PDF in < 5 s.
- **SC-004** (NFR7): the verify result shown in the report equals `GET /audit/verify` for the same scope.
- **SC-005** (NFR13): 100 % of MVP reports and PDFs show the simulated label; 100 % of curated stories are labelled simulated.
- **SC-006**: Each PDF download produces exactly one `DONOR_REPORT_GENERATED` audit entry whose fingerprint matches the PDF footer.

## Assumptions

- Donors are linked to whole district funds; reporting shows the full totals of those funds, not a pro-rata share of the donor's contribution (the donor's own contribution total is shown alongside).
- The PDF is in English in the MVP; Urdu story text is in the JSON API.
- Reports are "as of now"; date-range filtering is roadmap.
- Confirmations come from FR15; outcome stories from FR22 are roadmap.
- The minimum cell size is 5 beneficiaries.

## Traceability

| Item | How this FR covers it |
|---|---|
| UC11 steps 1–2 | Summary metrics |
| UC11 step 3 | By district, by type, district × type |
| UC11 step 4 | Anonymised stories (curated + simulated label in MVP) |
| UC11 step 5 | Verify link and verify in PDF (FR20) |
| UC11 step 6 | PDF download |
| UC11 alt 3a | 403 on individual data |
| UC09 alt 3a | Aggregate payment counts |
| NFR6, NFR8 | Role scope, DonorScope schemas, contract tests |
| NFR7 | Verify; report-generated audit entry |
| NFR13 | Simulated labels and banner |
| NFR1 | Performance targets |
| D5 | Uses the canonical entities only |
| Principles II, III, VI, VIII, XI | See plan.md Constitution Check |

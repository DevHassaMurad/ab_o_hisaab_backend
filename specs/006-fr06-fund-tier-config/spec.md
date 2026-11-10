# Feature Specification: FR6 Fund and Tier Configuration

**Feature Branch**: `006-fr06-fund-tier-config`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: FR6 (project document, Functional requirements): "Let the PDMA officer set each district's fund, tier thresholds and release percentages"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Configure a district's fund and tiers (Priority: P1)

At the start of the monsoon, or after a policy change, the PDMA officer opens Fund settings, picks a district, enters the fund amount in PKR, sets the thresholds (Watch 40, Warning 60, Action 80), the release increments (10%, 30%, 60%), the household/shopkeeper split (70/30) and the cap per shopkeeper, and saves. The system validates the whole form, saves it and writes one audit entry. The new settings apply from the next daily forecast run.

**Why this priority**: Nothing can trigger or pay without a configured fund. It is the first step of the demo story.

**Independent Test**: With a seeded district, submit a valid settings form and check the stored fund, the three tiers and one new audit entry.

**Acceptance Scenarios**:

1. **Given** the officer is logged in and Dadu has a fund, **When** they save amount PKR 50,000,000, thresholds 40/60/80, increments 10/30/60, split 70 and cap PKR 50,000, **Then** the settings are stored, the fund `version` goes up by 1, and exactly one `FUND_SETTINGS_UPDATED` audit entry holds the before and after values.
2. **Given** saved settings, **When** the officer reloads Fund settings, **Then** they see the saved values, the derived shopkeeper share (30), the current balances and a note that changes apply from the next daily run.
3. **Given** a save at 14:00 UTC, **When** the next daily run starts, **Then** tier mapping and release amounts use the new values. A tier already fired in an open event keeps the amount it was fired with.
4. **Given** the officer submits values identical to the stored ones, **When** they save, **Then** the response is 200, nothing is written and no audit entry is added.
5. **Given** any request body that includes `requires_approval`, **When** it is saved, **Then** the request is rejected and Action still requires approval.

---

### User Story 2 - Invalid settings are rejected and nothing is saved (Priority: P1)

If the increments do not add up to 100, thresholds do not increase, or any amount is out of range, the officer sees every error at once and nothing is saved (UC01-5a).

**Why this priority**: A bad configuration releases the wrong amount of public money. This is a money path (Principle XI).

**Independent Test**: Submit each invalid case and check the error list, the unchanged fund row, the unchanged `version` and an unchanged audit chain length.

**Acceptance Scenarios**:

1. **Given** increments 10/30/50, **When** saved, **Then** the response lists `TIER_PCT_SUM_INVALID` and nothing changes.
2. **Given** thresholds 40/60/60 or 60/40/80, **When** saved, **Then** `TIER_THRESHOLD_ORDER_INVALID` is listed.
3. **Given** a threshold of 0 or 101, **When** saved, **Then** `TIER_THRESHOLD_RANGE_INVALID` is listed.
4. **Given** several problems at once (e.g. a bad sum and a negative cap), **When** saved, **Then** all errors are returned in one response.
5. **Given** a fund amount below what is already reserved plus disbursed, **When** saved, **Then** `FUND_BELOW_COMMITTED` is returned.
6. **Given** two officers edit the same fund, **When** the second one saves with an old `version`, **Then** they get a 409 `VERSION_CONFLICT` and the first save stands.

---

### User Story 3 - Copy settings from another district (Priority: P2)

The officer copies the rules of a configured district (e.g. Dadu) into another (e.g. Jacobabad), edits them and saves (UC01-1a).

**Why this priority**: It saves time when rolling out many districts (NFR11). It is not needed for the core demo.

**Independent Test**: Request a copy draft, check that it matches the source rules and that nothing was written; then save it through the normal path.

**Acceptance Scenarios**:

1. **Given** Dadu is configured, **When** the officer requests a draft for Jacobabad copied from Dadu, **Then** the draft holds Dadu's thresholds, increments, split, cap, household amounts, stock percentage and close days, Jacobabad's own fund amount and Jacobabad's `version`, and no row or audit entry changes.
2. **Given** `include_amount=true`, **When** the draft is requested, **Then** the fund amount is copied too.
3. **Given** the draft is edited and saved, **Then** it goes through the same validation and audit as User Story 1.

---

### User Story 4 - Read-only views by role (Priority: P2)

The DC sees their own district's settings and balances. Officers and admins see all districts. Donors cannot open settings.

**Why this priority**: DCs need to know the amounts they will be asked to approve. It is read-only.

**Independent Test**: Call the read endpoints with each seeded role.

**Acceptance Scenarios**:

1. **Given** the Dadu DC, **When** they read Dadu's settings, **Then** they get 200; **When** they read Buner's settings, **Then** they get 403.
2. **Given** the Dadu DC or an admin, **When** they try to save, **Then** they get 403.
3. **Given** a donor, **When** they call any settings endpoint, **Then** they get 403.
4. **Given** the officer, **When** they list fund settings, **Then** they get every active district with its amount, remaining balance and current tier summary.

### Edge Cases

- **Edit during an open trigger event**: allowed. The response flags `open_event`. Tiers already fired keep their stored amounts. Later tiers use the new amount through the cumulative rule (FR7).
- **Increment of 0%** on a tier: allowed if the sum is 100. That tier then fires with PKR 0, which is still recorded and audited by FR7.
- **Fund amount of 0**: allowed (district monitored, nothing to release).
- **Amount with more than 2 decimal places**, or a negative amount: rejected with `FUND_AMOUNT_INVALID`.
- **District without a fund** (new district added by config): the first save creates the fund and its three tiers, with `version` 1.
- **Inactive district**: settings can be edited, but the pipeline skips the district (NFR11).
- **Replay scenario header present**: the edit applies to that scenario's fund only. Live data is untouched.
- **Copy from a district without a fund**: 404 `SOURCE_FUND_NOT_FOUND`.
- **Copy from itself**: 422 `COPY_SOURCE_SAME_AS_TARGET`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR06-001**: The system MUST let a PDMA officer set, for one district, the fund amount (PKR), the Watch/Warning/Action thresholds, the three release increments, the household share, the shopkeeper cap, the shopkeeper stock percentage, the household amount per tier and the event close days.
- **FR06-002**: The system MUST treat release percentages as increments (D4) and reject any set that does not sum to exactly 100.
- **FR06-003**: The system MUST reject thresholds that are not whole numbers from 1 to 100 or that do not strictly increase Watch < Warning < Action.
- **FR06-004**: The system MUST validate the whole submission, return every error in one response, and save nothing when any check fails.
- **FR06-005**: The system MUST reject a fund amount lower than the money already reserved plus disbursed.
- **FR06-006**: The system MUST NOT allow `requires_approval` to be changed through any interface. Action always requires approval. Watch and Warning never do.
- **FR06-007**: The system MUST save fund and tier changes atomically, together with exactly one audit entry holding the before and after values, the actor and the new version.
- **FR06-008**: The system MUST NOT write anything, including an audit entry, when a submission changes nothing.
- **FR06-009**: The system MUST detect concurrent edits with a version number and reject the stale one.
- **FR06-010**: The system MUST apply saved settings from the next daily run. Amounts of releases that already fired MUST NOT change.
- **FR06-011**: The system MUST let the officer build an unsaved draft by copying another district's settings, without its fund amount unless asked.
- **FR06-012**: The system MUST restrict writes to PDMA officers, limit DCs to reading their own district, and deny donors.
- **FR06-013**: The system MUST provide one shared rule that maps a score to a tier with the district's thresholds (score ≥ threshold enters the tier), used by forecasting and triggers alike.
- **FR06-014**: Default values for a new fund MUST come from configuration, not code (40/60/80, 10/30/60, 70, PKR 50,000, 40%, 14 days).

### Key Entities

- **DisasterFund**: one per district per scenario. Holds the amount, balances, split, cap, stock percentage, household amounts by tier, event close days and version.
- **TriggerTier**: three per fund (Watch, Warning, Action) with threshold, increment and the locked approval flag.
- **AuditLog**: one `FUND_SETTINGS_UPDATED` entry per effective save.
- **District**, **User**: scope and actor.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of invalid submissions in the test suite leave the fund, the tiers and the audit chain unchanged (UC01-5a).
- **SC-002**: Every effective save produces exactly one audit entry, and the chain verifies afterwards (NFR7).
- **SC-003**: Adding a district's fund needs only a seed or API change, with zero code changes (NFR11).
- **SC-004**: Settings reads respond with p95 < 500 ms for the 68 pilot districts (NFR1).
- **SC-005**: No request, from any role, can turn off approval for the Action tier (NFR12).
- **SC-006**: An officer can complete the settings form for a district in under 2 minutes, or under 1 minute with copy.

## Assumptions

- The UC01 phrase "admin role" means the PDMA officer role. The system `ADMIN` role can read but not write settings (see clarify.md).
- Pilot officers manage all districts. Province scoping is roadmap.
- Funds are seeded for the pilot districts from `data/seeds/funds.yaml`.
- "Next daily run" means the next scheduled pipeline run. The pipeline reads settings when it starts each district.

## Traceability

| Item | Covered by |
|---|---|
| UC01 main flow 1–6 | US1, FR06-001…007, FR06-010 |
| UC01-1a copy | US3, FR06-011 |
| UC01-5a validation fails | US2, FR06-004 |
| NFR1 | SC-004 |
| NFR6 | FR06-012 |
| NFR7 | FR06-007, SC-002 |
| NFR11 | FR06-014, SC-003 |
| NFR12 | FR06-006, SC-005 |
| D4 increments | FR06-002 |
| D5 fund fields | FR06-001 |

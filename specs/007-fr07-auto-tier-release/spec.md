# Feature Specification: FR7 Automatic Tier Release

**Feature Branch**: `007-fr07-auto-tier-release`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: FR7 (project document, Functional requirements): "Automatically release Watch and Warning tier funds when a threshold is crossed"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Watch and Warning release automatically (Priority: P1)

Each day, after a district is scored, the system compares the score with the district's thresholds. When the score crosses Watch (40+) or Warning (60+) for the first time in a flood episode, the matching tranche of the district fund is released without waiting for anyone: Watch 10% of the fund, Warning a further 30%. Each tranche is split into household and shopkeeper pools, allocated to ranked beneficiaries, paid, and the beneficiaries are alerted.

**Why this priority**: This is the core promise: money moves before the water arrives (Principle I).

**Independent Test**: With a PKR 50 M fund and default tiers, feed a score of 45 then 65 on consecutive days. Check a PKR 5 M Watch release and a PKR 15 M Warning release, each moving to allocation, with audit entries.

**Acceptance Scenarios**:

1. **Given** Dadu has no open event and a PKR 50,000,000 fund, **When** today's score is 45, **Then** a flood episode opens, one Watch release of PKR 5,000,000 is created (pools 3,500,000 / 1,500,000), it goes to allocation without approval, and a `TIER_FIRED` audit entry is written.
2. **Given** that Watch already fired in the open episode, **When** the next score is 65, **Then** only a Warning release of PKR 15,000,000 is created (40% released in total).
3. **Given** an open episode where Watch fired, **When** the score stays 45 for more days, **Then** no further release is created.
4. **Given** a release has been created, **When** allocation completes with every beneficiary funded, **Then** the release becomes Released; if some are Unmet need, it becomes Partially released.

---

### User Story 2 - Skipped tiers fire and Action waits for the DC (Priority: P1)

If the score jumps several tiers in one day, every lower tier that has not fired yet fires automatically, and the Action tier is created but held for DC approval.

**Why this priority**: Flash floods (Buner 2025) jump straight to high scores. Principle II forbids releasing Action without approval.

**Independent Test**: Feed a score of 85 to a district with no open episode. Check three releases: Watch and Warning moving to allocation, Action pending approval, an approval request, and no Action allocations.

**Acceptance Scenarios**:

1. **Given** no open episode, **When** the score is 85, **Then** Watch (PKR 5 M) and Warning (PKR 15 M) are released automatically and Action (PKR 30 M) is created as Pending approval with an approval request to the DC.
2. **Given** an Action release pending approval, **When** any part of the system tries to allocate or pay it, **Then** it is refused.
3. **Given** the DC rejects the Action release, **When** the score stays above 80, **Then** no new Action release is created in this episode and the PKR 30 M stays in the fund.

---

### User Story 3 - The DC hears about every tier change within 5 minutes (Priority: P1)

Whenever a district's tier changes (up or down), the DC gets a WhatsApp alert with the new tier, the score, the amount released (if any) and its purpose.

**Why this priority**: NFR2 and the live demo ("the phone buzzes").

**Independent Test**: Move a district NONE → WATCH → WARNING → WATCH; check one DC alert per change, queued in the same transaction as the evaluation.

**Acceptance Scenarios**:

1. **Given** a tier rise, **When** evaluation finishes, **Then** a DC tier alert is queued with a timestamp equal to the firing time (≤ 5 minutes, NFR2).
2. **Given** a tier fall (Warning → Watch), **When** evaluation finishes, **Then** the DC is alerted that risk is easing, and no money is taken back.
3. **Given** the tier did not change, **When** evaluation runs, **Then** no DC alert is sent.

---

### User Story 4 - Episodes open and close cleanly; re-runs are safe (Priority: P1)

A flood episode opens on the first day at or above Watch and closes after 14 consecutive days below Watch (configurable). Running the daily evaluation twice for the same day changes nothing.

**Why this priority**: Without this, re-runs could pay twice, and a second flood in the same season could never fire.

**Independent Test**: Run the same day twice; then feed 14 low days and a new high day; check one closed episode and a new one with fresh releases.

**Acceptance Scenarios**:

1. **Given** a day was already evaluated, **When** evaluation runs again with the same score, **Then** no release, alert or audit entry is added.
2. **Given** an open episode, **When** the score is below Watch for 14 consecutive days, **Then** the episode closes and an audit entry is written.
3. **Given** an Action approval is still pending, **When** the 14th low day arrives, **Then** the episode stays open until the approval is decided.
4. **Given** a closed episode, **When** the score crosses Watch again, **Then** a new episode opens and Watch fires again.
5. **Given** a replay scenario is running, **When** it fires tiers, **Then** its episodes and releases are separate from live data and marked simulated.

---

### User Story 5 - Officers and DCs can see episodes and releases (Priority: P2)

The officer sees all episodes and releases with their amounts, pools, status and how each amount was computed. The DC sees their own district. An admin can re-evaluate a district or resume a stalled release.

**Why this priority**: Monitoring and recovery; not needed for the first firing.

**Independent Test**: Call the read endpoints per role; resume a release left in Allocating after a simulated failure.

**Acceptance Scenarios**:

1. **Given** releases exist, **When** the officer lists them filtered by district and status, **Then** each item shows tier, amount, pools, status and the calculation.
2. **Given** an allocation step failed, **When** the officer resumes the release, **Then** the pipeline re-runs without creating duplicate allocations or payments.

### Edge Cases

- **Fund smaller than the computed tranche** (money already used by an earlier episode): the tranche is capped at the money available; the release is marked Partially released; the cap is recorded.
- **Fund empty or 0% increment**: the tier still fires and is audited with PKR 0, so the record shows the trigger happened.
- **Empty beneficiary register**: the release completes with no allocations; the full tranche stays in the fund.
- **Score drops then rises within an episode** (Warning → Watch → Warning): no tier fires twice; the DC gets alerts for both changes.
- **Settings changed mid-episode**: tiers already fired keep their amounts; later tiers use the new fund amount through the cumulative rule.
- **Out-of-order evaluation** (an older run date after a newer one): ignored and logged.
- **Allocation, payment or alert step fails**: the firing is kept; the release stays Allocating with the error recorded and can be resumed.
- **Tier from the score disagrees with the stored tier on the score record**: the district's current thresholds win; a warning is logged.
- **DC has no phone**: the DC alert is still recorded by the alerting service, which flags it as undeliverable (FR14).

## Requirements *(mandatory)*

### Functional Requirements

- **FR07-001**: The system MUST evaluate each scored district every day using the district's current thresholds.
- **FR07-002**: The system MUST open a flood episode on the first day the score is at or above Watch, with at most one open episode per district per scenario.
- **FR07-003**: Within an episode, each tier MUST fire at most once (D4).
- **FR07-004**: When the score reaches a tier, the system MUST fire every lower tier that has not fired yet in the same episode, in order.
- **FR07-005**: The release amount for a tier MUST equal the fund amount × the cumulative percentage up to that tier, minus what this episode has already released (rejected releases excluded), never below zero and never above the money available.
- **FR07-006**: The sum of an episode's three releases MUST equal the fund amount exactly when no cap applies (no rounding loss).
- **FR07-007**: Watch and Warning releases MUST proceed to allocation, payment and beneficiary alerts without human approval.
- **FR07-008**: Action releases MUST be created as Pending approval and MUST NOT be allocated or paid until a DC approval is recorded (NFR12).
- **FR07-009**: Each release MUST carry its household and shopkeeper pools, purpose, the score that fired it, and the full calculation.
- **FR07-010**: The DC MUST be alerted on every change of tier, queued in the same step as the evaluation (≤ 5 minutes, NFR2).
- **FR07-011**: A falling score MUST NOT reduce or reverse any release (D4).
- **FR07-012**: An episode MUST close after the configured number of consecutive days below Watch (default 14), and MUST NOT close while an approval is pending.
- **FR07-013**: Re-evaluating a day already evaluated MUST NOT create releases, alerts or audit entries.
- **FR07-014**: Every episode open/close, tier firing, tier change and release status change MUST be audited in the same transaction.
- **FR07-015**: Releases in replay MUST belong to their scenario and be marked simulated; releases using the simulated payment rail MUST be marked simulated.
- **FR07-016**: A failure after a firing MUST NOT undo the firing; the release MUST be resumable without duplicates.
- **FR07-017**: Officers and admins MUST be able to list episodes and releases; DCs only their own district; donors are denied (they use the donor report).

### Key Entities

- **TriggerEvent**: one flood episode per district; open/closed; highest tier; current tier.
- **FundRelease**: one tranche per tier per episode with amount, pools, purpose, status and calculation.
- **RiskIndex**: the score that fired a release.
- **DisasterFund**, **TriggerTier**: settings read at evaluation.
- **Approval**: created by FR8 for Action.
- **Alert**: DC and beneficiary tier alerts.
- **AuditLog**.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Replays of Sindh 2022 and Buner 2025 each fire at least one release; the 2021 control fires none (NFR3).
- **SC-002**: 100% of DC tier alerts are queued within 5 minutes of the firing; in tests the gap is 0 (NFR2).
- **SC-003**: 0 Action releases are allocated or paid without an approval record across all tests and backtests (NFR12).
- **SC-004**: For any fund amount and increments summing to 100, the three releases of an uncapped episode sum exactly to the fund amount (property test, 1,000 cases).
- **SC-005**: Re-running a day produces 0 new rows in releases, alerts and audit.
- **SC-006**: Evaluating all 68 pilot districts adds < 30 s to the daily run (NFR1 budget of 15 min).
- **SC-007**: Every release can be traced to the score, settings and calculation that produced it (NFR4).

## Assumptions

- FR3 calls the engine once per district per daily run with the district-level `RiskIndex`.
- The fund amount is the district's total for the season; money released and not used stays in the fund.
- "Released" in the status means the tranche was allocated; payment results are tracked by FR17.
- The MVP has no satellite confirmation of episodes (FR21 is roadmap).

## Traceability

| Item | Covered by |
|---|---|
| UC04 step 6 "if the tier rose, start UC06 and UC08" | US1, US2, FR07-001…009 |
| UC08 preconditions and trigger | FR07-007, FR07-008 |
| UC08-1a fund too low | FR07-005 (cap), Edge cases |
| UC06 trigger "a tier rise" | FR07-007 |
| NFR2 alerts within 5 min | US3, FR07-010, SC-002 |
| NFR3 backtests | SC-001 |
| NFR4 explainability | FR07-009, SC-007 |
| NFR7 integrity | FR07-014 |
| NFR12 accountability | FR07-008, SC-003 |
| NFR13 transparency | FR07-015 |
| D4 incremental tiers | FR07-003…006, FR07-011, FR07-012 |
| D3 (hand-off to approval) | FR07-008 |
| D5 entities | Key entities |

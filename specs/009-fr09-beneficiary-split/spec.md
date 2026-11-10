# Feature Specification: FR9 Household and Shopkeeper Split

**Feature Branch**: `009-fr09-beneficiary-split`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: FR9 (project document, Functional requirements): "Let the PDMA officer set the household and shopkeeper split (70/30 by default)"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Officer sets the split (Priority: P1)

In Fund settings the officer sets the household share for a district (70 by default); the shopkeeper share is the rest. Before saving, they can preview how each tier's release would be divided.

**Why this priority**: UC01 step 4; the split decides how much reaches each group.

**Independent Test**: Save a household share of 60 through the settings form and preview it; check the stored share, the derived 40, and the preview amounts.

**Acceptance Scenarios**:

1. **Given** a new fund, **When** it is created, **Then** the household share is 70 and the shopkeeper share 30.
2. **Given** the officer enters 60, **When** they save, **Then** the household share is 60, the shopkeeper share shows 40, and the change is audited with the other settings.
3. **Given** the officer enters 101, -5 or 62.5, **When** they save, **Then** the whole form is refused with a split error and nothing is saved.
4. **Given** a PKR 50,000,000 fund at 10/30/60, **When** the officer previews a household share of 60, **Then** the preview shows Watch 3,000,000 / 2,000,000, Warning 9,000,000 / 6,000,000 and Action 18,000,000 / 12,000,000.

---

### User Story 2 - Every release is split into exact pools (Priority: P1)

When a tier fires, its tranche is divided into a household pool and a shopkeeper pool using the share in force at that moment. The two pools always add up exactly to the tranche.

**Why this priority**: UC06 step 4; allocation cannot start without pools.

**Independent Test**: Fire tiers with awkward amounts and shares; check the pools add up exactly and follow the rounding rule.

**Acceptance Scenarios**:

1. **Given** a Watch release of PKR 5,000,000 at 70%, **When** it fires, **Then** pools are 3,500,000 and 1,500,000.
2. **Given** a release of PKR 100,001 at 70%, **When** it fires, **Then** the household pool is 70,001 (70,000.70 rounded to the nearest rupee) and the shopkeeper pool 30,000.
3. **Given** a share of 100 or 0, **When** a release fires, **Then** one pool holds the whole tranche and the other is 0.
4. **Given** the share changed after Watch fired, **When** Warning fires, **Then** Watch keeps its pools and Warning uses the new share.

---

### User Story 3 - Unused pool money moves across, the rest stays in the fund (Priority: P2)

If households do not use their whole pool (for example, few registered households) while shopkeepers have unmet need, the leftover moves to the shopkeeper pool within the same release, and the other way round. Any money still unused after that stays in the district fund.

**Why this priority**: Avoids leaving money idle while people are unmet (UC06-4a), without moving money between releases.

**Independent Test**: Give a release a household pool larger than household need and shopkeepers with unmet need; check the move, the extra shopkeepers funded, and the amount returned to the fund.

**Acceptance Scenarios**:

1. **Given** a household pool of 3,500,000 with household need of 2,000,000, and shopkeepers with 900,000 unmet, **When** allocation runs, **Then** 900,000 moves to shopkeepers, more shopkeepers are funded in rank order, and the remaining 600,000 stays in the fund.
2. **Given** spillover is switched off for the district, **When** the same happens, **Then** nothing moves and 1,500,000 stays in the fund.
3. **Given** a household share of 100, **When** households leave money unused, **Then** nothing moves to shopkeepers (their share is 0).
4. **Given** both types leave money unused and neither has unmet need, **When** allocation finishes, **Then** nothing moves and both leftovers stay in the fund.

---

### User Story 4 - Pool breakdown per release (Priority: P2)

Officers and the DC see, for each release, the share used, the initial pools, any money moved, the money used by each type, the unmet need, and what returned to the fund.

**Why this priority**: Transparency of every rupee (NFR4).

**Independent Test**: Call the breakdown endpoint for a finished release and check the numbers add up.

**Acceptance Scenarios**:

1. **Given** a finished release, **When** the officer opens its pools, **Then** initial pools + moves = effective pools, and used + returned = tranche amount.
2. **Given** a release still waiting for allocation or approval, **When** the pools are opened, **Then** only the initial pools are shown, marked as pending.

### Edge Cases

- **Tranche of 0**: both pools 0; nothing to move.
- **Tranche below PKR 1** with a share between 1 and 99: household pool rounds to 0 or 1 but never exceeds the tranche.
- **Tranche with paisa** (only possible at the Action tier when the fund amount has paisa): household pool is whole rupees; the shopkeeper pool takes the paisa.
- **Both types have leftover and unmet need at once** (a beneficiary's amount did not fit the remaining pool): each type's leftover moves to the other, once, in one step.
- **Empty register for one type**: its whole pool is leftover and moves to the other type if it has unmet need.
- **Officer override after allocation** changes usage: the breakdown is recorded again.
- **Replay scenario**: the scenario's fund share is used; live funds are untouched.

## Requirements *(mandatory)*

### Functional Requirements

- **FR09-001**: The system MUST let the PDMA officer set a district's household share as a whole number from 0 to 100; the shopkeeper share is 100 minus it.
- **FR09-002**: The default household share MUST be 70, taken from configuration.
- **FR09-003**: An invalid share MUST make the whole settings save fail with nothing saved.
- **FR09-004**: Each release MUST be split into a household pool and a shopkeeper pool at the moment it fires, using the share in force then; later changes MUST NOT alter it.
- **FR09-005**: The household pool MUST be the tranche × share / 100 rounded half-up to the nearest rupee, limited to the tranche; the shopkeeper pool MUST be the tranche minus the household pool, so the two always sum exactly to the tranche.
- **FR09-006**: When spillover is on, after the first funding pass each type's unused pool money MUST move to the other type, up to that type's unmet need, within the same release only, and never into a type whose share is 0.
- **FR09-007**: Money still unused after spillover MUST stay in the district fund.
- **FR09-008**: Spillover MUST be switchable per district, on by default.
- **FR09-009**: The system MUST record, per release, the share, initial pools, rounding, moves, money used and unmet need per type, and money returned to the fund, and MUST audit it.
- **FR09-010**: Officers and the district's DC MUST be able to see the breakdown per release and a split preview per district; donors MUST NOT (they get aggregates in the donor report).

### Key Entities

- **DisasterFund**: holds the household share and the spillover switch.
- **FundRelease**: holds the initial pools and the split record.
- **Allocation**: FUNDED or UNMET_NEED per beneficiary; the source of usage and unmet need.
- **AuditLog**.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For 10,000 random tranche amounts and shares, the pools sum exactly to the tranche, and neither pool is negative.
- **SC-002**: Changing a district's split requires 0 code changes (NFR11).
- **SC-003**: For every finished release, used + returned to fund = tranche, to the paisa (NFR4).
- **SC-004**: With spillover on, no release ends with both unused money in one pool and unmet need of the other type that the money could cover.
- **SC-005**: Breakdown and preview reads respond with p95 < 500 ms (NFR1).

## Assumptions

- The share applies to the whole district; per-tehsil shares are roadmap.
- FR12 funds beneficiaries in rank order and rounds amounts to PKR 100, so small remainders are normal and go back to the fund.
- "Unmet need" for spillover is the total computed amount of a type's `UNMET_NEED` allocations in the release.

## Traceability

| Item | Covered by |
|---|---|
| UC01 step 4 (split and cap) | US1, FR09-001…003 |
| UC06 step 4 (apply the 70/30 split) | US2, FR09-004, FR09-005 |
| UC06-4a / UC08-1a budget short | US3, FR09-006, FR09-007 |
| NFR4 explainability | FR09-009, SC-003 |
| NFR5 fairness | FR09-006 (no spill into a 0% type); FR11 checks final shares |
| NFR11 config | FR09-002, SC-002 |
| D5 `household_share_pct` | FR09-001 |

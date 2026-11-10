# Feature Specification: FR12 Amount Rules

**Feature Branch**: `012-fr12-amount-rules`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: "FR12 Give households a flat tiered amount and shopkeepers a capped amount based on stock at risk" (Area: Beneficiaries, Priority: MVP)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Household flat amount per tier (Priority: P1)

Every household on a release's list gets the district's configured amount for that tier (for example PKR 3 000 at Watch, 7 000 at Warning, 15 000 at Action). The amounts are configuration, and 0 is allowed for a tier.

**Why this priority**: "Households get a flat tiered amount" is the core of the Pay pillar and UC06-4.

**Independent Test**: With a fund set to 3 000 / 7 000 / 15 000, compute targets for households on Watch, Warning and Action releases.

**Acceptance Scenarios**:

1. **Given** a Warning release and `household_amount_by_tier = {WATCH: 3000, WARNING: 7000, ACTION: 15000}`, **When** amounts are computed for a household with no earlier payment in the event, **Then** its amount is PKR 7 000 with rule `HOUSEHOLD_FLAT_TOPUP`.
2. **Given** the Watch amount is set to 0, **When** a Watch release is allocated, **Then** no household rows are created and the whole household pool is left for spillover or stays in the fund.
3. **Given** a configured amount of 3 050, **When** computed, **Then** the amount is rounded to PKR 3 100.

---

### User Story 2 - Shopkeeper capped grant (Priority: P1)

Each shopkeeper gets `stock_pct` of the stock value at risk, capped per shopkeeper (default 40% and PKR 50 000), rounded to the nearest PKR 100.

**Why this priority**: "Shopkeepers get a capped amount based on stock at risk"; the shopkeeper persona is the demo hero.

**Independent Test**: Compute targets for stock values 61 234, 125 000 and 200 000.

**Acceptance Scenarios**:

1. **Given** stock PKR 61 234 and 40%, **When** computed, **Then** the amount is PKR 24 500 (24 493.6 rounded).
2. **Given** stock PKR 200 000, **When** computed, **Then** 40% is 80 000 and the amount is the cap, PKR 50 000, with `capped=true`.
3. **Given** stock PKR 120, **When** computed, **Then** the amount rounds to 0 and the shopkeeper is not listed for this release.

---

### User Story 3 - Fund in rank order; the rest is unmet need (Priority: P1)

Within each pool, beneficiaries are funded in rank order until the next one no longer fits. Everyone from that point on is marked `UNMET_NEED` with the amount they would have received. Leftover money moves once to the other pool (if FR9's policy allows); anything still unspent stays in the fund (UC06-4a, UC08-1a).

**Why this priority**: The budget-short path is a constitution-listed money path (Principle XI).

**Independent Test**: A shopkeeper pool of PKR 100 000 and ranked grants 50 000, 24 000, 30 000, 20 000, plus a small household pool.

**Acceptance Scenarios**:

1. **Given** a shopkeeper pool of 100 000 and ranked grants 50 000, 24 000, 30 000, 20 000, **When** filled without spillover, **Then** ranks 1–2 are `FUNDED` (74 000), ranks 3–4 are `UNMET_NEED` (strict order: rank 4 is not funded ahead of rank 3), and 26 000 is unused.
2. **Given** the same shopkeepers and a household pool of 20 000 with three households at 7 000, **When** filled with spillover on, **Then** households 1–2 are funded, household 3 is unmet, 6 000 moves to shopkeepers making 32 000, shopkeeper rank 3 (30 000) is funded, rank 4 stays unmet, and the final 2 000 moves back to households but cannot fund household 3; 2 000 stays in the fund.
3. **Given** any release, **When** filled, **Then** Σ funded in each pool ≤ that pool plus spillover received, every amount is a multiple of PKR 100, and the funded rows form a prefix of the rank order.

---

### User Story 4 - Repeated tiers in one event top up, never duplicate (Priority: P1)

When a district moves from Watch to Warning to Action within one flood event, a household's amount at each tier is the tier's target minus what it already received in the event. A shopkeeper gets one capped grant per event.

**Why this priority**: D4 incremental tiers; without this, the same household would be paid 3 000 + 7 000 + 15 000 = 25 000 instead of 15 000.

**Independent Test**: Run Watch, Warning and Action releases in one event for the same register and sum each beneficiary's funded amounts.

**Acceptance Scenarios**:

1. **Given** household H was funded PKR 3 000 at Watch, **When** Warning is allocated, **Then** H's amount is PKR 4 000; at Action it is PKR 8 000; H's total is PKR 15 000.
2. **Given** household J was `UNMET_NEED` at Watch, **When** Warning is allocated and J is funded, **Then** J's amount is PKR 7 000.
3. **Given** shopkeeper S was funded PKR 50 000 at Watch, **When** Warning and Action are allocated, **Then** S is not on those lists (already covered).
4. **Given** H's Watch payment was cancelled by the DC, **When** Warning is allocated, **Then** H's prior amount is 0 and H's amount is PKR 7 000.
5. **Given** a new event opens after the previous one closed, **When** Watch fires, **Then** prior amounts start again from 0.

---

### User Story 5 - Override limits and re-fill (Priority: P2)

When the officer changes an amount (FR11), FR12 checks the limit, keeps the overridden row fixed, and re-fills the rest of the pool in rank order.

**Why this priority**: Needed for UC06-6; the default path works without overrides.

**Independent Test**: Override one funded row to 0 and one to a higher amount, then re-fill.

**Acceptance Scenarios**:

1. **Given** a household with a 7 000 Warning top-up and 3 000 prior, **When** the officer asks for 20 000, **Then** it is refused because the limit is PKR 12 000 (the highest tier target 15 000 minus 3 000 prior).
2. **Given** a full household pool, **When** rank 5 is overridden to 0, **Then** the first `UNMET_NEED` household that fits is funded.
3. **Given** overridden amounts in a pool that add up to more than the pool, **When** re-filled, **Then** the change is refused with `OVERRIDE_EXCEEDS_POOL`.

---

### User Story 6 - Summary of a release's allocation (Priority: P2)

The officer, DC and approval screen see per-pool totals: pool, spillover in/out, funded count and amount, unmet count and amount, excluded, unused, last funded rank, already covered.

**Why this priority**: The DC needs the total amount on the approval screen (UC07-2); the officer needs to see the budget-short effect.

**Independent Test**: Call the summary endpoint on the fixture from US3.

**Acceptance Scenarios**:

1. **Given** the US3-2 release, **When** the summary is requested, **Then** it shows households funded 14 000 (2), unmet 7 000 (1), shopkeepers funded 104 000 (3), unmet 20 000 (1), spillover HH→SK 6 000, SK→HH 2 000, unused 2 000.
2. **Given** a donor token, **When** the summary is requested, **Then** the response is 403.

---

### Edge Cases

- Pool amount not a multiple of 100: the remainder stays unused.
- Pool of 0 (split set to 100/0): every row of that type is `UNMET_NEED`; leftover from the other pool can still fund them if spillover is on.
- Amount table missing a tier key: treated as 0 and flagged `AMOUNT_TIER_MISSING` in `score_inputs.amount.warnings`.
- `household_amount_by_tier` decreasing (Warning < Watch): households already paid at Watch get 0 top-up and drop out; flagged for FR6.
- Prior amount above the target (earlier override upward): top-up 0; no clawback (D4).
- Earlier payment `FAILED` but not cancelled: counted as prior (it may still be retried); once cancelled, the next tier tops up.
- Earlier release `REJECTED` (Action only): its rows do not count as prior.
- Earlier release held by the fairness check (not saved): its `FUNDED` rows count as prior, so a later tier never double-plans the same need.
- Tiers fired on the same day (score jump): releases are allocated in tier order; a household gets two payments that day (3 000 + 4 000).
- Shopkeeper whose stock value changed between tiers: the event target is recomputed from the current stock value; top-up = new target − prior, floored at 0.
- Override to an amount that is not a multiple of 100: refused (`AMOUNT_NOT_ROUNDED`, FR11).

## Requirements *(mandatory)*

### Functional Requirements

- **FR12-001**: System MUST set each household's tier target to the district fund's configured amount for the release tier, allowing 0.
- **FR12-002**: System MUST set each shopkeeper's event target to `min(stock_value × stock_pct / 100, cap)` using the district fund's stock percentage (default 40) and cap (default PKR 50 000).
- **FR12-003**: System MUST round every target to the nearest PKR 100, halves rounding up, before filling; a cap that is not a multiple of 100 MUST be rounded down so the cap is never exceeded.
- **FR12-004**: System MUST compute each beneficiary's prior amount in the same trigger event as the sum of final amounts of their `FUNDED` allocations in other, non-rejected releases of the event, excluding allocations whose payment was cancelled.
- **FR12-005**: System MUST set the amount for the release to `max(0, target − prior)` and leave beneficiaries with 0 off the release's list (counted as already covered).
- **FR12-006**: System MUST fund each pool strictly in rank order: a row is funded only if its amount fits the remaining pool and every higher-ranked unpinned row in the pool was funded; all others are `UNMET_NEED`.
- **FR12-007**: When the spillover policy (FR9) is on, System MUST move unspent household money to the shopkeeper pool and continue the strict fill there, then move what is still unspent to the household pool and continue, once each; remaining money MUST stay unreserved in the fund.
- **FR12-008**: System MUST store with each allocation the amount rule, tier, target, prior, top-up, stock value, stock %, cap, `capped` flag, rounding, pool, remaining pool before the row and spillover received.
- **FR12-009**: System MUST give `UNMET_NEED` rows a final amount equal to the amount they would have received, so unmet need can be totalled.
- **FR12-010**: System MUST limit an override to the beneficiary's highest event target minus prior (households: highest tier amount; shopkeepers: cap), not below 0, and re-fill unpinned rows around pinned overrides; it MUST refuse when pinned amounts exceed their pool.
- **FR12-011**: System MUST mark rows overridden to 0 as `EXCLUDED`, counted as neither funded nor unmet.
- **FR12-012**: System MUST provide a per-release summary of pools, spillover, funded, unmet, excluded, unused, last funded rank and already covered, readable by the officer, admin and the DC of the district, and denied to donors.
- **FR12-013**: System MUST publish the amount rules (table, stock %, cap, rounding, fill mode, repeated-tier modes) inside the published allocation rules.
- **FR12-014**: All money MUST be exact decimal PKR; no floating-point arithmetic on amounts.

### Key Entities

- **DisasterFund** (§4): `household_amount_by_tier`, `shopkeeper_cap_pkr`, `shopkeeper_stock_pct`.
- **FundRelease** (§4): `tier`, `event_id`, `household_pool_pkr`, `shopkeeper_pool_pkr`, `status`.
- **TriggerEvent** (§4): groups releases for the top-up rule.
- **Allocation** (§5): `computed_amount_pkr`, `final_amount_pkr`, `status` (`FUNDED`, `UNMET_NEED`, proposed `EXCLUDED`), `score_inputs.amount`, `score_inputs.fill`.
- **Payment** (§6): read to exclude cancelled prior payments.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On 500 seeded random releases, 0 invariant violations: funded ≤ pool + spillover in; funded rows are a rank prefix; all amounts multiples of 100; no negative amounts.
- **SC-002**: Across Watch → Warning → Action in one event, 0 households receive more than the Action target and 0 shopkeepers receive more than the cap (D4).
- **SC-003**: 100% of allocations carry an amount explanation from which `computed_amount_pkr` can be recomputed exactly (NFR4).
- **SC-004**: Amount and fill for 10 000 rows complete in under 2 seconds (part of FR11's 10 s budget, NFR2).
- **SC-005**: Summary endpoint p95 < 500 ms (NFR1).
- **SC-006**: Changing amounts, cap or stock % needs only a settings change through FR6, never code (NFR11, Principle IX).

## Assumptions

- Pools on `FundRelease` are already computed by FR9 when `allocate` runs.
- Amounts in `household_amount_by_tier` are intended as **cumulative targets per event** ("Action household amount = 15 000 in total"), which is why later tiers top up.
- A shopkeeper's stock-protection need arises once per event, so one capped grant per event.
- Payment rails accept any multiple of PKR 100 up to the wallet limit (FR17 handles `LIMIT_REACHED`).

## Traceability

| Item | How FR12 covers it |
|---|---|
| UC06-4 | Applies the split pools and the cap per shopkeeper |
| UC06-4a | Top ranked funded, rest `UNMET_NEED` |
| UC06-5 | Amounts on the ranked list |
| UC08-1a | Fund too low → pay in rank order, rest unmet |
| UC01-4 | Uses the officer's cap per shopkeeper |
| UC07-2 | Total amount shown to the DC (summary) |
| NFR4 | Amount formula stored per row |
| NFR11 | Amounts are configuration |
| D4 | Top-up per tier within one event; no clawback; unspent stays in the fund |
| D5 | `household_amount_by_tier`, `shopkeeper_cap_pkr` on `DisasterFund`; `Allocation` entity |

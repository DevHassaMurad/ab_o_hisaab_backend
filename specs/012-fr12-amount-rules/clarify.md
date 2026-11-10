# Clarifications: FR12 Amount Rules

## Clarifications

### Session 2026-11-10

- Q: How are repeated tiers within one `TriggerEvent` handled for households? → A: **Top up to the tier's target.** `household_amount_by_tier` is a cumulative target per event. At each tier the amount is `max(0, target(tier) − prior)`, where prior is what the household already got in this event. Defaults 3 000 / 7 000 / 15 000 give payments of 3 000, then 4 000, then 8 000, total 15 000. Why: D4 makes release percentages increments of one fund; paying the full flat amount at every tier would pay 25 000 and drain the pool three times faster. Top-ups also mean a household that was unmet at Watch gets the full Warning amount.
- Q: And for shopkeepers? → A: **One capped grant per event** (`EVENT_ONCE`). The target `min(stock × stock_pct, cap)` is the same at every tier; a shopkeeper funded at Watch is "already covered" later. Only shopkeepers who were unmet (or got less via an override) are funded or topped up at later tiers. Why: protecting stock is a one-time action, and UC01-4 sets a "cap per shopkeeper", not per tier.
- Q: Why not scale the shopkeeper grant by tier? → A: It adds a rule the document does not mention and makes the cap harder to explain. Kept as open item 2.
- Q: What counts as "prior" in the event? → A: Σ `final_amount_pkr` of the beneficiary's `FUNDED` allocations in other releases of the same `event_id`, where the release is not `REJECTED` and the allocation's payment is not `CANCELLED` (no payment yet counts, so held or same-day releases are seen). `FAILED` payments still count because FR18 may retry them; when the DC cancels, the money returns to the fund and the next tier tops up.
- Q: Does an override carry forward to the next tier? → A: No. The next tier recomputes from the rules: if the officer lowered H to 1 000 at Watch, Warning tops H up to 7 000 (6 000). If H was `EXCLUDED` (0), H is eligible again next tier; permanent exclusion is done by deactivating the beneficiary in FR10.
- Q: What is the rounding rule? → A: Nearest PKR 100, halves up (`ROUND_HALF_UP`): 24 493.6 → 24 500; 24 450 → 24 500; 24 449.99 → 24 400. The cap is rounded **down** to a multiple of 100 if needed, so it is never exceeded. Guide: "Round to the nearest PKR 100".
- Q: Strict rank order or skip ahead to smaller grants that still fit? → A: **Strict.** Once a row does not fit, every lower-ranked unpinned row in that pool is `UNMET_NEED`, even if a smaller grant would fit. Why: UC06-4a and UC08-1a say "the top ranked beneficiaries are funded and the rest are marked Unmet need"; skipping ahead would let a lower-ranked shopkeeper with small stock jump a higher-ranked one, which is hard to defend publicly. Households have equal amounts so it makes no difference for them. The leftover goes to spillover or stays in the fund. Kept configurable (`ALLOCATION_FILL_MODE`), open item 1.
- Q: No partial grants for the boundary beneficiary? → A: No. A partial grant to the boundary row would make amounts depend on pool remainders, which is harder to explain, and the leftover is small (less than one grant). It stays for spillover.
- Q: How does spillover work? → A: FR9 owns the on/off policy (recommended on). FR12 implements: pass 1 fills each pool strictly; then unspent household money is added to the shopkeeper pool and the strict fill continues from the first unmet shopkeeper; then the shopkeeper pool's unspent money (including what it just received) is added to the household pool and continues from the first unmet household. One round each, always household → shopkeeper first (household pool is larger by default, so its remainder is the bigger one). Remaining money is not reserved and stays in `DisasterFund.remaining_pkr` (D4).
- Q: What amount does an `UNMET_NEED` row carry? → A: `computed_amount_pkr` = `final_amount_pkr` = the top-up it would have received. So "unmet need (PKR)" = Σ `final_amount_pkr` of `UNMET_NEED` rows. Payments are only created for `FUNDED` rows.
- Q: What does "override to 0" mean? → A: The row becomes `EXCLUDED` (proposed new status value): not funded and not unmet. It frees money for the next unmet row in rank order.
- Q: What is the maximum override? → A: Households: the highest value in `household_amount_by_tier` minus prior. Shopkeepers: the cap minus prior. Never below 0. And the pool must hold all pinned amounts in that pool (`OVERRIDE_EXCEEDS_POOL` otherwise). Overrides do not draw spillover; that keeps the rule explainable per pool.
- Q: How are overrides combined with re-fill? → A: Overridden rows are pinned. Re-fill = pool − Σ pinned, then strict fill of unpinned rows in rank order, then spillover as above. Deterministic; the cascade is reported.
- Q: What if `household_amount_by_tier` lacks a tier key or has a non-multiple of 100? → A: Missing → 0 with warning `AMOUNT_TIER_MISSING`. Non-multiple → rounded per the rule with warning `AMOUNT_ROUNDED`. FR6 should prevent both.
- Q: Same-day multiple tiers (score jumps from 30 to 85)? → A: FR7 creates Watch, Warning, Action releases (D4) and calls `allocate` in tier order in one transaction. A household gets 3 000 (Watch) and 4 000 (Warning) on the same day, and an 8 000 Action draft waits for the DC. Two payments are simpler to trace than merging releases.
- Q: Does the summary need new storage for "already covered"? → A: No. It reads `already_covered` from the immutable `ALLOCATION_COMPUTED` audit payload written by FR11 for the release.

## Resolved by constitution decisions

- **D4**: Increments within one event; no clawback when the score falls; unreleased and unspent money stays in the fund. This is why tiers top up and why leftovers are not reserved.
- **D5**: `DisasterFund` carries `household_amount_by_tier` and `shopkeeper_cap_pkr`; `Allocation` is canonical. FR12 only proposes the `EXCLUDED` status value.
- **D3/Principle II**: Action amounts are drafts until FR11's `finalize` after DC approval.
- Principle **IX**: amounts and caps are config; Principle **XI**: tests first for amounts, caps, budget-short path.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Fill mode: strict rank order vs skip ahead to smaller grants that fit | `STRICT` (`ALLOCATION_FILL_MODE`) | `SKIP_AHEAD` spends more of the pool but lets lower ranks jump; affects shopkeepers only | PDMA officer |
| 2 | Should the shopkeeper grant scale by tier (e.g. 10/40/100% of the capped grant)? | No: one capped grant per event (`SHOPKEEPER_TIER_MODE=EVENT_ONCE`) | Scaling spreads shopkeeper money across more shops at Watch but pays each less early | PDMA officer |
| 3 | Is `household_amount_by_tier` a cumulative target (top-up) or a per-tier payment? | Cumulative target per event (`HOUSEHOLD_TIER_MODE=TOPUP_TO_TARGET`) | Per-tier payments would pay 25 000 over three tiers with defaults; FR6 labels must match | PDMA officer + FR6 owner |
| 4 | Rounding: nearest 100 half up vs always down | Nearest, half up (guide) | Always-down never exceeds stock × pct exactly; difference ≤ PKR 50 per row | Finance (PDMA) |
| 5 | Spillover direction and rounds | One round each, household → shopkeeper first | Other orders change who is funded at the margin | FR9 owner |
| 6 | Count `FAILED` (not yet cancelled) payments as prior | Yes | No → risk of double payment if the failed one is retried | FR18 owner |

# Clarifications: FR9 Household and Shopkeeper Split

## Clarifications

### Session 2026-11-10

- Q: Where is the split edited: a separate FR9 endpoint or the FR6 settings form? → A: The FR6 form (UC01 is one screen, one save, one audit entry). FR9 owns the rule (`validate_split`), the maths, the spillover and the read endpoints. No second write path, so the sum and audit rules cannot diverge.
- Q: What values are allowed? → A: Whole numbers 0–100 for the household share. The shopkeeper share is derived (100 − household) and never stored separately. Fractions are refused (`SPLIT_PCT_INVALID`).
- Q: What is the rounding rule for pools? → A: `household = clamp(round_half_up(amount × share / 100, whole PKR), 0, amount)`; `shopkeeper = amount − household`. Exact sum, whole-rupee household pool; the shopkeeper pool absorbs any paisa. Beneficiary-level rounding to PKR 100 is FR12's.
- Q: Which share applies to a release? → A: The share in force when the tier fires; the pools are stored on the `FundRelease` and never recomputed (matches FR6 "applies from the next run").
- Q: What happens to an under-used pool? → A: With spillover on (default), after pass 1 each type's leftover moves to the other type, up to that type's unmet need, inside the same release. One step only, no iteration. FR12 then funds more of the receiving type in rank order. Anything still unused stays in the district fund (it was never reserved). With spillover off, all leftovers stay in the fund.
- Q: Can money spill into a type whose share is 0%? → A: No. A 0% share is an explicit policy that the type gets nothing from this fund. Spillover only flows into a type with share > 0.
- Q: What if both types have leftover and unmet need? → A: Possible when a beneficiary's amount did not fit the remaining pool. Both moves are computed from pass-1 numbers and applied together.
- Q: Is the leftover moved across releases (e.g. Watch leftover into Warning)? → A: No. Each release is closed on its own; leftovers return to the fund, and the fund feeds later tiers through FR7's cumulative rule and `available_to_release`.
- Q: Where is the outcome recorded? → A: Proposed `FundRelease.split_details` JSONB (initial pools, rounding, moves, usage, unmet, returned) plus a `POOL_SPLIT_RECORDED` audit entry. `household_pool_pkr` / `shopkeeper_pool_pkr` keep the initial values.
- Q: Who triggers spillover? → A: FR12 during allocation, through the pure `plan_spillover` and the persisting `record_split_outcome`. FR9 does not rank or fund anyone.
- Q: Does an officer override (FR11) re-run spillover? → A: No re-run of the moves; FR11/FR12 call `record_split_outcome` again with the new usage so the record and the "returned" amount stay correct.
- Q: Who can read the breakdown and preview? → A: PDMA_OFFICER and ADMIN all districts; DC own district; DONOR 403.

## Resolved by constitution decisions

- **D5**: `DisasterFund.household_share_pct` default 70.
- **D4**: Releases are per tier; nothing is clawed back, unreleased money stays in the fund, which fits "leftover returns to the fund".
- Principle IX: share and spillover switch are configuration.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Spillover on by default? | On (`pool_spillover_enabled=true`) | Off leaves more money idle in the fund during a flood | PDMA product owner |
| 2 | Allow spill into a 0% type? | No | Would contradict an explicit 100/0 policy | PDMA product owner |
| 3 | Should leftovers carry to the next tier of the same episode instead of returning to the fund? | No, return to fund (FR7's cumulative rule already reuses fund money) | Would need a release-to-release transfer record | PDMA product owner |
| 4 | Should the split be allowed per tier (e.g. Watch 50/50, Action 80/20)? | No, one share per district | Data-model change to `TriggerTier` | PDMA product owner |
| 5 | Rounding granularity of pools | Whole PKR | PKR 100 granularity would align with FR12 but leave bigger remainders | Backend lead + FR12 owner |

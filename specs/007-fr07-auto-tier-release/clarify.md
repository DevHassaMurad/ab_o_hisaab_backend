# Clarifications: FR7 Automatic Tier Release

## Clarifications

### Session 2026-11-10

- Q: Does a tier's percentage apply to the original fund amount or to what is left? → A: The original configured amount, as increments (D4): Watch 10%, Warning +30%, Action +60%.
- Q: How are rounding and mid-episode setting changes handled? → A: Cumulative rule. `amount = target(cum_pct) − already_released_in_episode`, where `target = round_half_up(allocated × cum_pct / 100, whole PKR)`, and at cumulative 100% `target = allocated` exactly. Rejected releases do not count as released. The result is floored at 0. This makes an uncapped episode sum to the fund exactly, and a fund increase mid-episode is reflected in later tiers without re-paying earlier ones.
- Q: What if the fund cannot cover the tranche (an earlier episode used money)? → A: Cap at `available_to_release(fund) = remaining_pkr − Σ amount_pkr of this fund's releases in PENDING_APPROVAL, APPROVED or ALLOCATING`. Earmarked but unallocated tranches are subtracted so two tranches can never promise the same rupee. This is conservative: it may under-release briefly, never over-release. `amount_calc.capped=true`, and the release ends `PARTIALLY_RELEASED`.
- Q: Should a 0 PKR tier (empty fund or 0% increment) still fire? → A: Yes. It creates the FundRelease with amount 0 and is audited, so the record shows the trigger happened (Principle III). Allocation still runs and records Unmet need, which shows the funding gap.
- Q: Which status does an automatic release start in? → A: `ALLOCATING` (it "goes straight" to allocation). Action starts in `PENDING_APPROVAL`; after approval FR8 sets `APPROVED` and calls `run_release_pipeline`, which moves it to `ALLOCATING`.
- Q: When is a release `RELEASED` vs `PARTIALLY_RELEASED`? → A: When allocations are saved: `PARTIALLY_RELEASED` if any allocation is `UNMET_NEED` or the amount was capped; otherwise `RELEASED`. Payment outcomes are FR17's concern and do not change this status. With no beneficiaries at all: `RELEASED` with zero allocations; the money stays in the fund.
- Q: What if the fairness check fails? → A: FR11 holds the allocation for officer acknowledgement. The pipeline stops at `ALLOCATING`. FR11 calls `complete_release` once saved.
- Q: Who gets alerts and when? → A: The DC gets `TIER_ALERT` (template `tier_change_dc`) on every change of `current_tier`, including downward. Beneficiaries with an allocation in the release (funded or unmet) get `TIER_ALERT` (template `tier_alert_<tier>`) after allocation. Payment notices are FR17 → FR14. All are enqueued synchronously, so `queued_at` equals the firing time (NFR2).
- Q: How is a tier change detected without duplicates on re-run? → A: The episode stores `current_tier` and `last_evaluated_date` (proposed fields). Alerts are sent only when `current_tier` changes, so a re-run with the same score sends nothing.
- Q: How is "14 consecutive days below Watch" counted? → A: Episode field `below_watch_streak`. It increases by 1 on the first evaluation of a new run date below Watch, and resets to 0 at Watch or above. A same-day re-run that rises resets it; one that falls does not add a second day (conservative: closing can be delayed by one day, never hastened).
- Q: Can an episode close while the Action approval is pending? → A: No. It stays open until the DC decides. This keeps the approval valid and avoids a new "expired" state.
- Q: What if a DC rejected Action and the score stays high? → A: No new Action release in that episode (one firing per tier per episode, D4). A new episode can fire Action again.
- Q: What if a run date older than the last evaluated date arrives? → A: Ignore it (return `[]`) and log a warning. Replays always move forward; FR5 seek-backwards resets the scenario first.
- Q: Should a pipeline failure roll back the firing? → A: No. The firing is committed in its own savepoint; allocation, payments and alerts run in a nested savepoint per release. On failure the release stays `ALLOCATING` with `pipeline_error` set and is resumable (`POST /releases/{id}/resume`, or the next evaluation of the district retries it).
- Q: Which tier is authoritative: `RiskIndex.tier` or recomputation? → A: Recompute with FR6 `tier_for_score` and the district's current `TierConfig`. A mismatch is logged (FR3 uses the same function, so it should not happen).
- Q: Is a release "simulated"? → A: `simulated = scenario_id is not None or settings.PAYMENT_RAIL_SIMULATED`. In the MVP the rail is always simulated, so all releases are labelled (Principle VIII).
- Q: Purpose text per tier? → A: From config `fund_defaults.purposes`: Watch "Preposition boats and food", Warning "Evacuate, open camps", Action "Cash support".
- Q: Who can read releases? → A: PDMA_OFFICER and ADMIN all; DC own district; DONOR 403 (aggregates live in FR19).

## Resolved by constitution decisions

- **D4**: increments, at most once per tier per episode, skipped tiers fire, no clawback, close after 14 days (configurable).
- **D3**: Action hands off to the FR8 approval, which records the channel.
- **D5**: `TriggerEvent` and `FundRelease` as defined in data-model.md.
- Principle II: no path allocates an unapproved Action release.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Allow a second Action request in the same episode after a rejection? | No (D4: once per tier per episode) | Would need a re-request flow and relaxing `unique(event_id, tier)` | PDMA product owner |
| 2 | Alert the DC on downward tier changes? | Yes ("risk easing") | Remove one branch; fewer messages | PDMA product owner |
| 3 | Alert all registered beneficiaries in the district, or only those in the release's allocation list? | Allocation list only (bounded, relevant) | Broadcast to the full register needs FR14 fan-out and Twilio volume | PDMA product owner + FR14 owner |
| 4 | Should a release be `PARTIALLY_RELEASED` when the amount was capped even if every beneficiary was funded? | Yes (the tranche was short) | Status definition only | Backend lead |
| 5 | Close an episode while an approval is pending, by expiring the approval? | No; stays open | Needs an `EXPIRED` approval decision (data-model change) | PDMA product owner |
| 6 | Stalled release threshold shown as `is_stalled` | 10 minutes in `ALLOCATING` | Config value | Backend lead |

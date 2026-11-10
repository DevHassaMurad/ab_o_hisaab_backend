# Clarifications: FR6 Fund and Tier Configuration

## Clarifications

### Session 2026-11-10

- Q: UC01 says the officer is "logged in with the admin role". Which role writes settings? → A: `PDMA_OFFICER` only. `ADMIN` reads. The document's actor table says the PDMA officer "owns the fund and sets the rules", and keeping the technical admin out of money rules keeps duties separate.
- Q: Are 10/30/60 cumulative or increments? → A: Increments that sum to 100 (D4). Watch 10% (10% total), Warning +30% (40%), Action +60% (100%).
- Q: Can a single increment be 0? → A: Yes. Each increment is a whole number 0–100 and the sum must be exactly 100. A 0% tier still fires and is audited, with PKR 0.
- Q: What are valid thresholds? → A: Whole numbers 1–100, strictly increasing Watch < Warning < Action. A score enters a tier when `score >= min_score`.
- Q: What does "apply from the next daily run" mean in practice? → A: Saves are written immediately. The daily pipeline reads settings per district when it evaluates that district, so the next run uses them. A tier that already fired keeps its stored `amount_pkr`. FR7's cumulative rule stops already-released money from being paid twice. A same-day manual re-run uses the new values; this is acceptable and documented.
- Q: Can settings change while a trigger event is open? → A: Yes, with a warning (`open_event` in the response). Blocking edits during a flood would stop the officer from adding money.
- Q: Can the fund amount go below money already committed? → A: No. `allocated_pkr >= reserved_pkr + disbursed_pkr`, otherwise `FUND_BELOW_COMMITTED`. This keeps `remaining_pkr` non-negative.
- Q: How are errors reported? → A: All errors at once, as RFC 7807 problem JSON with an `errors` array of `{field, code, message}`. Top-level `code` is the first error's code. HTTP 422.
- Q: What does copy do? → A: It returns an unsaved draft for the target district. It copies the rules but keeps the target's own fund amount unless `include_amount=true`. Saving goes through the normal PUT. No write and no audit at copy time.
- Q: What if the district has no fund yet? → A: PUT creates the fund and its three tiers (`version` = 1) and audits `FUND_SETTINGS_UPDATED` with `"created": true`.
- Q: How are concurrent edits handled? → A: Optimistic locking: the client sends `version`. A mismatch gives 409 `VERSION_CONFLICT`.
- Q: What happens when nothing changed? → A: 200, no write, no version bump, no audit entry.
- Q: How is `requires_approval` protected? → A: The input schema has an optional `requires_approval` field only to catch it. Any value gives 422 `REQUIRES_APPROVAL_LOCKED`. The value is fixed at fund creation: `true` for Action only.
- Q: Which scenario does an edit touch? → A: The fund matching the active `ScenarioContext` (`X-Scenario-Id` header, or live when absent). This lets a demo operator tweak a replay without touching live data.
- Q: Who can read? → A: `PDMA_OFFICER` and `ADMIN` read all districts. `DC` reads their own district. `DONOR` gets 403 (donors have the FR19 report).
- Q: Which fields does the settings form hold, given FR9 and FR12 own the split and the amount rules? → A: One form, as in UC01. FR6 owns the endpoint, storage and audit. FR9 owns `validate_split`. FR12 owns the meaning of household amounts and the stock percentage; FR6 checks shape and ranges only.
- Q: Should household amounts be multiples of 100? → A: No hard rule here. FR12 rounds payouts to the nearest PKR 100. FR6 checks the keys are exactly WATCH/WARNING/ACTION and values are ≥ 0.
- Q: Range for `event_close_days`? → A: 1–60, default 14 (D4).

## Resolved by constitution decisions

- **D4**: increments sum to 100; event close after 14 days (configurable here).
- **D5**: `DisasterFund` carries `household_share_pct`, `shopkeeper_cap_pkr`, `household_amount_by_tier`.
- **D6**: no Director General role; nothing to configure for escalation targets in the MVP.
- Principle II: `requires_approval` locked. Principle IX: defaults in seeds.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Can the system `ADMIN` edit fund settings? | No, read-only | One role check in `settings.py` | PDMA product owner |
| 2 | Should officers be scoped to a province (Sindh officer cannot edit KP)? | No scoping in the MVP | Needs `User.province` (data-model change) and a scope filter | PDMA product owner |
| 3 | Should copy include the fund amount by default? | No (`include_amount=false`) | Flip the query default | PDMA officer persona |
| 4 | Should edits be blocked while a district has an `OPEN` event? | Allowed with a warning | Add a 409 `EVENT_OPEN` check | PDMA product owner |
| 5 | Should a settings change take effect only at the next scheduled run, even for manual re-runs? | No, any run after the save uses it | Would need `effective_from` and pending-settings storage | Backend lead |
| 6 | Upper bound for the fund amount | PKR 999,999,999,999.99 (column limit) | Change one constant | Backend lead |

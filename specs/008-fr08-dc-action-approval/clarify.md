# Clarifications: FR8 DC Approval of Action-Tier Releases

## Clarifications

### Session 2026-11-10

- Q: UC07-1a escalates to the "PDMA Director General", which does not exist as a role. → A: Escalate to the PDMA officer (D6): `decision=ESCALATED`, dashboard flag, WhatsApp notice to every active `PDMA_OFFICER`. The officer cannot approve. The DC gets a reminder.
- Q: UC07's precondition says the UC06 allocation list is ready before approval, but the guide says approval starts allocation. Which order? → A: Allocation runs **after** approval (guide FR8, Principle II). Building and saving an allocation earlier would make FR17 reserve payments for money not yet approved. The detail view shows what the DC needs to decide: total amount, household/shopkeeper pools and the register size per type in the district. A draft allocation preview is an open item.
- Q: UC07-3a says "funds stay reserved" while the guide says "the money stays in the fund". → A: Same outcome: on rejection nothing is reserved against payments; the release becomes `REJECTED`, which removes it from FR7's earmark, so the PKR stays in `DisasterFund.remaining_pkr` and is available to future episodes.
- Q: What is `<code>` in `APPROVE <code>`? Is it the dashboard OTP? → A: No. A separate 6-digit approval code generated with the request and included in the WhatsApp request message. It lives 24 h (the OTP lives 10 min and would expire before most DCs read the message). Both are bcrypt-hashed. Separate counters, 5 attempts each.
- Q: How is a WhatsApp sender authenticated? → A: FR15 validates the Twilio signature; FR8 matches `sender_phone_hash` to an active `DC` user, and the release's district must equal the DC's district. The code proves the DC is answering this request.
- Q: `REJECT <reason>` has no code. Which approval does it reject? → A: If the DC has exactly one open approval (PENDING or ESCALATED, across live and replay scenarios), that one. If more than one, the reply asks for `REJECT <code> <reason>`. A 6-digit first token after REJECT is read as a code.
- Q: Does a dashboard rejection need the OTP? → A: No. Rejection moves no money and is reversible in effect (a new episode can fire again). JWT auth plus the DC role is enough. Open item #2.
- Q: Minimum rejection reason? → A: 5 characters after trimming, maximum 1,000.
- Q: Can the DC decide after escalation? → A: Yes. `ESCALATED → APPROVED | REJECTED`. The officer is told the outcome.
- Q: Does an approval expire? → A: No. It stays open until the DC decides (FR7 keeps the episode open). The WhatsApp code expires after 24 h; the DC or officer can call resend to get a fresh code.
- Q: Where do OTP and code values go? → A: Generated with `secrets`, sent once through `AlertService.enqueue(..., context={"otp": …}, sensitive_keys=["otp"])`, stored as bcrypt hashes. FR14 must mask sensitive keys in `Alert.message_text`. Audit payloads never contain them.
- Q: Which time does escalation use in replays? → A: The scenario's `ReplayClock`. The interval job handles live approvals (`scenario_id IS NULL`) with `SystemClock`; FR5 calls `escalate_overdue_approvals(db, now=replay_now, scenario_id=sid)` on each replay step.
- Q: How does replay decide? → A: `REPLAY_APPROVAL_MODE=SIMULATED` (default): FR5 calls `simulate_pending_approvals` after the trigger step of each replay day (not inside `request_approval`, so Watch/Warning fired the same day are allocated first), and the approval is decided `APPROVED`, `channel=REPLAY_SIM`, `simulated=true`, `approver_id` = the district's DC user (or null), `decided_at = requested_at + REPLAY_APPROVAL_DELAY_MIN` (45). `LIVE_WHATSAPP`: wait for the real reply. Simulation is refused for live approvals.
- Q: What if the district has no active DC? → A: The request is created and escalated immediately (`escalation_reason=NO_DC_ASSIGNED`) so officers know. It can only be decided once a DC is assigned.
- Q: How are concurrent decisions handled? → A: The approval row is locked `FOR UPDATE`; the second decision gets 409 `APPROVAL_ALREADY_DECIDED` (dashboard) or an "already decided" reply (WhatsApp).
- Q: Are failed OTP attempts persisted when the request fails? → A: Yes. The attempt counter and the `APPROVAL_CODE_FAILED` audit entry are committed before the error response, otherwise attempts could be retried without limit.
- Q: Which keywords are accepted? → A: `APPROVE` / `منظور` and `REJECT` / `مسترد`, case-insensitive, with Urdu (۰–۹) and Arabic-Indic (٠–٩) digits normalised, and spaces inside the code ignored.
- Q: Who gets notified on decisions? → A: Reject: all active PDMA officers (`approval_rejected_officer`). Approve: officers only if the request had escalated (`approval_decided_officer`). The DC gets the WhatsApp reply (WhatsApp channel) or the API response (dashboard).

## Resolved by constitution decisions

- **D3**: two channels, one `Approval` row, `channel` recorded; 2 h escalation.
- **D6**: escalation goes to the PDMA officer; DG role is roadmap; the officer cannot approve.
- **D4**: Action fires once per episode, so at most one approval per episode.
- Principle II: no bypass, replay uses an explicit simulated record.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Show a draft allocation preview (ranked list) before approval, to meet UC07's precondition literally? | No; show pools and register counts | Needs an FR11/12 dry-run `preview(db, release)` that writes nothing, and FR17 must not reserve | PDMA product owner + FR11/12 owner |
| 2 | Require the OTP for dashboard rejection too? | No | One extra step for the DC | PDMA product owner |
| 3 | FR14 support for masking `sensitive_keys` in stored message text | Required by FR8 | If not done, OTPs are readable in the DB; fallback is to send OTPs with `persist_text=false` | FR14 owner |
| 4 | Alert purpose for decision notices to officers | Propose `DECISION_NOTICE`; until merged, use `ESCALATION` with template `approval_rejected_officer` | Enum change in data-model.md | Foundation owner |
| 5 | WhatsApp code lifetime | 24 h | Shorter means more resends | PDMA product owner |
| 6 | Should a DC's rejection auto-notify donors? | No (donors see aggregates only) | Would need an FR19 aggregate event | PDMA product owner |
| 7 | Second escalation step (e.g. to province secretary) | None in MVP | Roadmap with the DG role | PDMA product owner |

# Clarifications: FR14 WhatsApp alerts

## Clarifications

### Session 2026-11-10

- Q: NFR2 says alerts "go out" within 5 minutes; the constitution says "queued ≤ 5 min"; data-model says `sent_at − trigger time ≤ 5 min`. Which one? → A: The stricter one: **handed to the provider** (`sent_at`) within 300 s of `triggered_at`. Queuing happens in the trigger's own transaction, so queuing is effectively instant; the worker budget is what matters. Officials target 60 s.
- Q: What is "trigger time" for each purpose? → A: Tier alerts and DC tier change: the moment the tier fired (`FundRelease.created_at`). Approval request: `Approval.requested_at`. Escalation: the escalation time. Payment notice: `Payment.paid_at`. Stored as the proposed `Alert.triggered_at`.
- Q: Which language order and which recipients get both languages? → A: Every message, including to officials, is Urdu first then English (`language = UR_EN`). `preferred_language = EN` does not drop Urdu, because NFR10 and the constitution's Language standard require Urdu first. Officials' command lines (`APPROVE`, `REJECT`) are Latin because D3 defines them that way.
- Q: Western or Urdu digits in Urdu text? → A: Western digits with thousands separators. Wallet SMS in Pakistan show amounts that way, and FR15 accepts either kind of digit in replies.
- Q: Should tier levels be numbered for low-literacy users? → A: No. Numbers would clash with the reply digits 1 and 2. Tiers are words (Watch, Warning, Action); the Urdu wording is added by a native speaker.
- Q: Who decides when to send? → A: The FR that owns the event. FR14 provides helpers (`notify_*`) that build context and call `enqueue`. FR7 calls tier helpers, FR8 approval helpers, FR17 the payment helper.
- Q: Does a beneficiary get a tier alert and a payment notice? → A: Yes, two different messages. The tier alert (at the tier) carries safety advice and the expected amount; the payment notice (at Paid) says the money was sent and asks for the 1/2 reply. Only the payment notice expects a reply.
- Q: Who gets tier alerts? → A: Every beneficiary with an `Allocation` row for the release (funded or `UNMET_NEED`). People outside the register are out of scope.
- Q: What about Action while approval is pending? → A: The beneficiary alert goes out at fire time with safety advice only (safety must not wait for a signature). The amount arrives later in the payment notice after approval.
- Q: What does an `UNMET_NEED` beneficiary hear? → A: Safety advice only. The message does not say they were not funded; that would cause harm without helping them act.
- Q: Several tiers fire in one run (D4). How many messages? → A: One DC message listing all fired tiers and the total released; one beneficiary message for the highest tier. Dedupe key uses the event and the highest tier.
- Q: Does the DC hear about tier falls and event close? → A: Yes. FR7 says "the DC is alerted on any tier change". Beneficiaries do not get "all clear" messages in the MVP.
- Q: What makes enqueue idempotent? → A: A deterministic `dedupe_key` per purpose (see plan.md), unique per scenario. A repeat call returns the existing alert and writes no audit entry.
- Q: How is the queue built? → A: The `alert` table is the outbox. An APScheduler interval job (every 10 s) plus an after-commit kick drains it with `FOR UPDATE SKIP LOCKED`. No broker (Principle X).
- Q: Can a message be sent twice? → A: Rarely: if the process dies after Twilio accepts the message and before the row is updated. Twilio's Messages API has no idempotency key that we rely on (verify), so delivery is at-least-once. Accepted.
- Q: How do we avoid messaging real strangers from a synthetic register? → A: `MESSAGING_MODE=dry_run` by default. In `twilio` mode only numbers in `MESSAGING_LIVE_ALLOWLIST` get a real send, unless `MESSAGING_SEND_TO_REAL_BENEFICIARIES=true` and the beneficiary is not simulated. Everything else goes through `DryRunSender` and is marked `simulated=true`.
- Q: How are status callbacks secured? → A: `X-Twilio-Signature` validated with Twilio's `RequestValidator` against `PUBLIC_BASE_URL + path`, with the form params. Bad signature → 403. Validation can be turned off only when `ENV=test`.
- Q: Which Twilio errors flag the beneficiary? → A: Only invalid-number classes (21211, 21614, 30005, 30006, all **verify**). "Not on WhatsApp" (63003, **verify**) does not flag the phone (it may still take SMS). Sandbox and template errors (63015, 63016, **verify**) are configuration problems and never flag.
- Q: What if a status callback arrives out of order? → A: Statuses only move forward by rank: QUEUED 0 < SENT 1 < DELIVERED 2 < READ 3. `FAILED` can replace `QUEUED` or `SENT` only. Lower or equal ranks are ignored.
- Q: Where does the Urdu district name come from? → A: Proposed `District.name_ur`, seeded for the active districts. Fallback: the English name.
- Q: Are audit payloads allowed to hold the message text? → A: No. They hold `text_sha256`, the template key, ids and enums (FR20 hand-off: no free text).
- Q: Is Twilio throughput enough for NFR2? → A: For the pilot yes, if the production sender has the usual 80 messages per second (**verify**); 5 000 messages take about a minute. The sandbox is slow and limited (**verify**), so only allowlisted phones use it.

## Resolved by constitution decisions

- **D3**: the approval request text carries `APPROVE <code>` and `REJECT <reason>` exactly.
- **D4**: one message per run for multi-tier jumps; "money is not taken back" on tier falls.
- **D6**: escalation goes to the PDMA officer (not a Director General), and says only the DC can approve.
- Principle VIII / NFR13: demo label on simulated money; dry-run sends are `simulated=true`.
- Principle IX: all text in `catalogue.yaml`; all timings in settings.
- Principle X: DB outbox plus in-process worker; no broker.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Add purposes `REPLY_ACK` and `DECISION_NOTICE` to the `Alert.purpose` enum? | Yes (proposed in plan.md). Until merged, acks use `PAYMENT_NOTICE`/`APPROVAL_REQUEST` with template keys `receipt.*`/`dc.approval_result.*`, and decision notices use `ESCALATION` with key `pdma.approval_*` | Stats by purpose become less clear without them | Foundation owner |
| 2 | Should the Urdu text be reviewed by a native speaker for reading level? | Yes, before the demo; current text uses everyday Sindhi-Urdu-friendly words | Wording only (catalogue.yaml), no code | Frontend & pitch |
| 3 | Add coloured circle emoji to tier headers to help low-literacy readers? | No in the MVP (plain text, safest on all phones) | Catalogue change only | Frontend & pitch |
| 4 | Include a district helpline or nearest camp in Warning/Action messages? | No; there is no field for it. Advice says "listen to official announcements" | Needs a district config field and template variable | PDMA officer / team |
| 5 | Send an "all clear" to beneficiaries when an event closes? | No | One more template and fan-out | Team |
| 6 | Replay resets and `dedupe_key` | FR5 reset deletes the scenario's alert rows (or bumps `run_no`, and FR14 then includes `run_no` in the key) | Without it, a second replay run sends nothing | FR5 owner |
| 7 | Should FR8 or FR15 send the reply to a DC's `APPROVE`/`REJECT`? | FR15 renders `DcCommandResult` via `send_reply`; FR8 only sends the PDMA decision notice | Two senders could double-message the DC | FR8 + FR15 owners |
| 8 | Message the PDMA officer on every tier change too? | No; only escalations and decisions | More messages for the officer | PDMA officer |
| 9 | Production sender | A WhatsApp Business sender with approved UTILITY templates via Twilio Content API, mapped in `TWILIO_CONTENT_SIDS` | Needs Meta business verification; weeks of lead time | Team / PDMA |
| 10 | Max attempts and backoff | 5 attempts: 0, 10, 30, 60, 120 s (≈ 4 min total) | Longer backoff breaks NFR2 | Backend & alerts |

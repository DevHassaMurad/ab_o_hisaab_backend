# Clarifications: FR15 Receipt confirmation

## Clarifications

### Session 2026-11-10

- Q: The document only says "Beneficiary replies 1 to confirm" (UC10-2). What does `2` mean? → A: `2` = not received, as data-model.md `Confirmation.status` already defines. The payment notice says so in both languages.
- Q: Which digit forms count? → A: ASCII, Extended Arabic-Indic (`۰–۹`, what Urdu keyboards type), Arabic-Indic (`٠–٩`) and full-width. Unicode NFKC alone does not map the Arabic-script digits, so they are translated explicitly.
- Q: Accept words like "ہاں" or "mil gaya"? → A: Yes, but only as an **exact** whole-message match from a short list. Substring matching would misread "kab milega?" ("when will I get it?"). The list is a setting.
- Q: What does "latest open payment notice" mean? → A: The most recent alert with `expects_reply=true` and purpose `PAYMENT_NOTICE` whose `to_phone_hash` (or the beneficiary's `phone_hash`) matches the sender, whose status is `SENT`, `DELIVERED` or `READ`, and whose `sent_at` is within `RECEIPT_REPLY_WINDOW_DAYS` (7). Any channel counts, so an SMS fallback notice (FR16) is answerable from WhatsApp.
- Q: One confirmation per alert, or one per reply? → A: One row per reply (append-only). The effective status is the latest `CONFIRMED`/`NOT_RECEIVED` row; `UNPARSEABLE` never overrides. Reasons: every reply is evidence (Principle III), corrections (`2` then `1`) are natural, and the unique inbound message id makes idempotency simple. This changes the relationship summary from `Alert 1─0..1 Confirmation` to `Alert 1─* Confirmation` (proposed).
- Q: Can a beneficiary change their answer? → A: Yes; the latest valid reply wins and each change is audited. Donor "received" uses the effective status.
- Q: Which beneficiary is matched when two share a phone? → A: Candidates are all beneficiaries with that `phone_hash`; the latest open notice across them wins.
- Q: How are DCs recognised? → A: `User.phone_hash` match, `role = DC`, `is_active = true`. The message must start with `APPROVE` or `REJECT` (case-insensitive). FR15 normalises digits so `APPROVE ۴۸۲۹۱۳` works.
- Q: What if a PDMA officer sends `APPROVE`? → A: FR15 does not call FR8 and replies "only the district's DC can approve" (D6, Principle II). FR8 also checks the role (defence in depth).
- Q: Who replies to the DC: FR8 or FR15? → A: FR15, from the `DcCommandResult`, through `send_reply`, in the same transaction as FR8's decision. FR8 sends only the PDMA decision notice (FR14 `notify_decision`).
- Q: How is idempotency enforced? → A: `pg_advisory_xact_lock(hashtext(MessageSid))` at the start of the transaction, then a check for an existing `Confirmation.provider_message_id` or an `Alert.dedupe_key = 'ACK:{sid}'`. Both are written in the same transaction as the effect, so "exists" means "fully processed". Unique constraints back this up.
- Q: Which scenario does a reply belong to? → A: The scenario of the alert it answers (replay or live). The webhook has no `X-Scenario-Id`, so FR15 sets it with `use_scenario(alert.scenario_id)`.
- Q: Should the webhook reply with TwiML `<Message>`? → A: No. It returns an empty `<Response/>` and queues the ack through FR14, so every outbound message is an audited `Alert` with a status.
- Q: What about strangers? → A: Unknown numbers get no reply (no spam amplification) and no rows; only a structured log line with the phone hash.
- Q: Is the raw reply PII? → A: Treat it as PII: people may type names or CNICs. Proposed: encrypt `Confirmation.reply_raw`. The audit payload holds `reply_sha256`.
- Q: Where do "not received" reports go in the MVP? → A: They appear in `GET /confirmations?status=NOT_RECEIVED` and in the payment's receipt column (FR17) for the DC. With `FR16_FALLBACK` on, FR16 also opens a field-agent task.
- Q: How do replays get realistic "received" numbers without real phones? → A: A job writes deterministic simulated replies for dry-run notices only (`provider_message_id` starts with `SIM-`), labelled `simulated=true`, default mix 85/5/10.
- Q: How fast must the webhook be? → A: p95 < 2 s. All work is a few indexed queries plus FR8's handler; outbound sends are queued.

## Resolved by constitution decisions

- **D3**: WhatsApp `APPROVE <code>` / `REJECT <reason>` from the DC creates the same `Approval` as the dashboard; FR15 is the WhatsApp entry point.
- **D6**: officers cannot approve; FR15 tells them so.
- Principle II: only active DC phones reach FR8.
- Principle III: one audit entry per confirmation, same transaction.
- Principle VIII / NFR13: simulated replies and replies about simulated payments are labelled.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Make `Confirmation` append-only (`Alert 1─*`), add `provider_message_id` (unique), `scenario_id`, `simulated`, `recorded_by`, encrypt `reply_raw` | Yes (plan.md "Proposed data-model additions") | Without it, idempotency and corrections need a separate inbound-message table | Foundation owner |
| 2 | Keyword list for yes/no | On, exact whole-message match, list in settings | Off = digits only; more `UNPARSEABLE` from low-literacy users | Frontend & pitch + PDMA |
| 3 | Should one reply confirm **all** open notices of the beneficiary (e.g. Watch and Warning payments sent minutes apart)? | No; latest notice only (guide: "latest open payment notice") | Yes = fewer silent notices, but a `1` could confirm money that never arrived | PDMA officer |
| 4 | Reply window | 7 days | Longer = late replies still count; shorter = more "nothing waiting" replies | PDMA officer |
| 5 | Should `NOT_RECEIVED` message the DC immediately? | No in the MVP; dashboard list only (FR16 adds field tasks) | Extra DC messages during a busy event | DC / PDMA |
| 6 | `DcCommandResult` exact shape | Resolved: FR8 plan defines `outcome`, `approval_id`, `scenario_id`, `reply_text`, `state_changed`. FR15 forwards `reply_text` through template `dc.passthrough` | — | FR8 + FR15 owners |
| 7 | Accept Urdu command words (`منظور` / `مسترد`) as `APPROVE` / `REJECT`? | No (D3 defines Latin keywords) | Easier for some DCs; needs FR8 parsing change | Team |
| 8 | Simulated reply mix | 85% confirmed / 5% not received / 10% silent, replays only | Changes the replay's donor "received" figure | Frontend & pitch |
| 9 | Help-reply throttle | One per phone per 10 minutes | Shorter = more messages to confused users | Backend & alerts |

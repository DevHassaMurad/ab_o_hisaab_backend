# Clarifications: FR18 Payment failure reasons, retry and cancel

## Clarifications

### Session 2026-11-10

- Q: "At most 3" retries: 3 attempts in total, or 3 retries after the first send? → A: 3 **retries** after the first send, so up to 4 attempts. `retry_count` goes 0 → 3, matching data-model.md ("Max 3") and the key format `{allocation_id}:{retry_count}` (keys `:0` to `:3`).
- Q: Who may retry or cancel? → A: Only a user with role `DC` whose `district_id` equals the payment's `district_id`. The document's actor table says the DC "fixes failed payments", and the guide says "by the DC of that district". The PDMA officer and admin get 403 `DC_ONLY_ACTION`; donors get 403 `DONOR_AGGREGATE_ONLY`. (Open item 1.)
- Q: Note rules? → A: Required for both actions; trimmed length 5–500 characters. Empty, blank or shorter → 422 `NOTE_REQUIRED`; longer → 422 `NOTE_TOO_LONG`. Why 5: blocks "ok"/"." without being bureaucratic.
- Q: Where is the note kept, given it might contain PII (e.g. a corrected wallet number)? → A: On `PaymentEvent.note`, readable by the DC (own district) and officer. The audit payload stores `note_sha256` and `payment_event_id`, which binds the note to the chain without copying its text. Donor views never show notes.
- Q: How do we stop a double click from consuming two retries? → A: The retry body must include `expected_retry_count`. Under the payment row lock, if it differs from the current `retry_count` → 409 `STALE_PAYMENT_STATE`. A second request that arrives while the first is in flight sees `PROCESSING` → 409 `INVALID_TRANSITION`.
- Q: Which order are the checks done in? → A: 401/403 role → 404 (payment not in scope/scenario) → 403 district → 422 note → 409 status (`INVALID_TRANSITION`) → 409 limit (`RETRY_LIMIT_REACHED`) → 409 stale (`STALE_PAYMENT_STATE`). Fixed order makes tests and UI messages predictable.
- Q: Can a retry be refused for reasons that won't change (e.g. invalid wallet not yet corrected)? → A: No, the DC decides (Principle II: officials decide). The failure summary and detail show a suggested action ("correct the wallet in the register first"). The retry will simply fail again and use one retry.
- Q: Which wallet does a retry use? → A: The beneficiary's current wallet, read and decrypted when the rail instruction is built (FR17 `build_instruction`). So a fix in the register (FR10) takes effect on the next retry.
- Q: What does the retry endpoint return? → A: The payment detail after the rail answered (synchronous with the simulated rail): usually `PAID` or `FAILED`; `PROCESSING` if the wallet is a pending/timeout case. HTTP 200 in all three cases.
- Q: What happens to failure fields on retry? → A: `failure_reason` and `failure_detail` are cleared on the Payment when it goes back to Processing; the previous values stay in the earlier `PaymentEvent` rows (FR17 adds `failure_reason` to `PaymentEvent`).
- Q: Can a Processing payment be cancelled (e.g. stuck for 2 days)? → A: No. Money may be in flight. The stuck path is reconcile (FR17) → Failed → then cancel. Open item 3 proposes a "force fail after N days" action for the officer, not in the MVP.
- Q: Is cancelled money re-allocated to Unmet-need beneficiaries? → A: No, not in the MVP. It returns to `remaining_pkr` and shows as unused in the donor report. Re-allocation needs a new allocation run (FR11/FR12). Open item 2.
- Q: Does the beneficiary get a message when their payment is cancelled? → A: No in the MVP (no `Alert.purpose` for it; the DC handles it in the field). Open item 4.
- Q: What suggested action goes with each reason? → A: `INVALID_WALLET` → "Correct the wallet in the beneficiary register, then retry; or cancel." `NO_WALLET` → "Add a wallet in the register, then retry; or cancel." `LIMIT_REACHED` → "Retry later; the wallet limit may have reset." `PROVIDER_TIMEOUT` → "Retry; the earlier instruction did not land." `PROVIDER_ERROR` → "Retry." Plus "Retries used up: cancel" when `retry_count = 3`. Text is English; the frontend may localise by code.
- Q: Are DC actions allowed in replay mode? → A: Yes, with `X-Scenario-Id`. The payment lookup is scoped to that scenario (404 otherwise) and the audit entry goes to the replay run's chain. This lets the demo show a live DC retry during the replay.

## Resolved by constitution decisions

- **Principle II**: officials hold authority; the DC (not the system or the officer) decides to re-send or cancel.
- **Principle III**: same-transaction audit through FR17's `transition()`.
- **Principle VI**: notes never leave DC/officer views; donors refused.
- **D5**: `Payment` state machine and `PaymentEvent` timeline.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | May the PDMA officer also retry/cancel (e.g. when the DC is unavailable)? | No, DC only | Allowing it weakens separation of duties; would need a role check change only | PDMA (product) |
| 2 | Re-allocate cancelled money to Unmet-need beneficiaries automatically? | No; stays in `remaining_pkr` | Auto re-allocation needs FR11/FR12 changes and fairness re-check | PDMA + FR12 owner |
| 3 | An officer "force fail" for payments stuck > N days | Not in MVP | Without it, a truly lost Processing payment needs a manual DB fix in production | Backend lead |
| 4 | Notify the beneficiary on cancel | No | Needs a new `Alert.purpose` and templates (FR14) | Product |
| 5 | Minimum note length | 5 characters | Longer = more friction for the DC on mobile | Product |
| 6 | Retry limit | 3 (setting `PAYMENT_MAX_RETRIES`) | Higher = more attempts and more provider fees | PDMA |

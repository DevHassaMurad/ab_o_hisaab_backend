# Clarifications: FR17 Payment tracking

## Clarifications

### Session 2026-11-10

- Q: data-model.md says `reserved_pkr` = sum of Reserved **or Processing** payments. Where does a Failed payment's money sit? → A: It stays **reserved**. FR18 says cancel "returns the money to the fund", which only makes sense if a Failed payment still holds it; and a retry must not find the money gone. So reserved = Reserved + Processing + Failed. Proposed as a data-model correction in plan.md.
- Q: When exactly is a payment reserved? The guide says "when the allocation is saved". For the Action tier, allocations are saved before the DC decides (UC07 precondition). → A: Reserve when the allocation is saved **and** the release is payable (Watch/Warning, or Action after approval). For Action, `008-fr08-dc-action-approval` calls `reserve_for_release` on approval. Why: on rejection no payments exist, so "the money stays in the fund" (guide FR8) needs no unwinding, and the payment state machine never needs a system-initiated cancel. Open item 1.
- Q: Which transitions exist? → A: Exactly data-model.md §6: Reserved→Processing, Processing→Paid, Processing→Failed, Failed→Processing (retry), Failed→Cancelled, Reserved→Cancelled. Processing→Cancelled is refused (money in flight). Everything else returns 409 `INVALID_TRANSITION`.
- Q: Which balance changes go with each transition? → A: Reserve: reserved +A, remaining −A. Paid: reserved −A, disbursed +A. Cancel: reserved −A, remaining +A. Send/fail/retry: none. `allocated_pkr` never changes here.
- Q: How are balances kept consistent under concurrency? → A: `SELECT … FOR UPDATE` on the `DisasterFund` row inside the transition transaction, and SQL increments (`reserved_pkr = reserved_pkr + :a`). We do **not** bump `DisasterFund.version`: that column is FR6's optimistic lock for settings edits, and balance updates must not make the officer's settings form fail.
- Q: Should the rail be called inside the DB transaction? → A: No. Step 1 (commit): Reserved→Processing + audit. Step 2: call the rail (no transaction open). Step 3 (commit): Paid/Failed + ledger + audit (+ alert). Audit locks and fund locks are never held during a network call.
- Q: What if the process dies between step 1 and step 3, or the rail times out? → A: The payment stays Processing. The timer job (every 15 min) calls `rail.get_status(idempotency_key)` for Processing payments older than 10 min and applies the answer. If still pending after 24 h, it is flagged stuck.
- Q: Does a timeout become Failed immediately? → A: No. A timeout is ambiguous (money may have moved). It becomes `FAILED/PROVIDER_TIMEOUT` only when the provider's status says the instruction never landed. This prevents a retry (new key) from double-paying.
- Q: The foundation Protocol is `send(payment)`. What is passed? → A: A `RailInstruction` dataclass built from the payment and the decrypted beneficiary wallet: `idempotency_key`, `amount_pkr`, `wallet_provider`, `wallet_id`, `retry_count`, `payment_id`. The rail never touches the ORM or the DB. FR17 owns the Protocol and adds `get_status(idempotency_key)`.
- Q: SimulatedRail rules? → A: In order: (1) no wallet → `NO_WALLET`; (2) wallet id ends `000` → `INVALID_WALLET`; (3) ends `998` → timeout (then `get_status` says `FAILED/PROVIDER_TIMEOUT`); (4) ends `999` → `PENDING` forever (stuck demo); (5) amount > `SIM_RAIL_WALLET_LIMIT_PKR` (default 40 000) and `retry_count < SIM_RAIL_LIMIT_CLEARS_AFTER_RETRIES` (default 1) → `LIMIT_REACHED`; (6) seeded uniform draw from `sha256(seed:key)` < `SIM_RAIL_FAILURE_RATE` → `PROVIDER_ERROR`; (7) otherwise `PAID` with txn id `SIMJC-…`/`SIMEP-…`. Rule 5 models a limit that resets, so a DC retry can succeed in the demo.
- Q: Default random failure rate? → A: `0.0` in tests, `0.05` in the demo `.env`. Deterministic per key, so the same replay gives the same failures.
- Q: Provider enum has no value for beneficiaries without a wallet. → A: `provider` becomes nullable; null when `wallet_provider = NONE` (proposed addition). Otherwise `JAZZCASH → SIMULATED_JAZZCASH`, `EASYPAISA → SIMULATED_EASYPAISA`.
- Q: Is "stuck" a status? → A: No. It is the `is_stuck` flag on a Processing payment, set strictly after 24 h (`now − processing_since > 24 h`), with a `PaymentEvent` (from = to = PROCESSING, action `STUCK_FLAG`) and an audit entry `PAYMENT_STUCK_FLAGGED`. It clears on leaving Processing. `processing_since` resets on each retry.
- Q: Which clock? → A: `Clock.now()`, so replays step through time. FR5 calls `run_payment_timers(clock.now())` after each replay day.
- Q: Who flips `FundRelease.status` to Released? → A: FR17, at the end of `dispatch_release`: `RELEASED` when no payment of the release is still Reserved and no allocation is `UNMET_NEED`; `PARTIALLY_RELEASED` if any allocation is `UNMET_NEED`. Sets `released_at`. Audited as `FUND_RELEASE_STATUS_CHANGED`. Open item 3 (coordination with 007/012).
- Q: Dispatch synchronous or background? → A: `PAYMENT_DISPATCH_MODE=background` (APScheduler `run_now`) for live; `inline` for tests and replays so a replay step finishes with payments resolved. Each payment uses its own short transactions.
- Q: Who sees what on the list? → A: Officer/admin: all districts. DC: own district (auto-scoped; asking for another district → 403 `DISTRICT_FORBIDDEN`). Donor: 403 `DONOR_AGGREGATE_ONLY` on every `/payments` route (UC09-3a); donor counts live in FR19.
- Q: Which beneficiary fields appear? → A: List/detail: beneficiary id, type, tehsil, display name (decrypted, DC/officer only), masked wallet (`****` + last 4), provider. Never phone or CNIC. CSV: no name and no wallet at all, because files leave the system.
- Q: Should a CSV export be audited? → A: Yes, `PAYMENTS_EXPORTED` with the filters and row count (data leaving the system). Not a state change, but cheap and useful.
- Q: Ordering of dispatch? → A: By allocation rank within the release (households and shopkeepers interleaved by rank, then by allocation id), so the highest-priority beneficiaries are paid first.
- Q: Payment notice on Paid only, or also on Failed? → A: Paid only (UC08-5). Failures are for the DC (FR18), not the beneficiary.
- Q: Audit note text? → A: The timeline note is stored on `PaymentEvent.note`; the audit payload carries `payment_event_id` and `note_sha256`, never the note text (it could contain PII).

## Resolved by constitution decisions

- **D4**: releases are per tier and per event; FR17 pays per allocation within a release.
- **D5**: `Payment` holds the state machine; `PaymentEvent` holds the timeline.
- **Principle III**: one audit entry per status change, same transaction.
- **Principle VIII**: `SimulatedRail` only in the MVP, everything labelled.
- **Principle IX**: limits, rates and timings are settings.
- **Principle XI**: state machine tests first.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Reserve Action-tier payments at allocation save or at approval? | At approval (FR8 calls `reserve_for_release`) | Reserving earlier needs a system cancel path on rejection and contradicts "money stays in the fund" | Backend lead with FR8 owner |
| 2 | `reserved_pkr` includes Failed payments | Yes | If not, a retry could overdraw and cancel would "return" money twice | Foundation owner (data-model.md) |
| 3 | FR17 sets `FundRelease` to Released / Partially released after dispatch | Yes | Otherwise FR7 or FR12 must own it; status semantics must be agreed | FR7 + FR12 owners |
| 4 | Simulated wallet limit | 40 000 PKR, clears after 1 retry | Higher limit → no `LIMIT_REACHED` in the demo; never clearing → retry demo can't succeed | Frontend & pitch |
| 5 | Demo random failure rate | 5 % (tests 0 %) | More failures make the DC screen busier | Frontend & pitch |
| 6 | Stuck timer interval | 15 min | Shorter = faster flag, more DB load | Backend lead |
| 7 | Show beneficiary display name to the DC in list/detail | Yes (masked wallet, no phone/CNIC) | Hiding names makes field follow-up hard | PDMA (privacy) |
| 8 | Notify the DC by WhatsApp when a payment becomes stuck | No (dashboard flag only; no alert purpose exists) | Would need a new `Alert.purpose` value | Product |

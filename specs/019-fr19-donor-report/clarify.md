# Clarifications: FR19 Donor report

## Clarifications

### Session 2026-11-10

- Q: "Allocated" is ambiguous: the fund's configured amount, or the money assigned to beneficiaries? → A: The fund's configured amount (`DisasterFund.allocated_pkr`): what the PDMA committed to anticipatory action in that district. Money assigned to people is covered by released/disbursed, and the shortfall by "unmet need". Why: it is the only reading under which "unused" is meaningful for a donor.
- Q: Exact definitions? → A: (S = funds in scope; counted releases = status `APPROVED`, `ALLOCATING`, `RELEASED`, `PARTIALLY_RELEASED`)
  - `allocated_pkr = Σ DisasterFund.allocated_pkr`
  - `released_pkr = Σ FundRelease.amount_pkr` (counted releases)
  - `awaiting_approval_pkr = Σ FundRelease.amount_pkr` where `PENDING_APPROVAL`
  - `disbursed_pkr = Σ Payment.amount_pkr` where `PAID`
  - `received_pkr = Σ Payment.amount_pkr` where `PAID` and the latest non-`UNPARSEABLE` `Confirmation` is `CONFIRMED`
  - `not_received_pkr` = same with `NOT_RECEIVED`; `awaiting_confirmation_pkr = disbursed − received − not_received`
  - `in_flight_pkr = Σ Payment.amount_pkr` where `RESERVED`, `PROCESSING`, `FAILED` (= Σ `reserved_pkr`)
  - `unused_pkr = allocated − disbursed − in_flight` (= Σ `remaining_pkr`)
  - `unmet_need_pkr = Σ Allocation.computed_amount_pkr` where `UNMET_NEED` (counted releases only), plus `unmet_need_count`
  - Identities: `allocated = disbursed + in_flight + unused`; `received + not_received + awaiting_confirmation = disbursed`; `disbursed ≤ released ≤ allocated`.
- Q: Why compute from rows and not just read the fund ledger? → A: We compute both and assert they agree (`in_flight == Σ reserved_pkr`, `disbursed == Σ disbursed_pkr`). A mismatch is logged as an error and the response sets `ledger_consistent=false`. This turns the donor report into a live consistency check.
- Q: Which confirmation counts if there are several? → A: The latest by `received_at`, ignoring `UNPARSEABLE`. A beneficiary who first says "2" and later "1" counts as received.
- Q: Does a Rejected Action release appear? → A: Not as released. It appears in the by-tier table with status `REJECTED` and amount 0 released; the money stays in unused.
- Q: How is the donor's scope defined? → A: Districts of the funds linked through `DonorContribution` for this donor. In replay mode (`X-Scenario-Id`), the funds of the **same districts** in that scenario (replay funds are separate rows). Optional `district_id` narrows the scope; a district outside the scope → 403 `FUND_NOT_LINKED`.
- Q: Full fund totals or the donor's pro-rata share? → A: Full totals of the linked funds, plus the donor's own `contributed_pkr` shown alongside. Pro-rata attribution would invent precision we don't have. Open item 1.
- Q: Who can open the donor report? → A: `DONOR` (own scope), `PDMA_OFFICER` and `ADMIN` (preview; all funds or a chosen district). `DC` → 403 `ROLE_FORBIDDEN` (they have the operational payment views).
- Q: Breakdown by type: what is "allocated" per type? → A: Not split. Per type we report: released pool (Σ `household_pool_pkr` / `shopkeeper_pool_pkr` of counted releases), disbursed, received, in flight, unmet need, beneficiaries reached. Allocated and unused are fund-level only (the split is applied per release, not to the whole fund).
- Q: Small cells could identify a person (e.g. "1 shopkeeper in Buner received 50 000"). → A: Suppress any breakdown cell with `beneficiaries_reached < DONOR_MIN_CELL_SIZE` (5) **and** `beneficiaries_reached > 0`: amounts and counts become null with `suppressed=true`. Secondary suppression: if exactly one sibling in a group is suppressed, also suppress the next-smallest sibling, so it cannot be derived from the total. Totals are never suppressed. Zero cells are shown as zero (nothing to protect). Open item 2.
- Q: What about tehsil-level breakdowns? → A: Not in donor responses (UC11 says district and type). Tehsil cells would be small and easier to re-identify.
- Q: How does the "403 for individual data" work concretely (UC11-3a)? → A: (1) Explicit routes `GET /reports/donor/beneficiaries`, `/reports/donor/payments`, `/reports/donor/payments/{id}` always return 403 `DONOR_AGGREGATE_ONLY` with a `links.report` pointer. (2) All operational routes (`/payments/*`, `/beneficiaries/*`, `/allocations/*`, `/alerts/*`, `/audit` list/export) refuse the donor role. (3) A route-sweep contract test enumerates the app's routes and fails if the donor role can reach anything outside the allowlist.
- Q: How does the contract test prove "no PII" (NFR8)? → A: Three layers: (a) **schema**: every model in `app/schemas/reports.py` inherits `DonorScope`, and a recursive walk of each model's JSON schema finds no field from the extended deny-list (`name`, `full_name`, `phone`, `phone_hash`, `cnic`, `wallet_id`, `wallet_masked`, `display_name`, `beneficiary_id`, `beneficiary_ref`, `payment_id`, `allocation_id`, `confirmation_id`, `alert_id`, `idempotency_key`, `provider_txn_id`, `lat`, `lon`, `note`, `email`, `tehsil_id`); (b) **routes**: the sweep above; (c) **data**: seed canary beneficiaries (name `CANARY-NAME-7731`, phone `+923001234567`, CNIC `42101-1234567-1`, wallet `03009998877`) and scan every donor response body and the uncompressed PDF bytes for those strings, for any beneficiary/payment UUID, and for phone/CNIC regex matches.
- Q: Stories in the MVP? → A: Curated YAML (`data/seeds/donor_stories.yaml`), written by the team as illustrative examples, each `simulated: true`, `source: CURATED`, labelled "Illustrative story (simulated)". They use roles, never names ("a shopkeeper in Johi tehsil"). Tagged by `district_pcode` and optional `scenario_key` so a replay shows matching stories. A loader rejects any story with phone/CNIC-like digits, more than 600 characters, or a missing label.
- Q: Stories when FR22 is on? → A: `OutcomeStoryProvider` builds templated aggregate stories ("7 of 9 shopkeepers in Johi said they moved stock before the water came") from `OutcomeReport` groups of ≥ 5, with `source: OUTCOME_AGGREGATE`. Flag name: `FR22_OUTCOME` (as in `app/core/flags.py`).
- Q: PDF library? → A: ReportLab (pure Python wheels; works on Windows dev without GTK). WeasyPrint needs native Pango/GTK libraries on Windows, which risks the 32-hour build. Trade-off: ReportLab does not shape Urdu script, so the PDF is English-only in the MVP. Open item 3.
- Q: What makes the PDF "verified"? → A: At generation we call `verify_chain(current_chain_key())` and print valid/count/head seq/head hash. We compute `report_sha256 = sha256(canonical_json(summary_json))` and print it in the footer, and record `DONOR_REPORT_GENERATED {report_sha256, districts, as_of, scenario_id, chain_head_seq, chain_head_hash}`. Anyone can later match a printed report to the log.
- Q: Should the summary call verify on every request? → A: No; verify is O(chain length). The summary returns `audit_verify_url`; the button calls FR20. The PDF does call verify once.
- Q: Simulated label rule? → A: `simulated = true` if any counted release, payment or served story in scope has `simulated=true`, or the scope is a replay. In the MVP this is always true. The PDF shows a red "SIMULATED DATA — prototype, no real money moved" banner on every page.
- Q: Time filters? → A: Not in the MVP; the report is cumulative "as of now". Open item 4.

## Resolved by constitution decisions

- **Principle VI**: aggregates only; `DonorScope`; contract test.
- **Principle VIII**: simulated labels on data and stories.
- **Principle III / FR20**: verify and the report-generated audit entry.
- **Principle II**: donors fund and view only; no donor write endpoints.
- **D5**: canonical entities only; no new entities.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Show full fund totals or the donor's pro-rata share? | Full totals + the donor's contributed amount | Pro-rata needs an attribution rule per fund and per release | Donors + PDMA |
| 2 | Minimum cell size for breakdowns | 5 beneficiaries, with secondary suppression | Lower = more detail, more re-identification risk; 0 disables | PDMA (privacy) |
| 3 | PDF in English only (ReportLab) vs English + Urdu (WeasyPrint or reshaping) | English only in MVP | Urdu needs `arabic-reshaper` + `python-bidi` + a Naskh font, or WeasyPrint with GTK | Frontend & pitch |
| 4 | Date-range filters (e.g. "this monsoon") | Cumulative as of now | Ranges need flow-based definitions (paid_at in range) and break the stock identities | Donors |
| 5 | Can a DC see the donor report for their district? | No | Low risk either way | Product |
| 6 | Curated story wording | Team writes 2–3 per demo district, reviewed for no PII | Pitch tone | Frontend & pitch |

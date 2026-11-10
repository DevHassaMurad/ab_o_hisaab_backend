# Clarifications: FR11 Beneficiary Ranking

## Clarifications

### Session 2026-11-10

- Q: Which factors does the household score use? UC06-2 lists poverty, family size and exposure; FR11 lists need, exposure, urgency and existing support. → A: All of them. Household: poverty (need), family size (need), flood exposure, urgency, minus existing support. Defaults: poverty **0.40**, family size **0.20**, exposure **0.30**, urgency **0.10**, support penalty **0.25**. Poverty and exposure dominate, matching UC06-2; urgency is small because all households in one release share similar tehsil risk.
- Q: Which factors does the shopkeeper score use? → A: UC06-3 exactly: stock at risk **0.40**, exposure **0.30**, urgency **0.30**, minus existing support **0.25**.
- Q: How is each factor normalised to 0–1? → A:
  - poverty `p = poverty_score` (already 0–1; 1 = poorest).
  - family size `f = (min(family_size, 12) − 1) / 11` (1 person → 0, 12+ → 1).
  - exposure `x = flood_exposure` (FR10, 0–1).
  - urgency `u = tehsil_score/100 × (1 − 0.05 × (peak_lead_day − 1))` (day 1 → ×1.00, day 7 → ×0.70). Sooner water is more urgent; slow-onset 7-day leads still score well.
  - stock at risk `k = min(stock_value_pkr / 250 000, 1)`.
  - existing support `s = min(existing_support_pkr / 50 000, 1)` (≈ one year of a BISP-style transfer).
- Q: What is the exact score? → A: `priority_score = round(100 × clamp(Σ wᵢ·factorᵢ − w_support·s, 0, 1), 2)`, where the positive weights for each type must sum to 1. A beneficiary with every positive factor at 1 and no support scores 100.
- Q: Why subtract support instead of using it as a factor? → A: Need is reduced by help already received; a penalty is easy to explain ("score lowered by 12.5 points because of PKR 25 000 support"). Capped at 50 000 so it never dominates.
- Q: Is ranking larger shops first unfair to small traders? → A: The document asks for "stock at risk", so we keep it, but cap the factor at PKR 250 000 so very large shops do not run away with it, give exposure and urgency 60% together, and apply the support penalty. Weights are configurable per district.
- Q: How are missing values handled? → A: `poverty_score` null → tehsil median of active households, then district median, then 0.5. `family_size` and `stock_value_pkr` are required by FR10. Exposure is always present (FR10 imputes its components). Tehsil without its own `RiskIndex` → district `RiskIndex` of the release. Every imputation is listed in `score_inputs.imputed`.
- Q: What are the tie-breakers? → A: Sort by `priority_score` (2 dp, as stored and displayed) descending, then:
  - households: poverty factor ↓, exposure ↓, family size ↓, existing support ↑, `register_ref` ↑
  - shopkeepers: exposure ↓, urgency ↓, existing support ↑, stock value ↓, `register_ref` ↑
  `register_ref` is unique, visible and stable, so the order is total and reproducible across replays. Sorting on the displayed 2-dp score means "equal-looking scores" are always resolved by the visible tie-break chain.
- Q: Which beneficiaries are eligible? → A: Active beneficiaries (FR10 `is_active`) in the release district's **affected tehsils**: tehsils whose `RiskIndex.tier ≥ WATCH` on the release's run date in the same scenario. If no tehsil-level index exists, all tehsils of the district (`DISTRICT_FALLBACK`). Beneficiaries with `phone_valid=false` or no wallet stay eligible (payment and alerts handle them; see FR10 open item 5).
- Q: Do later tiers re-rank everyone? → A: Yes, each release is ranked fresh from current inputs. Beneficiaries whose FR12 top-up for this tier is 0 (already covered) are left out of the list and counted as `already_covered`.
- Q: What exactly is the fairness metric (NFR5)? → A: `fair-v1`, per affected tehsil t in the release:
  - `need_t = population_t × risk_t × v`, with `risk_t` = tehsil score/100 and `v` = `District.vulnerability_weight`
  - `expected_share_t = need_t / Σ need`
  - `actual_share_t = funded_t / Σ funded` (funded = Σ `final_amount_pkr` of `FUNDED` rows, both types)
  - `ratio_t = actual_share_t / expected_share_t`
  - `sparse_t` = relative coverage < **0.5** or completeness < **0.7** or no registered households (from FR10 stats)
  - **flag** `SPARSE_UNDERFUNDED` when `sparse_t` and `expected_share_t ≥ 0.05` and `ratio_t < 0.5`
  - `passed` = no flags. Underfunded but not sparse tehsils get an `UNDERFUNDED_INFO` note and do not fail the check. No funded money at all → passed with `NO_FUNDED_ROWS`.
  This is the constitution's wording ("districts or tehsils with sparse data get a share that falls well below their vulnerability-weighted share") with "well below" set to half.
- Q: Why population and not registered count for the expected share? → A: Sparse registers are the problem NFR5 targets. Using the register's own count would hide it.
- Q: What happens on a failed check? → A: The list is not saved, no payment is reserved, the release stays `ALLOCATING` (or `PENDING_APPROVAL`/`APPROVED` for Action), and the officer gets a WhatsApp/dashboard `ESCALATION` alert with template `fairness_hold`. The officer either overrides amounts (the check re-runs) or acknowledges with a note and saves.
- Q: Who saves Watch/Warning lists, given UC06 says the officer reviews but FR7 says these tiers release automatically? → A: The system saves them automatically when fairness passes (Principle I: no human delay on low tiers). The officer's review window exists for Action lists (while the DC decides) and for any list held by the fairness check. The officer can still see every saved list and its reasons.
- Q: When is the Action list made? → A: At release creation (UC07 precondition: "the allocation list from UC06 is ready"), so the DC sees the total. It is saved only by `finalize` after an `APPROVED` decision. Nothing is reserved before that.
- Q: What can an override change? → A: Only `final_amount_pkr` of a row in an unsaved list, with a reason of 10–500 characters. 0 means `EXCLUDED`. Limits come from FR12 (`override_limit`). Overridden rows are **pinned**; FR12 re-fills the other rows in rank order around them. The fairness check re-runs and any previous acknowledgement is cleared because the numbers changed.
- Q: Who can override? → A: `PDMA_OFFICER` only (UC06 primary actor). DC reads; admin reads; donor has no access.
- Q: Can override reasons contain personal data? → A: They are stored and audited, so we reject reasons that match a phone (`(\+92|0)3\d{9}`) or CNIC (`\d{5}-?\d{7}-?\d`) pattern with `REASON_CONTAINS_PII`.
- Q: Where do reason strings come from? → A: Templates in `app/services/explain/allocation_templates.py`, owned by FR11. The foundation lists FR3 as the `ExplainService` owner, so FR3's `explain_allocation` delegates to these templates, and FR13 can rephrase them later. Template text is always stored (D2).
- Q: Are weights global or per district? → A: Defaults in settings; optional per-district override in the proposed `DisasterFund.ranking_weights`. Edited only through `PUT /allocation-rules/{district_id}`; changes apply to the next allocation run, never retroactively.
- Q: How do replays handle a fairness hold? → A: With `REPLAY_AUTO_ACK_FAIRNESS=true` (default), the replay acknowledges as `replay-sim` with `metrics.acknowledged_simulated=true`, exactly like FR5's simulated DC approval. The record always exists; it is never skipped.

## Resolved by constitution decisions

- **D4**: Each tier fires once per event; FR11 ranks per release, and repeated-tier behaviour is handled by FR12's top-up (already-covered beneficiaries drop out).
- **D5**: `Allocation` and `FairnessCheck` are canonical entities; FR11 only proposes additions.
- **D2**: Reasons are template-generated in EN and UR; LLM rephrasing is roadmap (FR13).
- **D3/D6**: Approval itself is FR8; FR11 only enforces "no Action save without APPROVED".
- Principles **II, III, IV, V, VI, IX, XI** apply directly.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Default weights and norms (HH 0.40/0.20/0.30/0.10, −0.25; SK 0.40/0.30/0.30, −0.25; stock norm 250 000; support norm 50 000; family cap 12) | As stated, in settings | Ranking order changes; config only, audited | PDMA officer with Data & ML |
| 2 | NFR5 says "poorer **districts**"; funds are per district, so allocation cannot move money between districts. Add a province-level check comparing districts' fund per vulnerable person? | Tehsil-level check within each release (MVP); province-level comparison is roadmap | Would add a cross-release report and possibly FR6 warnings | Product + FR6 owner |
| 3 | Fairness thresholds: ratio < 0.5, sparse = relative coverage < 0.5 or completeness < 0.7, min expected share 0.05 | As stated, in settings | Looser → fewer holds; stricter → more holds that can delay low tiers | PDMA officer |
| 4 | Should a failed fairness check hold the Watch tier (small, low-regret) or only warn? | Hold all tiers (constitution: "officer must see the warning before saving") | Not holding Watch removes a possible delay but weakens Principle V | PDMA officer / constitution owner |
| 5 | Replay auto-acknowledges fairness holds | `REPLAY_AUTO_ACK_FAIRNESS=true`, labelled simulated | Off → the presenter must click acknowledge during the demo | Frontend & pitch |
| 6 | Should affected tehsils include tehsils covered by earlier releases of the same event that have since dropped below Watch? | No; only tehsils at Watch+ on this run date | Yes → broader eligibility at later tiers | PDMA officer |
| 7 | Allow the DC (not only the officer) to override during the approval window? | No, officer only (UC06 actor) | DC overrides would need FR8 UI and RBAC changes | Product |

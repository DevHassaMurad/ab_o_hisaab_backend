# Feature Specification: FR11 Beneficiary Ranking

**Feature Branch**: `011-fr11-beneficiary-ranking`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: "FR11 Rank beneficiaries with transparent rules: need, exposure, urgency, existing support" (Area: Beneficiaries, Priority: MVP)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ranked list with reasons for every release (Priority: P1)

When a tier fires for a district, the system pulls the households and shopkeepers in the affected tehsils, scores households on poverty, family size, flood exposure and urgency minus existing support, and scores shopkeepers on stock at risk, exposure and urgency minus existing support. It ranks each type, and each beneficiary gets a short reason in English and Urdu (UC06-1, 2, 3, 5).

**Why this priority**: The core of UC06 and the "Prioritise" pillar. Without a ranking, amounts and payments cannot be decided.

**Independent Test**: Create a Warning release for Dadu with a fixed register fixture, call `allocate`, and compare ranks, scores and reasons with the expected table.

**Acceptance Scenarios**:

1. **Given** household A (poverty 0.90, family 8, exposure 0.80, support 0) and household B (poverty 0.60, family 5, exposure 0.50, support PKR 50 000), both in a tehsil with risk 85 and peak lead day 1, **When** ranking runs with default weights, **Then** A scores 81.23, B scores 29.77, and A ranks above B.
2. **Given** shopkeeper S1 (stock PKR 200 000, exposure 0.70, support 0) and S2 (stock PKR 60 000, exposure 0.90, support PKR 10 000) in the same tehsil, **When** ranking runs, **Then** S1 scores 78.50, S2 scores 57.10, and households and shopkeepers are ranked separately (each starts at rank 1).
3. **Given** any register, **When** the same release is ranked twice or with the input rows shuffled, **Then** the ranks are identical.
4. **Given** two households with equal scores, **When** ranked, **Then** the tie is broken by higher poverty, then higher exposure, then larger family, then lower existing support, then `register_ref` ascending.
5. **Given** a ranked list, **When** the officer opens it, **Then** each row shows rank, score, amount, status and a reason in Urdu and English that names the factors and their values.

---

### User Story 2 - Fairness check before saving (Priority: P1)

Before any allocation is saved, the system compares each affected tehsil's share of the funded money with its vulnerability-weighted expected share. If a tehsil with sparse register data gets well below its expected share, the check fails and the officer must see and acknowledge the warning before the list is saved (UC06-7, 7a; NFR5).

**Why this priority**: NFR5 and Principle V. It is the guard against sparse data silently starving poorer areas.

**Independent Test**: Two fixtures: a balanced register (check passes, Watch list auto-saves) and one where a high-risk tehsil has only 10% relative coverage (check fails, list is held, officer is alerted).

**Acceptance Scenarios**:

1. **Given** a Watch release where every affected tehsil has normal coverage, **When** allocation runs, **Then** the fairness check passes and the list is saved automatically and handed to payments.
2. **Given** a Warning release where tehsil T has relative coverage 0.2, expected share 0.30 and actual share 0.06 (ratio 0.2), **When** allocation runs, **Then** the check fails with flag `SPARSE_UNDERFUNDED` for T, the list is not saved, no payment is reserved, and the officer receives a fairness-hold notice.
3. **Given** a held list, **When** the officer saves with `acknowledge_fairness=true` and a note, **Then** the acknowledgement is recorded with the officer and time, the list is saved, payments are reserved, and two audit entries are written (acknowledged, saved).
4. **Given** a held list, **When** the officer tries to save without acknowledging, **Then** the request is refused with `FAIRNESS_ACK_REQUIRED`.
5. **Given** an underfunded tehsil that is not sparse, **When** the check runs, **Then** it passes and the tehsil is listed as an information note only.

---

### User Story 3 - Officer override with a reason (Priority: P2)

The officer reviews an unsaved list (an Action release awaiting the DC, or a list held by the fairness check), changes a beneficiary's amount with a written reason, and sees how the rest of the list re-fills (UC06-6).

**Why this priority**: Required by UC06-6 and Principle V, but the list works without it.

**Independent Test**: Override one row on an Action release in `PENDING_APPROVAL` and check the cascade, audit entry, re-run fairness and reasons.

**Acceptance Scenarios**:

1. **Given** an unsaved Action list, **When** the officer sets a household's amount from PKR 15 000 to 0 with reason "Household relocated to Hyderabad before the season", **Then** the row becomes `EXCLUDED`, the freed money funds the next `UNMET_NEED` household in rank order, the reason text is stored, and an audit entry records computed, previous and new amounts plus the cascade counts.
2. **Given** an override without a reason or with fewer than 10 characters, **When** submitted, **Then** it is rejected with `OVERRIDE_REASON_REQUIRED`.
3. **Given** a reason containing a phone number or CNIC, **When** submitted, **Then** it is rejected with `REASON_CONTAINS_PII`.
4. **Given** a saved list, **When** the officer tries to override, **Then** it is refused with `ALLOCATION_LOCKED`.
5. **Given** a DC or donor, **When** they try to override, **Then** the response is 403.

---

### User Story 4 - Action tier stays behind DC approval (Priority: P1)

An Action release gets its ranked list as soon as it is created, so the DC sees the total, but the list is only saved and paid after the DC approves (UC07 precondition, Principle II).

**Why this priority**: Non-negotiable Principle II on a money path.

**Independent Test**: Create an Action release in `PENDING_APPROVAL`; call `allocate`; call `finalize` before and after approval.

**Acceptance Scenarios**:

1. **Given** an Action release awaiting approval, **When** allocation runs, **Then** the ranked list exists, nothing is saved and no payment is reserved.
2. **Given** that release, **When** anything calls `finalize` before approval, **Then** it fails with `APPROVAL_REQUIRED` and nothing changes.
3. **Given** the DC approves, **When** FR8 calls `finalize`, **Then** the list is saved and payments are reserved (if fairness passed or was acknowledged).
4. **Given** the DC rejects, **When** anything calls `finalize`, **Then** it fails with `RELEASE_REJECTED`; the draft list stays for the record.

---

### User Story 5 - Published, configurable rules (Priority: P3)

Anyone signed in, including donors, can read the ranking rules and weights for a district. The officer can change a district's weights; the change is validated, audited and used from the next allocation.

**Why this priority**: Principle V ("published") and IX; defaults work without it.

**Independent Test**: `GET /allocation-rules?district_id=` as donor; `PUT` new weights as officer; rank a new release.

**Acceptance Scenarios**:

1. **Given** default settings, **When** a donor reads the rules, **Then** the formulas, weights, norms, tie-break order and fairness thresholds are returned with no beneficiary data.
2. **Given** the officer submits household weights that sum to 0.9, **When** saved, **Then** the request fails with `WEIGHTS_INVALID` and nothing changes.
3. **Given** valid new weights, **When** saved, **Then** an audit entry stores old and new weights, and the next allocation stores the new `rules_version` in `score_inputs`. Saved allocations are not re-ranked.

---

### Edge Cases

- No eligible beneficiaries in the affected tehsils: no rows; fairness passes with note `NO_FUNDED_ROWS`; release saved with zero payments; the officer sees "register empty for affected tehsils".
- Missing `poverty_score`: imputed with the tehsil median, then district median, then 0.5; listed in `score_inputs.imputed` and lowers completeness.
- Tehsil without its own `RiskIndex`: urgency uses the district `RiskIndex` of the release, flagged `urgency_source=DISTRICT`.
- No tehsil-level `RiskIndex` at all for the run date: all tehsils of the district are eligible (`tehsil_scope=DISTRICT_FALLBACK`).
- A beneficiary already fully covered in an earlier tier of the same event (top-up 0, FR12): not included in this release's list; counted as `already_covered`.
- A beneficiary deactivated after a draft list was made: the row stays until `recompute`; `finalize` drops nothing silently; recompute removes them and is audited.
- Weights changed while a list is unsaved: the unsaved list keeps its `rules_version`; `recompute` applies the new weights.
- Override that pushes pinned amounts above the pool: refused with `OVERRIDE_EXCEEDS_POOL`; nothing changes.
- Re-running `allocate` on the same release (FR7 re-run on the same day): returns the existing rows; no new audit entry.
- Replay run: rows carry the replay `scenario_id`; a failed fairness check is auto-acknowledged as simulated when configured.
- Concurrent overrides on the same release: serialised by a row lock on the `FundRelease`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR11-001**: System MUST select as eligible every active beneficiary in the release district's affected tehsils (tehsils at Watch or above on the release's run date; all district tehsils if no tehsil scores exist).
- **FR11-002**: System MUST compute a household priority score from poverty, family size, flood exposure and urgency, minus an existing-support penalty, using the published `rank-v1` formula and configured weights.
- **FR11-003**: System MUST compute a shopkeeper priority score from stock at risk, flood exposure and urgency, minus an existing-support penalty, using the published `rank-v1` formula and configured weights.
- **FR11-004**: System MUST normalise every factor to 0–1 as published, impute missing optional inputs by documented rules, and record imputations.
- **FR11-005**: System MUST rank households and shopkeepers separately per release, with rank 1 the highest priority, using the published deterministic tie-break order; identical inputs MUST give identical ranks.
- **FR11-006**: System MUST store with each allocation the raw inputs, normalised factors, weights, contributions, rules version, imputations and tie-break values.
- **FR11-007**: System MUST produce a reason for each allocation in Urdu and in English that states the rank, score, main factors with values and the amount basis, and MUST state the override reason when an amount was changed.
- **FR11-008**: System MUST run the `fair-v1` fairness check on every allocation run and every override, store its metrics, and mark it failed when any sparse-data tehsil with an expected share of at least 5% receives less than half its vulnerability-weighted expected share.
- **FR11-009**: System MUST NOT save an allocation whose fairness check failed until a PDMA officer acknowledges the warning with a note; it MUST notify the officer when a list is held.
- **FR11-010**: System MUST automatically save Watch and Warning allocations when the fairness check passes, and hand them to payment reservation in the same transaction.
- **FR11-011**: System MUST NOT save an Action allocation unless the release has a recorded DC approval, and MUST NOT save any allocation for a rejected release.
- **FR11-012**: The PDMA officer MUST be able to change an unsaved allocation's amount with a written reason (10–500 characters, no phone or CNIC), reset an override, and see the re-filled list; overrides on saved lists MUST be refused.
- **FR11-013**: The PDMA officer MUST be able to recompute an unsaved list (keeping overrides on still-eligible beneficiaries).
- **FR11-014**: System MUST publish ranking rules (formulas, weights, norms, tie-breaks, fairness thresholds) to every signed-in role and let the officer change a district's weights with validation (weights in 0–1, positive weights per type summing to 1).
- **FR11-015**: System MUST audit every allocation run, override, override reset, recompute, fairness acknowledgement, save and rules change in the same transaction.
- **FR11-016**: Allocation lists and details MUST be readable by the officer and admin, read-only by the DC of that district, and denied to donors.

### Key Entities

- **Allocation** (§5): one row per beneficiary per release; FR11 sets `rank`, `priority_score`, `score_inputs`, `reason_en`, `reason_ur`, `override_reason`, `overridden_by`; FR12 sets amounts and `status`.
- **FairnessCheck** (§5): one per release; `passed`, `metrics`, `acknowledged_by` (+ proposed `acknowledged_at`, `acknowledgement_note`).
- **FundRelease** (§4): the release being allocated; proposed `allocation_saved_at`, `allocation_saved_by`.
- **DisasterFund** (§4): proposed `ranking_weights` per-district override.
- **Beneficiary** (§5), **RiskIndex** (§3), **Tehsil**/**District** (§1), **Approval** (§4), **AuditLog** (§8).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of allocations have non-empty `reason_en`, `reason_ur` and `score_inputs` from which the stored `priority_score` can be recomputed exactly (NFR4).
- **SC-002**: 200 seeded shuffles of a 1 000-beneficiary register produce identical rankings (Principle V determinism).
- **SC-003**: The fairness check flags 100% of the seeded sparse-underfunded fixtures and 0% of the balanced fixtures (NFR5).
- **SC-004**: 0 Action allocations are saved without an `APPROVED` approval across the test suite and all three replay scenarios (NFR12).
- **SC-005**: `allocate` for 10 000 eligible beneficiaries finishes in under 10 seconds, so tier alerts and payment reservations stay within the 5-minute window (NFR2).
- **SC-006**: Allocation list p95 under 500 ms for a page of 50 (NFR1).
- **SC-007**: Every allocation run, override, acknowledgement and save has exactly one audit entry and the chain verifies (NFR7).

## Assumptions

- `poverty_score` is a 0–1 proxy where 1 is poorest (FR10).
- Tehsil-level `RiskIndex` rows exist for most run dates (FR3).
- Within one district all tehsils share `District.vulnerability_weight`, so the fairness check's expected share is effectively population × risk; the vulnerability term matters once checks span districts (roadmap).
- The PDMA officer is province-wide in the MVP; any officer may override any district's unsaved list.
- Reasons are for officials and the DC; the beneficiary-facing message (FR14) may reuse the Urdu reason's amount sentence only.

## Traceability

| Item | How FR11 covers it |
|---|---|
| UC06-1 | Eligible beneficiaries in affected tehsils |
| UC06-2 | Household score (poverty, family size, exposure; plus urgency and support) |
| UC06-3 | Shopkeeper score (stock at risk, exposure, existing support, urgency) |
| UC06-5 | Ranked list with reasons (amounts from FR12) |
| UC06-6 | Override with written reason |
| UC06-7, UC06-7a | Fairness check, warning before save |
| UC07 precondition | Action list ready before approval; saved only after |
| NFR4 | `score_inputs`, reasons |
| NFR5 | `fair-v1` check |
| NFR6, NFR8 | Role scoping; donor 403 |
| NFR7 | Audit entries |
| NFR12 | Action gate |
| D4 | Ranking per release within one event; earlier-tier coverage excluded via FR12 top-up |
| D5 | `Allocation`, `FairnessCheck` entities |

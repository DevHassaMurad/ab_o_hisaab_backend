---
description: "Task list for FR19 Donor report"
---

# Tasks: FR19 Donor report

**Input**: Design documents from `/specs/019-fr19-donor-report/`

**Prerequisites**: plan.md, spec.md, clarify.md; `000-foundation`, `020-fr20-audit-log` (US1 + US2), `017-fr17-payment-tracking` (models + ledger) merged. `015-fr15-receipt-confirmation` is optional (received = 0 without it).

**Tests**: MANDATORY (Principle XI: donor-endpoint aggregation, no PII leaks). Write each test task first and see it fail.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 headline metrics · US2 breakdowns + counts · US3 no individual data · US4 verify + PDF · US5 stories

---

## Phase 1: Setup

- [ ] T001 Create package `app/services/reporting/` with empty `__init__.py`, `donor_scope.py`, `donor_metrics.py`, `suppression.py`, `stories.py`, `donor_pdf.py`; create `app/schemas/reports.py` and `app/api/v1/reports.py`
- [ ] T002 [P] Add `reportlab` to `[project.dependencies]` in `pyproject.toml` (one line; tell the foundation owner)
- [ ] T003 [P] Add settings to `app/core/config.py`: `DONOR_MIN_CELL_SIZE=5`, `DONOR_STORIES_PATH="data/seeds/donor_stories.yaml"`, `DONOR_PDF_COMPRESS=true`
- [ ] T004 [P] Create `tests/integration/reporting/conftest.py` with the **reference fixture** `donor_world`: donor linked to 2 district funds (50 000 000 PKR each), Watch + Warning releases counted, one Action `PENDING_APPROVAL`, one `REJECTED` release in a third (unlinked) district, ~120 payments across all statuses and both types, confirmations incl. a "2 then 1" sequence and an `UNPARSEABLE`, 10 `UNMET_NEED` allocations, a beneficiary paid in two tiers, and a replay copy of one district under a scenario; plus **canary beneficiaries** (name `CANARY-NAME-7731`, phone `+923001234567`, CNIC `42101-1234567-1`, wallet `03009998877`) with Paid payments; expose `expected` = hand-computed metric values as Decimals

---

## Phase 2: Foundational (blocking)

- [ ] T005 [P] Write failing tests in `tests/unit/reporting/test_suppression.py`: cells with 1–4 reached → amounts/counts null + `suppressed=true`; 0 → shown as zeros; ≥ 5 → shown; exactly one suppressed sibling → next-smallest sibling also suppressed; totals never suppressed; `k=0` disables
- [ ] T006 [P] Write failing tests in `tests/contract/test_donor_schemas.py`: every model in `app/schemas/reports.py` subclasses `DonorScope`; a recursive walk of `model_json_schema()` finds no field from the extended deny-list (see clarify.md); defining a test model with `payment_id` under `DonorScope` raises `TypeError`
- [ ] T007 Implement `suppress_cells(rows, k)` in `app/services/reporting/suppression.py` (makes T005 pass)
- [ ] T008 Implement all response models in `app/schemas/reports.py` (`ScopeOut`, `Totals`, `TypeRow`, `DistrictRow`, `TierRow`, `DonorSummaryOut`, `PaymentCountsOut`, `StoryOut`, `StoriesOut`) inheriting `DonorScope`; money as `Decimal` serialised as strings; extend the deny-list check with FR19's extra names in a module-level validator (makes T006 pass)
- [ ] T009 Implement `DonorScopeSpec` and `resolve_donor_scope(db, user, district_id)` in `app/services/reporting/donor_scope.py`: donor → districts of linked funds (live) or the same districts' scenario funds (replay); officer/admin → all or chosen; DC → `ROLE_FORBIDDEN`; no links → `NO_LINKED_FUNDS`; foreign district → `FUND_NOT_LINKED`

**Checkpoint**: Scope, schemas and suppression are ready.

---

## Phase 3: User Story 1 - Headline metrics (P1) 🎯 MVP

**Goal**: Exact metric totals per the spec definitions.

**Independent Test**: `pytest tests/unit/reporting/test_metric_definitions.py tests/integration/reporting/test_donor_summary.py -k totals`

### Tests for User Story 1 ⚠️

- [ ] T010 [P] [US1] Write failing tests in `tests/unit/reporting/test_metric_definitions.py` (pure functions over in-memory rows): each of FR19-001…FR19-011 against hand-built inputs; identities `allocated = disbursed + in_flight + unused`, `received + not_received + awaiting = disbursed`, `disbursed ≤ released ≤ allocated`; latest-confirmation rule; rates null on zero divisors; rejected and pending-approval releases excluded from released
- [ ] T011 [P] [US1] Write failing tests in `tests/integration/reporting/test_donor_summary.py::test_totals_match_reference` on `donor_world`: every totals field equals `expected`; `ledger_consistent` true; corrupting a fund's `reserved_pkr` in the fixture (test-only) → `ledger_consistent=false`; replay header returns only replay numbers and `mode=REPLAY`; `simulated=true`; quiet scope (no releases) → released 0, unused = allocated

### Implementation for User Story 1

- [ ] T012 [US1] Implement pure aggregation helpers (`compute_totals(...)`, `rates(...)`) and the SQL queries (releases, payments with latest confirmation via `DISTINCT ON`, unmet need) in `app/services/reporting/donor_metrics.py`
- [ ] T013 [US1] Implement `DonorReportService.summary(db, scope)` (totals + ledger cross-check + `as_of` from `Clock.now()` + simulated rule) in `app/services/reporting/donor_metrics.py`
- [ ] T014 [US1] Implement `GET /reports/donor/summary` in `app/api/v1/reports.py` (`require_role(DONOR, PDMA_OFFICER, ADMIN)`, scope resolution, tag `Reporting`) and register the router in `app/api/v1/__init__.py`

---

## Phase 4: User Story 2 - Breakdowns and payment counts (P1)

**Goal**: By district, by type, district × type, by tier; aggregate payment counts.

**Independent Test**: `pytest tests/integration/reporting/test_donor_summary.py -k "breakdown or counts"`

### Tests for User Story 2 ⚠️

- [ ] T015 [P] [US2] Extend `tests/integration/reporting/test_donor_summary.py`: district rows sum to totals for every metric; type rows sum to totals for released pool, disbursed, received, in flight, unmet need, reached (a person paid in two tiers counted once per cell); by-tier rows match; a district × type cell with 3 reached is suppressed and its sibling too; `GET /reports/donor/payment-counts` totals equal FR17 `count_by_status()` for the same funds and sum to `total`

### Implementation for User Story 2

- [ ] T016 [US2] Add grouping sets (district, type, district × type, tier) to the payment query and build `DistrictRow`/`TypeRow`/`TierRow` lists, then apply `suppress_cells` per sibling group, in `app/services/reporting/donor_metrics.py`
- [ ] T017 [US2] Implement `DonorReportService.payment_counts(db, scope)` reusing FR17 `count_by_status()` per district, in `app/services/reporting/donor_metrics.py`, with suppression of district rows
- [ ] T018 [US2] Implement `GET /reports/donor/payment-counts` in `app/api/v1/reports.py`

---

## Phase 5: User Story 3 - Individual data is never shown (P1)

**Goal**: 403 on individual data; no PII anywhere a donor can reach.

**Independent Test**: `pytest tests/contract/test_donor_no_pii.py tests/contract/test_donor_route_sweep.py`

### Tests for User Story 3 ⚠️

- [ ] T019 [P] [US3] Write failing `tests/contract/test_donor_route_sweep.py`: enumerate `app.routes`; for each route compute whether a donor token passes its role dependency (`__roles__`); assert the set equals the allowlist in plan.md; then call every non-allowlisted GET route under `/payments`, `/beneficiaries`, `/allocations`, `/alerts`, `/audit` (list/export) and `/reports/donor/{beneficiaries,payments,payments/{id}}` as donor → 403 (`DONOR_AGGREGATE_ONLY` or `ROLE_FORBIDDEN`)
- [ ] T020 [P] [US3] Write failing `tests/contract/test_donor_no_pii.py` on `donor_world` with canaries: call summary (with and without `district_id`), payment-counts, stories, report.pdf (with `DONOR_PDF_COMPRESS=false`), and `/audit/verify` as donor; assert no canary string, no beneficiary/payment/allocation UUID from the DB, no match of FR20's alphanumeric-bounded `PHONE_RE` / `CNIC_RE` (`app/services/audit/pii_guard.py`) in any JSON body or in the decoded text strings of the PDF; repeat under `X-Scenario-Id`
- [ ] T021 [P] [US3] Write failing test in `tests/integration/reporting/test_donor_scope.py`: donor without contributions → 403 `NO_LINKED_FUNDS`; foreign `district_id` → 403 `FUND_NOT_LINKED`; DC → 403; officer sees all districts; the unlinked district's numbers never appear in the donor's totals

### Implementation for User Story 3

- [ ] T022 [US3] Implement the three denial routes in `app/api/v1/reports.py` returning 403 `DONOR_AGGREGATE_ONLY` with `links`
- [ ] T023 [US3] Add a shared `deny_donor` dependency (raise `DONOR_AGGREGATE_ONLY` for donors) to `app/api/v1/reports.py` and export it for routers that want the specific code (FR17 has its own; keep codes identical)

**Checkpoint**: NFR8 proven; this gates any frontend donor work.

---

## Phase 6: User Story 4 - Verify and PDF (P2)

**Goal**: PDF with verify result and fingerprint; audited.

**Independent Test**: `pytest tests/integration/reporting/test_donor_pdf.py`

### Tests for User Story 4 ⚠️

- [ ] T024 [P] [US4] Write failing tests in `tests/integration/reporting/test_donor_pdf.py`: response is `application/pdf` starting with `%PDF`; `X-Report-SHA256` equals `sha256(canonical_json(summary))` of the same scope; uncompressed bytes contain "SIMULATED DATA", the fingerprint, "Audit chain: VALID" and the head seq; exactly one new `DONOR_REPORT_GENERATED` audit entry with matching `report_sha256`; with a tampered replay chain the PDF contains "BROKEN" and the first broken seq; generation < 5 s; summary `audit_verify_url` present and `GET /audit/verify` result equals the PDF's

### Implementation for User Story 4

- [ ] T025 [US4] Implement `report_fingerprint(summary)` (FR20 `canonical_json` over `summary.model_dump(mode="json")`) and `render_donor_pdf(summary, stories, verify, *, compress)` (ReportLab `SimpleDocTemplate`, tables, banner on every page via `onPage`, footer with fingerprint) in `app/services/reporting/donor_pdf.py`
- [ ] T026 [US4] Implement `GET /reports/donor/report.pdf` in `app/api/v1/reports.py`: build summary + stories, `verify_chain(db, current_chain_key())`, render, record `DONOR_REPORT_GENERATED` and commit, return bytes with headers

---

## Phase 7: User Story 5 - Outcome stories (P3)

**Goal**: Curated, labelled stories in the MVP; FR22-backed when flagged.

**Independent Test**: `pytest tests/unit/reporting/test_stories.py`

### Tests for User Story 5 ⚠️

- [ ] T027 [P] [US5] Write failing tests in `tests/unit/reporting/test_stories.py`: loader accepts the shipped YAML; rejects a story with a phone-like or CNIC-like number, > 600 chars, missing `label`, `simulated` false with `source: CURATED`; filters by district pcodes and optional `scenario_key`; with `FR22_OUTCOME` on and a fake `OutcomeStoryProvider`, aggregated stories with `group_size < 5` are dropped

### Implementation for User Story 5

- [ ] T028 [P] [US5] Write `data/seeds/donor_stories.yaml`: 2–3 stories each for Dadu (sindh-2022) and Buner (buner-2025), EN + UR, roles not names, `simulated: true`, `source: CURATED`, `label: "Illustrative story (simulated)"`
- [ ] T029 [US5] Implement `StoryProvider` Protocol, `load_curated_stories()`, `CuratedStoryProvider`, `OutcomeStoryProvider` stub (returns `[]` until FR22) and `get_story_provider()` (flag switch) in `app/services/reporting/stories.py`
- [ ] T030 [US5] Implement `GET /reports/donor/stories` in `app/api/v1/reports.py`

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T031 [P] Performance test `tests/integration/reporting/test_donor_summary.py::test_perf_50k` (`@pytest.mark.slow`): 50 000 payments, summary < 500 ms; add index notes to the PR if needed (FR17 owns payment indexes)
- [ ] T032 [P] Add an OpenAPI description block with the metric definitions (copy of spec FR19-001…010) to the summary route's `description` in `app/api/v1/reports.py`
- [ ] T033 Run `ruff check app/services/reporting app/api/v1/reports.py app/schemas/reports.py` and `mypy --strict app/services/reporting`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup → Foundational** → US1 → US2; US3 tests can be written as soon as Phase 2 is done and must pass before the frontend consumes donor routes.
- **US4** needs US1 (summary object) and FR20 verify.
- **US5** is independent after Phase 2 (stories endpoint) but the PDF (US4) includes stories, so do T029 before T026 or pass `[]`.
- **Polish** last.

### Within Each User Story

- Tests first and failing; pure helpers → SQL → service → router.

## Parallel opportunities

- T002, T003, T004 in Setup; T005, T006 in Phase 2.
- T010, T011 in parallel; T019, T020, T021 in parallel; T024, T027, T028 in parallel; T031, T032 in parallel.

```bash
pytest tests/unit/reporting -q
pytest tests/integration/reporting -q -m "not slow"
pytest tests/contract/test_donor_no_pii.py tests/contract/test_donor_route_sweep.py tests/contract/test_donor_schemas.py -q
```

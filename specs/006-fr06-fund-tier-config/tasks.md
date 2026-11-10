---
description: "Task list for FR6 Fund and Tier Configuration"
---

# Tasks: FR6 Fund and Tier Configuration

**Input**: Design documents from `/specs/006-fr06-fund-tier-config/`

**Prerequisites**: plan.md, spec.md, clarify.md; 000-foundation merged (models, auth, AuditService stub)

**Tests**: MANDATORY. Settings drive the release maths (Principle XI). Write each test first and watch it fail.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: User story from spec.md (US1–US4)

---

## Phase 1: Setup

- [ ] T001 Create package `app/services/triggers/__init__.py` and test folders `tests/unit/triggers/`, `tests/integration/triggers/`
- [ ] T002 [P] Add fund/tier fixtures `make_fund(district, **overrides)`, `make_tiers(fund, thresholds, increments)`, `dc_headers(district)` in `tests/integration/triggers/conftest.py`
- [ ] T003 [P] Confirm `fund_defaults` in `data/seeds/funds.yaml` match plan.md; load them into `settings.fund_defaults` via foundation `app/core/config.py` (read only; raise a foundation PR if the key is missing)

---

## Phase 2: Foundational (shared tier helpers)

**⚠️ Blocks FR3 tier mapping and FR7 firing.**

- [ ] T004 [P] Write failing boundary tests (39→NONE, 40→WATCH, 59→WATCH, 60→WARNING, 79→WARNING, 80→ACTION, 100→ACTION, 0→NONE, custom 30/55/90) and `cumulative_pct` tests (10/40/100) in `tests/unit/triggers/test_tier_for_score.py`
- [ ] T005 Implement `TierRule`, `TierConfig`, `TIER_ORDER`, `tier_rank`, `tier_for_score`, `load_tier_config` in `app/services/triggers/tiers.py`

**Checkpoint**: FR3 and FR7 can import the tier helpers.

---

## Phase 3: User Story 1 - Configure a district's fund and tiers (P1) 🎯 MVP

**Goal**: An officer reads and saves valid settings atomically, with one audit entry.

**Independent Test**: PUT a valid form for Dadu; fund, tiers, version and one audit entry are correct.

### Tests for User Story 1 ⚠️

- [ ] T006 [P] [US1] Contract tests for GET/PUT response shapes, string decimals, `cumulative_pct`, `shopkeeper_share_pct` derived in `tests/contract/test_fund_settings_contract.py`
- [ ] T007 [P] [US1] Integration test: valid PUT stores fund + tiers, bumps version, returns `changed_fields` in `tests/integration/triggers/test_fund_settings_api.py`
- [ ] T008 [P] [US1] Integration test: exactly one `FUND_SETTINGS_UPDATED` entry with before/after; no-op PUT writes nothing; audit failure (monkeypatched to raise) rolls back the fund change in `tests/integration/triggers/test_fund_settings_audit.py`
- [ ] T009 [P] [US1] Test `requires_approval` in the body → 422 `REQUIRES_APPROVAL_LOCKED`, and Action stays `true` after any save, in `tests/integration/triggers/test_fund_settings_api.py`
- [ ] T010 [P] [US1] Test first save for a district without a fund (`version: 0`) creates fund + 3 tiers with `requires_approval` only on ACTION, in `tests/integration/triggers/test_fund_settings_api.py`
- [ ] T011 [P] [US1] Test `X-Scenario-Id` edits only that scenario's fund, in `tests/integration/triggers/test_fund_settings_api.py`

### Implementation for User Story 1

- [ ] T012 [P] [US1] Create schemas `TierIn`, `TierOut`, `FundSettingsIn`, `FundSettingsOut`, `FieldError` in `app/schemas/fund_settings.py`
- [ ] T013 [US1] Implement `FundSettingsService.get_settings` and `update_settings` (lock fund row, diff, apply, recompute `remaining_pkr`, bump version, audit) in `app/services/triggers/fund_settings.py`
- [ ] T014 [US1] Add `GET` and `PUT /districts/{district_id}/fund-settings` with `require_role(PDMA_OFFICER)` on PUT in `app/api/v1/settings.py` and register the router in `app/api/v1/__init__.py`

**Checkpoint**: Valid configuration works end to end.

---

## Phase 4: User Story 2 - Invalid settings rejected, nothing saved (P1)

**Goal**: Every rule in plan.md "Validation rules" returns its code; all errors at once; zero writes.

**Independent Test**: Parametrised invalid bodies leave fund, tiers, version and audit count unchanged.

### Tests for User Story 2 ⚠️

- [ ] T015 [P] [US2] Parametrised unit tests, one case per code (`TIER_SET_INVALID`, `TIER_THRESHOLD_RANGE_INVALID`, `TIER_THRESHOLD_ORDER_INVALID`, `TIER_PCT_RANGE_INVALID`, `TIER_PCT_SUM_INVALID`, `FUND_AMOUNT_INVALID`, `FUND_BELOW_COMMITTED`, `SPLIT_PCT_INVALID`, `SHOPKEEPER_CAP_INVALID`, `STOCK_PCT_INVALID`, `HOUSEHOLD_AMOUNTS_INVALID`, `EVENT_CLOSE_DAYS_INVALID`), plus a multi-error case, in `tests/unit/triggers/test_fund_settings_validation.py`
- [ ] T016 [P] [US2] Integration test: 422 response shape with `errors[]`, and DB + audit chain length unchanged, in `tests/integration/triggers/test_fund_settings_api.py`
- [ ] T017 [P] [US2] Integration test: stale `version` → 409 `VERSION_CONFLICT`, first save kept, in `tests/integration/triggers/test_fund_settings_api.py`

### Implementation for User Story 2

- [ ] T018 [US2] Implement `validate_fund_settings(body, fund)` in `app/services/triggers/validation.py`; call FR9 `validate_split` when available, else the inline 0–100 check with code `SPLIT_PCT_INVALID`
- [ ] T019 [US2] Raise `FundSettingsInvalid(errors)` from `update_settings` and map it to 422 problem JSON with `errors[]` in `app/api/v1/settings.py`

**Checkpoint**: UC01-5a covered.

---

## Phase 5: User Story 3 - Copy settings from another district (P2)

### Tests for User Story 3 ⚠️

- [ ] T020 [P] [US3] Integration test: draft copies rules but keeps target amount and version; `include_amount=true` copies amount; no rows or audit written; 404 `SOURCE_FUND_NOT_FOUND`; 422 `COPY_SOURCE_SAME_AS_TARGET`, in `tests/integration/triggers/test_fund_settings_api.py`

### Implementation for User Story 3

- [ ] T021 [US3] Implement `FundSettingsService.build_copy_draft` in `app/services/triggers/fund_settings.py`
- [ ] T022 [US3] Add `GET /districts/{district_id}/fund-settings/draft` in `app/api/v1/settings.py`

---

## Phase 6: User Story 4 - Read-only views by role (P2)

### Tests for User Story 4 ⚠️

- [ ] T023 [P] [US4] Role matrix test (officer, admin, DC own/other district, donor) for all 4 routes in `tests/integration/triggers/test_fund_settings_api.py`
- [ ] T024 [P] [US4] Test list endpoint paging, province filter, `open_event_tier` in `tests/integration/triggers/test_fund_settings_api.py`

### Implementation for User Story 4

- [ ] T025 [US4] Implement `FundSettingsService.list_settings` and DC scoping in `app/services/triggers/fund_settings.py`
- [ ] T026 [US4] Add `GET /fund-settings` and DC scope checks in `app/api/v1/settings.py`

---

## Phase 7: Polish

- [ ] T027 [P] Add `FUND_SETTINGS_UPDATED` to `app/services/audit/event_types.py` (FR20 registry; one-line PR)
- [ ] T028 [P] Timing check: GET p95 < 500 ms over 68 seeded districts in `tests/integration/triggers/test_fund_settings_api.py` (marked `@pytest.mark.perf`)
- [ ] T029 Run `ruff`, `mypy --strict app/services/triggers`, and the full FR6 test selector

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 → US2. US2 builds on the PUT path from US1.
- US3 and US4 depend only on US1 and can run in parallel after it.
- Phase 2 (tier helpers) unblocks FR3 and FR7 early; merge it first.
- Within each story: tests (fail) → schemas → service → router.

## Parallel opportunities

- T002, T003 together.
- T006–T011 (all US1 tests) together; T012 alongside them.
- T015–T017 together.
- US3 (T020–T022) and US4 (T023–T026) in parallel by two people.
- T027, T028 together.

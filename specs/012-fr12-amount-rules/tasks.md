---
description: "Task list for FR12 Amount Rules"
---

# Tasks: FR12 Amount Rules

**Input**: Design documents from `/specs/012-fr12-amount-rules/`

**Prerequisites**: plan.md, spec.md, clarify.md; FR11 dataclasses (`RankedCandidate`, `AllocationContext`) in `app/services/allocation/context.py`; FR6/FR9 fund and pool fields (use factories until merged)

**Tests**: **MANDATORY and FIRST** (Principle XI: amount calculation, caps, budget-short path). Commit each test task failing before its implementation task.

**Organization**: US1 household amounts + US4 top-ups (shared code), US2 shopkeeper cap, US3 fill/unmet, US5 overrides, US6 summary.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Create `app/services/allocation/amounts.py`, `fill.py`, `summary.py` with module docstrings and `__all__`
- [ ] T002 [P] Add settings `AMOUNT_ROUNDING_STEP_PKR`, `ALLOCATION_FILL_MODE`, `HOUSEHOLD_TIER_MODE`, `SHOPKEEPER_TIER_MODE`, `ALLOCATION_POOL_SPILLOVER` in `app/core/config.py`

---

## Phase 2: Foundational

- [ ] T003 Write migration `alembic/versions/0012_fr12_amount_status.py` adding `EXCLUDED` to the `allocation_status` enum (`ALTER TYPE … ADD VALUE`, outside a transaction block per Postgres rules)
- [ ] T004 [P] Define dataclasses `Target`, `PoolFill`, `ReleaseFill`, `FillRow` in `app/services/allocation/amounts.py` and `app/services/allocation/fill.py`
- [ ] T005 [P] Implement `round_pkr(amount, step=Decimal("100"), mode=ROUND_HALF_UP)` and `floor_pkr(amount, step)` in `app/services/allocation/amounts.py` with doctests

---

## Phase 3: User Stories 1 + 4 - Household flat amount and repeated-tier top-ups (P1) 🎯 MVP

### Tests (mandatory, write first) ⚠️

- [ ] T006 [P] [US1] Unit tests `household_target`: tier lookup, 0 allowed, missing tier → 0 + `AMOUNT_TIER_MISSING`, 3 050 → 3 100 + `AMOUNT_ROUNDED`; `round_pkr` table (24 493.6 → 24 500, 24 450 → 24 500, 24 449.99 → 24 400, 0 → 0) in `tests/unit/allocation/test_amounts.py`
- [ ] T007 [P] [US4] Unit tests `topup`: prior 0/3 000/7 000/20 000 against targets; never negative; shopkeeper `EVENT_ONCE` semantics in `tests/unit/allocation/test_topup.py`
- [ ] T008 [P] [US4] Integration tests across one event: Watch → Warning → Action pays H 3 000/4 000/8 000; J unmet at Watch gets 7 000 at Warning; S funded at Watch absent later; cancelled Watch payment → full Warning amount; failed (not cancelled) counts as prior; rejected Action release ignored; new event resets; same-day tier jump allocates in order in `tests/integration/allocation/test_repeated_tiers.py`

### Implementation

- [ ] T009 [US1] Implement `household_target(fund, tier) -> tuple[Decimal, list[str]]` in `app/services/allocation/amounts.py`
- [ ] T010 [US4] Implement `prior_event_amounts(db, fund_release, beneficiary_ids)` (one aggregate query: same `event_id`, other releases, release not REJECTED, allocation FUNDED, payment missing or not CANCELLED) and `compute_targets(db, ctx, candidates) -> dict[UUID, Target]` in `app/services/allocation/amounts.py` (depends on T009, T012)

**Checkpoint**: household amounts and top-ups correct.

---

## Phase 4: User Story 2 - Shopkeeper capped grant (P1)

### Tests (mandatory, write first) ⚠️

- [ ] T011 [P] [US2] Unit tests `shopkeeper_target`: 61 234 → 24 500; 125 000 → 50 000 (`capped` false at exactly cap); 200 000 → 50 000 `capped=true`; 120 → 0; 125 → 100; cap 50 050 → floored 50 000; custom stock % 25 in `tests/unit/allocation/test_amounts.py`

### Implementation

- [ ] T012 [US2] Implement `shopkeeper_target(fund, stock_value_pkr) -> tuple[Decimal, dict]` (raw, capped flag, warnings) in `app/services/allocation/amounts.py`

---

## Phase 5: User Story 3 - Rank-order fill and UNMET_NEED (P1)

### Tests (mandatory, write first) ⚠️

- [ ] T013 [P] [US3] Unit tests `fill_pool`/`fill_release`: spec US3-1 (no spillover) and US3-2 (with spillover) exact statuses and amounts; zero pool; pool not multiple of 100; empty lists; pinned rows deducted first; `OVERRIDE_EXCEEDS_POOL` when pinned > pool in `tests/unit/allocation/test_fill.py`
- [ ] T014 [P] [US3] Property test: 500 seeded random releases (random pools, 0–300 rows, random amounts multiples of 100, random pins) → invariants (funded ≤ pool + spill in; funded unpinned rows are a rank prefix per pool; multiples of 100; non-negative; unused = total pools − funded − pinned) in `tests/unit/allocation/test_fill.py::test_invariants`
- [ ] T015 [P] [US3] Integration test budget-short through `AllocationServiceImpl.allocate`: Dadu Action draft with pool smaller than need → top N funded, rest `UNMET_NEED` with would-be amounts, `score_inputs.fill` present, unspent not reserved in `tests/integration/allocation/test_budget_short.py`

### Implementation

- [ ] T016 [US3] Implement `fill_pool(rows, pool_pkr, *, pinned_pkr, mode, start_index=0)` and `fill_release(households, shopkeepers, *, household_pool, shopkeeper_pool, spillover, mode) -> ReleaseFill` (pass 1, HH→SK continuation, SK→HH continuation) in `app/services/allocation/fill.py`

---

## Phase 6: User Story 5 - Override limits and re-fill (P2)

### Tests (mandatory, write first) ⚠️

- [ ] T017 [P] [US5] Unit/integration tests: `override_limit` household (15 000 − prior), shopkeeper (cap − prior), floor 0; `refill_release` after override to 0 funds next unmet; pinned increase that fits; pinned sum > pool raises `OverrideExceedsPool`; `EXCLUDED` rows untouched by re-fill in `tests/unit/allocation/test_fill.py::test_refill_*` and `tests/integration/allocation/test_budget_short.py::test_override_*`

### Implementation

- [ ] T018 [US5] Implement `override_limit(db, fund_release, allocation) -> Decimal` in `app/services/allocation/amounts.py`
- [ ] T019 [US5] Implement `refill_release(db, fund_release) -> ReleaseFill` (load rows by rank, pinned = `overridden_by IS NOT NULL`, re-run `fill_release` on unpinned using stored `computed_amount_pkr`, write `status`/`final_amount_pkr`/`score_inputs.fill`) in `app/services/allocation/fill.py`

---

## Phase 7: User Story 6 - Summary and published amount rules (P2)

### Tests ⚠️

- [ ] T020 [P] [US6] Contract tests: summary shape and numbers for the US3-2 fixture; money as strings; DC own district OK, other district 404; donor 403; `AmountRulesOut` embedded in `GET /allocation-rules` in `tests/contract/test_allocation_summary_api.py`

### Implementation

- [ ] T021 [US6] Implement `release_summary(db, fund_release) -> AllocationSummaryOut` (aggregate SQL by type × status; spillover from rows' `score_inputs.fill`; `already_covered` from the `ALLOCATION_COMPUTED` audit payload) in `app/services/allocation/summary.py`
- [ ] T022 [P] [US6] Implement `amount_rules(fund) -> AmountRulesOut` in `app/services/allocation/amounts.py` and schemas `AllocationSummaryOut`, `PoolSummaryOut`, `AmountRulesOut` in `app/schemas/allocations.py`
- [ ] T023 [US6] Add `GET /fund-releases/{release_id}/allocations/summary` in the `# --- FR12 ---` section of `app/api/v1/allocations.py`

---

## Phase 8: Polish

- [ ] T024 [P] Performance: 10 000 rows targets + fill < 2 s in `tests/unit/allocation/test_fill.py::test_perf` (`@pytest.mark.slow`)
- [ ] T025 mypy strict + ruff on `amounts.py`, `fill.py`, `summary.py`; grep guard test that no `float(` appears in those modules in `tests/unit/allocation/test_amounts.py::test_no_float_in_money_modules`
- [ ] T026 Walk the implement.md verification checklist with FR11 on a `sindh-2022` replay

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup → Foundational → US2 (T012) → US1/US4 (T009, T010) → US3 (T016) → US5 → US6 → Polish.
- FR11 T019 (`allocate`) needs T010 and T016. FR11 T033 (`override`) needs T018 and T019.
- T008 and T015 need FR11's `allocate` to exist; until then they run against `compute_targets` + `fill_release` directly with a thin harness, then are switched to `allocate`.

### Within each story

- Tests first and failing; pure functions before DB functions; DB before routes.

### Parallel Opportunities

- T002 with T001. T004, T005 in parallel.
- T006, T007, T011, T013, T014 all in parallel (pure unit tests, different test functions/files).
- T022 in parallel with T021.

```bash
pytest tests/unit/allocation/test_amounts.py tests/unit/allocation/test_topup.py tests/unit/allocation/test_fill.py -q
```

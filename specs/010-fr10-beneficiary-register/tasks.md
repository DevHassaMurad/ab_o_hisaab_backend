---
description: "Task list for FR10 Beneficiary Register"
---

# Tasks: FR10 Beneficiary Register

**Input**: Design documents from `/specs/010-fr10-beneficiary-register/`

**Prerequisites**: plan.md, spec.md, clarify.md; `000-foundation` merged (crypto, security, models, seeds)

**Tests**: Included. FR10 is not a money path, but contract tests protect the CSV format, RBAC and encryption (Principle XI, "tests where they protect a contract"). Write them first and see them fail.

**Organization**: Grouped by user story (US1 CSV import, US2 synthetic generator, US3 CRUD, US4 RBAC/masking, US5 exposure/stats).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Create package `app/services/beneficiaries/__init__.py` and empty modules `register.py`, `validation.py`, `csv_import.py`, `exposure.py`, `synthetic.py`, `masking.py`, `stats.py`
- [ ] T002 [P] Add settings keys `EXPOSURE_WEIGHTS`, `HOUSEHOLD_SIZE_BY_PROVINCE`, `REGISTER_IMPORT_MAX_ROWS`, `REGISTER_IMPORT_MAX_MB`, `SYNTH_CONFIG_PATH`, `SYNTH_DEFAULT_SEED`, `DEMO_PERSONAS_PATH` to `app/core/config.py` (coordinate one PR hunk with foundation owner)
- [ ] T003 [P] Add `data/seeds/demo_personas.yaml` to `.gitignore`; commit `data/seeds/demo_personas.example.yaml` with placeholder phones

---

## Phase 2: Foundational (blocks all stories)

- [ ] T004 Write migration `alembic/versions/0010_fr10_beneficiary_register.py`: add `register_ref` (unique), `cnic_hash`, `is_active`, `deactivated_at`, `deactivated_reason`, `exposure_inputs`; partial unique index `(type, cnic_hash) WHERE cnic_hash IS NOT NULL`; indexes `(tehsil_id, type, is_active)`, `phone_hash`, `simulated`
- [ ] T005 Add the proposed fields to `app/models/beneficiaries.py` (after the foundation owner merges them into data-model.md)
- [ ] T006 [P] Write `BeneficiaryFactory` (household and shopkeeper traits) in `tests/factories/beneficiaries.py`
- [ ] T007 [P] Implement `mask_phone`, `mask_cnic`, `mask_wallet` in `app/services/beneficiaries/masking.py`
- [ ] T008 Define Pydantic schemas `BeneficiaryCreate`, `BeneficiaryUpdate`, `BeneficiaryOut` (masked, `simulated`), `RevealIn/RevealOut`, `DeactivateIn`, `ImportReport`, `RowError`, `SyntheticRequest`, `SyntheticReport`, `RegisterStatsOut` in `app/schemas/beneficiaries.py`
- [ ] T009 Create router skeleton with `require_role` guards and DC district scoping helper in `app/api/v1/beneficiaries.py`; register it in `app/api/v1/__init__.py`

**Checkpoint**: schema migrated, router mounted, factories ready.

---

## Phase 3: User Story 1 - Load a register from CSV (P1) 🎯 MVP

**Goal**: Officer imports a validated CSV with encrypted PII and an audit entry.

**Independent Test**: import `data/seeds/beneficiaries_sample.csv` (dry run, then real), list results.

### Tests for User Story 1 ⚠️ write first, must fail

- [ ] T010 [P] [US1] Unit tests for `validate_pk_mobile` (all accepted forms, landline, foreign, short, Urdu digits rejected) and `normalise_cnic` in `tests/unit/beneficiaries/test_validation.py`
- [ ] T011 [P] [US1] Unit tests for `parse_csv`: BOM, CRLF, Urdu names, unknown column, missing column, duplicate `register_ref`, type-specific required fields, wallet rules, coordinate rules in `tests/unit/beneficiaries/test_csv_parse.py`
- [ ] T012 [P] [US1] Integration tests: strict mode saves nothing on one bad row; `skip_rows` saves the rest; `dry_run` saves and audits nothing; re-import updates not duplicates; `DUPLICATE_CNIC`; one `REGISTER_IMPORTED` audit entry in `tests/integration/beneficiaries/test_csv_import.py`
- [ ] T013 [P] [US1] Integration test: raw SQL shows ciphertext for name/phone/cnic/wallet_id; `phone_hash` equals `app.core.crypto.phone_hash(normalised)`; audit payloads contain no phone/CNIC/name patterns in `tests/integration/beneficiaries/test_pii_encryption.py`

### Implementation for User Story 1

- [ ] T014 [US1] Implement `validate_pk_mobile`, `normalise_cnic`, `validate_wallet`, `validate_row(row: dict, ctx: ValidationContext) -> RowResult` in `app/services/beneficiaries/validation.py`
- [ ] T015 [US1] Implement `parse_csv(stream, *, max_rows) -> Iterator[RowResult]` (header checks, row numbering from 2) in `app/services/beneficiaries/csv_import.py`
- [ ] T016 [US1] Implement `import_register(db, upload, *, dry_run, on_error, actor) -> ImportReport` with per-district advisory lock, upsert by `register_ref`, CNIC duplicate check, exposure derivation call, one audit entry in `app/services/beneficiaries/csv_import.py` (depends on T014, T015, T032)
- [ ] T017 [US1] Add `POST /beneficiaries/import` and `GET /beneficiaries/import/template` in `app/api/v1/beneficiaries.py`
- [ ] T018 [P] [US1] Write `data/seeds/beneficiaries_sample.csv` (20 households, 5 shopkeepers in Dadu tehsils, one row per wallet provider, all marked as sample data with `SAMPLE-` refs)

**Checkpoint**: CSV import works end to end.

---

## Phase 4: User Story 2 - Synthetic register (P1)

**Goal**: Deterministic, labelled, realistic simulated register for replay districts.

**Independent Test**: generate twice with seed 2022; compare; check distributions.

### Tests for User Story 2 ⚠️

- [ ] T019 [P] [US2] Unit tests: same seed → identical rows and ids; different seed → different; per-tehsil counts proportional to population (±1); mean household size Sindh within ±0.3; no-wallet share within ±3 pp; ~2% wallet ids end in `000`; all rows `simulated=true` and `SYN-` refs in `tests/unit/beneficiaries/test_synthetic.py`
- [ ] T020 [P] [US2] Integration test: `POST /beneficiaries/synthetic` twice → second run creates 0, updates 0; one `REGISTER_GENERATED` audit entry per run; personas from a temp personas file are created with their phones in `tests/integration/beneficiaries/test_register_crud.py::test_synthetic_*`

### Implementation for User Story 2

- [ ] T021 [P] [US2] Write `data/seeds/synthetic_register.yaml` (province distributions, shop type mix, sample rate, invalid wallet rate, all values labelled approximate with sources)
- [ ] T022 [P] [US2] Write `data/seeds/synthetic_names.yaml` (Sindhi, Saraiki, Pashto and Urdu given names and family names, male/female, Urdu script and Latin)
- [ ] T023 [US2] Implement `load_synth_config`, `generate_register(db, *, district_pcodes, sample_rate, seed, include_demo_personas, actor) -> SyntheticReport` in `app/services/beneficiaries/synthetic.py`
- [ ] T024 [US2] Add `POST /beneficiaries/synthetic` (accepts `scenario_key`, resolved through `ReplayScenario.districts`) in `app/api/v1/beneficiaries.py`
- [ ] T025 [US2] Write CLI `scripts/generate_synthetic_register.py` (`--districts`, `--scenario`, `--seed`, `--sample-rate`, `--personas`) calling `generate_register`

**Checkpoint**: demo register can be produced reproducibly.

---

## Phase 5: User Story 3 - Maintain individual records (P2)

**Goal**: create, update, deactivate one beneficiary.

### Tests for User Story 3 ⚠️

- [ ] T026 [P] [US3] Contract tests for POST/GET/PATCH/DELETE request and response shapes, money as string decimals, error codes `REGISTER_REF_EXISTS`, `FIELD_NOT_ALLOWED_FOR_TYPE`, `IMMUTABLE_FIELD`, `REASON_REQUIRED` in `tests/contract/test_beneficiaries_api.py`
- [ ] T027 [P] [US3] Integration tests: phone update re-hashes and resets `phone_valid`; deactivation keeps row; inactive rows excluded by `eligible_query`; audit entries list changed field names only in `tests/integration/beneficiaries/test_register_crud.py`

### Implementation for User Story 3

- [ ] T028 [US3] Implement `create_beneficiary`, `update_beneficiary`, `deactivate_beneficiary`, `get_beneficiary`, `list_beneficiaries`, `eligible_query` in `app/services/beneficiaries/register.py`
- [ ] T029 [US3] Add `POST /beneficiaries`, `GET /beneficiaries`, `GET /beneficiaries/{id}`, `PATCH /beneficiaries/{id}`, `DELETE /beneficiaries/{id}` in `app/api/v1/beneficiaries.py`

---

## Phase 6: User Story 5 - Exposure and statistics (P3, needed by US1 import)

> Exposure derivation is called by import (T016), so T032 is built before T016 even though the story is P3.

### Tests for User Story 5 ⚠️

- [ ] T030 [P] [US5] Unit tests for `derive_exposure`: worked example (E=0.9, R=0.85, H=0.667 → 0.824), missing components → 0.5 and listed in `imputed`, single-tehsil district → E=0.5, satellite history fallback in `tests/unit/beneficiaries/test_exposure.py`
- [ ] T031 [P] [US5] Unit/integration test for `tehsil_register_stats`: coverage, relative coverage (median), completeness, zero-population tehsil in `tests/unit/beneficiaries/test_exposure.py::test_stats_*`

### Implementation for User Story 5

- [ ] T032 [US5] Implement `derive_exposure(inputs: ExposureInputs, ctx: TehsilContext, weights) -> tuple[float, dict]` and `recompute_exposure(db, district_id, actor)` in `app/services/beneficiaries/exposure.py`
- [ ] T033 [US5] Implement `tehsil_register_stats(db, tehsil_ids) -> dict[UUID, TehsilRegisterStats]` in `app/services/beneficiaries/stats.py`
- [ ] T034 [US5] Add `POST /beneficiaries/exposure/recompute` and `GET /beneficiaries/stats` in `app/api/v1/beneficiaries.py`

---

## Phase 7: User Story 4 - RBAC and masking (P2)

### Tests for User Story 4 ⚠️

- [ ] T035 [P] [US4] RBAC matrix test: every route × {PDMA_OFFICER, DC Dadu, DC Buner, DONOR, ADMIN}; donor 403 everywhere; DC cross-district detail 404; DC list filtered in `tests/integration/beneficiaries/test_rbac.py`
- [ ] T036 [P] [US4] Unit tests for masks (`+92300*****67`, `*****-*******-3`, `*******5678`, null-safe) in `tests/unit/beneficiaries/test_masking.py`
- [ ] T037 [P] [US4] Integration test: reveal returns decrypted PII for officer, 403 for DC/admin/donor, writes `PII_REVEALED` in `tests/integration/beneficiaries/test_rbac.py::test_reveal_*`

### Implementation for User Story 4

- [ ] T038 [US4] Implement `reveal_pii(db, beneficiary_id, *, purpose, actor)` in `app/services/beneficiaries/register.py` and `POST /beneficiaries/{id}/reveal` in `app/api/v1/beneficiaries.py`
- [ ] T039 [US4] Add a structlog processor (or reuse foundation's) that drops keys `name`, `phone`, `cnic`, `wallet_id` from log events; verify in `tests/integration/beneficiaries/test_pii_encryption.py::test_logs_have_no_pii`

---

## Phase 8: Polish & cross-cutting

- [ ] T040 [P] Performance check: seed 50k rows, assert list p95 < 500 ms and 10k import < 60 s in `tests/integration/beneficiaries/test_csv_import.py::test_perf_*` (marked `@pytest.mark.slow`)
- [ ] T041 [P] OpenAPI tag `Beneficiaries`, examples on every route in `app/api/v1/beneficiaries.py`
- [ ] T042 ruff + mypy strict on `app/services/beneficiaries/`
- [ ] T043 Run implement.md verification checklist

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (T001–T003) → Foundational (T004–T009) → stories.
- T032 (exposure) must land before T016 (import) and T023 (generator).
- US1 and US2 are both P1; US2 depends only on T032 and T028's `create`/upsert helper (shared `_upsert_beneficiary` in `register.py`).
- US4 tests can be written any time after T009; the implementation is mostly guards already in T009/T029.

### Within Each User Story

- Tests first, failing. Validation/services before routes. Audit calls inside the same transaction as the write.

### Parallel Opportunities

- T002, T003 in parallel. T006, T007 in parallel.
- All `[P]` tests in a story in parallel (different files).
- T021, T022 (YAML seeds) in parallel with T014–T016.
- US4 tests (T035–T037) in parallel with US3 implementation.

```bash
# Parallel example: US1 tests
pytest tests/unit/beneficiaries/test_validation.py tests/unit/beneficiaries/test_csv_parse.py -q
```

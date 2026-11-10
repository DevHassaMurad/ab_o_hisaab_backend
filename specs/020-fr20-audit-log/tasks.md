---
description: "Task list for FR20 Tamper-proof audit log"
---

# Tasks: FR20 Tamper-proof audit log

**Input**: Design documents from `/specs/020-fr20-audit-log/`

**Prerequisites**: plan.md, spec.md, clarify.md; `000-foundation` merged

**Tests**: MANDATORY (Principle XI: audit hash chaining and verification). Write each test task first and watch it fail before implementing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 chained record · US2 verify · US3 immutability · US4 scenario chains · US5 list/export

---

## Phase 1: Setup

- [ ] T001 Create package `app/services/audit/__init__.py` and empty modules `canonical.py`, `chain.py`, `service.py`, `verify.py`, `pii_guard.py`, `event_types.py`, `export.py`
- [ ] T002 [P] Add settings keys `AUDIT_LOCK_NAMESPACE=2020`, `AUDIT_VERIFY_BATCH=5000`, `AUDIT_LIST_MAX_PAGE_SIZE=500`, `ALLOW_TAMPER_DEMO=false`, `TAMPER_DEMO_DATABASE_URL=""` to `app/core/config.py` (append-only edit in the Settings class)
- [ ] T003 [P] Create test folders `tests/unit/audit/`, `tests/integration/audit/` and `tests/integration/audit/conftest.py` with a `committing_session` fixture (real commits on a dedicated schema, dropped at teardown, because the append-only trigger forbids DELETE/TRUNCATE cleanup)

---

## Phase 2: Foundational (blocking for all stories)

- [ ] T004 [P] Write failing tests in `tests/unit/audit/test_canonical_json.py`: key sorting, no whitespace, Urdu text unescaped, `Decimal("15000.00")` → `"15000.00"`, UUID/date/datetime/Enum forms, tuple → list, NaN/inf/`1e15`/NUL/non-str key/unknown type → `AuditPayloadError`, idempotence `canonical_json(json.loads(canonical_json(x))) == canonical_json(x)`
- [ ] T005 [P] Write failing tests in `tests/unit/audit/test_hash.py`: `format_ts` always 27 chars with `Z`; `compute_hash` matches a hard-coded known vector; changing any envelope field or the timestamp changes the hash; genesis is 64 zeros
- [ ] T006 [P] Write failing tests in `tests/unit/audit/test_pii_guard.py`: keys `name`, `full_name`, `phone`, `cnic`, `wallet_id`, `email`, `lat`, `lon`, `otp`, `password`, `address` (any nesting depth) are refused; strings shaped like `+923001234567`, `03001234567`, `42101-1234567-1` are refused; `phone_hash`, UUIDs, idempotency keys (`<uuid>:2`), 64-char hex hashes containing long digit runs, amounts and enums pass (property test over 10 000 random UUIDs and hashes: 0 false positives)
- [ ] T007 Implement `normalise()`, `canonical_json()`, `format_ts()` and `AuditPayloadError` in `app/services/audit/canonical.py` (makes T004 pass)
- [ ] T008 Implement `GENESIS_HASH`, `build_envelope()`, `compute_hash()`, `chain_key_for()`, `current_chain_key()` (with `run_no` fallback 1) in `app/services/audit/chain.py` (makes T005 pass)
- [ ] T009 Implement `assert_no_pii()` in `app/services/audit/pii_guard.py` (makes T006 pass)
- [ ] T010 [P] Implement the registry and `validate_event_type()` in `app/services/audit/event_types.py`
- [ ] T011 Extend `app/models/audit.py`: `chain_key` column, `UniqueConstraint("chain_key", "seq")`, indexes `(chain_key, event_type, timestamp)` and `(entity_type, entity_id)`, FK `scenario_id` with `ondelete="RESTRICT"`
- [ ] T012 Write migration `alembic/versions/<rev>_fr20_audit_hash_chain.py`: add column, backfill and re-hash existing stub rows per chain in seq order, set NOT NULL, swap unique constraint, create `audit_log_append_only()` and both triggers; downgrade drops triggers first

**Checkpoint**: Pure hashing and canonical JSON are proven; the schema is ready.

---

## Phase 3: User Story 1 - Every state change leaves a chained record (P1) 🎯 MVP

**Goal**: `record()` appends a correctly linked entry inside the caller's transaction.

**Independent Test**: `pytest tests/integration/audit/test_append_chain.py tests/integration/audit/test_concurrent_appends.py`

### Tests for User Story 1 ⚠️

- [ ] T013 [P] [US1] Write failing tests in `tests/integration/audit/test_append_chain.py`: first entry seq 1 / genesis prev; seq N+1 links to head; stored hash recomputes; several records in one transaction get consecutive seqs; a rolled-back transaction leaves no row and no gap; `record()` does not commit (the caller's rollback removes it); timestamps never decrease even if the clock is patched backwards; a PII payload raises and the caller's transaction is aborted
- [ ] T014 [P] [US1] Write failing test in `tests/integration/audit/test_concurrent_appends.py`: 20 threads × 50 records each on separate connections → seqs 1…1000 unique and gap-free, chain verifies; assert connection isolation is READ COMMITTED
- [ ] T015 [P] [US1] Write failing test in `tests/integration/audit/test_jsonb_roundtrip.py`: 200 generated payloads (Decimals, Urdu strings, nested lists, floats in range) → stored, re-read from JSONB, `canonical_json(row.payload)` equals the canonical text used at write time

### Implementation for User Story 1

- [ ] T016 [US1] Implement `HashChainAuditService.record()` in `app/services/audit/service.py`: validate type, PII guard, normalise payload, resolve chain key, `pg_advisory_xact_lock(ns, hashtext(chain_key))`, read head, clamp timestamp, build envelope, hash, insert, `flush()`; never commit
- [ ] T017 [US1] Register the implementation: `get_audit()` returns `HashChainAuditService()` in `app/core/deps.py` (one-line change) and export it from `app/services/audit/__init__.py`
- [ ] T018 [US1] Add structured log line `audit.recorded` (chain_key, seq, event_type; no payload) in `app/services/audit/service.py`

**Checkpoint**: Every FR can now write real chained entries.

---

## Phase 4: User Story 2 - Anyone can verify the chain (P1)

**Goal**: `verify_chain()` and `GET /audit/verify` find the first broken seq.

**Independent Test**: `pytest tests/unit/audit/test_verify_logic.py tests/integration/audit/test_verify_endpoint.py`

### Tests for User Story 2 ⚠️

- [ ] T019 [P] [US2] Write failing tests in `tests/unit/audit/test_verify_logic.py` for the pure `verify_rows(rows)`: empty → valid, count 0, head = genesis; intact 1000 → valid; altered payload / actor / event_type / entity_id / timestamp at K → `HASH_MISMATCH` at K; missing K → `SEQ_GAP` at K; rewritten prev_hash at K → `PREV_HASH_MISMATCH` at K; swapped K and K+1 → broken at K; only the **first** break is reported
- [ ] T020 [P] [US2] Write failing tests in `tests/integration/audit/test_verify_endpoint.py`: each of the 4 roles gets 200; response keys are exactly the contract keys (no payload, actor, entity_id); tamper via superuser connection with `ALTER TABLE audit_log DISABLE TRIGGER USER` then `UPDATE` → endpoint reports the seq; verify ignores rows appended after it read the head
- [ ] T021 [P] [US2] Write failing test in `tests/contract/test_audit_api.py` for the `/audit/verify` response schema (`VerifyResultOut`) and 401 without a token

### Implementation for User Story 2

- [ ] T022 [US2] Implement `VerifyResult`, `verify_rows()` (pure) and `verify_chain()` (REPEATABLE READ read-only transaction, head read first, `yield_per(AUDIT_VERIFY_BATCH)`) in `app/services/audit/verify.py`
- [ ] T023 [US2] Implement `VerifyResultOut` in `app/schemas/audit.py`
- [ ] T024 [US2] Implement `GET /audit/verify` (any authenticated role, chain from `X-Scenario-Id` + optional `run_no`, 404 `SCENARIO_NOT_FOUND`) in `app/api/v1/audit.py` and register the router (tag `Reporting`) in `app/api/v1/__init__.py`

**Checkpoint**: One-click verify works for donors (unblocks FR19's verify button).

---

## Phase 5: User Story 3 - The log cannot be edited (P1)

**Goal**: The database refuses UPDATE, DELETE and TRUNCATE.

**Independent Test**: `pytest tests/integration/audit/test_immutability_trigger.py`

### Tests for User Story 3 ⚠️

- [ ] T025 [P] [US3] Write failing tests in `tests/integration/audit/test_immutability_trigger.py`: with the app login, `UPDATE audit_log SET payload='{}'`, `DELETE FROM audit_log WHERE seq=1`, `TRUNCATE audit_log` each raise with `AUDIT_APPEND_ONLY`; row count unchanged; ORM `session.delete(entry)` also fails; deleting a `ReplayScenario` with audit rows fails (RESTRICT)

### Implementation for User Story 3

- [ ] T026 [US3] Confirm the migration from T012 installs both triggers; add a startup self-check in `app/services/audit/service.py` (`assert_triggers_installed(db)` queried from `pg_trigger`) called once from the app factory hook, logging an error and failing `/health` details if missing
- [ ] T027 [US3] Map SQLSTATE `P0001` with `AUDIT_APPEND_ONLY` to a 500 problem response `AUDIT_APPEND_ONLY` in the audit router's exception handler (`app/api/v1/audit.py`)

---

## Phase 6: User Story 4 - Live and replay chains never mix (P2)

**Goal**: One chain per live/replay-run context.

**Independent Test**: `pytest tests/integration/audit/test_scenario_chains.py`

### Tests for User Story 4 ⚠️

- [ ] T028 [P] [US4] Write failing tests in `tests/integration/audit/test_scenario_chains.py`: interleaved live and scenario-A writes → each chain gap-free from seq 1 and verifies alone; scenario run 2 starts a new chain while run 1 still verifies; `X-Scenario-Id` selects the scenario chain for verify and list; concurrent writes to two chains do not block each other (lock keys differ)

### Implementation for User Story 4

- [ ] T029 [US4] Finish `chain_key_for()` / `current_chain_key()` in `app/services/audit/chain.py` using `current_scenario()` and `current_run_no()` (fallback 1 until FR5 lands); support `run_no` override for reads
- [ ] T030 [US4] Add `REPLAY_STARTED` / `REPLAY_RESET` to `app/services/audit/event_types.py` and document the FR5 hand-off in `app/services/audit/chain.py` docstring

---

## Phase 7: User Story 5 - Officers browse and export (P2)

**Goal**: Filterable list and re-verifiable export.

**Independent Test**: `pytest tests/integration/audit/test_export.py tests/contract/test_audit_api.py`

### Tests for User Story 5 ⚠️

- [ ] T031 [P] [US5] Extend `tests/contract/test_audit_api.py`: `GET /audit` schema, filters (event_type repeatable, entity, actor, time and seq ranges), default order desc, page size cap 500 → 422 beyond; DC and DONOR → 403 on list and export
- [ ] T032 [P] [US5] Write failing tests in `tests/integration/audit/test_export.py`: JSONL header line + one line per entry; CSV columns exact; the export writes one `AUDIT_EXPORTED` entry; `scripts/verify_audit_export.py` on a full export prints `VALID` with the API head hash; on a range export checks internal links and prints the expected prev hash of the first row; on a tampered file prints the broken seq and exits 1

### Implementation for User Story 5

- [ ] T033 [P] [US5] Implement `AuditEntryOut`, `AuditPage`, `AuditFilter` in `app/schemas/audit.py`
- [ ] T034 [US5] Implement `GET /audit` with filters and paging in `app/api/v1/audit.py` (`require_role(PDMA_OFFICER, ADMIN)`)
- [ ] T035 [US5] Implement `iter_jsonl()` and `iter_csv()` (server-side cursor, streamed) in `app/services/audit/export.py`
- [ ] T036 [US5] Implement `GET /audit/export` (StreamingResponse, filename, `AUDIT_EXPORTED` entry written in its own short transaction before streaming) in `app/api/v1/audit.py`
- [ ] T037 [US5] Implement `scripts/verify_audit_export.py` reusing `verify_rows()` and `compute_hash()` (no DB import)

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T038 [P] Implement `scripts/audit_tamper_demo.py`: refuses unless `ALLOW_TAMPER_DEMO=true`, a scenario key is given and the target chain starts with `replay:`; uses `TAMPER_DEMO_DATABASE_URL` (superuser) to disable the trigger, alter one payload, re-enable the trigger, then prints the verify result
- [ ] T039 [P] Add a payload sweep test `tests/integration/audit/test_append_chain.py::test_no_pii_in_any_payload` that scans every stored payload after the suite's fixtures for PII keys/shapes
- [ ] T040 [P] Benchmark script `scripts/bench_audit_verify.py`: insert 100 000 entries in a scratch chain and time verify (< 5 s target); note the result in the PR
- [ ] T041 Run `ruff check app/services/audit app/api/v1/audit.py` and `mypy --strict app/services/audit`
- [ ] T042 [P] Stretch: nightly self-verify job `audit.self_verify` registered in `app/jobs/timers.py` (02:00 UTC) that logs and alerts the admin when `valid=false`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: after `000-foundation` is merged.
- **Foundational (Phase 2)**: blocks all stories. T004–T006 before T007–T009. T011 before T012.
- **US1 (Phase 3)**: needs Phase 2. **Unblocks every other FR** once T016–T017 are merged.
- **US2 (Phase 4)**: needs US1 (it verifies what US1 writes). T022 before T024.
- **US3 (Phase 5)**: needs T012; independent of US2.
- **US4 (Phase 6)**: needs US1.
- **US5 (Phase 7)**: needs US1 and `verify_rows()` from US2 (for T037).
- **Polish**: after US2 and US5.

### Within Each User Story

- Test tasks first, run them, see them fail.
- Schemas before routers; services before routers.

## Parallel opportunities

- T002, T003 in Setup.
- T004, T005, T006 (three unit test files), then T007/T008/T009 (three modules) in parallel pairs.
- After T016: US3 (T025–T027) and US4 (T028–T030) can run in parallel with US2.
- T031, T032, T033 in parallel; T038, T039, T040, T042 in parallel.

```bash
# Phase 2 tests in parallel
pytest tests/unit/audit/test_canonical_json.py &
pytest tests/unit/audit/test_hash.py &
pytest tests/unit/audit/test_pii_guard.py &
```

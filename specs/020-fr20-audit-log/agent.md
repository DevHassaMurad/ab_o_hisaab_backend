# Agent Brief: FR20 Tamper-proof audit log

## Owner role
Backend & alerts.

## Mission
Replace the foundation's `StubAuditService` with a real hash-chained, append-only audit log. Every state change in the system (settings, triggers, approvals, allocations, payments, alerts, confirmations) writes exactly one entry in the same database transaction, and anyone (including a donor) can verify the chain end to end with one call. This FR is built **first after the foundation**, because every other FR writes to it.

## Scope
**In (MVP)**
- `HashChainAuditService.record(...)`: canonical JSON, SHA-256 chaining, a per-chain `pg_advisory_xact_lock`, a PII guard on payloads
- One chain per `chain_key`: `live` for live data, and one per replay scenario run (FR5)
- A DB trigger that blocks `UPDATE`, `DELETE` and `TRUNCATE` on `audit_log`
- Migration that re-hashes any rows the foundation stub already wrote, then locks the table
- `GET /api/v1/audit` (filterable list), `GET /api/v1/audit/verify`, `GET /api/v1/audit/export` (JSONL or CSV)
- `scripts/verify_audit_export.py` (offline verifier) and `scripts/audit_tamper_demo.py` (dev-only tamper demo on a replay chain)
- The audit event-type registry (`app/services/audit/event_types.py`)

**Out**
- Deciding *what* each FR audits: each FR calls `record(...)` itself.
- External anchoring of the head hash (e.g. publishing it to a public ledger): roadmap.
- DB role hardening (app runs as a non-owner role with INSERT/SELECT only): documented for production, not built in the MVP.

## Read first
- `.specify/memory/constitution.md` (Principle III, Principle VI, Principle XI)
- `specs/_authoring-guide.md` (FR20 facts, shared rules)
- `specs/000-foundation/data-model.md` §8 `AuditLog`
- `specs/000-foundation/plan.md` (`AuditService` interface, API conventions)
- `specs/000-foundation/implement.md` (`current_scenario()`, `use_scenario`, `app/core/deps.py` providers)
- This folder: `spec.md`, `clarify.md`, `plan.md`

## Depends on / Blocks
- **Depends on**: `000-foundation` only (models, `get_db`, `require_role`, `current_scenario()`, `deps.get_audit()`).
- **Blocks**: every FR that changes state. In particular `006`, `007`, `008`, `011`, `012`, `014`, `015`, `017`, `018`, `021`, `022` call `AuditService.record`, and `019-fr19-donor-report` calls `verify_chain` for its one-click verify and PDF.
- **Soft link**: `005-fr05-event-replay` must expose the replay run number (`run_no`) so each replay run gets its own chain (see plan.md "Proposed data-model additions").

## Interfaces
- **Provides**
  - `AuditService` Protocol implementation: `record(db, *, event_type, entity_type, entity_id, actor, payload) -> AuditLog` (signature unchanged from `app/services/interfaces.py`)
  - `verify_chain(db, chain_key, *, up_to_seq=None) -> VerifyResult` in `app/services/audit/verify.py`
  - `chain_key_for(scenario_id, run_no) -> str` and `current_chain_key()` in `app/services/audit/chain.py`
  - `canonical_json(obj) -> str` in `app/services/audit/canonical.py` (reused by FR19 for its report hash)
- **Consumes**: `current_scenario()` and the replay run number from `app/core/scenario.py`; `require_role`, `get_db`.

## Files you own
- `app/services/audit/__init__.py`, `canonical.py`, `chain.py`, `service.py`, `verify.py`, `pii_guard.py`, `event_types.py`, `export.py`
- `app/models/audit.py` (extend the foundation model with `chain_key`; no other changes)
- `app/schemas/audit.py`
- `app/api/v1/audit.py`
- `alembic/versions/<rev>_fr20_audit_hash_chain.py`
- `scripts/verify_audit_export.py`, `scripts/audit_tamper_demo.py`
- `tests/unit/audit/test_canonical_json.py`, `test_hash.py`, `test_pii_guard.py`, `test_verify_logic.py`
- `tests/integration/audit/conftest.py`, `test_append_chain.py`, `test_concurrent_appends.py`, `test_immutability_trigger.py`, `test_verify_endpoint.py`, `test_scenario_chains.py`, `test_jsonb_roundtrip.py`, `test_export.py`
- `tests/contract/test_audit_api.py`
- One line in `app/core/deps.py`: `get_audit()` returns `HashChainAuditService` (the only foundation file you touch, as the foundation hand-off note says).

## Guardrails
- **III (non-negotiable)**: `record()` never commits. It runs inside the caller's transaction, so the business change and its audit entry commit or roll back together. No code path updates or deletes audit rows; the trigger enforces it. Replay resets do **not** delete audit rows; they start a new chain.
- **VI / NFR8**: payloads hold ids, hashes, amounts and enums only. `pii_guard.assert_no_pii()` rejects deny-listed keys and phone/CNIC-shaped strings. Verify responses contain no payloads, so donors can call them.
- **VIII**: replay chains are separate from the live chain, so simulated replay activity can never be mixed into the live record.
- **XI**: tests first for canonical JSON, hashing, append under concurrency, the trigger, and verify (including the first broken seq).
- **X**: no external ledger, no message broker. One table, one trigger, one advisory lock.

## Definition of Done
- [ ] All tests in `tests/unit/audit`, `tests/integration/audit` and `tests/contract/test_audit_api.py` written first, seen failing, now green
- [ ] `get_audit()` returns `HashChainAuditService`; the stub is no longer used anywhere
- [ ] `UPDATE`, `DELETE` and `TRUNCATE` on `audit_log` fail with `AUDIT_APPEND_ONLY` (test)
- [ ] 20 concurrent writers on one chain produce gap-free seqs and a valid chain (test)
- [ ] Verify finds a tampered payload, a tampered hash, a removed row and a reordered row, and reports the first broken seq (tests)
- [ ] Live and replay chains are independent; each verifies on its own (test)
- [ ] `GET /audit/verify` works for all four roles and returns no payload data (contract test)
- [ ] Export of 10 000 entries re-verifies offline with `scripts/verify_audit_export.py`
- [ ] Verify of 100 000 entries finishes in < 5 s on a dev laptop (benchmark note in PR)
- [ ] `ruff` and `mypy --strict app/services/audit` clean

## Hand-off notes
- Every FR: call `audit.record(db, event_type=..., entity_type=..., entity_id=..., actor=..., payload=...)` **after** your change is added to the session and **before** commit. Keep transactions short: the chain lock is held until commit.
- Put ids, enums, amounts (as `Decimal`) and hashes in payloads. Never names, phones, CNICs, wallet ids or free-text notes (store the note on your own table and put `note_sha256` in the payload).
- New event types: add a constant to `app/services/audit/event_types.py` in your own PR. Unknown but well-formed (`UPPER_SNAKE`) types are accepted with a warning log, so parallel FRs are never blocked.
- `019-fr19-donor-report`: call `verify_chain(db, current_chain_key())` for the PDF footer; the dashboard button calls `GET /api/v1/audit/verify` directly.
- `005-fr05-event-replay`: increment `ReplayScenario.run_no` on reset and set it in the scenario context; never delete audit rows.

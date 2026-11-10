# Implementation Plan: FR20 Tamper-proof audit log

**Branch**: `020-fr20-audit-log` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/020-fr20-audit-log/spec.md`

## Summary

Replace `StubAuditService` with `HashChainAuditService`. Each `record()` call, inside the caller's transaction, takes a per-chain advisory lock, reads the chain head, builds a canonical JSON envelope, computes `SHA-256(prev_hash ‖ canonical_json(envelope) ‖ ts)` and inserts the row. A Postgres trigger blocks UPDATE, DELETE and TRUNCATE. Verify streams the chain in seq order and returns the first broken seq. List and export endpoints serve officers and admins. This plan is **self-contained**: it depends only on `000-foundation`, because FR20 is built before any other FR.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, psycopg 3. Stdlib `hashlib`, `json`, `csv`. No new third-party packages.

**Storage**: PostgreSQL 16 (table `audit_log`, two triggers, one PL/pgSQL function, advisory locks)

**Testing**: pytest, httpx TestClient, the foundation Postgres test DB. Concurrency tests use real connections (threads), not the rollback fixture.

**Target Platform**: Linux container; Windows/macOS dev

**Project Type**: web-service

**Performance Goals**: `record()` adds < 5 ms per entry uncontended. Verify 100 000 entries < 5 s. List p95 < 500 ms (NFR1).

**Constraints**: Must run inside the caller's transaction and never commit. READ COMMITTED isolation for writers. No PII in payloads. Money in payloads as `Decimal` → string.

**Scale/Scope**: One live chain plus one chain per replay run; up to ~300 000 entries per chain in the pilot.

## Constitution Check

| Principle | How satisfied | ✅/⚠️ |
|---|---|---|
| III Every rupee is traceable | Same-transaction `record()`, hash chain, BEFORE UPDATE/DELETE row trigger + BEFORE TRUNCATE statement trigger, verify endpoint. Envelope hashing makes every column tamper-evident | ✅ (envelope interpretation recorded in clarify.md item 1) |
| VI Privacy by role | `pii_guard.assert_no_pii()` on every payload; verify returns no payloads; list/export restricted to officer/admin | ✅ |
| VIII Honest prototype | Replay runs use separate chains; live chain only holds live activity | ✅ |
| IX Config over code | Lock namespace, batch sizes and the tamper-demo switch come from settings | ✅ |
| X Simplicity | One table, one function, two triggers, one advisory lock; no external ledger | ✅ |
| XI Test the money paths | Unit + integration tests for hashing, concurrency, trigger, verify written first | ✅ |
| II Human authority | Not touched | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| `pg_advisory_xact_lock(2020, hashtext(chain_key))` to serialise appends | Per-chain lock, auto-released at commit/rollback, no extra table | `SELECT … FOR UPDATE` on the head row (no row exists for an empty chain); `SERIALIZABLE` (retries everywhere); a global lock (live and replay would block each other) |
| Per-chain `seq` computed as `head.seq + 1` under the lock | Gap-free, so a missing entry is detectable | A DB sequence (gaps on rollback, so gaps mean nothing) |
| Hash the envelope, not just the payload | NFR7: any edit must be detectable, including `actor`, `event_type`, `entity_id` | Payload-only (literal formula) leaves columns editable |
| Store exactly the hashed (normalised) payload | JSONB re-formats numbers and drops key order; storing `json.loads(canonical)` makes recompute stable | Separate `payload_canonical` text column (duplicates data and must itself be checked) |
| Wall-clock timestamp, clamped to ≥ head timestamp | Honest record of when the system acted; monotonic along the chain | Replay clock (would look like 2022 records) |
| Separate chain per replay run (`replay:{scenario_id}:{run_no}`) | Principle III forbids deletes; FR5 resets need a fresh chain | Trigger exception for replay deletes (violates a non-negotiable principle) |
| Verify streams with `yield_per(5000)` inside a REPEATABLE READ read-only transaction | Constant memory; consistent snapshot while writers append | Loading all rows; Postgres-side recursive SQL hashing (pgcrypto, harder to keep byte-identical to Python) |
| Statement trigger for TRUNCATE | Row triggers don't fire on TRUNCATE | Relying on permissions only |
| PII guard by key deny-list + phone/CNIC regex on strings | Cheap, catches the common leaks | Full DLP scanning (overkill) |

## Data model slice

From `specs/000-foundation/data-model.md` §8 `AuditLog`: all fields (`seq`, `event_type`, `entity_type`, `entity_id`, `actor`, `payload`, `timestamp`, `prev_hash`, `hash`, `scenario_id`). Read-only use of `ReplayScenario` (§9) for the run number. `User.role` for access.

### Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

| Entity | Change | Why |
|---|---|---|
| `AuditLog` | **Add** `chain_key: str NOT NULL` (`live` or `replay:{scenario_id}:{run_no}`), indexed | Each chain is selected and locked by this key; also distinguishes replay runs |
| `AuditLog` | **Change** `seq` uniqueness from global to `UNIQUE (chain_key, seq)`; `seq` starts at 1 per chain | Gap detection and "first broken seq" are per chain |
| `AuditLog` | `scenario_id` FK uses `ON DELETE RESTRICT` | A cascade would hit the append-only trigger |
| `AuditLog` | Index `(chain_key, event_type, timestamp DESC)`, `(entity_type, entity_id)` | List filters (NFR1) |
| `ReplayScenario` (owned by `005-fr05-event-replay`) | **Add** `run_no: int NOT NULL DEFAULT 1`, incremented on reset; exposed by `app/core/scenario.py` as `current_run_no()` | Replay reset starts a fresh audit chain instead of deleting entries |

Until FR5 adds `run_no`, `current_run_no()` returns 1 (a one-line fallback in `chain.py`).

## Hash specification (normative)

```
GENESIS     = "0" * 64
ts_text     = ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")      # always 27 chars
envelope    = {
  "chain_key": chain_key, "seq": seq, "event_type": event_type,
  "entity_type": entity_type, "entity_id": str(entity_id),
  "actor": actor, "scenario_id": str(scenario_id) | null,
  "payload": payload_norm
}
material    = prev_hash + canonical_json(envelope) + ts_text               # str concat
hash        = sha256(material.encode("utf-8")).hexdigest()                 # 64 lowercase hex
```

`canonical_json(x) = json.dumps(normalise(x), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`

| Python type | Canonical form |
|---|---|
| `dict` | keys must be `str`; sorted |
| `list`, `tuple` | JSON array, order kept |
| `Decimal` | string, `format(d, "f")` (e.g. `"15000.00"`) |
| `UUID` | lowercase string |
| `datetime` | `ts_text` format (UTC) |
| `date` | `YYYY-MM-DD` |
| `Enum` | `.value` |
| `bool`, `int`, `str`, `None` | as is (`str` must not contain `\x00`) |
| `float` | finite and `abs(x) < 1e15`, else `AuditPayloadError` |
| anything else | `AuditPayloadError` |

## API contracts

All under `/api/v1`. The chain is chosen by the `X-Scenario-Id` header (absent = `live`). Errors are RFC 7807 problem JSON.

### `GET /audit/verify` (any authenticated role: `PDMA_OFFICER`, `DC`, `DONOR`, `ADMIN`)

Query: `run_no` (optional, replay only; default = current run).

200, intact:
```json
{
  "chain_key": "live",
  "valid": true,
  "count": 18452,
  "head_seq": 18452,
  "head_hash": "9f2c…e1a0",
  "first_broken_seq": null,
  "reason": null,
  "verified_at": "2026-11-10T09:14:03.120044Z",
  "duration_ms": 812
}
```
200, broken:
```json
{
  "chain_key": "replay:5b1e…:2",
  "valid": false,
  "count": 1312,
  "head_seq": 1312,
  "head_hash": "77ab…09cd",
  "first_broken_seq": 500,
  "reason": "HASH_MISMATCH",
  "verified_at": "2026-11-10T09:15:10.004210Z",
  "duration_ms": 95
}
```
Errors: 401 unauthenticated; 404 `SCENARIO_NOT_FOUND`.

### `GET /audit` (`PDMA_OFFICER`, `ADMIN`)

Query: `event_type` (repeatable), `entity_type`, `entity_id`, `actor`, `from`, `to` (ISO UTC), `seq_from`, `seq_to`, `order` (`desc` default | `asc`), `page` (1), `page_size` (50, max 500).

200:
```json
{
  "items": [
    {
      "seq": 18452,
      "chain_key": "live",
      "event_type": "PAYMENT_STATUS_CHANGED",
      "entity_type": "payment",
      "entity_id": "3f0d6c1e-…",
      "actor": "system",
      "payload": {"from": "PROCESSING", "to": "PAID", "amount_pkr": "7000.00", "payment_id": "3f0d…"},
      "timestamp": "2026-11-10T09:12:44.551203Z",
      "prev_hash": "1c4e…",
      "hash": "9f2c…",
      "scenario_id": null
    }
  ],
  "total": 18452, "page": 1, "page_size": 50
}
```
Errors: 403 `ROLE_FORBIDDEN` (DC, DONOR); 422 `INVALID_FILTER`.

### `GET /audit/export` (`PDMA_OFFICER`, `ADMIN`)

Query: `format` (`jsonl` default | `csv`), `seq_from`, `seq_to`, `from`, `to`.

200: streamed `application/x-ndjson` or `text/csv`, `Content-Disposition: attachment; filename="audit_{chain_key}_{seq_from}-{seq_to}.jsonl"`. One line per entry with all fields above. The first line of JSONL is a header object:
```json
{"_export": {"chain_key": "live", "seq_from": 1, "seq_to": 18452, "head_hash": "9f2c…", "exported_at": "…", "hash_spec": "fr20-v1"}}
```
CSV columns: `seq,chain_key,event_type,entity_type,entity_id,actor,scenario_id,timestamp,prev_hash,hash,payload_json` (`payload_json` is the canonical JSON string).

Side effect: one `AUDIT_EXPORTED` entry with `{format, seq_from, seq_to, row_count}`.

Errors: 403 `ROLE_FORBIDDEN`; 422 `INVALID_RANGE`.

### Internal (Python) contract

```python
class HashChainAuditService(AuditService):
    def record(self, db: Session, *, event_type: str, entity_type: str, entity_id: UUID,
               actor: str, payload: dict[str, Any]) -> AuditLog: ...

def verify_chain(db: Session, chain_key: str, *, up_to_seq: int | None = None) -> VerifyResult: ...
def current_chain_key() -> str: ...
def chain_key_for(scenario_id: UUID | None, run_no: int | None) -> str: ...
```

Exceptions: `AuditPayloadError` (PII or non-canonical value, maps to 500 `AUDIT_PAYLOAD_INVALID` because it is a programming error), `AuditEventTypeError` (malformed type). The DB raises SQLSTATE `P0001` with message `AUDIT_APPEND_ONLY` from the trigger.

## Database objects (migration `<rev>_fr20_audit_hash_chain.py`)

1. Add `chain_key text` (nullable at first).
2. Backfill: `chain_key = 'live'` where `scenario_id IS NULL`, else `'replay:' || scenario_id || ':1'`; renumber `seq` per chain by original seq order; compute `prev_hash`/`hash` in Python, row by row.
3. Set `chain_key`, `prev_hash`, `hash` NOT NULL; drop the global unique on `seq`; add `UNIQUE (chain_key, seq)`; add indexes; drop any `seq` default/sequence.
4. Create the function and triggers:
```sql
CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'AUDIT_APPEND_ONLY: audit_log rows cannot be %', TG_OP
    USING ERRCODE = 'P0001';
END $$;

CREATE TRIGGER audit_log_no_update_delete
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

CREATE TRIGGER audit_log_no_truncate
  BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
```
5. Downgrade drops the triggers first (dev only).

## Scheduled jobs

None. (Optional later: a nightly self-verify that alerts the admin if `valid=false`; listed under Polish as a stretch task.)

## Event-type registry (`app/services/audit/event_types.py`)

`FUND_SETTINGS_UPDATED`, `TIER_FIRED`, `FUND_RELEASE_CREATED`, `FUND_RELEASE_STATUS_CHANGED`, `APPROVAL_REQUESTED`, `APPROVAL_DECIDED`, `APPROVAL_ESCALATED`, `ALLOCATION_SAVED`, `ALLOCATION_OVERRIDDEN`, `FAIRNESS_ACKNOWLEDGED`, `PAYMENT_STATUS_CHANGED`, `PAYMENT_STUCK_FLAGGED`, `PAYMENTS_EXPORTED`, `ALERT_QUEUED`, `ALERT_STATUS_CHANGED`, `RECEIPT_CONFIRMED`, `FLOOD_VERIFIED`, `BENEFICIARY_IMPORTED`, `DONOR_REPORT_GENERATED`, `AUDIT_EXPORTED`, `REPLAY_STARTED`, `REPLAY_RESET`. Other FRs add their own.

## Project Structure

### Documentation (this feature)

```text
specs/020-fr20-audit-log/
├── agent.md
├── spec.md
├── clarify.md
├── plan.md
├── tasks.md
└── implement.md
```

### Source Code (repository root)

```text
app/
├── models/audit.py                     # extend: chain_key, UniqueConstraint(chain_key, seq), indexes
├── schemas/audit.py                    # AuditEntryOut, AuditPage, VerifyResultOut, AuditFilter
├── api/v1/audit.py                     # GET /audit, /audit/verify, /audit/export
├── core/deps.py                        # one line: get_audit() -> HashChainAuditService()
└── services/audit/
    ├── __init__.py                     # exports HashChainAuditService, verify_chain, canonical_json
    ├── canonical.py                    # normalise(), canonical_json(), format_ts()
    ├── chain.py                        # GENESIS_HASH, chain_key_for(), current_chain_key(), compute_hash(), build_envelope()
    ├── service.py                      # HashChainAuditService.record()
    ├── verify.py                       # VerifyResult, verify_chain(), verify_rows() (pure, reused by the offline script)
    ├── pii_guard.py                    # assert_no_pii()
    ├── event_types.py                  # registry + validate_event_type()
    └── export.py                       # iter_jsonl(), iter_csv()
alembic/versions/<rev>_fr20_audit_hash_chain.py
scripts/verify_audit_export.py          # offline verifier: python scripts/verify_audit_export.py file.jsonl
scripts/audit_tamper_demo.py            # dev-only; replay chain only
tests/
├── unit/audit/{test_canonical_json,test_hash,test_pii_guard,test_verify_logic}.py
├── integration/audit/{conftest,test_append_chain,test_concurrent_appends,test_immutability_trigger,
│                      test_verify_endpoint,test_scenario_chains,test_jsonb_roundtrip,test_export}.py
└── contract/test_audit_api.py
```

**Structure Decision**: single project at the repo root, following the constitution layout. Audit logic lives in `app/services/audit/`; the router is `app/api/v1/audit.py`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Envelope hashing deviates from the literal formula text in Principle III | NFR7 requires every edit to be detectable, including non-payload columns | Payload-only hashing leaves `actor`/`event_type`/`entity_id` editable without detection |

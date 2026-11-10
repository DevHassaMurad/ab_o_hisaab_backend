# Implementation Guide: FR20 Tamper-proof audit log

## Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T001–T003 | Package, settings, committing test fixture |
| 2 | T004–T006 → T007–T010 | Canonical JSON, hashing, PII guard proven by unit tests |
| 3 | T011–T012 | Schema change + migration with triggers |
| 4 | T013–T015 → T016–T018 | Real `record()`; **merge now** so other FRs stop using the stub |
| 5 | T019–T021 → T022–T024 | Verify service and endpoint |
| 6 | T025–T027 | Append-only proven at DB level |
| 7 | T028–T030 | Live/replay chain separation |
| 8 | T031–T037 | List, export, offline verifier |
| 9 | T038–T042 | Tamper demo, PII sweep, benchmark, lint |

## Key algorithms

### Canonical JSON (`app/services/audit/canonical.py`)

```python
import json, math
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

class AuditPayloadError(ValueError): ...

FLOAT_LIMIT = 1e15

def format_ts(ts: datetime) -> str:
    if ts.tzinfo is None:
        raise AuditPayloadError("naive datetime")
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")   # 27 chars

def normalise(v: Any) -> Any:
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        if "\x00" in v:
            raise AuditPayloadError("NUL in string")
        return v
    if isinstance(v, Decimal):
        if not v.is_finite():
            raise AuditPayloadError("non-finite Decimal")
        return format(v, "f")
    if isinstance(v, float):
        if not math.isfinite(v) or abs(v) >= FLOAT_LIMIT:
            raise AuditPayloadError(f"float out of canonical range: {v!r}")
        return v
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, datetime):          # check before date (datetime is a date)
        return format_ts(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Enum):
        return normalise(v.value)
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if not isinstance(k, str):
                raise AuditPayloadError(f"non-str key {k!r}")
            out[k] = normalise(x)
        return out
    if isinstance(v, (list, tuple)):
        return [normalise(x) for x in v]
    raise AuditPayloadError(f"unsupported type {type(v).__name__}")

def canonical_json(obj: Any) -> str:
    return json.dumps(normalise(obj), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)
```

### Chain key and hash (`app/services/audit/chain.py`)

```python
import hashlib
from uuid import UUID
from app.core.scenario import current_scenario
from .canonical import canonical_json, format_ts

GENESIS_HASH = "0" * 64
LIVE_CHAIN = "live"

def _current_run_no() -> int:
    try:
        from app.core.scenario import current_run_no      # added by FR5
        return current_run_no() or 1
    except ImportError:
        return 1

def chain_key_for(scenario_id: UUID | None, run_no: int | None) -> str:
    return LIVE_CHAIN if scenario_id is None else f"replay:{scenario_id}:{run_no or 1}"

def current_chain_key() -> str:
    return chain_key_for(current_scenario(), _current_run_no())

def build_envelope(*, chain_key, seq, event_type, entity_type, entity_id, actor, scenario_id, payload) -> dict:
    return {"chain_key": chain_key, "seq": seq, "event_type": event_type,
            "entity_type": entity_type, "entity_id": str(entity_id), "actor": actor,
            "scenario_id": None if scenario_id is None else str(scenario_id),
            "payload": payload}

def compute_hash(prev_hash: str, envelope: dict, ts) -> str:
    material = prev_hash + canonical_json(envelope) + format_ts(ts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
```

### PII guard (`app/services/audit/pii_guard.py`)

```python
import re
from .canonical import AuditPayloadError

PII_KEYS = {"name", "full_name", "phone", "cnic", "wallet_id", "email", "lat", "lon",
            "otp", "password", "address", "note"}          # free-text notes go on owning tables
# Alphanumeric boundaries so UUIDs, idempotency keys and hex hashes never match by accident
PHONE_RE = re.compile(r"(?<![0-9A-Za-z])(?:\+?92|0)3\d{9}(?![0-9A-Za-z])")
CNIC_RE = re.compile(r"(?<![0-9A-Za-z])\d{5}-?\d{7}-?\d(?![0-9A-Za-z])")

def assert_no_pii(obj, path="payload") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in PII_KEYS:
                raise AuditPayloadError(f"PII key at {path}.{k}")
            assert_no_pii(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_no_pii(v, f"{path}[{i}]")
    elif isinstance(obj, str) and (PHONE_RE.search(obj) or CNIC_RE.search(obj)):
        raise AuditPayloadError(f"PII-shaped value at {path}")
```

### Append (`app/services/audit/service.py`)

```python
import json, logging
from datetime import UTC, datetime
from sqlalchemy import select, text
from app.core.config import settings
from app.core.scenario import current_scenario
from app.models.audit import AuditLog
from .canonical import canonical_json
from .chain import GENESIS_HASH, build_envelope, compute_hash, current_chain_key
from .event_types import validate_event_type
from .pii_guard import assert_no_pii

log = logging.getLogger("audit")

class HashChainAuditService:
    def record(self, db, *, event_type, entity_type, entity_id, actor, payload) -> AuditLog:
        validate_event_type(event_type)
        assert_no_pii(payload)
        payload_norm = json.loads(canonical_json(payload))   # store exactly what we hash
        scenario_id = current_scenario()
        chain_key = current_chain_key()

        db.execute(text("SELECT pg_advisory_xact_lock(:ns, hashtext(:k))"),
                   {"ns": settings.AUDIT_LOCK_NAMESPACE, "k": chain_key})
        head = db.execute(
            select(AuditLog.seq, AuditLog.hash, AuditLog.timestamp)
            .where(AuditLog.chain_key == chain_key)
            .order_by(AuditLog.seq.desc()).limit(1)
        ).first()

        seq = head.seq + 1 if head else 1
        prev_hash = head.hash if head else GENESIS_HASH
        now = datetime.now(UTC)                      # µs precision, same as timestamptz
        ts = max(now, head.timestamp) if head else now

        env = build_envelope(chain_key=chain_key, seq=seq, event_type=event_type,
                             entity_type=entity_type, entity_id=entity_id, actor=actor,
                             scenario_id=scenario_id, payload=payload_norm)
        row = AuditLog(chain_key=chain_key, seq=seq, event_type=event_type,
                       entity_type=entity_type, entity_id=entity_id, actor=actor,
                       payload=payload_norm, timestamp=ts, prev_hash=prev_hash,
                       hash=compute_hash(prev_hash, env, ts), scenario_id=scenario_id)
        db.add(row)
        db.flush()                       # never commit: caller owns the transaction
        log.info("audit.recorded", extra={"chain_key": chain_key, "seq": seq, "event_type": event_type})
        return row
```

Note: `datetime.now(UTC)` has microsecond precision, the same as Postgres `timestamptz`, so the value read back for verify is identical. Never round-trip timestamps through a type with less precision.

### Verify (`app/services/audit/verify.py`)

```python
from dataclasses import dataclass
from typing import Iterable, Literal

Reason = Literal["SEQ_GAP", "PREV_HASH_MISMATCH", "HASH_MISMATCH"]

@dataclass(frozen=True)
class RowCheck:                     # result of walking the rows
    valid: bool
    checked: int                    # rows that passed
    first_broken_seq: int | None
    reason: Reason | None

@dataclass(frozen=True)
class VerifyResult:                 # what the API returns
    chain_key: str
    valid: bool
    count: int                      # rows stored in the chain (up to head_seq)
    head_seq: int | None            # stored head
    head_hash: str                  # stored head hash (genesis when empty)
    first_broken_seq: int | None
    reason: Reason | None

def verify_rows(rows: Iterable, *, start_seq: int = 1, start_prev: str = GENESIS_HASH) -> RowCheck:
    expected_seq, prev, ok = start_seq, start_prev, 0
    for r in rows:                                   # rows ordered by seq ASC
        if r.seq != expected_seq:
            return RowCheck(False, ok, expected_seq, "SEQ_GAP")
        if r.prev_hash != prev:
            return RowCheck(False, ok, r.seq, "PREV_HASH_MISMATCH")
        env = build_envelope(chain_key=r.chain_key, seq=r.seq, event_type=r.event_type,
                             entity_type=r.entity_type, entity_id=r.entity_id, actor=r.actor,
                             scenario_id=r.scenario_id, payload=r.payload)
        if compute_hash(r.prev_hash, env, r.timestamp) != r.hash:
            return RowCheck(False, ok, r.seq, "HASH_MISMATCH")
        prev, expected_seq, ok = r.hash, r.seq + 1, ok + 1
    return RowCheck(True, ok, None, None)

def verify_chain(db, chain_key: str, *, up_to_seq: int | None = None) -> VerifyResult:
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    q = select(AuditLog.seq, AuditLog.hash).where(AuditLog.chain_key == chain_key)
    if up_to_seq:
        q = q.where(AuditLog.seq <= up_to_seq)
    head = db.execute(q.order_by(AuditLog.seq.desc()).limit(1)).first()
    if head is None:
        return VerifyResult(chain_key, True, 0, None, GENESIS_HASH, None, None)
    count = db.scalar(select(func.count()).where(AuditLog.chain_key == chain_key,
                                                 AuditLog.seq <= head.seq))
    stmt = (select(AuditLog).where(AuditLog.chain_key == chain_key, AuditLog.seq <= head.seq)
            .order_by(AuditLog.seq).execution_options(yield_per=settings.AUDIT_VERIFY_BATCH))
    chk = verify_rows(db.scalars(stmt))
    if chk.valid and chk.checked != count:           # defensive: duplicates/out-of-range rows
        chk = RowCheck(False, chk.checked, head.seq, "SEQ_GAP")
    return VerifyResult(chain_key, chk.valid, count, head.seq, head.hash,
                        chk.first_broken_seq, chk.reason)
```

The verify endpoint opens a fresh session (`SessionLocal()`), so `SET TRANSACTION` is the first statement of its transaction and the head, count and rows all come from one snapshot. `count`, `head_seq` and `head_hash` always describe what is stored; `first_broken_seq` and `reason` describe the first problem.

Gap note: a **removed** row K makes row K+1 arrive when `expected_seq == K`, so the result is `SEQ_GAP` at K, which is exactly the missing seq. A **swapped** pair (rows K and K+1 exchange their seq values) puts K+1's content at position K; its stored `prev_hash` is K's hash, not K−1's, so the result is `PREV_HASH_MISMATCH` at K.

### Endpoint shape (`app/api/v1/audit.py`)

```python
router = APIRouter(prefix="/audit", tags=["Reporting"])

@router.get("/verify", response_model=VerifyResultOut)
def verify(user=Depends(get_current_user), scenario=Depends(get_scenario), run_no: int | None = None):
    chain_key = chain_key_for(scenario, run_no or (current_run_no() if scenario else None))
    started = time.perf_counter()
    with SessionLocal() as db:
        res = verify_chain(db, chain_key)
    return VerifyResultOut(**asdict(res), verified_at=datetime.now(UTC),
                           duration_ms=int((time.perf_counter() - started) * 1000))
```

### Offline verifier (`scripts/verify_audit_export.py`)

Reads the JSONL header, then builds lightweight row objects (`SimpleNamespace`) from each line (timestamp parsed with `datetime.strptime(..., "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)`) and calls `verify_rows(rows, start_seq=first.seq, start_prev=first.prev_hash if first.seq > 1 else GENESIS_HASH)`, then compares the last row's hash to the header's `head_hash`. If `start_seq > 1`, it prints "partial export: first prev_hash = … (match against seq N-1 in the system)". Exit code 0 when valid, 1 when broken. It imports only `app.services.audit.canonical`, `chain` and `verify` (no DB modules).

## Configuration

| Key | Default | Notes |
|---|---|---|
| `AUDIT_LOCK_NAMESPACE` | `2020` | First int of the two-int advisory lock |
| `AUDIT_VERIFY_BATCH` | `5000` | `yield_per` size |
| `AUDIT_LIST_MAX_PAGE_SIZE` | `500` | |
| `ALLOW_TAMPER_DEMO` | `false` | Must be `true` for `scripts/audit_tamper_demo.py` |
| `TAMPER_DEMO_DATABASE_URL` | `""` | Superuser URL, dev only; never set in production |

## Commands

```bash
alembic revision -m "fr20 audit hash chain"        # then hand-write the migration body
alembic upgrade head
pytest -q tests/unit/audit
pytest -q tests/integration/audit -p no:randomly    # concurrency test uses real commits
pytest -q tests/contract/test_audit_api.py
python scripts/verify_audit_export.py audit_live_1-18452.jsonl
ALLOW_TAMPER_DEMO=true python scripts/audit_tamper_demo.py --scenario sindh-2022 --seq 120
python scripts/bench_audit_verify.py --rows 100000
psql "$DATABASE_URL" -c "UPDATE audit_log SET actor='x' WHERE seq=1"   # must fail: AUDIT_APPEND_ONLY
```

## Verification checklist

| Acceptance | How |
|---|---|
| US1-1, US1-2 | `test_append_chain.py::test_genesis_and_links` |
| US1-3 | `test_append_chain.py::test_rollback_leaves_no_gap` |
| US1-4, SC-005 | `test_concurrent_appends.py` |
| US1-5, SC-006 | `test_pii_guard.py`, `test_append_chain.py::test_pii_payload_aborts_tx`, T039 sweep |
| US2-1 | `test_verify_endpoint.py::test_intact_all_roles` |
| US2-2/3/4, SC-002 | `test_verify_logic.py` (pure) + `test_verify_endpoint.py::test_tamper_detected` |
| US2-5, FR20-011 | `test_verify_endpoint.py::test_response_has_no_payload_fields` |
| US3-1/2, SC-003 | `test_immutability_trigger.py` |
| US3-3 | `test_scenario_chains.py::test_new_run_new_chain` |
| US4-1/2 | `test_scenario_chains.py` |
| US5-1/2 | `tests/contract/test_audit_api.py` |
| US5-3/4, SC-007 | `test_export.py` |
| SC-004 | `scripts/bench_audit_verify.py` (manual, result in PR) |
| SC-001 | Cross-FR: each FR's tests assert one entry per action; FR20 provides `audit_entries(db, event_type)` helper in `tests/integration/audit/conftest.py` |

## Demo notes

- During the replay (minute 1:30–4:30 of the pitch), the donor screen's "Verify chain" button calls `GET /audit/verify` with the scenario header and shows a green tick, the entry count and a shortened head hash.
- Optional "tamper" beat (≈15 s): run `scripts/audit_tamper_demo.py --scenario sindh-2022 --seq <a payment entry>` from a terminal, click Verify again, and the screen shows red with "first broken entry #N". Then reset the replay (new run, new clean chain). Rehearse this; never run it against `live`.

## Pitfalls

- **Never commit inside `record()`**. The lock and the row must share the caller's transaction.
- **Keep business transactions short**: the advisory lock is held until commit. Do not call external APIs (Twilio, payment rail) while a transaction that has recorded audit entries is open.
- **Isolation level**: under REPEATABLE READ a writer's snapshot may predate the lock and it would compute a duplicate seq (the unique index then fails the transaction). Writers must use READ COMMITTED.
- **TRUNCATE in tests**: the trigger blocks it. Use the foundation's rollback fixture, or the committing fixture that drops a scratch schema.
- **JSONB number re-formatting**: keep floats `< 1e15` and prefer `Decimal` for money. Always store `payload_norm`, never the raw dict.
- **Timezone**: use `timestamptz` and aware datetimes everywhere; a naive datetime raises.
- **`hashtext()`** may collide for two chain keys; that only means they share a lock (slower), never a wrong chain.
- **Alembic autogenerate** does not see triggers or functions; keep them hand-written in the migration.
- **Superuser bypass**: `ALTER TABLE … DISABLE TRIGGER` still works for the owner/superuser. That is why verify exists. In production, run the app as a non-owner role with only `SELECT, INSERT` on `audit_log`.

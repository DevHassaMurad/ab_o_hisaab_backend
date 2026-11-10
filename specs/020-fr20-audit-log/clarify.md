# Clarifications: FR20 Tamper-proof audit log

## Clarifications

### Session 2026-11-10

- Q: The constitution's formula hashes `canonical_json(payload)` only. Then an attacker could change `event_type`, `actor` or `entity_id` without breaking the chain. Is that acceptable? → A: No. NFR7 says "any edit is detectable". We keep the formula's shape, `SHA-256(prev_hash ‖ canonical_json(X) ‖ timestamp)`, but X is the **entry envelope**: `{chain_key, seq, event_type, entity_type, entity_id, actor, scenario_id, payload}`. The stored `payload` column still holds only the caller's payload. This is a strengthening, not a weakening, of Principle III. Flagged to the user as an open item in case they want a constitution PATCH wording update.
- Q: What exactly is `‖`? → A: Plain string concatenation, then UTF-8 encode. It is unambiguous because `prev_hash` is always 64 hex chars and the timestamp always has the fixed form `YYYY-MM-DDTHH:MM:SS.ffffffZ` (27 chars).
- Q: Canonical JSON rules? → A: `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` after normalising: `Decimal` → plain string (`format(d, "f")`), UUID → string, `datetime` → the fixed UTC form above, `date` → ISO date, `Enum` → value, tuples → lists. Floats are allowed only if finite and `abs(x) < 1e15`. Why: Postgres JSONB re-prints numbers, and huge floats come back as integers, which would change the canonical text and break verification.
- Q: Stored payload vs hashed payload could differ after the JSONB round trip. → A: We store exactly what we hash: `payload_norm = json.loads(canonical_json(payload))` is both hashed and stored. A dedicated test round-trips samples through real JSONB.
- Q: `seq` is "unique, monotonic" in data-model.md, but each scenario has its own chain. Global or per chain? → A: Per chain. `seq` restarts at 1 for each chain, and uniqueness is on `(chain_key, seq)`. Why: "first broken seq" and gap detection only make sense inside one chain. Proposed as a data-model change.
- Q: How are appends serialised? → A: `SELECT pg_advisory_xact_lock(2020, hashtext(chain_key))` before reading the head. The lock is transaction-scoped, so it is released at commit or rollback. The unique index on `(chain_key, seq)` is the safety net if anyone ever skips the lock.
- Q: Which isolation level? → A: READ COMMITTED (Postgres default). After the lock is granted, the head query takes a fresh snapshot and sees the previous writer's committed row. Under REPEATABLE READ the snapshot could predate the lock, so the service asserts the isolation level in tests.
- Q: Does a rolled-back business transaction leave a gap? → A: No. The audit row is part of the same transaction and disappears with it; the next writer computes the seq from the committed head.
- Q: FR5 replay reset would normally delete scenario rows, but audit rows cannot be deleted (Principle III is non-negotiable). → A: Never delete. Each replay run gets its own chain: `chain_key = "replay:{scenario_id}:{run_no}"`. FR5 increments `ReplayScenario.run_no` on reset. Old runs stay and remain verifiable (`?run_no=`). The live chain key is `live`.
- Q: Which timestamp goes into the hash in replay mode? → A: The wall-clock UTC time of the write (`datetime.now(UTC)`, truncated to microseconds), not the replay clock. The audit log records when the system acted. If the head's timestamp is later (clock skew), we use the head's timestamp, so timestamps never go backwards along a chain.
- Q: How do we block edits? → A: A `BEFORE UPDATE OR DELETE ... FOR EACH ROW` trigger and a `BEFORE TRUNCATE ... FOR EACH STATEMENT` trigger, both raising `AUDIT_APPEND_ONLY`. Row triggers do not fire on `TRUNCATE`, hence the second trigger. The FK from `audit_log.scenario_id` uses `ON DELETE RESTRICT`.
- Q: What does verify do on a broken chain? → A: It stops at the first problem and returns that seq and reason: `SEQ_GAP` (expected seq missing; returns the missing seq), `PREV_HASH_MISMATCH` (stored previous hash differs from the prior entry's hash), `HASH_MISMATCH` (recomputed hash differs from the stored one).
- Q: Verify while writers are appending? → A: Verify reads the head seq first and only checks `seq ≤ head_seq`, inside one read-only REPEATABLE READ transaction, so it sees a consistent snapshot.
- Q: Who can verify? → A: Every signed-in role, including donors (UC11 step 5). The response has no payloads, so NFR8 holds.
- Q: Who can list and export? → A: `PDMA_OFFICER` and `ADMIN`. The DC and donor get 403. (Open item: a DC view of their own district's entries.)
- Q: Free-text notes (e.g. a DC's retry note) may contain PII. Put them in the payload? → A: No. The owning FR stores the note on its own table (e.g. `PaymentEvent.note`) and puts `note_sha256` and the row id in the payload. The PII guard also scans string values for phone and CNIC shapes.
- Q: What about rows the foundation stub wrote before FR20 shipped? → A: The FR20 migration hashes them per chain in seq order (seq renumbered per chain from 1), sets columns NOT NULL, then installs the triggers. In practice there should be none, because FR20 ships first.
- Q: Should unknown event types be rejected? → A: Only malformed ones (not `^[A-Z][A-Z0-9_]{2,63}$`). Unknown well-formed types are accepted with a warning, so parallel FRs are not blocked waiting for a registry edit.
- Q: Tail truncation (deleting the newest entries) cannot be detected by the chain alone. → A: Accepted for the MVP: it requires a superuser to disable the trigger. Mitigation: the verify response, every export and every donor PDF print the head seq and head hash, so a later verify with a lower head is visibly suspicious. External anchoring is roadmap.
- Q: Should exports and verify calls be audited? → A: Exports yes (`AUDIT_EXPORTED`, data leaving the system). Verify calls no (read-only, and auditing them would change the head on every click, which confuses the demo).

## Resolved by constitution decisions

- **Principle III**: one entry per state change, same transaction, append-only, DB trigger, verify endpoint.
- **D5**: `AuditLog` is in the canonical model; FR20 only proposes additions.
- **Principle VIII / FR5 isolation**: separate replay chains.
- **Principle XI**: tests first for hashing and verification.

## Open items

| # | Question | Recommended default (used in this spec) | Impact if changed | Who decides |
|---|---|---|---|---|
| 1 | Hash the full envelope or only the payload (literal constitution text)? | Full envelope | Payload-only would leave event type, actor and entity undetectably editable; would need a constitution note either way | Backend lead + user (constitution PATCH) |
| 2 | Per-chain `seq` with unique `(chain_key, seq)` instead of a global unique `seq` | Per chain | Global seq would make gap detection impossible per chain | Foundation owner (data-model.md) |
| 3 | Replay chains keyed by run (`replay:{scenario_id}:{run_no}`), requiring `ReplayScenario.run_no` from FR5 | Yes | Without it, FR5 reset either fails on the trigger or needs a delete exception (violates Principle III) | FR5 owner + foundation owner |
| 4 | Can a DC list audit entries for their own district? | No (officer/admin only) | Would need `district_id` on audit rows or a payload filter | Product (PDMA) |
| 5 | Wall-clock vs replay-clock timestamps in replay chains | Wall clock | Replay clock would make replay chains look like 2022 records | Backend lead |
| 6 | Ship `scripts/audit_tamper_demo.py` (dev-only, replay chain only) for the pitch | Yes, guarded by `ALLOW_TAMPER_DEMO=true` and a separate superuser URL | Without it, the "any edit is detectable" claim is only shown in tests | Frontend & pitch |
| 7 | External anchoring of the head hash (e.g. daily email/hash publication) | Roadmap | Would close the tail-truncation gap | PDMA + donors |

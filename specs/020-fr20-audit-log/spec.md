# Feature Specification: FR20 Tamper-proof audit log

**Feature Branch**: `020-fr20-audit-log`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: FR20 (project document, Reporting): "Write every trigger, approval and payment to a tamper proof audit log"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every state change leaves a chained record (Priority: P1)

Whenever the system changes something that matters for money or trust (fund settings, a tier firing, an approval, an allocation, a payment status, an alert, a confirmation), it writes exactly one audit entry at the same moment. Each entry is linked to the one before it by a hash, so the entries form a chain.

**Why this priority**: Principle III is non-negotiable, and every other FR depends on this service. Without it, nothing else can be "done".

**Independent Test**: Record three entries through the service inside one business transaction, commit, and check that seqs are 1, 2, 3, that entry 1 links to the genesis hash and each later entry links to the previous hash. Roll back a fourth and check it left no entry.

**Acceptance Scenarios**:

1. **Given** an empty live chain, **When** the first entry is recorded, **Then** it gets seq 1 and its previous hash is the genesis value (64 zeros).
2. **Given** a chain with head seq N, **When** a new entry is recorded, **Then** it gets seq N+1, its previous hash equals the head's hash, and its own hash is recomputable from its stored content.
3. **Given** a business change that fails and rolls back, **When** it had recorded an audit entry, **Then** the entry is gone too and the next entry reuses that seq (no gap).
4. **Given** 20 writers recording at the same time on the same chain, **When** all commit, **Then** seqs are 1…20 with no gaps or duplicates and the chain verifies.
5. **Given** a payload containing a phone number, CNIC, name or wallet id, **When** a caller tries to record it, **Then** the record is refused with an error and the caller's transaction fails.

---

### User Story 2 - Anyone can verify the chain with one click (Priority: P1)

Any signed-in user, including a donor, can ask the system to recompute the chain from the first entry to the latest. The answer says whether the chain is intact, how many entries it has, the head hash, and if broken, the first seq where it breaks and why.

**Why this priority**: This is the donor-facing proof of integrity (UC11 step 5, NFR7) and a headline demo moment.

**Independent Test**: Seed 1 000 entries, call verify and get `valid=true`. Then, as a database superuser with the guard disabled, change the payload of seq 500 and call verify again: get `valid=false`, `first_broken_seq=500`.

**Acceptance Scenarios**:

1. **Given** an intact chain of N entries, **When** any role calls verify, **Then** the result is `valid=true`, `count=N`, the head seq and head hash, and no broken seq.
2. **Given** the payload, actor, event type, entity or timestamp of entry K was altered, **When** verify runs, **Then** `valid=false`, `first_broken_seq=K`, reason `HASH_MISMATCH`.
3. **Given** entry K was removed, **When** verify runs, **Then** `valid=false`, `first_broken_seq=K`, reason `SEQ_GAP`.
4. **Given** the stored previous hash of entry K was rewritten, **When** verify runs, **Then** `valid=false`, `first_broken_seq=K`, reason `PREV_HASH_MISMATCH`.
5. **Given** a donor calls verify, **When** the response arrives, **Then** it contains no payloads, actors or entity ids.

---

### User Story 3 - The log cannot be edited, even by the application (Priority: P1)

The database itself refuses any edit or deletion of audit entries, not just the application code.

**Why this priority**: Principle III requires enforcement by a DB trigger, not by convention.

**Independent Test**: With the application's database login, try `UPDATE`, `DELETE` and `TRUNCATE` on the audit table; each fails and the row count is unchanged.

**Acceptance Scenarios**:

1. **Given** existing entries, **When** anyone runs an update on an entry, **Then** the database rejects it with an "append-only" error.
2. **Given** existing entries, **When** anyone deletes an entry or empties the table, **Then** the database rejects it.
3. **Given** a replay scenario is reset, **When** the replay starts again, **Then** the earlier run's audit entries are still there and a new, separate chain starts at seq 1.

---

### User Story 4 - Live and replay histories never mix (Priority: P2)

Each replay run (Sindh 2022, Buner 2025, the 2021 control) writes to its own chain. The live chain only ever contains live activity.

**Why this priority**: FR5 runs the real pipeline during demos. Mixing replay entries into the live chain would make the live record dishonest (Principle VIII).

**Independent Test**: Record entries with no scenario and with scenario A; each chain starts at seq 1, links only to itself, and verifies independently. Verify with the scenario header selects the scenario chain.

**Acceptance Scenarios**:

1. **Given** live and replay activity at the same time, **When** both record entries, **Then** each chain has its own gap-free seqs and they never link to each other.
2. **Given** a request with a scenario header, **When** verify or list is called, **Then** only that scenario's current run chain is used.

---

### User Story 5 - Officers browse and export the log (Priority: P2)

The PDMA officer and the admin can list audit entries with filters (event type, entity, actor, time range, seq range) and export a range as a file that an outside auditor can re-verify without access to the system.

**Why this priority**: Auditors and the PDMA need to inspect history; export makes the integrity claim checkable outside the system.

**Independent Test**: Filter by `event_type=PAYMENT_STATUS_CHANGED` and get only those entries, newest first. Export the whole chain and run the offline verifier: it prints `VALID` with the same head hash as the API.

**Acceptance Scenarios**:

1. **Given** entries of several types, **When** the officer filters by event type and date range, **Then** only matching entries return, paged.
2. **Given** a DC or donor, **When** they call the list or export, **Then** they get 403.
3. **Given** a full export, **When** the offline verifier runs on it, **Then** it reports the same validity, count and head hash as the verify endpoint.
4. **Given** a partial export (seq range starting above 1), **When** the offline verifier runs, **Then** it checks internal links and reports the first seq's expected previous hash for the auditor to match.

---

### Edge Cases

- Two transactions append at once: the second waits for the first to commit, then links to it.
- A business transaction records several entries: they get consecutive seqs in call order.
- A long-running transaction holds the chain lock: other writers wait. Callers must keep transactions short; this is a documented constraint, not a bug.
- Clock skew or a wall-clock step backwards: the stored timestamp is never earlier than the head's timestamp.
- Payload contains `Decimal`, UUID, dates, enums, nested lists or Urdu text: canonical JSON handles them deterministically.
- Payload contains a float that the database's JSON store would re-format (e.g. a very large number): rejected at record time so the hash always re-verifies.
- Payload contains NaN, infinity or a NUL character: rejected.
- Empty chain: verify returns `valid=true`, `count=0`, head hash = genesis.
- Rows written by the foundation stub before FR20 shipped: the migration hashes them once, in seq order, before locking the table.
- The tail of the chain is cut off (requires a superuser bypass): the chain alone cannot show this. The head seq and head hash in each donor PDF and export let anyone compare against a later verify (see clarify.md).

## Requirements *(mandatory)*

### Functional Requirements

- **FR20-001**: The system MUST write exactly one audit entry for each state change listed in Principle III, inside the same transaction as the change.
- **FR20-002**: Each entry MUST store: chain key, seq, event type, entity type, entity id, actor, payload, timestamp (UTC, microseconds), previous hash, hash, scenario id.
- **FR20-003**: The hash MUST be SHA-256 over the previous hash, the canonical JSON of the entry content, and the timestamp (see plan.md for the exact byte layout). The entry content MUST include the chain key, seq, event type, entity type, entity id, actor, scenario id and payload, so that changing any of them is detectable.
- **FR20-004**: Canonical JSON MUST be deterministic: sorted keys, no whitespace, UTF-8, exact decimals as strings, UUIDs and dates as ISO strings; NaN, infinity, NUL characters and floats of magnitude ≥ 10¹⁵ are rejected.
- **FR20-005**: The first entry of every chain MUST have seq 1 and previous hash = 64 zeros.
- **FR20-006**: Appends to one chain MUST be serialised so that seqs are gap-free and unique per chain, even under concurrent writers.
- **FR20-007**: The database MUST reject any update, delete or truncate of audit entries.
- **FR20-008**: Each live/replay context MUST have its own chain: one live chain, and one chain per replay scenario run. A replay reset MUST start a new chain rather than delete entries.
- **FR20-009**: The system MUST refuse payloads that contain beneficiary or user PII (names, phones, CNICs, wallet ids, emails, coordinates, OTPs, passwords).
- **FR20-010**: Verify MUST recompute the chain from seq 1 to the head and return: valid, count, head seq, head hash, first broken seq (or none), reason (`SEQ_GAP`, `PREV_HASH_MISMATCH`, `HASH_MISMATCH`), the chain key and when it ran.
- **FR20-011**: Verify MUST be available to every signed-in role, including donors, and MUST NOT return payloads, actors or entity ids.
- **FR20-012**: The officer and admin MUST be able to list entries with filters (event type, entity type, entity id, actor, from/to time, from/to seq), paged, newest first by default.
- **FR20-013**: The officer and admin MUST be able to export entries (whole chain or a seq/time range) as JSON Lines or CSV with every field needed to recompute hashes. Each export is itself audited.
- **FR20-014**: An offline verifier MUST re-verify an export file without database access.
- **FR20-015**: The service MUST keep a registry of known event types; unknown types in `UPPER_SNAKE` form are accepted and logged, malformed ones are refused.
- **FR20-016**: Stored timestamps MUST be non-decreasing along each chain.
- **FR20-017**: The DC and donor roles MUST get 403 on list and export.

### Key Entities

- **AuditLog** (data-model.md §8): one append-only entry. FR20 adds `chain_key` and makes `seq` unique per chain (see plan.md "Proposed data-model additions").
- **ReplayScenario** (§9): its run number selects the replay chain (owned by FR5).
- **User** (§8): roles decide access to list/export.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (NFR7, Principle III): 100% of state-changing service calls in the test suite produce exactly one audit entry; a cross-FR test counts entries per business action.
- **SC-002** (NFR7): Any single-field edit, row deletion or reordering in a 1 000-entry chain is detected, and the reported first broken seq is exact in 100% of the tamper tests.
- **SC-003** (NFR7): 0 successful `UPDATE`/`DELETE`/`TRUNCATE` statements against the audit table from the application login.
- **SC-004** (NFR1): Verify of 100 000 entries returns in < 5 s; list pages return p95 < 500 ms.
- **SC-005**: 20 concurrent writers × 50 entries each produce 1 000 gap-free seqs and a valid chain.
- **SC-006** (NFR8): 0 PII values in any audit payload in the full test suite (the PII guard raises, and a sweep test scans all stored payloads).
- **SC-007**: An exported chain re-verifies offline with the same head hash as the API.

## Assumptions

- PostgreSQL 16 is the only database; advisory locks and triggers are available.
- The application connects as the table owner in the MVP. A superuser can still disable the trigger; the hash chain makes that detectable. A non-owner app role is a production hardening step.
- Audit timestamps are wall-clock UTC, even during replays; the replay business date goes in the payload where it matters.
- Volume: up to a few hundred thousand entries per chain during the pilot.
- FR5 provides a replay run number that increments on reset.

## Traceability

| Item | How this FR covers it |
|---|---|
| UC01 step 6, UC02 step 5, UC06 step 7, UC07 step 4, UC08 step 4, UC09 step 4, UC10 step 3 | Each of these steps writes its entry through `AuditService.record` |
| UC11 step 5 | One-click verify (`GET /audit/verify`) |
| NFR7 Integrity | Hash chain, trigger, verify |
| NFR8 Privacy | PII guard; verify returns no payloads |
| NFR6 Security | Role checks on list/export |
| NFR13 Transparency | Separate replay chains; simulated activity never enters the live chain |
| Principle III, VI, VIII, XI | See plan.md Constitution Check |
| D5 | `AuditLog` is part of the canonical model; FR20 proposes two field changes |

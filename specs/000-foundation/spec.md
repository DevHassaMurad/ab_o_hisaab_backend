# Feature Specification: Foundation (shared backend platform)

**Feature Branch**: `000-foundation`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP (blocking)

**Input**: Constitution v1.0.0, plus NFR6 (role-based access, encrypted beneficiary data), NFR9 (last valid data, flagged), NFR11 (configuration only), NFR13 (simulated data labelled).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Each role signs in and sees only what it may see (Priority: P1)

A PDMA officer, a Deputy Commissioner, a donor and an admin each sign in with email and password. Each gets access only to their own areas. A DC is limited to their own district.

**Why this priority**: Every later feature relies on roles. Approvals (FR8), donor privacy (FR19) and settings (FR6) all depend on it.

**Independent Test**: Sign in as each seeded user, call an endpoint for another role and get "forbidden". Sign in as the Dadu DC, ask for Buner data and get "forbidden".

**Acceptance Scenarios**:

1. **Given** the seeded users, **When** the officer signs in with correct credentials, **Then** they receive a session token carrying the role `PDMA_OFFICER`.
2. **Given** a wrong password, **When** anyone signs in, **Then** access is refused with a generic message that doesn't say which part was wrong.
3. **Given** a donor token, **When** the donor requests the beneficiary list, **Then** the request is refused.
4. **Given** the Dadu DC's token, **When** they request Buner's payments, **Then** the request is refused.

---

### User Story 2 - Districts and tehsils are available as configuration (Priority: P1)

The system knows the pilot districts and tehsils for Sindh and KP: their boundaries, flood mode and vulnerability weight. These come from seed files, so adding a district needs no code change.

**Why this priority**: Ingestion, forecasting, the map and the funds are all keyed by district and tehsil.

**Independent Test**: Run the seed, then list districts with their tehsils as map shapes. Add a new district to the seed file, re-run the seed, and the new district appears.

**Acceptance Scenarios**:

1. **Given** an empty database, **When** the seed runs, **Then** every pilot district and tehsil exists with a boundary, and every active district has a default fund with tiers 40/60/80 and increments 10/30/60.
2. **Given** the seed has already run, **When** it runs again, **Then** nothing is duplicated.
3. **Given** a district marked inactive, **When** the daily pipeline runs, **Then** that district is skipped.

---

### User Story 3 - Beneficiary personal data is protected at rest (Priority: P1)

Names, phone numbers, CNICs and wallet ids are stored encrypted. They can still be looked up by phone for incoming WhatsApp replies.

**Why this priority**: NFR6 and Principle VI. Retro-fitting encryption later is expensive.

**Independent Test**: Insert a beneficiary, read the raw database row and see ciphertext. Look up the beneficiary by phone through the application and find them.

**Acceptance Scenarios**:

1. **Given** a stored beneficiary, **When** the raw table is queried directly, **Then** no name, phone, CNIC or wallet id appears in plain text.
2. **Given** an incoming phone number in any common format (`0300…`, `+92300…`, `92300…`), **When** it is looked up, **Then** it matches the same beneficiary.

---

### User Story 4 - Replays never mix with live data (Priority: P2)

Any data written while replaying a past event is tagged with that replay. Live screens never show it, and replay screens show only that replay.

**Why this priority**: The demo runs the real pipeline on 2022 data. That data must never appear as live payments.

**Independent Test**: Write the same kind of record once live and once inside a replay. Live reads return one record; replay reads return the other.

**Acceptance Scenarios**:

1. **Given** a record written inside the `sindh-2022` replay, **When** live payments are listed, **Then** the record is absent.
2. **Given** the same replay is selected, **When** payments are listed, **Then** only replay records appear.

---

### User Story 5 - Simulated and stale data is always labelled (Priority: P2)

Any response that contains simulated data (payments, register, replay approvals) or stale data (old weather) says so in the data and in a response header.

**Why this priority**: NFR13 and NFR9. Honesty in the demo.

**Independent Test**: Request a simulated payment and see `simulated: true` plus the response header. Mark a data source stale and see the health endpoint report it with the date of its last valid data.

**Acceptance Scenarios**:

1. **Given** a simulated payment, **When** it is read, **Then** it is labelled simulated.
2. **Given** NASA POWER failed today, **When** data-source health is read, **Then** it shows unhealthy, the date of the last valid data and the last error.

### Edge Cases

- An expired or tampered token is rejected, and a role claim that doesn't match the database is rejected.
- A DC account without a district cannot be created.
- A missing or wrong encryption key stops start-up with a clear error. It never stores plaintext.
- A boundary file with invalid geometry is repaired or rejected with the P-code named.
- Two seed runs at once must not create duplicates (seeding is idempotent and keyed by P-code).

## Requirements *(mandatory)*

### Functional Requirements

- **F0-001**: The system MUST authenticate users with email and password and issue a time-limited token carrying the role and, for a DC, the district.
- **F0-002**: The system MUST enforce the roles `PDMA_OFFICER`, `DC`, `DONOR` and `ADMIN` on every endpoint, and limit a DC to their own district.
- **F0-003**: The system MUST store districts and tehsils with boundaries, flood mode, vulnerability weight and an active flag, loaded from seed files.
- **F0-004**: The system MUST create a default fund (tiers 40/60/80, increments 10/30/60, split 70/30) for every active district at seed time.
- **F0-005**: The system MUST encrypt beneficiary and user PII at rest and support lookup by a normalised phone hash.
- **F0-006**: The system MUST tag every operational record with its replay scenario, or with none for live data, and scope reads accordingly.
- **F0-007**: The system MUST label simulated records and responses, and expose data-source health with staleness.
- **F0-008**: The system MUST provide replaceable shared services (audit, alerts, explanations, payment rail, trigger engine, allocation, clock) so features can be built in parallel.
- **F0-009**: The system MUST provide feature flags for the roadmap features (FR13, FR16, FR21 live, FR22), all off by default.
- **F0-010**: The system MUST run scheduled jobs (daily pipeline and timers) and allow an admin to trigger the daily pipeline on demand.

### Key Entities

`User`, `District`, `Tehsil`, `DisasterFund`, `TriggerTier`, `DataSourceStatus` and `ReplayScenario` (the full model is in [data-model.md](data-model.md)).

## Success Criteria *(mandatory)*

- **SC-001**: 100% of endpoints declare a role requirement, as checked by an automated test that walks the route table.
- **SC-002**: 0 plaintext PII values in a raw dump of the beneficiaries and users tables.
- **SC-003**: Adding a district needs only a seed-file change. This is proven by a test that seeds an extra district.
- **SC-004**: A cold start to a healthy API takes under 60 seconds on a laptop.
- **SC-005**: Dashboard read endpoints answer in under 500 ms p95 with the full pilot seed (supports NFR1).

## Assumptions

- The pilot covers Sindh and KP only. Other provinces exist in the enum but aren't seeded.
- The demo uses email/password accounts. SSO is out of scope.
- One API instance. Horizontal scaling is out of scope for the MVP.
- OCHA COD-AB boundaries for Pakistan (adm2 = district, adm3 = tehsil) are used under their licence.

## Traceability

NFR6, NFR8 (via `DonorScope`), NFR9, NFR11, NFR13 · Principles II, III, VI, VII, VIII, IX, X · Decision D5 (data model).

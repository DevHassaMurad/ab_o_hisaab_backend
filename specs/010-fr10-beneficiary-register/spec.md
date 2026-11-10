# Feature Specification: FR10 Beneficiary Register

**Feature Branch**: `010-fr10-beneficiary-register`

**Created**: 2026-11-10

**Status**: Draft

**Priority**: MVP

**Input**: "FR10 Store households and shopkeepers with location, contact and type specific data" (Area: Beneficiaries, Priority: MVP)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Load a register from CSV (Priority: P1)

The PDMA officer uploads a CSV of households and shopkeepers for a district before the monsoon. The system checks every row, shows a report of errors by row number, and stores valid rows with personal details encrypted. Re-uploading the same file updates the rows instead of duplicating them.

**Why this priority**: UC06 has the precondition "a beneficiary register exists". Without it, no ranking, amount or payment can happen.

**Independent Test**: Upload `data/seeds/beneficiaries_sample.csv` with dry run, then for real, and query the list. Delivers a usable register on its own.

**Acceptance Scenarios**:

1. **Given** a valid CSV with 20 households and 5 shopkeepers in Dadu tehsils, **When** the officer imports it, **Then** 25 beneficiaries are stored, each with an encrypted name, phone, CNIC and wallet id, a `phone_hash`, a derived `flood_exposure`, and one audit entry is written for the import.
2. **Given** the same CSV with one row whose phone is `021-1234567` (a landline), **When** the officer imports it in the default mode, **Then** nothing is saved and the report lists row 7 with code `PHONE_INVALID`.
3. **Given** the same file in `skip_rows` mode, **When** imported, **Then** 24 rows are saved and row 7 is reported as rejected.
4. **Given** a file already imported, **When** it is imported again with one changed family size, **Then** one row is updated, zero are created, and the report says so.
5. **Given** `dry_run=true`, **When** the officer imports, **Then** the report is returned and nothing is saved or audited.

---

### User Story 2 - Generate a synthetic register for the demo (Priority: P1)

An admin or the officer generates a simulated register for the replay districts (Dadu and nearby Sindh districts, Buner) with realistic household sizes, poverty, wallet coverage and shop stock. Every row is labelled simulated.

**Why this priority**: The MVP has no real register (Principle VIII). The 3-minute replay demo needs one, and it must be the same every run.

**Independent Test**: Run the generator twice with seed 2022 and compare: identical ids and values. Check distributions against the configured targets.

**Acceptance Scenarios**:

1. **Given** the Dadu district and sample rate 0.01, **When** the generator runs with seed 2022, **Then** about 1% of the estimated households are created across Dadu's tehsils in proportion to tehsil population, plus shopkeepers at the configured ratio, all with `simulated=true` and `register_ref` starting `SYN-`.
2. **Given** the same inputs and seed, **When** it runs again, **Then** no new rows are created (idempotent upsert) and every field is identical.
3. **Given** a generated Sindh register, **When** statistics are computed, **Then** the mean household size is within ±0.3 of the configured Sindh mean and the share of households with no wallet is within ±3 percentage points of the configured value.
4. **Given** a demo persona file with team phone numbers, **When** generation runs, **Then** those personas are created with their real (encrypted) phones and `simulated=true`, so WhatsApp demo messages reach the team.

---

### User Story 3 - Maintain individual records (Priority: P2)

The officer adds, edits or deactivates a single beneficiary, for example when a phone number is flagged invalid after a failed WhatsApp message (UC10-1a).

**Why this priority**: Needed to fix flagged records during the season; bulk import covers the initial load.

**Independent Test**: Create, edit, deactivate a beneficiary through the API and check audit entries and list results.

**Acceptance Scenarios**:

1. **Given** a beneficiary with `phone_valid=false`, **When** the officer updates the phone to a valid number, **Then** the phone is re-encrypted, `phone_hash` is recomputed, `phone_valid` becomes true, and an audit entry records which fields changed (names only, no values).
2. **Given** a household, **When** the officer tries to set `stock_value_pkr`, **Then** the request is rejected with `FIELD_NOT_ALLOWED_FOR_TYPE`.
3. **Given** a beneficiary referenced by allocations, **When** the officer deletes it, **Then** it is deactivated (not removed), keeps its history, and is excluded from future rankings.

---

### User Story 4 - Role-scoped access with masked PII (Priority: P2)

The DC sees beneficiaries of their own district only, with phone, CNIC and wallet masked. A donor cannot see any beneficiary. The officer can reveal a single record's PII, and that reveal is audited.

**Why this priority**: NFR6 and NFR8 are non-negotiable for a money system.

**Independent Test**: Call the list and detail routes with each seeded role.

**Acceptance Scenarios**:

1. **Given** the Dadu DC, **When** they list beneficiaries, **Then** only Dadu rows are returned and phones look like `+92300*****67`.
2. **Given** the Dadu DC, **When** they request a Buner beneficiary by id, **Then** the response is 404 (not 403, so existence is not leaked).
3. **Given** a donor, **When** they call any `/beneficiaries` route, **Then** the response is 403.
4. **Given** the officer, **When** they call reveal on one beneficiary, **Then** the full phone, CNIC and wallet id are returned and a `PII_REVEALED` audit entry is written.

---

### User Story 5 - Exposure and register statistics (Priority: P3)

The system derives each beneficiary's flood exposure from elevation, distance to river and flood history, and reports per-tehsil counts, coverage and data completeness. FR11 uses these for ranking and the fairness check.

**Why this priority**: Ranking works with a default exposure, but the fairness check (NFR5) needs coverage and completeness.

**Independent Test**: Import rows with and without exposure inputs, call recompute, then call the stats endpoint.

**Acceptance Scenarios**:

1. **Given** a row with `elevation_m`, `river_distance_km=1.5` and `floods_last_10y=2`, **When** exposure is derived, **Then** `flood_exposure` equals the documented formula result and `exposure_inputs` stores each component and `imputed=false`.
2. **Given** a row without exposure inputs, **When** exposure is derived, **Then** tehsil-level values are used, missing components take 0.5, and `exposure_inputs.imputed` lists them.
3. **Given** a district, **When** the officer requests stats, **Then** each tehsil shows household and shopkeeper counts, estimated households, coverage, relative coverage and completeness.

---

### Edge Cases

- CSV saved by Excel with a UTF-8 BOM, Windows line endings or Urdu names: accepted.
- CSV with an unknown column: rejected with `UNKNOWN_COLUMN` (prevents silent mis-mapping). A missing required column fails the whole file with `MISSING_COLUMN`.
- Two rows with the same `register_ref` in one file: both rejected with `DUPLICATE_IN_FILE`.
- Same CNIC under a different `register_ref` and the same type: rejected with `DUPLICATE_CNIC`. One person may be both a household head and a shopkeeper (two rows, two types).
- Two households sharing one phone (family phone): allowed. `phone_hash` is not unique; FR15 matches by the latest open payment notice.
- `wallet_provider=NONE` with a `wallet_id`: rejected. `JAZZCASH`/`EASYPAISA` without a wallet id: rejected (`WALLET_ID_REQUIRED`).
- Coordinates outside Pakistan: rejected. Coordinates inside Pakistan but outside the given tehsil: accepted with warning `POINT_OUTSIDE_TEHSIL`.
- `tehsil_pcode` not found or belonging to an inactive district: rejected.
- File above 100 000 rows or 20 MB: rejected with `FILE_TOO_LARGE` before parsing.
- Concurrent imports into the same district: the second waits on a district-level advisory lock.
- Editing a beneficiary while an allocation for them is in draft: allowed; FR11's recompute picks up the change; saved allocations keep their stored `score_inputs`.
- Register is shared between live and replay runs (see clarify Q4); generator output for a replay district is the same rows used live.

## Requirements *(mandatory)*

### Functional Requirements

- **FR10-001**: System MUST store beneficiaries of type `HOUSEHOLD` or `SHOPKEEPER`, each linked to one tehsil, with optional point location, contact phone, preferred language, wallet provider and wallet id.
- **FR10-002**: System MUST store household fields (`family_size` required, `poverty_score` 0–1 optional) and shopkeeper fields (`stock_value_pkr` and `shop_type` required), and reject type-inappropriate fields.
- **FR10-003**: System MUST store `existing_support_pkr` (support received in the last 12 months, default 0) for both types.
- **FR10-004**: System MUST encrypt name, phone, CNIC and wallet id at rest and MUST never write them to logs or audit payloads.
- **FR10-005**: System MUST normalise phones to E.164 and accept only Pakistani mobile numbers matching `+923` followed by 9 digits. It MUST store a hash of the normalised phone for lookup.
- **FR10-006**: System MUST normalise CNIC to `XXXXX-XXXXXXX-X`, store a hash for de-duplication, and reject a second beneficiary of the same type with the same CNIC.
- **FR10-007**: System MUST let the PDMA officer create, read, list (with filters by district, tehsil, type, active, simulated, phone validity), update and deactivate beneficiaries. Deactivation MUST keep the row and its history.
- **FR10-008**: System MUST import beneficiaries from a CSV in the documented format, with a dry-run option, an all-or-nothing default mode, an optional skip-invalid-rows mode, upsert by `register_ref`, and a report listing each rejected row with its row number, column and error code.
- **FR10-009**: System MUST derive `flood_exposure` (0–1) for every beneficiary on create, update and import, store the derivation inputs, and offer a recompute per district.
- **FR10-010**: System MUST provide a synthetic register generator that is deterministic for a given seed, creates rows in proportion to tehsil population, uses configured Sindh and KP distributions, and labels every row simulated.
- **FR10-011**: System MUST support demo personas whose real phone numbers are supplied from configuration outside the repository.
- **FR10-012**: System MUST restrict writes to the PDMA officer and admin, reads to the officer, admin and the DC of the beneficiary's district, and deny donors entirely.
- **FR10-013**: System MUST mask phone, CNIC and wallet id in list and detail responses and offer an audited single-record reveal to the PDMA officer only.
- **FR10-014**: System MUST write one audit entry per create, update, deactivate, import, generate, exposure recompute and reveal, in the same transaction, with ids, hashes, field names and counts only.
- **FR10-015**: System MUST report per-tehsil register statistics: household and shopkeeper counts, estimated households, coverage, relative coverage, data completeness.
- **FR10-016**: System MUST expose `phone_valid` and reset it to true when the phone is changed.

### Key Entities

- **Beneficiary** (data-model.md §5): the register row. Uses the existing fields plus the proposed `register_ref`, `cnic_hash`, `is_active`, `deactivated_at`, `deactivated_reason`, `exposure_inputs` (plan.md).
- **Tehsil**, **District** (§1): location, population, elevation and province for distributions and exposure.
- **SatelliteFloodMap** (§2): optional flood-history input.
- **AuditLog** (§8): one entry per register change.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A 10 000-row CSV validates and imports in under 60 seconds (dry run under 30 seconds).
- **SC-002**: 100% of PII columns are ciphertext when read with raw SQL; 0 PII values appear in audit payloads or logs during the test suite (NFR6).
- **SC-003**: Donor tokens get 403 on 100% of `/beneficiaries` routes; DC tokens see 0 rows outside their district (NFR6, NFR8).
- **SC-004**: The synthetic generator is byte-for-byte reproducible for a seed, and 100% of generated rows carry `simulated=true` (NFR13).
- **SC-005**: Generated distributions are within tolerance of configuration: mean household size ±0.3, no-wallet share ±3 pp, shopkeeper ratio ±10% relative.
- **SC-006**: List endpoint p95 under 500 ms for a page of 50 with 50 000 rows in the table (NFR1).
- **SC-007**: Adding a new district's register needs only a CSV or a generator config entry, no code change (NFR11).

## Assumptions

- The MVP register is synthetic. Real NSER/BISP data access is out of scope.
- Phone numbers are mobile numbers; WhatsApp needs a mobile.
- JazzCash and Easypaisa mobile accounts use the account holder's mobile number as the wallet id (`03XXXXXXXXX` form), which we normalise the same way as phones.
- Tehsil population and SRTM mean elevation are loaded by the foundation seeds and FR2. If elevation is missing, a neutral 0.5 is used.
- One CSV covers any number of districts; the officer's scope is province-wide in the MVP.
- The register is reference data shared by live runs and replays (clarify Q4).

## Traceability

| Item | How FR10 covers it |
|---|---|
| UC06 precondition | "A beneficiary register exists" |
| UC06-1 | Supplies households and shopkeepers per tehsil |
| UC10 precondition, UC10-1a | Phone on record; `phone_valid` flag and officer correction |
| UC08 | Wallet provider and id for payments |
| NFR6 | Encryption, RBAC, masking |
| NFR8 | Donor 403 on all register routes |
| NFR10 | `preferred_language` (default Urdu) |
| NFR11 | Districts added by data only |
| NFR13 | Synthetic rows labelled simulated |
| Principle III / NFR7 | Audit on every change |
| D5 | `Beneficiary` entity as defined in the canonical data model |

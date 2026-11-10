# Agent Brief: FR10 Beneficiary Register

## Owner role

**Backend & alerts**

## Mission

Store the households and shopkeepers who can receive anticipatory cash, with location, contact and type-specific data, encrypted PII and a derived flood-exposure value. Give the PDMA officer CRUD and a CSV import, and give the demo a deterministic **synthetic register** (labelled simulated) with realistic Sindh and KP distributions. Every later step (ranking, amounts, payments, alerts, confirmations) reads from this register.

## Scope

**In (MVP)**
- `Beneficiary` CRUD (create, read, list with filters, update, soft-deactivate).
- CSV import with dry run, row-level validation report, upsert on `register_ref`, all-or-nothing by default.
- Pakistani mobile validation and normalisation to E.164 `+923XXXXXXXXX`; CNIC normalisation; wallet id validation.
- Column-level Fernet encryption of `name`, `phone`, `cnic`, `wallet_id`; `phone_hash` and `cnic_hash` for lookup and de-duplication.
- Masked PII in list and detail responses; an audited "reveal" for the PDMA officer.
- Flood-exposure derivation (`flood_exposure` 0–1) from elevation, river distance and flood history, recomputed on import and on demand.
- Synthetic register generator (seeded, deterministic, `simulated=true`) for the replay districts.
- Register statistics per tehsil (counts, coverage, completeness) used by the FR11 fairness check.
- RBAC: officer (and admin) write; DC reads own district only; donor gets 403 everywhere.

**Out**
- Ranking and amounts (FR11, FR12).
- Setting `phone_valid=false` after a failed send (FR14 owns that write, UC10-1a); FR10 only exposes the field and lets the officer correct the number.
- Real BISP/NSER integration, CNIC verification with NADRA, live wallet verification (roadmap; noted in clarify).
- Beneficiary self-registration (roadmap).

## Read first

1. `specs/_authoring-guide.md`
2. `.specify/memory/constitution.md` (Principles III, VI, VIII, IX)
3. `specs/000-foundation/data-model.md` §1 Geography, §5 Beneficiary
4. `specs/000-foundation/plan.md` (crypto.py, security.py, API conventions)
5. `specs/010-fr10-beneficiary-register/spec.md`, `clarify.md`, `plan.md`
6. `specs/011-fr11-beneficiary-ranking/plan.md` (what FR11 reads from the register)

## Depends on / Blocks

| Direction | FR | Reason |
|---|---|---|
| Depends on | 000-foundation | `EncryptedString`, `normalise_phone`, `phone_hash`, `require_role`, `Tehsil`/`District` seeds |
| Depends on | FR20 `020-fr20-audit-log` | `AuditService.record` for every register change (stub works until FR20 merges) |
| Soft-depends | FR2 `002-fr02-earth-engine-ingestion` | `Tehsil.elevation_mean_m` from SRTM feeds exposure; a neutral default is used if missing |
| Soft-depends | FR21 `021-fr21-satellite-verification` | Precomputed `SatelliteFloodMap.flooded_pct` can feed flood history; CSV column used otherwise |
| Blocks | FR11, FR12 | Need beneficiaries with factors to rank and price |
| Blocks | FR14, FR15 | Need encrypted phone, `phone_hash`, `preferred_language` |
| Blocks | FR17 | Needs `wallet_provider` and `wallet_id` |
| Blocks | FR5 | Replays need a register in Dadu/Buner and nearby districts |
| Blocks | FR22 | Survey recipients |

## Interfaces

**Consumes**
- `AuditService.record(db, *, event_type, entity_type, entity_id, actor, payload)` (FR20)
- `app.core.crypto`: `EncryptedString`, `normalise_phone`, `phone_hash`
- `app.core.security.require_role`, `get_current_user`
- `Clock.now()`

**Provides**
- REST: `/api/v1/beneficiaries*` (see plan.md)
- Python: `app.services.beneficiaries.stats.tehsil_register_stats(db, tehsil_ids) -> dict[UUID, TehsilRegisterStats]` (used by FR11 fairness)
- Python: `app.services.beneficiaries.register.eligible_query(db, *, tehsil_ids, type) -> Select` (active beneficiaries, used by FR11)
- Python: `app.services.beneficiaries.validation.validate_pk_mobile(raw) -> str` (reused by FR14 / FR15)
- Script: `scripts/generate_synthetic_register.py`

## Files you own

```text
app/api/v1/beneficiaries.py
app/schemas/beneficiaries.py
app/services/beneficiaries/__init__.py
app/services/beneficiaries/register.py
app/services/beneficiaries/validation.py
app/services/beneficiaries/csv_import.py
app/services/beneficiaries/exposure.py
app/services/beneficiaries/synthetic.py
app/services/beneficiaries/masking.py
app/services/beneficiaries/stats.py
alembic/versions/0010_fr10_beneficiary_register.py
scripts/generate_synthetic_register.py
data/seeds/synthetic_register.yaml
data/seeds/synthetic_names.yaml
data/seeds/demo_personas.example.yaml
data/seeds/beneficiaries_sample.csv
tests/unit/beneficiaries/test_validation.py
tests/unit/beneficiaries/test_csv_parse.py
tests/unit/beneficiaries/test_exposure.py
tests/unit/beneficiaries/test_synthetic.py
tests/unit/beneficiaries/test_masking.py
tests/integration/beneficiaries/test_register_crud.py
tests/integration/beneficiaries/test_csv_import.py
tests/integration/beneficiaries/test_pii_encryption.py
tests/integration/beneficiaries/test_rbac.py
tests/contract/test_beneficiaries_api.py
```

`app/models/beneficiaries.py` is created by the foundation; FR10 adds the proposed fields there once the foundation owner merges them into data-model.md.

## Guardrails

| Principle | How it applies here |
|---|---|
| III Traceability | Every create, update, deactivate, import, generate, exposure recompute and PII reveal writes one audit entry in the same transaction. Payloads hold ids, hashes and counts, never raw PII |
| VI Privacy | PII columns use `EncryptedString`. Lists mask phone/CNIC/wallet. Donors get 403 on every route. DC is scoped to own district. PII never appears in logs |
| VIII Honest prototype | Generated rows have `simulated=true`, `register_ref` prefix `SYN-`, and responses carry `simulated` plus the `X-Simulated-Data` header |
| IX Config over code | Distributions, sample rate, seed and exposure weights live in `data/seeds/synthetic_register.yaml` and settings, not code |
| XI Tests | Not a money path, but contract tests protect the CSV format, RBAC and encryption |

## Definition of Done

- [ ] Migration `0010` applied; proposed fields merged into data-model.md by the foundation owner
- [ ] CRUD endpoints pass contract and RBAC tests (officer write, DC own-district read, donor 403)
- [ ] CSV import: dry run, strict mode, skip-rows mode, upsert by `register_ref`, error report with row numbers and codes
- [ ] Phone normalisation accepts `03XXXXXXXXX`, `3XXXXXXXXX`, `923XXXXXXXXX`, `+92 3XX XXXXXXX`, `00923…` and rejects landlines and non-PK numbers
- [ ] PII is ciphertext in the database (raw SQL test) and masked in list responses
- [ ] `flood_exposure` and `exposure_inputs` are set for every row; recompute endpoint works
- [ ] Synthetic generator is deterministic for a given seed, labels rows simulated, and produces the configured distributions within tolerance
- [ ] Every state change has exactly one audit entry with no raw PII
- [ ] `tehsil_register_stats` returns counts, coverage and completeness for FR11
- [ ] ruff and mypy clean

## Hand-off notes

- **FR11** reads `poverty_score`, `family_size`, `stock_value_pkr`, `flood_exposure`, `existing_support_pkr` and `tehsil_register_stats`. `poverty_score` may be null (FR11 imputes); `family_size` is required for households and `stock_value_pkr` for shopkeepers.
- **FR14** must refuse to message `simulated=true` beneficiaries unless the phone is in `DEMO_PHONE_ALLOWLIST`. Synthetic numbers may belong to real people.
- **FR14** sets `phone_valid=false` (UC10-1a). Updating the phone via `PATCH` resets `phone_valid=true`.
- **FR15** looks up senders by `phone_hash`; FR10 guarantees the hash is computed from the normalised E.164 number with `app.core.crypto.phone_hash`.
- **FR17** reads `wallet_provider` and the decrypted `wallet_id`. The generator deliberately makes about 2% of wallet ids end in `000` so FR17's `INVALID_WALLET` path shows in the demo.
- **FR5** should call `scripts/generate_synthetic_register.py --scenario <key>` (or the API) when seeding a replay, with the same seed every time.

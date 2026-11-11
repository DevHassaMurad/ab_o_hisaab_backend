# Implementation Plan: FR10 Beneficiary Register

**Branch**: `010-fr10-beneficiary-register` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/010-fr10-beneficiary-register/spec.md`

## Summary

A register of households and shopkeepers with encrypted PII, derived flood exposure, CSV import, a deterministic synthetic generator for the demo, and role-scoped, masked read access. It is plain CRUD on the canonical `Beneficiary` entity plus three services: validation/import, exposure derivation, and synthetic generation. It also exposes per-tehsil register statistics for the FR11 fairness check.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy 2.x, GeoAlchemy2 (point-in-tehsil check), cryptography (Fernet via `app.core.crypto.EncryptedString`), numpy (generator RNG), PyYAML, stdlib `csv`

**Storage**: PostgreSQL 16 + PostGIS. Table `beneficiary` (foundation) plus FR10 migration for proposed fields and indexes

**Testing**: pytest, httpx TestClient, factory-boy `BeneficiaryFactory`, Postgres test DB

**Target Platform**: Linux container; Windows/macOS dev

**Project Type**: web-service

**Performance Goals**: list p95 < 500 ms (page 50, 50k rows); 10k-row import < 60 s; generator 50k rows < 60 s

**Constraints**: no PII in logs or audit; money as `Decimal`; UTC; idempotent import and generation

**Scale/Scope**: up to 50 000 beneficiaries (pilot), 68 districts, ~260 tehsils

## Constitution Check

| Principle | How satisfied | Status |
|---|---|---|
| II Human authority | Not affected (no money moves here) | ✅ |
| III Traceability | `AuditService.record` in the same transaction for create, update, deactivate, import (one entry per batch with file SHA-256 and counts), generate, exposure recompute, PII reveal | ✅ |
| IV Explainable | `exposure_inputs` stores every component and imputation of `flood_exposure` | ✅ |
| VI Privacy | `EncryptedString` on name, phone, cnic, wallet_id; masks in schemas; donor 403; DC scoped by `tehsil.district_id`; structlog processor drops PII keys | ✅ |
| VII Degrade gracefully | Missing elevation or history → 0.5 with `imputed` list, never silent | ✅ |
| VIII Honest prototype | Generator sets `simulated=true`, `SYN-` refs; responses include `simulated`; header middleware fires | ✅ |
| IX Config over code | Distributions, sample rate, seed, exposure weights in YAML/settings | ✅ |
| X Simplicity | stdlib csv, no Celery; import runs in-request (≤100k rows) | ✅ |
| XI Tests | Contract tests on CSV format, RBAC, encryption (not a money path) | ✅ |

## Research decisions

| Decision | Rationale | Alternatives rejected |
|---|---|---|
| Upsert key `register_ref` (string supplied by the source) | Re-imports are idempotent; matches how PDMA lists carry their own ids | Matching on encrypted name/phone (impossible without decrypting all rows); CNIC (optional) |
| All-or-nothing import by default | Easy to audit, mirrors UC01-5a | Always partial (silent gaps) |
| Exposure from 3 components with fixed weights | Uses data already in the system, explainable | ML exposure model (no labels, not explainable in 32 h) |
| Relative coverage for fairness | Synthetic register is a sample; absolute coverage would mark every tehsil sparse | Absolute coverage threshold |
| Deterministic RNG per tehsil + `uuid5` ids | Same register every demo run; tehsils can be regenerated independently | Global RNG (adding a tehsil reshuffles everyone) |
| Soft delete | History integrity for allocations, payments, audit | Hard delete with cascade |
| Masking in Pydantic schema, reveal via separate audited POST | Hard to leak by accident; reveal is explicit and logged | Query flag `?reveal=true` on GET (easy to cache/log) |

## Data model slice

| Entity | Fields touched | Access |
|---|---|---|
| `Beneficiary` | all fields | read/write |
| `Tehsil` | `pcode`, `district_id`, `population`, `elevation_mean_m`, `geom` | read |
| `District` | `province`, `vulnerability_weight`, `is_active` | read |
| `SatelliteFloodMap` | `tehsil_id`, `flooded_pct`, `scenario_id IS NULL` or precomputed | read (flood history) |
| `AuditLog` | via `AuditService` | append |

## Proposed data-model additions

> ✅ **Merged** into `000-foundation/data-model.md` v1.1 (2026-11-10). That file is now canonical; this table is kept for history.

To be merged by the foundation owner into `specs/000-foundation/data-model.md` §5 `Beneficiary`:

| Field | Type | Notes |
|---|---|---|
| `register_ref` | str, unique | Source register id; upsert key for CSV import. Synthetic rows use `SYN-<tehsil_pcode>-<H\|S>-<nnnnn>` |
| `cnic_hash` | str, nullable | `sha256(normalised CNIC)` (or HMAC if foundation adopts it). Partial unique index on `(type, cnic_hash) WHERE cnic_hash IS NOT NULL` |
| `is_active` | bool, default true | Inactive rows are excluded from ranking |
| `deactivated_at` | timestamptz, nullable | |
| `deactivated_reason` | text, nullable | Required when deactivating |
| `exposure_inputs` | JSONB | `{"method":"exposure-v1","elevation_m":..,"elevation_component":..,"river_distance_km":..,"river_component":..,"floods_last_10y":..,"history_component":..,"history_source":"CSV\|SATELLITE\|DEFAULT","weights":{...},"imputed":["river"]}` |

Indexes (FR10 migration): `(tehsil_id, type, is_active)`, `phone_hash`, `simulated`.

## CSV format

UTF-8 (BOM allowed), comma delimiter, `"` quoting, header row required, one beneficiary per row. Max 100 000 rows and 20 MB.

| Column | Required | Type / rule | Example |
|---|---|---|---|
| `register_ref` | yes | 1–64 chars `[A-Za-z0-9_-]`, unique in file | `DADU-HH-000123` |
| `type` | yes | `HOUSEHOLD` \| `SHOPKEEPER` | `HOUSEHOLD` |
| `tehsil_pcode` | yes | existing tehsil in an active district | `PK60501` |
| `name` | yes | 2–120 chars, Urdu or Latin | Ghulam Rasool |
| `phone` | yes | PK mobile, normalised to `+923XXXXXXXXX` | `0301-2345678` |
| `cnic` | no | 13 digits, dashes optional | `41101-1234567-1` |
| `wallet_provider` | yes | `JAZZCASH` \| `EASYPAISA` \| `NONE` | `JAZZCASH` |
| `wallet_id` | if provider ≠ NONE | PK mobile form `03XXXXXXXXX`; empty if `NONE` | `03012345678` |
| `lat`, `lon` | no (both or neither) | inside Pakistan bbox 23.5–37.1 N, 60.8–77.9 E | `26.73`, `67.78` |
| `preferred_language` | no | `UR` (default) \| `EN` | `UR` |
| `poverty_score` | no (household only) | 0–1, 1 = poorest | `0.82` |
| `family_size` | household: yes | int 1–30 | `9` |
| `stock_value_pkr` | shopkeeper: yes | decimal 0–10 000 000 | `180000` |
| `shop_type` | shopkeeper: yes | `KIRYANA`, `CLOTH`, `AGRI_INPUTS`, `PHARMACY`, `HARDWARE`, `FOOD_STALL`, `MOBILE_ELECTRONICS`, `OTHER` | `KIRYANA` |
| `existing_support_pkr` | no | decimal ≥ 0, default 0 | `54000` |
| `elevation_m` | no | float −10–8000 | `41` |
| `river_distance_km` | no | float ≥ 0 | `1.5` |
| `floods_last_10y` | no | int 0–10 | `2` |

Row error codes: `MISSING_REQUIRED`, `INVALID_TYPE`, `TEHSIL_NOT_FOUND`, `DISTRICT_INACTIVE`, `PHONE_INVALID`, `CNIC_INVALID`, `WALLET_ID_REQUIRED`, `WALLET_ID_NOT_ALLOWED`, `WALLET_ID_INVALID`, `OUT_OF_RANGE`, `FIELD_NOT_ALLOWED_FOR_TYPE`, `COORDS_INCOMPLETE`, `COORDS_OUTSIDE_PAKISTAN`, `DUPLICATE_IN_FILE`, `DUPLICATE_CNIC`. Warnings (row still accepted): `POINT_OUTSIDE_TEHSIL`. File error codes: `MISSING_COLUMN`, `UNKNOWN_COLUMN`, `FILE_TOO_LARGE`, `NOT_UTF8`, `EMPTY_FILE`.

## Exposure derivation (`exposure-v1`)

```
E = 1 − clamp((elev − d_min) / (d_max − d_min), 0, 1)     elev = elevation_m or tehsil.elevation_mean_m
                                                         d_min/d_max = min/max tehsil.elevation_mean_m in the district
                                                         d_max == d_min or elev missing → 0.5 (imputed)
R = 1 − clamp(river_distance_km / 10, 0, 1)              missing → 0.5 (imputed)
H = clamp(floods_last_10y / 3, 0, 1)                     else clamp(max tehsil flooded_pct / 30, 0, 1) from precomputed maps
                                                         else 0.5 (imputed)
flood_exposure = round(0.40·E + 0.35·R + 0.25·H, 3)       weights = settings.EXPOSURE_WEIGHTS
```

## Register statistics (`stats.py`)

Per tehsil: `households`, `shopkeepers` (active only), `estimated_households = population / HOUSEHOLD_SIZE_BY_PROVINCE[province]`, `coverage = households / estimated_households`, `relative_coverage = coverage / median(coverage over district tehsils with population > 0)` (0 if median is 0), `completeness` (share of active beneficiaries with no imputed ranking input). Returned as `TehsilRegisterStats` dataclass.

## API contracts

Tag: **Beneficiaries**. Base `/api/v1`. Errors are RFC 7807 problem JSON with `code`.

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/beneficiaries` | PDMA_OFFICER, ADMIN, DC (own district) | Paged list with filters |
| POST | `/beneficiaries` | PDMA_OFFICER, ADMIN | Create one |
| GET | `/beneficiaries/{id}` | PDMA_OFFICER, ADMIN, DC (own district, else 404) | Detail (masked) |
| PATCH | `/beneficiaries/{id}` | PDMA_OFFICER, ADMIN | Update fields |
| DELETE | `/beneficiaries/{id}` | PDMA_OFFICER, ADMIN | Deactivate (body: reason) |
| POST | `/beneficiaries/{id}/reveal` | PDMA_OFFICER | Decrypted PII, audited |
| POST | `/beneficiaries/import` | PDMA_OFFICER, ADMIN | CSV upload (multipart) |
| GET | `/beneficiaries/import/template` | PDMA_OFFICER, ADMIN | Header-only CSV |
| POST | `/beneficiaries/synthetic` | PDMA_OFFICER, ADMIN | Generate simulated register |
| POST | `/beneficiaries/exposure/recompute` | PDMA_OFFICER, ADMIN | Recompute exposure for a district |
| GET | `/beneficiaries/stats` | PDMA_OFFICER, ADMIN, DC (own district) | Per-tehsil statistics |

DONOR → 403 `FORBIDDEN_ROLE` on every route above.

**GET `/beneficiaries?district_id=&tehsil_id=&type=HOUSEHOLD&is_active=true&simulated=&phone_valid=false&register_ref=&page=1&page_size=50`**

```json
{
  "items": [
    {
      "id": "6f1c…",
      "register_ref": "SYN-PK60501-H-00042",
      "type": "HOUSEHOLD",
      "tehsil": {"id": "…", "pcode": "PK60501", "name": "Dadu"},
      "name": "Ghulam Rasool",
      "phone_masked": "+92300*****67",
      "cnic_masked": "*****-*******-3",
      "wallet_provider": "JAZZCASH",
      "wallet_id_masked": "*******4567",
      "preferred_language": "UR",
      "poverty_score": 0.82,
      "family_size": 9,
      "stock_value_pkr": null,
      "shop_type": null,
      "existing_support_pkr": "0.00",
      "flood_exposure": 0.824,
      "exposure_inputs": {"method": "exposure-v1", "imputed": []},
      "phone_valid": true,
      "is_active": true,
      "simulated": true
    }
  ],
  "total": 2731, "page": 1, "page_size": 50
}
```

**POST `/beneficiaries`** (request)

```json
{
  "register_ref": "DADU-SK-000017",
  "type": "SHOPKEEPER",
  "tehsil_pcode": "PK60502",
  "name": "Abdul Sattar",
  "phone": "0301 2345678",
  "cnic": "4110112345671",
  "wallet_provider": "EASYPAISA",
  "wallet_id": "03012345678",
  "lat": 26.81, "lon": 67.63,
  "stock_value_pkr": "180000",
  "shop_type": "KIRYANA",
  "existing_support_pkr": "0",
  "river_distance_km": 0.8,
  "floods_last_10y": 2
}
```

Response `201` with the masked detail body. Errors: `422 VALIDATION_ERROR` (row codes above in `errors[]`), `409 REGISTER_REF_EXISTS`, `409 DUPLICATE_CNIC`.

**PATCH `/beneficiaries/{id}`**: partial body with any mutable field except `type` and `register_ref` (`409 IMMUTABLE_FIELD`). Changing `phone` recomputes `phone_hash` and sets `phone_valid=true`. Changing exposure inputs or `tehsil_pcode` recomputes exposure.

**DELETE `/beneficiaries/{id}`** body `{"reason": "Duplicate of DADU-HH-000120"}` → `200` `{"id": "…", "is_active": false}`. Reason 5–500 chars (`422 REASON_REQUIRED`).

**POST `/beneficiaries/{id}/reveal`** body `{"purpose": "Verify wallet after failed payment"}` → `200`

```json
{"id": "…", "name": "Abdul Sattar", "phone": "+923012345678", "cnic": "41101-1234567-1", "wallet_id": "03012345678", "simulated": false}
```

**POST `/beneficiaries/import?dry_run=false&on_error=reject_file`** (multipart `file`)

```json
{
  "file_sha256": "9c1e…",
  "rows_total": 25,
  "rows_valid": 24,
  "rows_rejected": 1,
  "created": 0,
  "updated": 0,
  "saved": false,
  "dry_run": false,
  "on_error": "reject_file",
  "errors": [{"row": 7, "column": "phone", "code": "PHONE_INVALID", "message": "Not a Pakistani mobile number"}],
  "warnings": [{"row": 12, "column": "lat", "code": "POINT_OUTSIDE_TEHSIL"}]
}
```

`saved=false` with `on_error=reject_file` and any error → HTTP `422` with the same body under `detail`. File errors → `400` with `code`.

**POST `/beneficiaries/synthetic`**

```json
{"district_pcodes": ["PK605", "PK618"], "scenario_key": null, "sample_rate": 0.01, "seed": 2022, "include_demo_personas": true}
```

`scenario_key` (e.g. `sindh-2022`) expands to that scenario's districts. Response:

```json
{"simulated": true, "seed": 2022, "districts": 2, "households_created": 4810, "shopkeepers_created": 192, "updated": 0, "personas": 2, "invalid_wallet_ids": 98}
```

**POST `/beneficiaries/exposure/recompute`** `{"district_id": "…"}` → `{"recomputed": 5002, "imputed_any": 311}`.

**GET `/beneficiaries/stats?district_id=…`**

```json
{
  "district_id": "…",
  "simulated_share": 1.0,
  "tehsils": [
    {"tehsil_id": "…", "pcode": "PK60501", "households": 1210, "shopkeepers": 48, "estimated_households": 121000,
     "coverage": 0.0100, "relative_coverage": 1.02, "completeness": 0.97}
  ]
}
```

## Audit events

| event_type | entity | payload (no raw PII) |
|---|---|---|
| `BENEFICIARY_CREATED` | Beneficiary | `register_ref`, `type`, `tehsil_id`, `simulated` |
| `BENEFICIARY_UPDATED` | Beneficiary | `changed_fields` (names only), `phone_hash_changed: bool` |
| `BENEFICIARY_DEACTIVATED` | Beneficiary | `reason` |
| `REGISTER_IMPORTED` | ImportBatch (uuid4 id) | `file_sha256`, `rows_total`, `created`, `updated`, `rejected`, `on_error`, `district_ids` |
| `REGISTER_GENERATED` | GenerateRun (uuid5 of seed+districts) | `seed`, `sample_rate`, `district_ids`, `created`, `updated`, `config_sha256` |
| `EXPOSURE_RECOMPUTED` | District | `count`, `method`, `weights` |
| `PII_REVEALED` | Beneficiary | `purpose`, `fields` |

## Scheduled jobs

None.

## Configuration

| Key | Default | Notes |
|---|---|---|
| `EXPOSURE_WEIGHTS` | `{"elevation":0.40,"river":0.35,"history":0.25}` | Must sum to 1 |
| `HOUSEHOLD_SIZE_BY_PROVINCE` | `{"SINDH":5.9,"KP":7.4,"_default":6.3}` | Approximate, verify |
| `REGISTER_IMPORT_MAX_ROWS` | `100000` | |
| `REGISTER_IMPORT_MAX_MB` | `20` | |
| `SYNTH_CONFIG_PATH` | `data/seeds/synthetic_register.yaml` | |
| `SYNTH_DEFAULT_SEED` | `2022` | |
| `DEMO_PERSONAS_PATH` | `data/seeds/demo_personas.yaml` | git-ignored |

## Project Structure

### Documentation (this feature)

```text
specs/010-fr10-beneficiary-register/
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
├── api/v1/beneficiaries.py              # router, RBAC, DC scoping
├── schemas/beneficiaries.py             # BeneficiaryCreate/Update/Out (masked), RevealOut, ImportReport, SyntheticRequest/Report, RegisterStatsOut
├── models/beneficiaries.py              # (foundation file) + proposed fields
└── services/beneficiaries/
    ├── register.py                      # create/update/deactivate/list/get/reveal, eligible_query
    ├── validation.py                    # validate_pk_mobile, normalise_cnic, validate_row
    ├── csv_import.py                    # parse_csv, import_register
    ├── exposure.py                      # derive_exposure, recompute_exposure
    ├── synthetic.py                     # generate_register, load_synth_config
    ├── masking.py                       # mask_phone, mask_cnic, mask_wallet
    └── stats.py                         # tehsil_register_stats
alembic/versions/0010_fr10_beneficiary_register.py
scripts/generate_synthetic_register.py
data/seeds/synthetic_register.yaml
data/seeds/synthetic_names.yaml
data/seeds/demo_personas.example.yaml
data/seeds/beneficiaries_sample.csv
tests/unit/beneficiaries/{test_validation,test_csv_parse,test_exposure,test_synthetic,test_masking}.py
tests/integration/beneficiaries/{test_register_crud,test_csv_import,test_pii_encryption,test_rbac}.py
tests/contract/test_beneficiaries_api.py
```

**Structure Decision**: single project at repo root, constitution layout. Router registered in `app/api/v1/__init__.py` (one line, coordinated with foundation).

## Complexity Tracking

None.

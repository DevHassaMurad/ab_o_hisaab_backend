# Implementation Guide: FR10 Beneficiary Register

## Execution order

| Step | Tasks | Output |
|---|---|---|
| 1. Setup | T001–T003 | Package, settings, git-ignore for personas |
| 2. Schema | T004–T005 | Migration 0010, model fields |
| 3. Scaffolding | T006–T009 | Factories, masks, schemas, router with guards |
| 4. Exposure first | T030, T031 (tests) → T032, T033 | `derive_exposure`, `tehsil_register_stats` |
| 5. CSV import | T010–T013 (tests) → T014–T018 | Validated, encrypted, audited import |
| 6. CRUD | T026–T027 (tests) → T028–T029 | Single-record maintenance |
| 7. Generator | T019–T020 (tests) → T021–T025 | Deterministic simulated register |
| 8. RBAC/reveal | T035–T037 (tests) → T038–T039 | Masking, reveal, log hygiene |
| 9. Stats routes, polish | T034, T040–T043 | Endpoints, perf, lint |

## Key algorithms

### Phone, CNIC and wallet validation (`validation.py`)

```python
import re
from app.core.crypto import normalise_phone, phone_hash  # foundation

_PK_MOBILE = re.compile(r"^\+923\d{9}$")
_STRIP = re.compile(r"[\s\-().]")

class FieldError(ValueError):
    def __init__(self, column: str, code: str, message: str): ...

def validate_pk_mobile(raw: str, column: str = "phone") -> str:
    """Return +923XXXXXXXXX or raise FieldError(PHONE_INVALID)."""
    s = _STRIP.sub("", raw or "")
    if not s.isascii():                       # Urdu/Arabic-Indic digits are not accepted in the register
        raise FieldError(column, "PHONE_INVALID", "Use digits 0-9")
    if s.startswith("0092"):
        s = "+" + s[2:]
    elif s.startswith("92"):
        s = "+" + s
    elif s.startswith("03"):
        s = "+92" + s[1:]
    elif s.startswith("3") and len(s) == 10:
        s = "+92" + s
    s = normalise_phone(s)                    # foundation normaliser, idempotent on E.164
    if not _PK_MOBILE.match(s):
        raise FieldError(column, "PHONE_INVALID", "Not a Pakistani mobile number")
    return s

def wallet_to_local(e164: str) -> str:        # +923012345678 -> 03012345678
    return "0" + e164[3:]

def normalise_cnic(raw: str) -> str:
    d = re.sub(r"\D", "", raw or "")
    if len(d) != 13:
        raise FieldError("cnic", "CNIC_INVALID", "CNIC must have 13 digits")
    return f"{d[:5]}-{d[5:12]}-{d[12]}"
```

`validate_row(row, ctx)` checks, in order: required columns by type → enums → tehsil lookup (`ctx.tehsils_by_pcode`, preloaded once per file) → phone → CNIC → wallet rules → numeric ranges → coordinates (bbox; then `ST_Contains(tehsil.geom, point)` batched per file for the warning) → in-file duplicate `register_ref` and `cnic_hash`. It returns `RowResult(row_no, data | None, errors[], warnings[])`.

### CSV import (`csv_import.py`)

```python
REQUIRED = {"register_ref", "type", "tehsil_pcode", "name", "phone", "wallet_provider"}
ALLOWED = REQUIRED | {"cnic", "wallet_id", "lat", "lon", "preferred_language", "poverty_score",
                      "family_size", "stock_value_pkr", "shop_type", "existing_support_pkr",
                      "elevation_m", "river_distance_km", "floods_last_10y"}

def import_register(db, upload, *, dry_run: bool, on_error: str, actor: str) -> ImportReport:
    raw = upload.file.read(settings.REGISTER_IMPORT_MAX_MB * 2**20 + 1)
    if len(raw) > settings.REGISTER_IMPORT_MAX_MB * 2**20:
        raise BadRequest(code="FILE_TOO_LARGE")
    sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig")                       # strips BOM; UnicodeDecodeError -> NOT_UTF8
    results = list(parse_csv(io.StringIO(text, newline=""), max_rows=settings.REGISTER_IMPORT_MAX_ROWS,
                             ctx=build_validation_context(db)))
    report = ImportReport.from_results(results, file_sha256=sha, dry_run=dry_run, on_error=on_error)
    if dry_run or (on_error == "reject_file" and report.rows_rejected):
        report.saved = False
        return report                                      # router turns rejected strict import into 422
    valid = [r.data for r in results if r.data and not r.errors]
    for district_id in sorted({v.district_id for v in valid}):
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"register:{district_id}"})
    existing = load_by_register_ref(db, [v.register_ref for v in valid])
    exposure_ctx = build_exposure_context(db, {v.tehsil_id for v in valid})
    for v in valid:
        b = existing.get(v.register_ref)
        if b is not None and b.type != v.type:
            report.add_error(v.row_no, "type", "IMMUTABLE_FIELD"); continue
        _upsert_beneficiary(db, b, v, exposure_ctx)        # sets encrypted fields, hashes, exposure
        report.count(created=b is None)
    db.flush()                                             # unique (type, cnic_hash) violations -> DUPLICATE_CNIC
    AuditService.record(db, event_type="REGISTER_IMPORTED", entity_type="ImportBatch",
                        entity_id=uuid4(), actor=actor,
                        payload={"file_sha256": sha, "rows_total": report.rows_total,
                                 "created": report.created, "updated": report.updated,
                                 "rejected": report.rows_rejected, "on_error": on_error,
                                 "district_ids": sorted(map(str, {v.district_id for v in valid}))})
    report.saved = True
    return report
```

Updating an existing row through import does not write one `BENEFICIARY_UPDATED` per row; the batch entry covers it (Principle III: one entry per state change event, and the import is one event).

### Exposure (`exposure.py`)

```python
@dataclass(frozen=True)
class TehsilContext:
    elevation_mean_m: float | None
    district_elev_min: float | None
    district_elev_max: float | None
    max_flooded_pct: float | None            # from precomputed SatelliteFloodMap, 0-100

def _clamp(x: float) -> float: return max(0.0, min(1.0, x))

def derive_exposure(inp: ExposureInputs, ctx: TehsilContext, w: dict[str, float]) -> tuple[float, dict]:
    imputed: list[str] = []
    elev = inp.elevation_m if inp.elevation_m is not None else ctx.elevation_mean_m
    if elev is None or ctx.district_elev_min is None or ctx.district_elev_max == ctx.district_elev_min:
        e = 0.5; imputed.append("elevation")
    else:
        e = 1 - _clamp((elev - ctx.district_elev_min) / (ctx.district_elev_max - ctx.district_elev_min))
    if inp.river_distance_km is None:
        r = 0.5; imputed.append("river")
    else:
        r = 1 - _clamp(inp.river_distance_km / 10)
    if inp.floods_last_10y is not None:
        h, src = _clamp(inp.floods_last_10y / 3), "CSV"
    elif ctx.max_flooded_pct is not None:
        h, src = _clamp(ctx.max_flooded_pct / 30), "SATELLITE"
    else:
        h, src = 0.5, "DEFAULT"; imputed.append("history")
    value = round(w["elevation"] * e + w["river"] * r + w["history"] * h, 3)
    return value, {"method": "exposure-v1", "elevation_m": elev, "elevation_component": round(e, 4),
                   "river_distance_km": inp.river_distance_km, "river_component": round(r, 4),
                   "floods_last_10y": inp.floods_last_10y, "history_component": round(h, 4),
                   "history_source": src, "weights": w, "imputed": imputed}
```

Worked example for the unit test: district tehsil elevations 30–130 m, `elevation_m=40`, `river_distance_km=1.5`, `floods_last_10y=2` → E=0.9, R=0.85, H=0.6667 → `0.36 + 0.2975 + 0.1667 = 0.824`.

### Register statistics (`stats.py`)

```python
def tehsil_register_stats(db, tehsil_ids: list[UUID]) -> dict[UUID, TehsilRegisterStats]:
    rows = db.execute(select(Beneficiary.tehsil_id, Beneficiary.type, func.count(),
                             func.sum(case((_has_imputed_ranking_input(), 0), else_=1)))
                      .where(Beneficiary.tehsil_id.in_(tehsil_ids), Beneficiary.is_active)
                      .group_by(Beneficiary.tehsil_id, Beneficiary.type)).all()
    # estimated_households = population / HOUSEHOLD_SIZE_BY_PROVINCE[province]
    # coverage = households / estimated_households
    # relative_coverage = coverage / statistics.median([c for tehsils in the same district with population > 0]) or 0
    # completeness = complete / (households + shopkeepers), 1.0 if no rows (no data is caught by coverage)
```

`_has_imputed_ranking_input()`: household → `poverty_score IS NULL OR jsonb_array_length(exposure_inputs->'imputed') > 0`; shopkeeper → exposure imputed (stock is required, so never missing).

### Synthetic generator (`synthetic.py`)

```python
NAMESPACE = uuid.UUID("6b0e2f55-7d0c-4a43-9a8e-5f4b1f7a0010")   # fixed for FR10

def generate_register(db, *, district_pcodes, sample_rate, seed, include_demo_personas, actor) -> SyntheticReport:
    cfg = load_synth_config(settings.SYNTH_CONFIG_PATH)
    report = SyntheticReport(seed=seed, simulated=True)
    for district in load_districts(db, district_pcodes):          # sorted by pcode
        p = cfg.provinces[district.province]                      # KeyError -> 422 PROVINCE_NOT_CONFIGURED
        ctx = build_exposure_context(db, [t.id for t in district.tehsils])
        for tehsil in sorted(district.tehsils, key=lambda t: t.pcode):
            rng = np.random.default_rng(_tehsil_seed(seed, tehsil.pcode))   # sha256(f"{seed}:{pcode}") -> int
            n_hh = round(tehsil.population / p.mean_household_size * sample_rate)
            n_sk = round(n_hh / cfg.households_per_shopkeeper)
            for i in range(1, n_hh + 1):
                _upsert_beneficiary(db, *_synth_household(rng, seed, cfg, p, district, tehsil, i, ctx), report)
            for i in range(1, n_sk + 1):
                _upsert_beneficiary(db, *_synth_shopkeeper(rng, seed, cfg, p, district, tehsil, i, ctx), report)
    if include_demo_personas:
        report.personas = _load_personas(db, settings.DEMO_PERSONAS_PATH, report)
    AuditService.record(db, event_type="REGISTER_GENERATED", entity_type="GenerateRun",
                        entity_id=uuid.uuid5(NAMESPACE, f"{seed}:{','.join(sorted(district_pcodes))}"),
                        actor=actor, payload=report.audit_payload(cfg_sha256=cfg.sha256))
    return report

def _synth_household(rng, seed, cfg, p, district, tehsil, i, ctx):
    ref = f"SYN-{tehsil.pcode}-H-{i:05d}"
    family = int(np.clip(1 + rng.negative_binomial(p.hh_nb_n, p.hh_nb_p), 1, 20))   # mean = p.mean_household_size
    mean_pov = cfg.poverty.base + cfg.poverty.slope * district.vulnerability_weight
    pov = float(rng.beta(mean_pov * cfg.poverty.concentration, (1 - mean_pov) * cfg.poverty.concentration))
    wallet = rng.choice(["NONE", "JAZZCASH", "EASYPAISA"],
                        p=[p.no_wallet, (1 - p.no_wallet) * cfg.jazzcash_share, (1 - p.no_wallet) * (1 - cfg.jazzcash_share)])
    phone = _synth_phone(rng)                                     # "+92300" + 7 digits; see pitfalls
    wallet_id = None if wallet == "NONE" else _synth_wallet_id(rng, phone, cfg.invalid_wallet_rate)  # some end in 000
    support = Decimal(int(rng.uniform(*p.support_range_pkr) // 100 * 100)) if rng.random() < p.support_share else Decimal(0)
    river = float(rng.exponential(p.river_km_mean)); floods = int(min(rng.poisson(p.floods_mean), 10))
    elev = (tehsil.elevation_mean_m or 0) + float(rng.normal(0, p.elev_noise_m))
    name = _synth_name(rng, cfg.names, p.name_pool)
    return uuid.uuid5(NAMESPACE, f"{seed}:{ref}"), dict(register_ref=ref, type="HOUSEHOLD", ...)
```

`hh_nb_n`/`hh_nb_p` are derived in `load_synth_config` from the configured mean and variance (`mean_household_size − 1 = n(1−p)/p`). Point location uses `ST_GeneratePoints(tehsil.geom, n, seed)` once per tehsil (PostGIS ≥ 3.0 accepts a seed), so locations are deterministic too.

`data/seeds/synthetic_register.yaml` shape:

```yaml
label: "SIMULATED register. Approximate distributions; verify before external use."
households_per_shopkeeper: 25
jazzcash_share: 0.55
invalid_wallet_rate: 0.02
poverty: {base: 0.30, slope: 0.45, concentration: 6}
shop_types: {KIRYANA: 0.45, AGRI_INPUTS: 0.15, CLOTH: 0.10, FOOD_STALL: 0.10, HARDWARE: 0.08, MOBILE_ELECTRONICS: 0.07, PHARMACY: 0.05}
provinces:
  SINDH: {mean_household_size: 5.9, hh_variance: 6.0, no_wallet: 0.25, support_share: 0.35, support_range_pkr: [40000, 56000],
          stock_median_pkr: 120000, stock_sigma: 0.8, river_km_mean: 6.0, floods_mean: 1.5, elev_noise_m: 3, name_pool: [sindhi, urdu, saraiki]}
  KP:    {mean_household_size: 7.4, hh_variance: 8.0, no_wallet: 0.20, support_share: 0.25, support_range_pkr: [40000, 56000],
          stock_median_pkr: 150000, stock_sigma: 0.8, river_km_mean: 3.0, floods_mean: 1.0, elev_noise_m: 25, name_pool: [pashto, urdu]}
shopkeeper_support: {share: 0.10, range_pkr: [10000, 100000]}
```

`data/seeds/demo_personas.example.yaml`:

```yaml
# Copy to demo_personas.yaml (git-ignored) and put real team phones there.
- register_ref: DEMO-SK-0001
  type: SHOPKEEPER
  tehsil_pcode: PK60501        # verify against loaded boundaries
  name: "Demo shopkeeper (Rafiq)"
  phone: "+923000000000"       # replace locally
  wallet_provider: JAZZCASH
  wallet_id: "03000000000"
  stock_value_pkr: 220000
  shop_type: KIRYANA
  floods_last_10y: 2
  river_distance_km: 0.6
- register_ref: DEMO-HH-0001
  type: HOUSEHOLD
  tehsil_pcode: PK60501
  name: "Demo household (Zainab)"
  phone: "+923000000001"
  wallet_provider: EASYPAISA
  wallet_id: "03000000001"
  family_size: 8
  poverty_score: 0.86
```

Personas are `simulated=true` (the household is not real) but their phones are real, so FR14 adds them to `DEMO_PHONE_ALLOWLIST`.

### DC scoping (router)

```python
def scope_query(q: Select, user: User) -> Select:
    if user.role == Role.DC:
        return q.join(Tehsil).where(Tehsil.district_id == user.district_id)
    return q

@router.get("/beneficiaries/{id}", response_model=BeneficiaryOut)
def get_one(id: UUID, db=Depends(get_db), user=Depends(require_role("PDMA_OFFICER", "ADMIN", "DC"))):
    b = db.scalar(scope_query(select(Beneficiary).where(Beneficiary.id == id), user))
    if b is None:
        raise NotFound(code="BENEFICIARY_NOT_FOUND")   # same for missing and out-of-district
    return BeneficiaryOut.from_model(b)                # masks applied here
```

## Configuration

| Key | Default | Where |
|---|---|---|
| `FERNET_KEY` | (env, required) | foundation |
| `EXPOSURE_WEIGHTS` | `{"elevation":0.40,"river":0.35,"history":0.25}` | `app/core/config.py` |
| `HOUSEHOLD_SIZE_BY_PROVINCE` | `{"SINDH":5.9,"KP":7.4,"_default":6.3}` | `app/core/config.py` |
| `REGISTER_IMPORT_MAX_ROWS` / `_MAX_MB` | `100000` / `20` | `app/core/config.py` |
| `SYNTH_CONFIG_PATH` | `data/seeds/synthetic_register.yaml` | |
| `SYNTH_DEFAULT_SEED` | `2022` | |
| `DEMO_PERSONAS_PATH` | `data/seeds/demo_personas.yaml` | git-ignored |

## Commands

```bash
alembic upgrade head                                    # applies 0010
pytest tests/unit/beneficiaries -q
pytest tests/integration/beneficiaries -q
pytest tests/contract/test_beneficiaries_api.py -q
pytest -m slow tests/integration/beneficiaries/test_csv_import.py   # perf
python scripts/generate_synthetic_register.py --scenario sindh-2022 --seed 2022 --sample-rate 0.01 --personas
python scripts/generate_synthetic_register.py --districts PK605 --seed 2022
curl -H "Authorization: Bearer $TOKEN" -F file=@data/seeds/beneficiaries_sample.csv \
     "http://localhost:8000/api/v1/beneficiaries/import?dry_run=true"
```

## Verification checklist

| Acceptance scenario | Verified by |
|---|---|
| US1-1 valid import, encrypted, one audit entry | `test_csv_import.py::test_valid_import_creates_rows_and_one_audit` + `test_pii_encryption.py::test_ciphertext_at_rest` |
| US1-2 strict mode rejects file | `test_csv_import.py::test_reject_file_saves_nothing` |
| US1-3 skip_rows | `test_csv_import.py::test_skip_rows_saves_valid` |
| US1-4 re-import updates | `test_csv_import.py::test_reimport_is_upsert` |
| US1-5 dry run | `test_csv_import.py::test_dry_run_no_writes_no_audit` |
| US2-1 proportional, simulated | `test_synthetic.py::test_counts_proportional_to_population`, `::test_all_simulated` |
| US2-2 deterministic, idempotent | `test_synthetic.py::test_same_seed_identical`, `test_register_crud.py::test_synthetic_rerun_noop` |
| US2-3 distributions | `test_synthetic.py::test_distribution_tolerances` |
| US2-4 personas | `test_register_crud.py::test_synthetic_personas` |
| US3-1 phone update resets flag | `test_register_crud.py::test_phone_update_rehash_and_reset` |
| US3-2 field not allowed | `test_beneficiaries_api.py::test_household_rejects_stock_value` |
| US3-3 deactivate keeps history | `test_register_crud.py::test_deactivate_soft` |
| US4-1..4 | `test_rbac.py` matrix + `test_masking.py` |
| US5-1..3 | `test_exposure.py` + manual `GET /beneficiaries/stats?district_id=<Dadu>` |

## Demo notes

- Before the demo: `generate_synthetic_register.py --scenario sindh-2022 --personas` and `--scenario buner-2025 --personas`. Seed 2022 for both.
- During the replay the register is not shown directly; it appears through FR11's ranked list. If asked "is this real data?", the list shows the **SIMULATED** badge (from `simulated=true`) and the API returns `X-Simulated-Data: true`.
- The hero shopkeeper persona is the one whose phone buzzes on stage.

## Pitfalls

- **Never message synthetic numbers.** `_synth_phone` produces plausible numbers that may be real. FR14 must enforce `DEMO_PHONE_ALLOWLIST` for `simulated=true` rows. Re-state this in the FR14 PR review.
- Do not compute `phone_hash` on the raw input; always on the normalised E.164 value, or FR15 lookups miss.
- `EncryptedString` columns cannot be filtered or sorted in SQL. Filter by `register_ref`, `phone_hash` or `cnic_hash` only. Do not add "search by name".
- `csv.DictReader` with `utf-8` (not `utf-8-sig`) keeps the BOM in the first header and triggers `UNKNOWN_COLUMN`.
- `ST_GeneratePoints` may return fewer points than asked for very thin polygons; fall back to the tehsil centroid with jitter for the remainder (still seeded).
- Excel turns long numbers into `3.01E+09`; the error message for `PHONE_INVALID` should hint "format the column as text".
- `numpy` RNG streams must be created per tehsil, or adding a district changes every other district's register.
- Keep import inside one transaction; the audit entry must roll back with the rows.

# Implement Guide: FR4 Flood Modes

## Execution order

| Step | Tasks | Result |
|---|---|---|
| 1 Files | T001, T002 | Two YAMLs + seed file |
| 2 Spec model | T003 → T004, T005 | Validated registry, fail-fast start |
| 3 Routing | T006–T010 | Districts scored in their mode |
| 4 Mode behaviour | T011–T014 | Slow week-ahead, flash 24–48 h verified |
| 5 Config changes | T015–T019 | PATCH endpoint, audit, replay snapshot |
| 6 Report | T020–T022 | Calibration report + endpoint |
| 7 Polish | T023–T025 | CI check, docs, Buner backtest |

## Key algorithms

### ModeSpec and registry (`app/services/forecasting/modes.py`)
```python
CORE_FEATURES = {"rain_3d_mm", "rain_7d_mm", "soil_wetness_root", "elevation_mean_m", "vulnerability_weight"}
MODE_DIR = Path(os.getenv("FORECAST_MODES_DIR", "ml/config/modes"))

class FloodMode(StrEnum):
    SLOW_ONSET = "SLOW_ONSET"
    FLASH = "FLASH"

class FallbackTerm(BaseModel):
    feature: str
    alt_feature: str | None = None
    weight: float = Field(gt=0, le=1)
    ref: float = Field(gt=0)

class FallbackSpec(BaseModel):
    version: str
    terms: list[FallbackTerm]

class CalibrationSpec(BaseModel):
    method: Literal["isotonic", "sigmoid"] = "isotonic"
    min_positives: int = 50
    false_alarm_budget: dict[Literal["WATCH", "WARNING", "ACTION"], float]

class ModeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    mode: FloodMode
    spec_version: int
    mode_dir: str
    description: str
    artifact_version: str = "current"
    features: list[str]
    monotone: dict[str, Literal[-1, 0, 1]] = {}
    lead_weights: dict[int, float]
    lead_focus: dict[Literal["WATCH", "WARNING", "ACTION"], int]
    label: dict[str, float]
    calibration: CalibrationSpec
    fallback: FallbackSpec
    sha256: str = ""

    @model_validator(mode="after")
    def _check(self) -> "ModeSpec":
        from app.services.forecasting.features import FEATURES        # FR3 registry
        fs = set(self.features)
        if unknown := fs - FEATURES.keys():
            raise ValueError(f"unknown features {sorted(unknown)}")
        if missing := CORE_FEATURES - fs:
            raise ValueError(f"UC04-1 core features missing {sorted(missing)}")
        if "lead_day" not in fs:
            raise ValueError("lead_day required")
        if sorted(self.lead_weights) != list(range(1, 8)):
            raise ValueError("lead_weights must have keys 1..7")
        w = [self.lead_weights[i] for i in range(1, 8)]
        if any(not 0 < x <= 1 for x in w) or any(b > a for a, b in zip(w, w[1:])):
            raise ValueError("lead_weights must be in (0,1] and non-increasing")
        if abs(sum(t.weight for t in self.fallback.terms) - 1.0) > 1e-6:
            raise ValueError("fallback weights must sum to 1")
        for t in self.fallback.terms:
            if t.feature not in fs or (t.alt_feature and t.alt_feature not in fs):
                raise ValueError(f"fallback feature {t.feature} not in features")
        if set(self.monotone) - fs:
            raise ValueError("monotone keys must be features")
        return self

class ModeRegistry:
    _specs: dict[FloodMode, ModeSpec] = {}

    @classmethod
    def load_all(cls, root: Path = MODE_DIR) -> dict[FloodMode, ModeSpec]:
        specs = {}
        for f in sorted(root.glob("*.yaml")):
            raw = f.read_bytes()
            spec = ModeSpec.model_validate({**yaml.safe_load(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            if spec.mode in specs:
                raise ValueError(f"duplicate mode {spec.mode}")
            specs[spec.mode] = spec
        if set(specs) != set(FloodMode):
            raise ValueError(f"modes missing: {set(FloodMode) - set(specs)}")
        cls._specs = specs
        return specs

    @classmethod
    def get(cls, mode: FloodMode | str) -> ModeSpec:
        if not cls._specs:
            cls.load_all()
        return cls._specs[FloodMode(mode)]

    @classmethod
    def all(cls) -> list[ModeSpec]:
        return [cls.get(m) for m in FloodMode]
```

### Effective mode, change and snapshot
```python
def effective_mode(db: Session, district: District) -> FloodMode:
    sid = current_scenario()
    if sid is not None:
        snap = (db.get(ReplayScenario, sid).config or {}).get("modes", {})
        if (m := snap.get(str(district.id))) is not None:
            return FloodMode(m)
    return FloodMode(district.flood_mode)

def set_district_mode(db: Session, district_id: UUID, mode: FloodMode, reason: str, actor: str) -> ModeChangeOut:
    if current_scenario() is not None:
        raise Conflict("MODE_LOCKED_IN_REPLAY")
    if len(reason.strip()) < 10:
        raise Unprocessable("REASON_REQUIRED")
    d = db.get(District, district_id, with_for_update=True) or raise_(NotFound("DISTRICT_NOT_FOUND"))
    prev = FloodMode(d.flood_mode)
    if prev == mode:
        return ModeChangeOut(district_id=d.id, flood_mode=mode, previous_mode=prev, changed=False,
                             effective_from=effective_from(db))
    d.flood_mode = mode
    entry = AuditService.record(db, event_type="DISTRICT_FLOOD_MODE_CHANGED", entity_type="District",
                                entity_id=d.id, actor=actor,
                                payload={"district_id": str(d.id), "from": prev, "to": mode, "reason": reason})
    db.commit()
    return ModeChangeOut(district_id=d.id, flood_mode=mode, previous_mode=prev, changed=True,
                         effective_from=effective_from(db), audit_seq=entry.seq)

def effective_from(db: Session) -> date:
    today = get_clock().today()
    ran = db.scalar(select(func.count()).select_from(RiskIndex)
                    .where(RiskIndex.run_date == today, RiskIndex.scenario_id.is_(None)))
    return today + timedelta(days=1) if ran else today

def snapshot_modes(db: Session, district_ids: list[UUID]) -> dict[str, str]:
    rows = db.execute(select(District.id, District.flood_mode).where(District.id.in_(district_ids)))
    return {str(i): m for i, m in rows}
```

### Seed (`app/services/forecasting/mode_seed.py`)
```python
def apply_flood_mode_seed(db: Session, path: Path = Path("data/seeds/flood_modes.yaml")) -> int:
    cfg = yaml.safe_load(path.read_text())
    defaults, overrides = cfg["province_defaults"], {o["pcode"]: o["mode"] for o in cfg.get("overrides", [])}
    n = 0
    for d in db.scalars(select(District)):
        mode = overrides.get(d.pcode) or defaults.get(d.province, "SLOW_ONSET")
        if d.flood_mode != mode:
            d.flood_mode = mode; n += 1
    return n          # seeding is initial config, not an audited officer change
```
`data/seeds/flood_modes.yaml`:
```yaml
province_defaults: {SINDH: SLOW_ONSET, KP: FLASH, PUNJAB: SLOW_ONSET, BALOCHISTAN: FLASH, GB: FLASH, AJK: FLASH, ICT: FLASH}
validated_provinces: [SINDH, KP]
overrides: []          # e.g. - {pcode: "PK4xx", mode: SLOW_ONSET, note: "riverine (verify pcode)"}
```

### Thresholds calibration report (`ml/threshold_report.py`)
```python
def threshold_report(oof: pd.DataFrame, events: pd.DataFrame, spec: ModeSpec) -> dict:
    """oof: district_id, run_date, lead_day, p_cal (out-of-fold, district-aggregated by FR3 rule).
    events: district_id, onset_date, season."""
    w = pd.Series(spec.lead_weights)
    oof["score"] = (100 * oof.p_cal * oof.lead_day.map(w)).round().clip(0, 100)
    daily = oof.groupby(["district_id", "run_date"]).score.max().reset_index()
    rows = []
    for t in range(30, 95, 5):
        hits, leads = 0, []
        for e in events.itertuples():
            pre = daily[(daily.district_id == e.district_id) & (daily.run_date < e.onset_date)
                        & (daily.run_date >= e.onset_date - pd.Timedelta(days=10))]
            first = pre[pre.score >= t].run_date.min()
            if pd.notna(first):
                hits += 1; leads.append((e.onset_date - first).days)
        near = daily.merge(events, on="district_id", how="left")
        is_near = (near.run_date - near.onset_date).abs() <= pd.Timedelta(days=7)
        fa_days = daily.assign(near=is_near.groupby([near.district_id, near.run_date]).transform("any").values)
        fa = fa_days[(fa_days.score >= t) & ~fa_days.near]
        seasons = daily.run_date.dt.year.nunique() * daily.district_id.nunique()
        rows.append({"threshold": t, "hit_rate": round(hits / max(1, len(events)), 2),
                     "median_lead_days": int(np.median(leads)) if leads else None,
                     "false_alarm_days_per_season": round(len(fa) / max(1, seasons), 2)})
    budget = spec.calibration.false_alarm_budget
    suggest = {tier: min((r["threshold"] for r in rows if r["false_alarm_days_per_season"] <= b), default=None)
               for tier, b in budget.items()}
    suggest = enforce_increasing(suggest)          # WATCH < WARNING < ACTION, step 5, cap 95
    return {"mode": spec.mode, "thresholds": rows, "suggested_thresholds": suggest,
            "false_alarm_budget": budget, "source": "out_of_fold", "advisory": True}
```
`false_alarm_days_per_season` is per district-season. The `near` computation is written for clarity; vectorise if slow.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `FORECAST_MODES_DIR` | `ml/config/modes` | Mode YAML folder |
| `data/seeds/flood_modes.yaml` | see above | Province defaults and overrides |
| YAML `lead_weights` | slow all 1.0; flash `1,1,.85,.7,.6,.6,.6` | Lead-time policy |
| YAML `calibration.false_alarm_budget` | 3.0 / 1.5 / 0.5 days per season | Threshold suggestions |
| YAML `fallback.terms` | plan.md | UC04-3a rule per mode |

## Commands

```bash
python -c "from app.services.forecasting.modes import ModeRegistry; print([s.mode for s in ModeRegistry.all()])"
pytest tests/unit/forecasting/test_mode_spec.py tests/unit/forecasting/test_threshold_report.py -q
pytest tests/integration/forecasting/test_mode_routing.py -q
pytest tests/contract/test_forecast_modes_api.py -q
python -m ml.train --mode flash --data ml/data/train.parquet        # FR3 script, FR4 YAML
python -m ml.threshold_report --mode flash --version current
curl -X PATCH localhost:8000/api/v1/districts/$NOWSHERA/flood-mode -H "Authorization: Bearer $OFFICER" \
     -d '{"flood_mode":"SLOW_ONSET","reason":"Riverine flooding from the Kabul river"}'
pytest -m backtest -k buner
```

## Verification checklist

| Acceptance scenario | Check |
|---|---|
| US1-1/2 Sindh slow, KP flash | T006 |
| US1-3 flash artifact missing → KP fallback only | T006 |
| US2-1 slow features, full 7 leads | T003 (spec), T011 |
| US2-2 saturated soil scores higher | T011 |
| US3-1 flash features | T003 (spec) |
| US3-2/3 lead weighting, ≤ 60 from leads 5–7 | T013 |
| US4-1/2/3 change with reason, audit, roles | T015 |
| US4-4 replay snapshot | T016 |
| US5-1/2 report content, thresholds untouched | T020, T022 |
| SC-003/004 backtests, Buner peak lead ≤ 2 | T025 (FR5) |

## Demo notes

- At 4:30 in the pitch (Buner 2025 flash mode): show `GET /forecasting/modes` side by side (one line each: "Sindh: rain over 30 days + wet soil, looks 7 days ahead"; "KP: 3-hour bursts + slopes, acts 24–48 h ahead").
- In the Buner replay the explanation reads "bursts of up to N mm in 3 hours, steep slopes" — that is the flash mode talking.
- Optional Q&A answer to "What about false alarms?": open the calibration endpoint for slow onset and read the false-alarm days at 40.

## Pitfalls

- Validate lead weights as non-increasing: an accidental `1.0` at lead 7 in flash mode makes Action fire a week out.
- `features` order is the model input order; reordering the YAML without retraining makes FR3 reject the artifact (by design, falls back).
- Do not read `district.flood_mode` directly in FR3; always `effective_mode` (replays).
- Seeding is not an officer action, so it is not audited; any later change through the API is.
- The calibration report must use out-of-fold predictions, never 2021/2022/2025 data.

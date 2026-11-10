# Implement Guide: FR3 Flood Risk Score

## Execution order

| Step | Tasks | Result |
|---|---|---|
| 1 Setup | T001–T004 | Deps, packages, settings |
| 2 Foundations | T005–T009 | Migration, schemas, protocols, factories |
| 3 Pure functions (tests first) | T010–T012, T020, T031, T036 → T014, T016, T017, T022, T033, T037 | Features, scoring, tiers, aggregation, fallback, templates |
| 4 Forecast rain | T024–T029 | Open-Meteo client, ingest, stale shift |
| 5 Service | T013, T015, T018, T021, T023, T030, T032, T034, T035, T038, T039 | End-to-end daily run |
| 6 Pipeline + API | T019, T040–T042 | Steps registered, endpoints |
| 7 Model | T043–T045, T050 | Trained artifacts, backtests |
| 8 Polish | T046–T049 | Urdu review, perf, lint |

## Key algorithms

### Open-Meteo client (`app/integrations/forecast_rain.py`)
```python
@dataclass(frozen=True)
class GeoPoint:
    tehsil_id: UUID
    lat: float
    lon: float

@dataclass(frozen=True)
class ForecastRainRecord:
    tehsil_id: UUID
    target_date: date
    lead_day: int                 # 1..7
    rainfall_mm: float
    rainfall_max_3h_mm: float
    provenance: str               # LIVE_FORECAST | ARCHIVED_FORECAST | ARCHIVED_SHORT_LEAD_PROXY | PERFECT_FORECAST_PROXY
    forecast_model: str

class OpenMeteoClient:
    def __init__(self, s: ForecastSettings, http: httpx.Client | None = None):
        self.s = s
        self.http = http or httpx.Client(timeout=s.http_timeout_s)

    def fetch_forecast(self, points: list[GeoPoint], issue_date: date) -> list[ForecastRainRecord]:
        out: list[ForecastRainRecord] = []
        for batch in chunked(points, self.s.batch_size):           # 50 per request (verify limit)
            params = {
                "latitude": ",".join(f"{p.lat:.4f}" for p in batch),
                "longitude": ",".join(f"{p.lon:.4f}" for p in batch),
                "hourly": "precipitation", "forecast_days": 8, "past_days": 3,
                "timezone": "UTC", "models": self.s.open_meteo_model,
            }
            if self.s.open_meteo_api_key:
                params["apikey"] = self.s.open_meteo_api_key
            data = self._get(f"{self.s.open_meteo_base}/v1/forecast", params)
            payloads = data if isinstance(data, list) else [data]    # multi-location → list
            for p, body in zip(batch, payloads, strict=True):
                hourly = pd.Series(body["hourly"]["precipitation"],
                                   index=pd.to_datetime(body["hourly"]["time"], utc=True)).fillna(0.0)
                out += to_daily_records(p.tehsil_id, hourly, issue_date, "LIVE_FORECAST",
                                        f"open-meteo:{self.s.open_meteo_model}")
        return out

def to_daily_records(tid, hourly: pd.Series, issue: date, prov: str, model: str) -> list[ForecastRainRecord]:
    daily = hourly.resample("1D").sum()
    max3h = hourly.rolling(3, min_periods=1).sum().resample("1D").max()
    recs = []
    for d in range(1, 8):
        t = issue + timedelta(days=d)
        ts = pd.Timestamp(t, tz="UTC")
        recs.append(ForecastRainRecord(tid, t, d, float(daily.get(ts, 0.0)),
                                       float(max3h.get(ts, 0.0)), prov, model))
    return recs
```
`fetch_previous_runs(points, start, end)` requests `hourly=precipitation_previous_day1,…,precipitation_previous_day7` and maps `previous_dayN` on target T to `lead_day=N` (`ARCHIVED_FORECAST`). `fetch_historical_forecast(points, start, end)` returns one hourly series; for issue date t and lead d it takes target t+d from that series (`ARCHIVED_SHORT_LEAD_PROXY` for d ≥ 2, `ARCHIVED_FORECAST` for d = 1). Both are used by FR5's cache builder, not by the live step. Variable names and coverage: **verify** against the Open-Meteo docs.

### Ingest with stale shift (`app/services/forecasting/rain_ingest.py`)
```python
class ForecastRainSource(Protocol):
    def fetch(self, tehsils: Sequence[GeoPoint], issue_date: date) -> list[ForecastRainRecord]: ...

class ForecastRainIngestService:
    def ingest(self, db: Session, issue_date: date) -> IngestResult:
        points = tehsil_points(db)            # ST_PointOnSurface, active districts only
        try:
            recs = get_forecast_source().fetch(points, issue_date)   # OpenMeteoForecastSource, or ReplayForecastRainSource when current_scenario() is set
            stale_since = None
            mark_status(db, "FORECAST_RAIN", ok=True, last_valid_date=issue_date)
        except (httpx.HTTPError, ForecastSourceError) as e:
            recs, stale_since = shift_previous(db, issue_date)      # yesterday lead d+1 → today lead d; lead 7 = lead 6
            mark_status(db, "FORECAST_RAIN", ok=False, error=str(e))
            alert_admin(db, "FORECAST_RAIN_DOWN", {"issue_date": issue_date, "stale_since": stale_since})
        upsert_observations(db, recs, source="FORECAST_RAIN", is_stale=stale_since is not None,
                            stale_since=stale_since, scenario_id=current_scenario())
        return IngestResult(len(recs), stale_since)
```

### Window blending (`app/services/forecasting/features.py`)
```python
def daily_rain(day: date, run_date: date, obs: ObsIndex, fc: ForecastIndex, tid: UUID, did: UUID) -> tuple[float, str]:
    if day <= obs.imerg_last.get(tid, date.min) and (v := obs.imerg.get((tid, day))) is not None:
        return v, "IMERG"
    if day <= obs.power_last.get(did, date.min) and (v := obs.power.get((did, day))) is not None:
        return v, "NASA_POWER"
    if day <= run_date and (v := fc.past.get((tid, day))) is not None:
        return v, "OM_PAST"
    if day > run_date and (v := fc.lead.get((tid, day))) is not None:
        return v, "FORECAST"
    return 0.0, "MISSING"                         # counted; > 2 missing days in a window → is_stale_input

def window_sum(n: int, target: date, **ctx) -> tuple[float, list[str]]:
    vals = [daily_rain(target - timedelta(days=i), **ctx) for i in range(n)]
    return sum(v for v, _ in vals), [s for _, s in vals]

FEATURES: dict[str, Callable[[RowCtx], float]] = {
    "rain_3d_mm":  lambda c: c.window(3),
    "rain_7d_mm":  lambda c: c.window(7),
    "rain_14d_mm": lambda c: c.window(14),
    "rain_30d_mm": lambda c: c.window(30),
    "rain_fcst_target_mm": lambda c: c.fc_lead(c.target),
    "rain_24h_fcst_mm": lambda c: c.fc_lead(c.run_date + timedelta(1)),
    "rain_48h_fcst_mm": lambda c: c.fc_lead(c.run_date + timedelta(1)) + c.fc_lead(c.run_date + timedelta(2)),
    "max_3h_fcst_mm": lambda c: c.fc_max3h(c.target),
    "max_3h_obs_mm":  lambda c: c.obs_max3h_last(),
    "soil_wetness_root": lambda c: c.power_last_valid("soil_wetness"),
    "soil_wetness_top":  lambda c: c.power_last_valid("soil_wetness_top"),
    "elevation_mean_m":  lambda c: c.tehsil.elevation_mean_m or 0.0,
    "slope_mean_deg":    lambda c: c.slope_or_median(),
    "vulnerability_weight": lambda c: c.district.vulnerability_weight,
    "lead_day": lambda c: float(c.lead),
}

class FeatureBuilder:
    def build(self, db, run_date: date, tehsils: list[Tehsil], spec: ModeSpec) -> pd.DataFrame:
        ctx = load_indexes(db, run_date, tehsils, lookback_days=37)   # 30-day window + 7 leads
        rows = []
        for t in tehsils:
            for lead in range(1, 8):
                c = RowCtx(ctx, t, run_date, lead)
                feats = {name: FEATURES[name](c) for name in spec.features}
                rows.append({"tehsil_id": t.id, "district_id": t.district_id, "lead_day": lead,
                             "target_date": run_date + timedelta(lead), **feats,
                             "_window_sources": c.sources, "_stale": c.stale, "_stale_since": c.stale_since,
                             "_provenance": c.provenance})
        return pd.DataFrame(rows)
```
`soil_wetness_top` reads `GWETTOP`; FR1 stores only `soil_wetness` (= `GWETROOT`) in the data model. If FR1 does not add a top-layer column, FR4's flash YAML drops `soil_wetness_top` and uses `soil_wetness_root` (decision recorded in FR4 clarify).

### Model registry and contributions (`model_registry.py`)
```python
@dataclass
class LoadedModel:
    version: str; family: str; features: list[str]
    model: Any; calibrator: Any; scaler_means: np.ndarray | None = None

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        Xt = transform(X[self.features])                  # log1p on rain features (shared with ml/)
        raw = self.model.predict_proba(Xt)[:, 1]
        p = np.asarray(self.calibrator.predict(raw), dtype=float)
        if not np.all(np.isfinite(p)) or p.min() < 0 or p.max() > 1:
            raise InvalidPrediction(self.version)
        return p

    def contributions(self, X: pd.DataFrame) -> pd.DataFrame:
        Xt = transform(X[self.features])
        if self.family == "lgbm":
            c = self.model.booster_.predict(Xt, pred_contrib=True)[:, :-1]   # drop bias column
        else:                                                                # pipeline(scaler, logit)
            z = self.model[0].transform(Xt)
            c = z * self.model[-1].coef_[0]
        return pd.DataFrame(c, columns=self.features, index=X.index)

class ModelRegistry:
    def load(self, spec: ModeSpec) -> LoadedModel:               # cached per (mode, version)
        version = spec.artifact_version if spec.artifact_version != "current" else \
            (ARTIFACT_DIR / spec.mode_dir / "CURRENT").read_text().strip()
        base = ARTIFACT_DIR / spec.mode_dir / version
        card = json.loads((base / "model_card.json").read_text())
        if card["features"] != spec.features:
            raise IncompatibleArtifact(version)
        return LoadedModel(version, card["family"], card["features"],
                           joblib.load(base / "model.joblib"), joblib.load(base / "calibrator.joblib"))
```

### Fallback (`fallback.py`)
```python
class FallbackScorer:
    VERSION = "rules-fallback-1"

    def score(self, X: pd.DataFrame, spec: ModeSpec) -> tuple[np.ndarray, pd.DataFrame]:
        terms = {}
        for t in spec.fallback.terms:                   # [{feature, weight, ref, alt_feature?}]
            x = X[t.feature]
            if t.alt_feature:                           # flash: max(max_3h_fcst, max_3h_obs)
                x = np.maximum(x, X[t.alt_feature])
            terms[t.feature] = t.weight * np.minimum(1.0, x / t.ref)
        contrib = pd.DataFrame(terms, index=X.index)
        p = contrib.sum(axis=1).clip(0.0, 1.0).to_numpy()
        return p, contrib
```

### Scoring and tiers (`scoring.py`)
```python
def round_half_up_score(p: float) -> int:
    s = (Decimal(str(p)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(min(100, max(0, s)))

def apply_lead_weights(p: np.ndarray, leads: np.ndarray, spec: ModeSpec) -> np.ndarray:
    w = np.array([spec.lead_weights[int(l)] for l in leads])
    return p * w

def peak(lead_probs: dict[int, float]) -> tuple[int, float]:
    best = max(lead_probs.values())
    lead = min(l for l, v in lead_probs.items() if v == best)    # earliest on ties
    return lead, best

def tier_for(score: int, tiers: dict[str, int]) -> str:          # {"WATCH":40,"WARNING":60,"ACTION":80}
    tier = "NONE"
    for name in ("WATCH", "WARNING", "ACTION"):
        if score >= tiers[name]:
            tier = name
    return tier

def top_factors(contrib_row: pd.Series, feat_row: pd.Series, k: int = 3) -> list[dict]:
    pos = contrib_row[contrib_row > 0].drop(labels=["lead_day"], errors="ignore")
    if pos.empty:
        return []
    share = pos / pos.sum()
    return [{"factor": f, "value": display_value(f, feat_row[f]), "contribution": round(float(share[f]), 2)}
            for f in share.sort_values(ascending=False).index[:k]]
```

### District aggregation (`aggregation.py`)
```python
def aggregate_district(tehsil_p: pd.DataFrame, pops: dict[UUID, int | None], min_share: float) -> DistrictAgg:
    """tehsil_p: rows (tehsil_id, lead_day, p_adj). Returns per-lead district p and the source tehsil."""
    total = sum(v for v in pops.values() if v)
    eligible = [t for t, v in pops.items() if total and v and v / total >= min_share] or list(pops)
    sub = tehsil_p[tehsil_p.tehsil_id.isin(eligible)]
    idx = sub.groupby("lead_day")["p_adj"].idxmax()
    per_lead = sub.loc[idx, ["lead_day", "tehsil_id", "p_adj"]].set_index("lead_day")
    return DistrictAgg(p_by_lead=per_lead["p_adj"].to_dict(), source_by_lead=per_lead["tehsil_id"].to_dict(),
                       eligible=eligible, rule="max_pop_share", min_share=min_share)
```

### Daily run (`service.py`)
```python
class ForecastService:
    def run(self, db: Session, run_date: date, district_ids: list[UUID] | None = None) -> ForecastRunResult:
        acquire_run_lock(db)                                   # advisory lock per scenario
        res = ForecastRunResult(run_date)
        for mode, districts in group_active_districts(db, district_ids).items():   # grouped by FR4 effective_mode(db, d) (replay snapshot aware)
            spec = ModeRegistry.get(mode)                      # FR4
            try:
                model = self.registry.load(spec); model_err = None
            except Exception as e:                             # load failure → whole mode on rules
                model, model_err = None, e
                log.warning("model_load_failed", mode=mode, err=str(e))
            for d in districts:
                if risk_index_locked(db, d.id, run_date):      # referenced by a FundRelease
                    res.skipped_locked.append(d.id); continue
                with db.begin_nested():
                    ri = self._score_district(db, d, run_date, spec, model)
                    AuditService.record(db, event_type="RISK_SCORED", entity_type="RiskIndex",
                                        entity_id=ri.id, actor="system", payload=ri.audit_payload())
                db.commit()
                res.add(ri)
                try:
                    TriggerEngine.evaluate(db, d.id, ri); db.commit()   # separate transaction (FR7 idempotent)
                except Exception as e:
                    db.rollback(); res.trigger_errors.append({"district_id": d.id, "error": str(e)})
        return res

    def _score_district(self, db, d, run_date, spec, model) -> RiskIndex:
        X = self.features.build(db, run_date, active_tehsils(d), spec)
        if model is not None:
            try:
                p = model.predict_proba(X); contrib = model.contributions(X)
                X["_fallback"], X["_version"] = False, model.version
            except Exception:
                p, contrib = self.fallback.score(X, spec); X["_fallback"], X["_version"] = True, FallbackScorer.VERSION
        else:
            p, contrib = self.fallback.score(X, spec); X["_fallback"], X["_version"] = True, FallbackScorer.VERSION
        X["p"] = p
        X["p_adj"] = apply_lead_weights(p, X.lead_day.to_numpy(), spec)
        tiers = load_thresholds(db, d.id)                       # scenario-scoped TriggerTier
        upsert_tehsil_rows(db, d, run_date, X, contrib, spec, tiers, self.explainer)
        agg = aggregate_district(X[["tehsil_id", "lead_day", "p_adj"]],
                                 {t.id: t.population for t in active_tehsils(d)}, settings.district_min_pop_share)
        return upsert_district_rows(db, d, run_date, X, contrib, agg, spec, tiers, self.explainer)
```
Per-tehsil prediction failures (one bad row) are handled by predicting row by row only after a batch failure, so the common path stays vectorised.

### Template explanation (`template_explainer.py`)
```python
@dataclass(frozen=True)
class RiskExplainInputs:
    district_name: str; district_name_ur: str | None
    score: int; tier: str; peak_lead_day: int; target_date: date
    top_factors: list[dict]          # [{"factor","value","contribution"}]
    is_fallback: bool; is_stale_input: bool; stale_since: date | None; forecast_is_proxy: bool

class TemplateExplainService:
    def explain_risk(self, i: RiskExplainInputs) -> tuple[str, str]:
        en = [T.EN_MAIN.format(district=i.district_name, score=i.score, tier=T.TIER_EN[i.tier],
                               lead=i.peak_lead_day, date=i.target_date.strftime("%d %b"))]
        ur = [T.UR_MAIN.format(district=i.district_name_ur or i.district_name, score=i.score,
                               tier=T.TIER_UR[i.tier], lead=i.peak_lead_day, date=i.target_date.strftime("%d-%m"))]
        f = i.top_factors[:3]
        if f:
            en.append(T.EN_REASONS.format(", ".join(T.FACTOR_EN[x["factor"]].format(v=fmt(x)) for x in f)))
            ur.append(T.UR_REASONS.format("، ".join(T.FACTOR_UR[x["factor"]].format(v=fmt(x)) for x in f)))
        for flag, key in ((i.is_fallback, "fallback"), (i.is_stale_input, "stale"), (i.forecast_is_proxy, "proxy")):
            if flag:
                en.append(T.NOTICE_EN[key].format(date=i.stale_since and i.stale_since.strftime("%d %b")))
                ur.append(T.NOTICE_UR[key].format(date=i.stale_since and i.stale_since.strftime("%d-%m")))
        return " ".join(en), " ".join(ur)

def fmt(x: dict) -> str:   # mm → int, wetness/vulnerability 0–1 → %, degrees/metres → int
    v = x["value"]
    return str(round(v * 100)) if x["factor"] in PERCENT_FACTORS else str(round(v))
```
`top_factors.value` is stored already in display units for wetness (0.91) and formatted to "91" here; FR13's guardrail adds `round(v*100)` to its allowed-number set for percent factors.

### Training sketch (`ml/train.py`)
```python
def train(mode: str, data: pd.DataFrame, holdout_years=(2021, 2022, 2025)) -> Artifact:
    data = data[~data.year.isin(holdout_years)]
    spec = ModeRegistry.get(mode); X, y, g = transform(data[spec.features]), data.label, data.year
    cands = {"logit": make_pipeline(StandardScaler(), LogisticRegression(C=1.0, class_weight="balanced", max_iter=2000)),
             "lgbm": LGBMClassifier(num_leaves=15, min_child_samples=50, learning_rate=0.05, n_estimators=400,
                                    subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                    monotone_constraints=[spec.monotone.get(f, 0) for f in spec.features],
                                    scale_pos_weight=(y == 0).sum() / max(1, (y == 1).sum()), random_state=42)}
    cv = GroupKFold(n_splits=5)
    oof = {k: cross_val_predict(m, X, y, groups=g, cv=cv, method="predict_proba")[:, 1] for k, m in cands.items()}
    best = max(oof, key=lambda k: (average_precision_score(y, oof[k]), k == "logit"))
    cal = IsotonicRegression(out_of_bounds="clip").fit(oof[best], y) if y.sum() >= 50 \
          else LogisticRegression().fit(oof[best].reshape(-1, 1), y)      # Platt (wrap to .predict → proba)
    model = cands[best].fit(X, y)
    save_oof(data.assign(p_cal=cal_predict(cal, oof[best])))   # oof.parquet, district-aggregated, for FR4 threshold_report
    return Artifact(mode, best, model, cal, metrics=evaluate(y, cal_predict(cal, oof[best]), data))
```

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `OPEN_METEO_BASE` | `https://api.open-meteo.com` | Live forecast host (foundation `.env` key); path `/v1/forecast` |
| `OPEN_METEO_PREVIOUS_RUNS_URL` | `https://previous-runs-api.open-meteo.com/v1/forecast` | Replay cache builder (verify) |
| `OPEN_METEO_HISTORICAL_FORECAST_URL` | `https://historical-forecast-api.open-meteo.com/v1/forecast` | Replay cache builder (verify) |
| `OPEN_METEO_MODEL` | `best_match` | Model selector |
| `OPEN_METEO_API_KEY` | empty | Commercial use only |
| `FORECAST_HTTP_TIMEOUT_S` | `20` | Per request |
| `FORECAST_HTTP_RETRIES` | `2` | Exponential backoff 1 s, 3 s |
| `FORECAST_BATCH_SIZE` | `50` | Locations per request |
| `FORECAST_ARTIFACT_DIR` | `ml/artifacts` | Model root |
| `FORECAST_DISTRICT_MIN_POP_SHARE` | `0.10` | Aggregation rule |
| `FORECAST_DEFAULT_THRESHOLDS` | `40,60,80` | Used only when a district has no TriggerTier rows |
| `FORECAST_MAX_MISSING_WINDOW_DAYS` | `2` | More missing days in a window → stale |

## Commands

```bash
alembic upgrade head
pytest tests/unit/forecasting tests/unit/explain -q
pytest tests/integration/forecasting -q
pytest tests/contract/test_forecasts_api.py -q
pytest -m perf tests/perf/test_forecast_runtime.py
python -m ml.build_training_set --out ml/data/train.parquet
python -m ml.train --mode slow_onset --data ml/data/train.parquet
python -m ml.train --mode flash --data ml/data/train.parquet
python -m ml.evaluate --mode slow_onset --version current
curl -X POST localhost:8000/api/v1/forecasts/runs -H "Authorization: Bearer $ADMIN" -d '{"run_date":"2026-11-10"}'
pytest -m backtest tests/backtest   # FR5
```

## Verification checklist

| Acceptance scenario | Check |
|---|---|
| US1-1 seven leads, score = round(100 × max p_adj) | T013, T011 |
| US1-2 60/32/8 population aggregation | T012 |
| US1-3 inactive district skipped | T013 |
| US1-4 idempotent re-run | T013 |
| US2-1/2 tier mapping | T020 |
| US2-3/4 evaluate after commit, failure isolated | T021 |
| US3-1 seven leads with provenance | T024 |
| US3-2 provider down → stale shift + admin alert | T026 |
| US3-3 proxy flag in responses | T040 |
| US4-1/2 model load / prediction failure → fallback | T032 |
| US4-3/4 stale flags in rows, API, audit | T032 |
| US5-1/2/3 explanation content, notices, numbers ⊂ inputs | T036 |
| US6-1/2/3 map, DC scope, scenario filter | T040 |
| US7-1/2 holdout years excluded; version on rows | T043, T013 |
| SC-001 runtime | T047 |
| SC-002 reproduction | T048 |
| SC-004 backtests | T050 (FR5) |

## Demo notes

- The replay slider (FR5) reads `GET /forecasts/districts/{id}/history` and `/districts/{id}?run_date=` with `X-Scenario-Id`. The 7-day chart is `series`.
- Hero beat: Dadu's line climbs through 40 → 60 → 80 days before the reference onset; the explanation names the 7-day rain and soil wetness.
- The proxy notice appears on the replay explanation. Say it on stage: "for 2022 we feed the model the archived forecast where it exists, otherwise observed rain, and we label it".
- Fallback demo (optional, 10 s): rename `ml/artifacts/slow_onset/CURRENT`, re-run one day, show the "rainfall rule" notice and `is_fallback=true`.

## Pitfalls

- Python `round(0.5) == 0`: always use `round_half_up_score`.
- IMERG and Open-Meteo days must both be UTC days; PKT midnight is 19:00 UTC.
- Open-Meteo returns a JSON object (not a list) for a single location; handle both.
- Do not read `date.today()` or `datetime.utcnow()` anywhere: replay breaks silently. Lint with a grep in CI.
- `GroupKFold` by year, not random K-fold: neighbouring days leak.
- Never retrain on 2021/2022/2025: the backtests become meaningless.
- `NULLS NOT DISTINCT` needs PG 15+; the unique constraint otherwise lets live rows duplicate (scenario_id NULL).
- Re-running a date that already fired a release must skip that district, not overwrite its score.
- Keep the LightGBM monotone constraint list aligned with `spec.features` order.

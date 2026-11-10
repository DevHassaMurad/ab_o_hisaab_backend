# Implementation Guide: FR1 NASA POWER Ingestion

## Execution order

| Step | Tasks | Outcome |
|---|---|---|
| 1 | T001–T003 | Settings, fixtures, package skeleton |
| 2 | T004–T006, T008–T009 | Ingestion framework (FR2 unblocked) |
| 3 | T010–T013 | US1 tests, red |
| 4 | T014–T019 | Daily ingest green, backfill CLI |
| 5 | T020–T022 | Stale path green |
| 6 | T023–T027 | Replay client and committed caches |
| 7 | T028–T034 | Context indicators and vulnerability |
| 8 | T007 | Migration once data-model additions are merged |
| 9 | T035–T038 | Timing, sanity checks, lint |

## Key algorithms

### 1. NASA POWER client (`app/integrations/nasa_power.py`)

```python
POWER_PARAMS = ("PRECTOTCORR", "T2M", "GWETROOT", "GWETTOP")
EXPECTED_UNITS = {"PRECTOTCORR": "mm/day", "T2M": "C", "GWETROOT": "1", "GWETTOP": "1"}  # verify exact strings from a live response

@dataclass(frozen=True)
class PowerDay:
    obs_date: date
    values: dict[str, float | None]          # None = fill value / missing

@dataclass(frozen=True)
class PowerResponse:
    days: list[PowerDay]
    units: dict[str, str | None]

class SourceUnavailable(Exception): ...
class UnitMismatch(Exception): ...

class NasaPowerClient(Protocol):
    def fetch_daily(self, lat: float, lon: float, start: date, end: date) -> PowerResponse: ...

def parse_power_json(doc: dict) -> PowerResponse:
    fill = float(doc.get("header", {}).get("fill_value", -999.0))
    units = {p: (doc.get("parameters", {}).get(p) or {}).get("units") for p in POWER_PARAMS}
    series = doc["properties"]["parameter"]
    days: list[PowerDay] = []
    for key in sorted(series["PRECTOTCORR"]):
        vals: dict[str, float | None] = {}
        for p in POWER_PARAMS:
            v = series.get(p, {}).get(key)
            vals[p] = None if v is None or float(v) <= fill + 0.5 else float(v)
        days.append(PowerDay(datetime.strptime(key, "%Y%m%d").date(), vals))
    return PowerResponse(days=days, units=units)

class HttpNasaPowerClient:
    def __init__(self, http: httpx.Client, s: Settings) -> None:
        self.http, self.s = http, s

    def fetch_daily(self, lat, lon, start, end) -> PowerResponse:
        params = {
            "parameters": ",".join(POWER_PARAMS), "community": "AG",
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
            "format": "JSON", "time-standard": "UTC",          # verify param on daily endpoint
        }
        last: Exception | None = None
        for attempt in range(self.s.POWER_MAX_RETRIES + 1):
            try:
                r = self.http.get(self.s.NASA_POWER_BASE_URL, params=params, timeout=self.s.POWER_TIMEOUT_S)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                resp = parse_power_json(r.json())
                check_units(resp.units)                        # raises UnitMismatch
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                if attempt < self.s.POWER_MAX_RETRIES:
                    time.sleep(2 ** (attempt + 1))
        raise SourceUnavailable(f"{last} after {self.s.POWER_MAX_RETRIES} retries")

class CachedNasaPowerClient:
    """Replay: raw POWER JSON from data/replay/<key>/nasa_power/<pcode>.json, no lookahead."""
    def __init__(self, scenario_key: str, replay_today: date, lag_days: int, root: Path = Path("data/replay")):
        self.dir = root / scenario_key / "nasa_power"
        self.replay_today = replay_today
        self.cutoff = replay_today - timedelta(days=lag_days)
        self.outages = _load_outages(self.dir / "outages.json")   # [(start, end), ...] optional

    def fetch_for(self, pcode: str, start: date, end: date) -> PowerResponse:
        if any(a <= self.replay_today <= b for a, b in self.outages):
            raise SourceUnavailable("simulated outage (replay)")
        resp = parse_power_json(json.loads((self.dir / f"{pcode}.json").read_text()))
        keep = [d for d in resp.days if start <= d.obs_date <= min(end, self.cutoff)]
        return PowerResponse(days=keep, units=resp.units)
```
Note: the live client is keyed by lat/lon, the cached one by district pcode. `NasaPowerIngestor` calls through a small adapter `fetch(district, start, end)` so both fit. The outage check uses the replay date, not the cutoff.

### 2. Validation (`app/services/ingestion/validation.py`)

```python
RANGES = {"PRECTOTCORR": (0.0, settings.POWER_RAIN_MAX_MM), "T2M": (-30.0, 55.0),
          "GWETROOT": (0.0, 1.0), "GWETTOP": (0.0, 1.0)}

@dataclass
class ValidatedDay:
    obs_date: date
    rainfall_mm: float | None
    temp_c: float | None
    soil_wetness: float | None
    soil_wetness_top: float | None
    flags: list[str]

def validate_power_day(day: PowerDay) -> ValidatedDay:
    flags: list[str] = []
    out: dict[str, float | None] = {}
    for p, v in day.values.items():
        lo, hi = RANGES[p]
        if v is None:
            out[p] = None
            continue
        if p == "PRECTOTCORR" and -0.5 <= v < 0:
            v, _ = 0.0, flags.append("rain_neg_clamped")
        if p in ("GWETROOT", "GWETTOP") and 1.0 < v <= 100.0:
            v, _ = v / 100.0, flags.append(f"{p.lower()}_pct_to_fraction")
        if not lo <= v <= hi:
            flags.append(f"{p.lower()}_out_of_range:{v}")
            v = None
        out[p] = v
    return ValidatedDay(day.obs_date, out["PRECTOTCORR"], out["T2M"], out["GWETROOT"], out["GWETTOP"], flags)
```
A day is **fresh** only if `rainfall_mm is not None`. Missing temperature or soil wetness alone does not make it stale; those fields are stored as `NULL`.

### 3. Copy-forward and source health (`app/services/ingestion/staleness.py`, shared with FR2)

```python
@dataclass(frozen=True)
class StaleRow:
    obs_date: date
    copy_of: date            # = stale_since

def plan_copy_forward(*, fresh_dates: set[date], last_valid: date | None, window: tuple[date, date],
                      today: date, expected_lag: int, max_days: int) -> list[StaleRow]:
    due_until = today - timedelta(days=expected_lag)
    if last_valid is None:
        return []
    rows = []
    d = window[0]
    while d <= min(window[1], due_until):
        if d not in fresh_dates and d > last_valid and (d - last_valid).days <= max_days:
            rows.append(StaleRow(d, last_valid))
        d += timedelta(days=1)
    return rows

class Transition(StrEnum):
    DOWN = "DOWN"; UP = "UP"; NONE = "NONE"

def update_source_status(db, *, source: DataSource, healthy: bool, last_valid_date: date | None,
                         error: str | None, now: datetime, scenario_id: UUID | None) -> Transition:
    if scenario_id is not None:
        return Transition.NONE                       # replay never touches the live status table
    st = db.scalar(select(DataSourceStatus).where(DataSourceStatus.source == source).with_for_update())
    was = st.is_healthy if st else True
    st = st or DataSourceStatus(source=source)
    st.is_healthy, st.last_error = healthy, error
    if last_valid_date:
        st.last_valid_date = last_valid_date
    if healthy:
        st.last_success_at = now
    db.add(st)
    return Transition.DOWN if was and not healthy else Transition.UP if not was and healthy else Transition.NONE
```

### 4. Ingestor (`app/services/ingestion/nasa_power_ingestor.py`)

```python
class NasaPowerIngestor:
    source = DataSource.NASA_POWER

    def __init__(self, clock: Clock, audit: AuditService, alerts: AlertService, http: httpx.Client | None = None):
        ...

    def ingest(self, db, *, start=None, end=None, district_ids=None) -> IngestResult:
        today = self.clock.today()
        end = end or today - timedelta(days=1)
        start = start or end - timedelta(days=settings.POWER_LOOKBACK_DAYS - 1)
        scenario_id = current_scenario()
        districts = active_districts(db, district_ids, scenario_id)       # ReplayScenario.districts in replay
        client = self.client_for(scenario_id, today)

        # 1) network, outside the DB transaction
        fetched, errors = {}, []
        with ThreadPoolExecutor(settings.POWER_MAX_WORKERS) as pool:
            futs = {pool.submit(client.fetch, d, start, end): d for d in districts}
            for f in as_completed(futs):
                d = futs[f]
                try:
                    fetched[d.id] = f.result()
                except (SourceUnavailable, UnitMismatch) as exc:
                    errors.append({"district_pcode": d.pcode, "error": str(exc)})

        # 2) one transaction: lock, upsert, copy-forward, status, audit
        if not try_advisory_xact_lock(db, "ingest:NASA_POWER"):
            raise Conflict("INGEST_ALREADY_RUNNING")
        res = IngestResult(source=self.source, start=start, end=end, scenario_id=scenario_id,
                           districts_requested=len(districts), errors=errors)
        latest_fresh: list[date] = []
        for d in districts:
            days = [validate_power_day(x) for x in fetched[d.id].days] if d.id in fetched else []
            fresh = [v for v in days if v.rainfall_mm is not None]
            res.values_rejected += sum(1 for v in days for f in v.flags if "out_of_range" in f)
            upsert_observations(db, [to_row(d, v, scenario_id) for v in fresh])
            res.rows_fresh += len(fresh)
            last_valid = latest_valid_date(db, self.source, d.id, scenario_id)   # includes rows just upserted
            stale = plan_copy_forward(fresh_dates={v.obs_date for v in fresh}, last_valid=last_valid,
                                      window=(start, end), today=today,
                                      expected_lag=settings.POWER_EXPECTED_LAG_DAYS,
                                      max_days=settings.POWER_MAX_COPY_FORWARD_DAYS)
            existing_fresh = fresh_dates_in_db(db, self.source, d.id, start, end, scenario_id)
            stale = [s for s in stale if s.obs_date not in existing_fresh]       # never overwrite fresh with stale
            upsert_observations(db, [copy_row(db, d, s, scenario_id) for s in stale])
            res.rows_stale += len(stale)
            res.rows_not_yet_published += count_not_due(start, end, today, settings.POWER_EXPECTED_LAG_DAYS, fresh)
            if last_valid:
                latest_fresh.append(last_valid)
        res.is_healthy = not errors and res.rows_stale == 0
        res.last_valid_date = min(latest_fresh) if latest_fresh else None
        transition = update_source_status(db, source=self.source, healthy=res.is_healthy,
                                          last_valid_date=res.last_valid_date,
                                          error="; ".join(e["error"] for e in errors[:3]) or None,
                                          now=self.clock.now(), scenario_id=scenario_id)
        status_id = source_status_id(db, self.source)
        self.audit.record(db, event_type="DATA_INGESTED", entity_type="DataSourceStatus",
                          entity_id=status_id, actor="system", payload=res.audit_payload())
        if transition is Transition.DOWN:
            self.audit.record(db, event_type="DATA_SOURCE_STALE", entity_type="DataSourceStatus",
                              entity_id=status_id, actor="system",
                              payload={"source": "NASA_POWER", "last_valid_date": str(res.last_valid_date)})
            notify_admin_source_down(db, self.alerts, "NASA_POWER", res)
        elif transition is Transition.UP:
            self.audit.record(db, event_type="DATA_SOURCE_RECOVERED", ...)
        return res
```
In replay `status_id` is the `ReplayScenario.id` and `entity_type="ReplayScenario"` (no live status row is touched).

The upsert uses `ON CONFLICT ON CONSTRAINT uq_climate_observation DO UPDATE SET rainfall_mm=EXCLUDED.rainfall_mm, …, is_stale=EXCLUDED.is_stale, stale_since=EXCLUDED.stale_since, quality_flags=EXCLUDED.quality_flags, updated_at=now()`. It relies on the NULLS NOT DISTINCT key (proposed). Until it is merged, use `ON CONFLICT (district_id, obs_date, source) WHERE tehsil_id IS NULL AND lead_day IS NULL AND scenario_id IS NULL` plus a scenario variant, or delete-then-insert inside the transaction.

### 5. Pipeline step (`app/services/ingestion/jobs.py`)

```python
def run_nasa_power_step(db, clock: Clock) -> dict:
    try:
        return get_ingestor(DataSource.NASA_POWER).ingest(db).summary()
    except Exception as exc:                  # UC03-6: never block FR3
        log.exception("nasa_power_step_failed")
        db.rollback()
        mark_unhealthy_and_alert(db, DataSource.NASA_POWER, str(exc))   # own small transaction
        return {"source": "NASA_POWER", "is_healthy": False, "error": str(exc)}

def register_ingestion_jobs(pipeline, scheduler) -> None:
    register_ingestor(NasaPowerIngestor(...))
    pipeline.register_step(10, "ingest.nasa_power", run_nasa_power_step)
    scheduler.register_job("refresh_context_indicators", run_context_refresh, cron=settings.CONTEXT_REFRESH_CRON)
```

### 6. Vulnerability weight (`app/services/ingestion/vulnerability.py`)

```python
def compute_vulnerability(values: dict[UUID, dict[str, float | None]], province: dict[UUID, str],
                          weights: dict[str, float], inverted: set[str]) -> dict[UUID, VulnResult]:
    # 1) impute with province median, remember which were imputed
    filled, imputed = {}, defaultdict(set)
    for ind in weights:
        by_prov = defaultdict(list)
        for did, v in values.items():
            if v.get(ind) is not None:
                by_prov[province[did]].append(v[ind])
        for did, v in values.items():
            x = v.get(ind)
            if x is None:
                x = statistics.median(by_prov[province[did]]) if by_prov[province[did]] else None
                imputed[did].add(ind)
            filled.setdefault(did, {})[ind] = x
    # 2) min-max per indicator across districts, invert "good" indicators
    norm = {}
    for ind in weights:
        xs = [f[ind] for f in filled.values() if f[ind] is not None]
        lo, hi = min(xs), max(xs)
        for did, f in filled.items():
            n = 0.5 if hi == lo or f[ind] is None else (f[ind] - lo) / (hi - lo)
            norm.setdefault(did, {})[ind] = 1 - n if ind in inverted else n
    # 3) weighted mean
    tot = sum(weights.values())
    return {did: VulnResult(weight=round(sum(weights[i] * norm[did][i] for i in weights) / tot, 4),
                            inputs={i: {"raw": filled[did][i], "norm": round(norm[did][i], 4),
                                        "imputed": i in imputed[did]} for i in weights},
                            imputed=bool(imputed[did]))
            for did in values}
```

## Configuration

| Key | Default | Notes |
|---|---|---|
| `NASA_POWER_BASE_URL` | `https://power.larc.nasa.gov/api/temporal/daily/point` | |
| `POWER_LOOKBACK_DAYS` | 10 | Re-fetched every run |
| `POWER_EXPECTED_LAG_DAYS` | 3 | Verify (clarify #1) |
| `POWER_MAX_COPY_FORWARD_DAYS` | 3 | |
| `POWER_MAX_WORKERS` / `POWER_TIMEOUT_S` / `POWER_MAX_RETRIES` | 4 / 60 / 3 | |
| `POWER_RAIN_MAX_MM` | 600 | |
| `REPLAY_POWER_LAG_DAYS` | 2 | No-lookahead cut-off |
| `REPLAY_SPINUP_DAYS` | 30 | Cache padding before the scenario start |
| `VULNERABILITY_WEIGHTS` | `{"poverty_headcount":0.5,"housing_kutcha_pct":0.3,"literacy_rate":0.2}` | JSON |
| `VULNERABILITY_INVERTED` | `["literacy_rate"]` | |
| `CONTEXT_INDICATORS_FILE` | `data/seeds/context_indicators.csv` | |
| `CONTEXT_REFRESH_CRON` | `0 2 1 * *` | UTC |
| `WORLD_BANK_BASE_URL` | `https://api.worldbank.org/v2` | Reference series `SI.POV.NAHC` (verify code) |

## Commands

```bash
alembic upgrade head
pytest -q tests/unit/ingestion tests/integration/ingestion tests/contract/test_ingestion_api.py
# backfill for FR3 training (live rows)
python scripts/ingest_nasa_power.py --start 2001-01-01 --end 2025-12-31
# replay caches (needs internet once, then commit)
python scripts/ingest_nasa_power.py --scenario sindh-2022 --to-cache
python scripts/ingest_nasa_power.py --scenario buner-2025 --to-cache
python scripts/ingest_nasa_power.py --scenario control-2021 --to-cache
# manual run via API
curl -X POST localhost:8000/api/v1/ingestion/run -H "Authorization: Bearer $ADMIN" \
     -H "Content-Type: application/json" -d '{"source":"NASA_POWER"}'
# live sanity call
curl "https://power.larc.nasa.gov/api/temporal/daily/point?parameters=PRECTOTCORR,T2M,GWETROOT,GWETTOP&community=AG&latitude=26.73&longitude=67.78&start=20220801&end=20220831&format=JSON"
```

## Verification checklist

| Acceptance | How |
|---|---|
| US1-1 | `test_nasa_power_ingest.py::test_ingests_all_active_districts` |
| US1-2 | `test_nasa_power_parse.py::test_fill_value_is_none` |
| US1-3 | `test_validation.py` (range, pct conversion, flags) |
| US1-4 | `test_nasa_power_ingest.py::test_rerun_is_idempotent` |
| US1-5 | `test_nasa_power_ingest.py::test_step_never_raises` + manual `POST /api/v1/admin/pipeline/run` shows step 30 ran |
| US1-6 | `test_nasa_power_ingest.py::test_inactive_skipped` |
| US2-1..5 | `test_nasa_power_ingest.py -k "outage or recovery or latency or partial"`, `test_staleness.py` |
| US3-1 | Manual: files exist under `data/replay/<key>/nasa_power/` for every scenario district |
| US3-2..4 | `test_nasa_power_replay.py` |
| US4-1..4 | `test_vulnerability.py`, `test_context_refresh.py` |
| SC-001 | T035 timing note |
| SC-002 | T036 SQL check |
| Contracts | `tests/contract/test_ingestion_api.py` |

## Demo notes

- FR1 is invisible in the happy path. It shows up as the "Data as of 7 Aug 2022" line under the risk chart during replay.
- Optional (clarify #7): put `{"outages":[["2022-08-20","2022-08-22"]]}` in `data/replay/sindh-2022/nasa_power/outages.json` to show the stale banner and the admin alert, then recovery the next replay day. Keep it off unless rehearsed.

## Pitfalls

- POWER date keys are strings like `"20220815"`; some responses include a trailing month-total key `"202208"` in monthly APIs. Ignore any key that is not 8 digits.
- Do not hold the DB transaction open during HTTP calls. Fetch first, then write.
- `ON CONFLICT` with nullable key columns silently inserts duplicates without NULLS NOT DISTINCT. Test re-runs explicitly.
- The advisory lock key must be stable across processes: use `hashtext('ingest:NASA_POWER')` in SQL, not Python `hash()`.
- In replay the clock is `ReplayClock`; never call `date.today()` directly.
- APScheduler under `uvicorn --reload` may double-register jobs (foundation pitfall); `register_ingestion_jobs()` must be idempotent.
- NASA POWER sometimes returns HTTP 200 with an error message in `messages` and no `properties`. Treat a missing `properties.parameter` as `SourceUnavailable`.

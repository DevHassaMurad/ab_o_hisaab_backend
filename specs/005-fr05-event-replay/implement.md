# Implement Guide: FR5 Event Replay

## Execution order

| Step | Tasks | Result |
|---|---|---|
| 1 Setup | T001–T004 | Packages, YAML, marker, mini fixture |
| 2 Clock + scenarios | T005–T010 | Migration, `ReplayClock`, scenario rows, schemas |
| 3 Engine core | T011–T016 | start/step/state on mini, isolation proven |
| 4 Caches | T017–T020 | Manifest verify, build script, real caches committed |
| 5 Approvals | T021–T023 | AUTO_SIM and live pause |
| 6 Backtests | T024–T029 | NFR3 green, report script |
| 7 Seek/reset | T030–T032 | Rehearsal loop |
| 8 Stage | T033–T035 | Timeline, autoplay |
| 9 Polish | T036–T039 | Perf, determinism, rehearsal |

## Key algorithms

### ReplayClock (`app/services/replay/clock.py`)
```python
class ReplayClock:
    """Clock for one simulated day. today() = sim date; now() advances with wall time inside the step."""
    def __init__(self, sim_date: date, pipeline_time: time = time(1, 0)):
        self.sim_date = sim_date
        self._base = datetime.combine(sim_date, pipeline_time, tzinfo=UTC)
        self._wall0 = time_mod.monotonic()

    def today(self) -> date:
        return self.sim_date

    def now(self) -> datetime:
        return self._base + timedelta(seconds=time_mod.monotonic() - self._wall0)

@contextmanager
def clock_scope(clock: Clock):
    prev = get_clock()
    set_clock(clock)
    try:
        yield clock
    finally:
        set_clock(prev)
```

### Engine (`app/services/replay/engine.py`)
```python
LOCK_NS = 5005

class ReplayEngine:
    def __init__(self, settings: ReplaySettings):
        self.s = settings

    def start(self, db: Session, key: str, approval_mode: str = "AUTO_SIM") -> ReplayState:
        sc = get_scenario_by_key(db, key)
        if sc.status in ("RUNNING", "AWAITING_APPROVAL", "COMPLETED"):
            return self.state(db, key)                                  # idempotent
        with scenario_lock(db, key):
            manifest = verify_manifest(key)                             # raises CacheInvalid(file)
            defs = load_definitions()[key]
            with use_scenario(sc.id), clock_scope(ReplayClock(sc.start_date - timedelta(days=1))):
                for d_id in sc.districts:
                    create_fund_with_tiers(db, d_id, sc.id, allocated_pkr=Decimal(defs.funds.allocated_pkr))  # FR6
                load_precomputed_flood_maps(db, key)                     # FR21 D1 loader; no-op if absent
                spin_start = sc.start_date - timedelta(days=defs.spinup_days)
                for ing in all_ingestors():                              # FR1 registry (FR2 plugs in)
                    ing.ingest(db, start=spin_start, end=sc.start_date - timedelta(days=1),
                               district_ids=list(sc.districts))
                sc.config = {**defs.config_json(db), "approval_mode": approval_mode,
                             "modes": snapshot_modes(db, list(sc.districts)),           # FR4
                             "manifest_sha256": manifest.sha256,
                             "forecast_provenance": manifest.provenance_shares()}
                sc.status, sc.sim_date, sc.started_at = "RUNNING", sc.start_date - timedelta(days=1), utcnow()
                AuditService.record(db, event_type="REPLAY_STARTED", entity_type="ReplayScenario",
                                    entity_id=sc.id, actor=current_actor(),
                                    payload={"key": key, "run_no": sc.run_no, "approval_mode": approval_mode,
                                             "manifest_sha256": manifest.sha256})
            db.commit()
        return self.state(db, key)

    def step(self, db: Session, key: str, days: int = 1) -> ReplayState:
        sc = get_scenario_by_key(db, key)
        if sc.status == "IDLE":
            raise Conflict("REPLAY_NOT_STARTED")
        stepped: list[date] = []
        with scenario_lock(db, key, try_only=True):                      # 409 REPLAY_BUSY
            for _ in range(days):
                db.refresh(sc)
                if sc.status == "AWAITING_APPROVAL":
                    if pending_action_approvals(db, sc.id):
                        if not stepped:
                            raise Conflict("REPLAY_AWAITING_APPROVAL",
                                           extra={"approval_ids": pending_action_approvals(db, sc.id)})
                        break
                    sc.status = "RUNNING"
                if sc.sim_date >= sc.end_date:
                    self._complete(db, sc); break
                day = sc.sim_date + timedelta(days=1)
                try:
                    with use_scenario(sc.id), clock_scope(ReplayClock(day, self.s.pipeline_time)) as clk:
                        run_daily_pipeline(db, clk)                      # foundation runner: FR1→FR2→FR3→FR7…
                        drain_jobs(self.s.drain_jobs)                    # payments.dispatch, messaging.dispatch
                        waiting = resolve_approvals(db, sc, sc.config["approval_mode"])
                        drain_jobs(self.s.drain_jobs)
                    sc.sim_date, sc.last_step_at = day, utcnow()
                    sc.status = "AWAITING_APPROVAL" if waiting else "RUNNING"
                    db.commit(); stepped.append(day)
                except Exception as e:
                    db.rollback()
                    sc.status, sc.last_error = "FAILED", f"{day}: {e!r}"[:2000]
                    db.commit()
                    raise StepFailed("REPLAY_STEP_FAILED", day=day) from e
                if waiting:
                    break
        return self.state(db, key, stepped=stepped)

    def seek(self, db: Session, key: str, target: date, reset: bool = False) -> ReplayState:
        sc = get_scenario_by_key(db, key)
        if not sc.start_date <= target <= sc.end_date:
            raise Unprocessable("DATE_OUT_OF_WINDOW")
        if sc.sim_date is not None and target <= sc.sim_date:
            if not reset:
                raise Conflict("REPLAY_SEEK_BACKWARD")
            mode = (sc.config or {}).get("approval_mode", "AUTO_SIM")
            self.reset(db, key); self.start(db, key, mode)
            sc = get_scenario_by_key(db, key)
        if sc.status == "IDLE":
            self.start(db, key); sc = get_scenario_by_key(db, key)
        return self.step(db, key, days=(target - sc.sim_date).days)

    def reset(self, db: Session, key: str) -> ResetResult:
        autoplay.pause(key)
        sc = get_scenario_by_key(db, key)
        with scenario_lock(db, key):
            AuditService.record(db, event_type="REPLAY_RESET", entity_type="ReplayScenario", entity_id=sc.id,
                                actor=current_actor(), payload={"key": key, "run_no": sc.run_no})  # old chain
            db.commit()
            deleted = purge_scenario_rows(db, sc.id)
            sc.run_no += 1                                    # FR20: next record() opens replay:{sid}:{run_no}
            sc.status, sc.sim_date, sc.config, sc.last_error = "IDLE", None, None, None
            sc.started_at = sc.completed_at = sc.last_step_at = None
            db.commit()
        return ResetResult(key=key, run_no=sc.run_no, deleted=deleted)
```
`scenario_lock` uses `pg_try_advisory_lock(LOCK_NS, hashtext(key))` (session-level, released in `finally`); `try_only=False` waits up to 10 s.

### Reset purge (`app/services/replay/reset.py`)
```python
# Children first. Tables without scenario_id are deleted through their parent until the
# proposed denormalised scenario_id columns land.
RESET_ORDER: list[tuple[str, str | None]] = [
    ("confirmation",     "alert_id IN (SELECT id FROM alert WHERE scenario_id = :sid)"),
    ("alert",            None),
    ("payment_event",    "payment_id IN (SELECT id FROM payment WHERE scenario_id = :sid)"),
    ("payment",          None),
    ("fairness_check",   "fund_release_id IN (SELECT id FROM fund_release WHERE scenario_id = :sid)"),
    ("allocation",       "fund_release_id IN (SELECT id FROM fund_release WHERE scenario_id = :sid)"),
    ("approval",         "fund_release_id IN (SELECT id FROM fund_release WHERE scenario_id = :sid)"),
    ("field_agent_task", "payment_id IN (SELECT id FROM payment WHERE scenario_id = :sid)"),
    ("fund_release",     None),
    ("trigger_event",    None),
    ("risk_index",       None),
    ("flood_forecast",   None),
    ("satellite_flood_map", None),
    ("climate_observation", None),
    ("trigger_tier",     "fund_id IN (SELECT id FROM disaster_fund WHERE scenario_id = :sid)"),
    ("disaster_fund",    None),
]
NEVER_PURGE = {"audit_log", "replay_scenario", "beneficiary", "district", "tehsil", "user"}

def purge_scenario_rows(db: Session, sid: UUID) -> dict[str, int]:
    out = {}
    for table, where in RESET_ORDER:
        assert table not in NEVER_PURGE
        clause = where or "scenario_id = :sid"
        out[table] = db.execute(text(f"DELETE FROM {table} WHERE {clause}"), {"sid": sid}).rowcount
    return out
```
A unit test asserts that every mapped table with a `scenario_id` column (from SQLAlchemy metadata) appears in `RESET_ORDER`, so a new FR table cannot be forgotten.

### Approvals (`app/services/replay/approvals.py`)
```python
def resolve_approvals(db: Session, sc: ReplayScenario, mode: str) -> bool:
    """Returns True if the replay must wait for a real DC decision."""
    pending = pending_action_approvals(db, sc.id)                 # Approval.decision == PENDING, scenario via FundRelease
    if not pending:
        return False
    if mode != "AUTO_SIM":
        return True                                               # FR8 already alerted the DC (allowlisted phone)
    for ap in pending:
        rel = db.get(FundRelease, ap.fund_release_id)
        dc = seeded_dc_for(db, rel.fund.district_id)              # may be None
        ApprovalService.decide(db, ap.id, decision="APPROVED", channel="REPLAY_SIM",
                               actor="replay-sim", approver_id=dc.id if dc else None,
                               reason="Replay simulation: automatic approval", simulated=True)   # FR8 audits
    return False
```

### Backtest evaluation (`app/services/replay/backtest.py`)
```python
def evaluate_backtest(db: Session, sc: ReplayScenario) -> BacktestReport:
    cfg = sc.config or load_definitions()[sc.key].config_json(db)
    rel = releases_for(db, sc.id)                     # [(district_id, tier, run_date, release_id)] run_date = RiskIndex.run_date
    scores = district_scores(db, sc.id)               # DataFrame district_id, run_date, score, tier
    watch = watch_thresholds(db, sc.id)               # {district_id: min_score}
    failures: list[str] = []
    actions = [r for r in rel if r.tier == "ACTION"]
    approved = [r for r in actions if has_approval(db, r.release_id)]
    if len(approved) != len(actions):
        failures.append(f"{len(actions) - len(approved)} Action release(s) without approval")

    if sc.expected_outcome == "TRIGGER":
        hero = cfg["hero_district_id"]
        onset = date.fromisoformat(cfg["reference_onset"][hero])
        hero_rel = sorted((r for r in rel if r.district_id == hero), key=lambda r: r.run_date)
        if not hero_rel:
            failures.append("hero district never triggered")
            first = None
        else:
            first = hero_rel[0]
            lead = (onset - first.run_date).days
            if lead < cfg["min_lead_days"]:
                failures.append(f"first release {first.run_date} has lead {lead} < {cfg['min_lead_days']}")
    else:
        first = None
        if rel:
            failures.append(f"{len(rel)} release(s) in a NO_TRIGGER scenario")
        over = scores[scores.score >= scores.district_id.map(watch)]
        if not over.empty:
            failures.append(f"{len(over)} district-day(s) at or above Watch")

    return BacktestReport.build(sc, cfg, rel, scores, first, failures,
                                complete=sc.status == "COMPLETED",
                                provenance=cfg.get("forecast_provenance"),
                                model_versions=model_versions_used(db, sc.id))
```

### Backtest test (`tests/backtest/test_nfr3_backtests.py`)
```python
pytestmark = pytest.mark.backtest

@pytest.mark.parametrize("key", ["sindh-2022", "buner-2025", "control-2021"])
def test_nfr3(db, engine: ReplayEngine, key: str):
    engine.reset(db, key)
    engine.start(db, key, approval_mode="AUTO_SIM")
    sc = get_scenario_by_key(db, key)
    engine.step(db, key, days=(sc.end_date - sc.sim_date).days + 1)
    report = evaluate_backtest(db, get_scenario_by_key(db, key))
    write_report(report, Path("reports/backtest") / f"{key}.json")
    assert report.complete
    assert report.passed, "\n".join(report.failures)
```
The backtest DB is a dedicated Postgres database (`TEST_BACKTEST_DATABASE_URL`) seeded with boundaries, users and the synthetic register (FR10 seed 2022, sample rate 0.01).

### Cache build (`scripts/replay/build_cache.py`)
```python
def build(key: str) -> None:
    d = load_definitions()[key]
    span = (d.start_date - timedelta(days=d.spinup_days), d.end_date)
    run(["python", "scripts/ingest_nasa_power.py", "--scenario", key, "--to-cache",
         "--start", str(span[0]), "--end", str(span[1])])                                  # FR1
    run(["python", "scripts/cache_replay_imerg.py", "--scenario", key])                     # FR2
    run(["python", "scripts/replay/forecast_rain_cache.py", "--scenario", key, "--provenance", "auto"])  # FR3
    if d.expected_outcome == "TRIGGER":
        run_if_exists("scripts/precompute_flood_maps.py", "--scenario", key)                # FR21 (D1); name per FR21 plan
    write_manifest(key)
```

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `REPLAY_DEFINITIONS` | `data/replay/scenarios.yaml` | Scenario file |
| `REPLAY_CACHE_ROOT` | `data/replay` | Cache root |
| `REPLAY_PIPELINE_TIME_UTC` | `01:00` | Simulated pipeline time |
| `REPLAY_DRAIN_JOBS` | `payments.dispatch,messaging.dispatch` | Jobs run after each simulated day (align ids with FR17/FR14) |
| `REPLAY_MAX_STEP_DAYS` | `60` | Upper bound per step request |
| `REPLAY_AUTOPLAY_MIN_INTERVAL_S` | `2` | Floor for play interval |
| `REPLAY_POWER_LAG_DAYS` | `2` | FR1's no-lookahead lag (owned by FR1) |
| `REPLAY_IMERG_LAG_DAYS` | `1` | FR2's lag (owned by FR2) |
| `TEST_BACKTEST_DATABASE_URL` | — | Backtest database |

## Commands

```bash
alembic upgrade head
python scripts/replay/build_cache.py --all                 # once, needs network + EE credentials
pytest tests/unit/replay tests/integration/replay tests/contract/test_replay_api.py -q
pytest -m backtest -q                                      # NFR3
python scripts/replay/run_backtest.py --all --out reports/backtest --timing
# drive a replay by hand
curl -X POST localhost:8000/api/v1/replay/scenarios/sindh-2022/start -H "Authorization: Bearer $OFFICER" -d '{"approval_mode":"AUTO_SIM"}'
curl -X POST localhost:8000/api/v1/replay/scenarios/sindh-2022/seek  -H "Authorization: Bearer $OFFICER" -d '{"date":"2022-08-14"}'
curl -X POST localhost:8000/api/v1/replay/scenarios/sindh-2022/play  -H "Authorization: Bearer $OFFICER" -d '{"interval_seconds":3,"until":"2022-08-26"}'
curl localhost:8000/api/v1/replay/scenarios/sindh-2022/timeline -H "Authorization: Bearer $OFFICER"
curl -X POST localhost:8000/api/v1/replay/scenarios/sindh-2022/reset -H "Authorization: Bearer $OFFICER"
```

## Verification checklist

| Acceptance scenario | Check |
|---|---|
| US1-1 start state, funds, sim date | T011 |
| US1-2 step runs pipeline, scenario ids | T011 |
| US1-3 live untouched | T012, T026 |
| US1-4 completed after end | T011 |
| US1-5 busy | T011 |
| US2-1 manifest written | T019, T020 (manual check of `manifest.json`) |
| US2-2 tampered cache refused | T017 |
| US2-3 proxy share in state | T033 |
| US3-1/2/3 NFR3 outcomes | T025 |
| US3-4 Action has approval | T024, T025 |
| US3-5 report contents | T024, T029 |
| US4-1/2/3/4 approvals | T021 |
| US5-1/2/3/4 seek, reset, chains | T030 |
| US6-1/2/3 timeline, autoplay, DC scope | T033 |
| SC-005 timing | T036 |
| SC-006 determinism | T037 |

## Demo notes (3-minute live replay, pitch 1:30–4:30, then 4:30–5:15)

Before going on stage: `reset` all three; `start sindh-2022` with `LIVE_WHATSAPP`; `seek` to 2022-08-12 (calm days already run); `start buner-2025` and `control-2021` with `AUTO_SIM` and run both to the end (their slides are pre-computed by the same engine).

1. (0:00) Map shows Sindh green. Presenter: "This is 12 August 2022, replayed on the real system." Press play (3 s/day).
2. (0:20) Dadu turns Watch around 16 Aug (expected; verify on rehearsal). Explanation in Urdu and English on screen; 10% released; boats line on the release purpose.
3. (0:45) Warning: 30% more released; payments flow Reserved → Processing → Paid (simulated, labelled).
4. (1:10) Action: autoplay pauses itself (`AWAITING_APPROVAL`). The DC's phone buzzes; DC replies `APPROVE 123456`. Next step continues; 60% released.
5. (1:40) Donor view with the scenario header: allocated, disbursed, received; verify chain.
6. (2:20) Switch to Buner 2025 timeline (flash mode, 24–48 h lead) and control 2021 (flat line, zero releases) — use `GET …/backtest` numbers for the slide.

Fallback for stage: if WhatsApp is slow, approve from the dashboard with OTP (same `Approval` row, channel `DASHBOARD`). If network is down, restart the hero scenario in `AUTO_SIM`.

## Pitfalls

- Never call `date.today()` in any pipeline code; a single call breaks replay silently. CI grep: `rg "date\.today\(|datetime\.(utc)?now\(" app/services app/jobs` must only hit `app/core/clock.py`.
- Contextvars do not cross threads automatically: APScheduler jobs and thread pools (FR1 uses 4 workers) must copy the context (`contextvars.copy_context().run`).
- Reset must write `REPLAY_RESET` **before** bumping `run_no`, or it lands in the new chain.
- Do not reuse a live `DataSourceStatus` row in replay (FR1/FR2 already skip it).
- Freeze the caches before final model training; never train on replay years.
- Rehearse with the exact laptop; committed caches must match the manifest after a fresh clone (Git LFS not needed at this size; check `.gitattributes` does not convert CSV line endings — set `*.csv -text`).
- The DC's live approval must come from an allowlisted phone that has joined the Twilio sandbox.

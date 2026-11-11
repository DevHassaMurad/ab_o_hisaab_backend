from datetime import date

from sqlalchemy import func, select

from app.core.clock import FixedClock
from app.core.scenario import use_scenario
from app.models.core import AuditLog, ReplayScenario
from app.models.geography import District
from app.models.ingestion import ClimateObservation as CO
from app.models.ingestion import DataSourceStatus
from app.services.ingestion.jobs import run_source_step
from app.services.ingestion.nasa_power_ingestor import NasaPowerIngestor
from app.services.ingestion.registry import register_ingestor
from app.services.stubs import LogAlertService
from tests.conftest import PowerStub

S, E = date(2022, 8, 20), date(2022, 8, 26)


def make(stub, today=date(2022, 8, 30)):
    alerts = LogAlertService()
    return NasaPowerIngestor(clock=FixedClock(today), alerts=alerts, live_client=stub.client()), alerts


def rows(db, **kw):
    q = select(func.count()).select_from(CO).where(CO.source == "NASA_POWER", CO.scenario_id.is_(None))
    for k, v in kw.items():
        q = q.where(getattr(CO, k) == v)
    return db.scalar(q)


def active_count(db):
    return db.scalar(select(func.count()).select_from(District).where(District.is_active.is_(True)))


def test_ingests_all_active_districts_and_skips_inactive(seeded):
    db = seeded
    ing, _ = make(PowerStub())
    res = ing.ingest(db, start=S, end=E)
    assert res.is_healthy and res.rows_fresh == active_count(db) * 7
    assert rows(db) == active_count(db) * 7
    inactive = db.scalar(select(District).where(District.name == "Peshawar"))
    assert rows(db, district_id=inactive.id) == 0
    audit = db.scalars(select(AuditLog).where(AuditLog.event_type == "DATA_INGESTED")).all()
    assert len(audit) == 1 and audit[0].payload["rows_fresh"] == res.rows_fresh


def test_rerun_is_idempotent_and_updates_values(seeded):
    db = seeded
    stub = PowerStub(rain=5.0)
    ing, _ = make(stub)
    ing.ingest(db, start=S, end=E)
    stub.rain = 9.0
    ing.ingest(db, start=S, end=E)
    assert rows(db) == active_count(db) * 7
    assert db.scalar(select(func.max(CO.rainfall_mm)).where(CO.source == "NASA_POWER")) == 9.0


def test_outage_marks_stale_alerts_once_then_recovers(seeded):
    db = seeded
    stub = PowerStub()
    ing, _ = make(stub, today=date(2022, 8, 30))
    ing.ingest(db, start=S, end=E)  # healthy history up to 26 Aug

    stub.down = True
    ing2, alerts2 = make(stub, today=date(2022, 8, 31))
    res = ing2.ingest(db, start=date(2022, 8, 27), end=date(2022, 8, 30))
    assert not res.is_healthy and len(res.errors) == active_count(db)
    # due until 28 Aug (3-day lag): 27 and 28 copied forward per district, flagged stale since 26 Aug
    assert res.rows_stale == active_count(db) * 2
    stale = db.scalars(select(CO).where(CO.is_stale.is_(True))).all()
    assert {r.stale_since for r in stale} == {date(2022, 8, 26)}
    assert all("copied_forward" in r.quality_flags for r in stale)
    st = db.scalar(select(DataSourceStatus).where(DataSourceStatus.source == "NASA_POWER"))
    assert st.is_healthy is False and st.last_valid_date == date(2022, 8, 26)
    assert len(alerts2.sent) == 1 and alerts2.sent[0]["purpose"] == "DATA_SOURCE_DOWN"
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.event_type == "DATA_SOURCE_STALE")) == 1

    ing3, alerts3 = make(stub, today=date(2022, 9, 1))  # second outage day: no second alert
    ing3.ingest(db, start=date(2022, 8, 27), end=date(2022, 8, 31))
    assert alerts3.sent == []

    stub.down = False
    ing4, _ = make(stub, today=date(2022, 9, 2))
    res4 = ing4.ingest(db, start=date(2022, 8, 27), end=date(2022, 8, 29))
    assert res4.is_healthy
    assert db.scalar(select(func.count()).select_from(CO).where(CO.is_stale.is_(True),
                                                                CO.obs_date <= date(2022, 8, 29))) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)
                     .where(AuditLog.event_type == "DATA_SOURCE_RECOVERED")) == 1


def test_normal_latency_is_not_stale(seeded):
    db = seeded
    ing, alerts = make(PowerStub(), today=date(2022, 8, 28))  # days 26-27 not yet due
    res = ing.ingest(db, start=S, end=date(2022, 8, 25))
    assert res.is_healthy and res.rows_stale == 0 and alerts.sent == []


def test_pipeline_step_never_raises(seeded):
    class Boom:
        source = "NASA_POWER"

        def ingest(self, db, **kw):
            raise RuntimeError("boom")

    register_ingestor(Boom())
    try:
        out = run_source_step(seeded, FixedClock(date(2022, 8, 30)), "NASA_POWER")
        assert out["is_healthy"] is False and "boom" in out["error"]
    finally:
        register_ingestor(NasaPowerIngestor())


def test_replay_uses_cache_without_lookahead_and_isolates_rows(seeded):
    db = seeded
    sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == "sindh-2022"))
    ing = NasaPowerIngestor(clock=FixedClock(date(2022, 8, 15)), alerts=LogAlertService())
    with use_scenario(sc.id):
        res = ing.ingest(db)
    assert res.errors == [] and res.rows_fresh == 5 * 9  # window 5..14 Aug, cache cut-off 13 Aug
    replay_rows = db.scalars(select(CO).where(CO.scenario_id == sc.id)).all()
    assert len(replay_rows) == 45 and max(r.obs_date for r in replay_rows) == date(2022, 8, 13)
    assert rows(db) == 0  # nothing written as live data
    assert db.scalar(select(func.count()).select_from(DataSourceStatus)) == 0  # live status untouched
    audit = db.scalar(select(AuditLog).where(AuditLog.event_type == "DATA_INGESTED"))
    assert audit.scenario_id == sc.id and audit.chain_key.startswith("replay:")

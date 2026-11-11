"""FR5: engine start/step/seek/reset, isolation from live data, busy lock, manifest checks, reset coverage."""
import json
from datetime import date

import pytest
from sqlalchemy import func, select, text

from app.core.errors import DomainError
from app.main import register_all
from app.models.core import AuditLog, ReplayScenario
from app.models.forecasting import RiskIndex
from app.models.funds import DisasterFund
from app.models.ingestion import ClimateObservation as CO
from app.models.ingestion import DataSourceStatus
from app.services.replay.cache import verify_manifest, write_manifest
from app.services.replay.engine import ReplayEngine
from app.services.replay.reset import CHILDREN, NEVER_PURGE, scenario_tables
from app.services.triggers.fund_settings import seed_live_funds


@pytest.fixture
def eng(seeded):
    register_all()
    seed_live_funds(seeded)
    seeded.flush()
    return ReplayEngine(), seeded


def live_counts(db):
    return (db.scalar(select(func.count()).select_from(RiskIndex).where(RiskIndex.scenario_id.is_(None))),
            db.scalar(select(func.count()).select_from(CO).where(CO.scenario_id.is_(None))),
            db.scalar(select(func.count()).select_from(DataSourceStatus)))


def test_start_creates_funds_snapshot_and_spinup(eng):
    e, db = eng
    before = live_counts(db)
    st = e.start(db, "sindh-2022")
    sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == "sindh-2022"))
    assert st["status"] == "RUNNING" and st["sim_date"] == date(2022, 7, 31) and st["simulated"] is True
    assert db.scalar(select(func.count()).select_from(DisasterFund).where(DisasterFund.scenario_id == sc.id)) == 5
    assert set(sc.config["modes"].values()) == {"SLOW_ONSET"} and sc.config["manifest_sha256"]
    spin = db.scalar(select(func.count()).select_from(CO).where(CO.scenario_id == sc.id, CO.source == "NASA_POWER"))
    assert spin == 5 * 28  # spin-up 2..31 Jul, but on 31 Jul only data up to 29 Jul exists (2-day replay lag)
    assert e.start(db, "sindh-2022")["status"] == "RUNNING"  # idempotent
    assert live_counts(db) == before


def test_step_runs_real_pipeline_in_scenario_only(eng):
    e, db = eng
    e.start(db, "sindh-2022")
    before = live_counts(db)
    st = e.step(db, "sindh-2022", 3)
    sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == "sindh-2022"))
    assert st["stepped"] == ["2022-08-01", "2022-08-02", "2022-08-03"] and st["sim_date"] == date(2022, 8, 3)
    n = db.scalar(select(func.count()).select_from(RiskIndex).where(RiskIndex.scenario_id == sc.id,
                                                                    RiskIndex.tehsil_id.is_(None)))
    assert n == 3 * 5
    dadu = next(d for d in st["districts"] if d["name"] == "Dadu")
    assert dadu["score"] is not None and dadu["forecast_is_proxy"] is True
    assert live_counts(db) == before


def test_busy_lock(eng, engine):
    e, db = eng
    e.start(db, "control-2021")
    with engine.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(5005, hashtext('control-2021'))"))
        with pytest.raises(DomainError) as exc:
            e.step(db, "control-2021", 1)
        other.execute(text("SELECT pg_advisory_unlock(5005, hashtext('control-2021'))"))
    assert exc.value.code == "REPLAY_BUSY"


def test_seek_reset_and_audit_chains(eng):
    e, db = eng
    e.start(db, "control-2021")
    e.seek(db, "control-2021", date(2021, 8, 3))
    with pytest.raises(DomainError) as exc:
        e.seek(db, "control-2021", date(2021, 8, 2))
    assert exc.value.code == "REPLAY_SEEK_BACKWARD"
    sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == "control-2021"))
    old_chain = f"replay:{sc.id}:1"
    old_audit = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.chain_key == old_chain))
    out = e.reset(db, "control-2021")
    assert out["run_no"] == 2 and out["deleted"]["risk_index"] > 0 and out["deleted"]["trigger_tier"] == 15
    assert db.scalar(select(func.count()).select_from(RiskIndex).where(RiskIndex.scenario_id == sc.id)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.chain_key == old_chain)) \
        == old_audit + 1  # REPLAY_RESET written to the old chain; nothing deleted
    st = e.seek(db, "control-2021", date(2021, 8, 2))
    assert st["sim_date"] == date(2021, 8, 2) and st["run_no"] == 2
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.chain_key == f"replay:{sc.id}:2")) > 0


def test_seek_backward_with_reset(eng):
    e, db = eng
    e.seek(db, "control-2021", date(2021, 8, 3))
    st = e.seek(db, "control-2021", date(2021, 8, 1), reset=True)
    assert st["sim_date"] == date(2021, 8, 1) and st["run_no"] == 2


def test_step_before_start_and_out_of_window(eng):
    e, db = eng
    with pytest.raises(DomainError) as exc:
        e.step(db, "buner-2025", 1)
    assert exc.value.code == "REPLAY_NOT_STARTED"
    with pytest.raises(DomainError) as exc:
        e.seek(db, "buner-2025", date(2030, 1, 1))
    assert exc.value.code == "DATE_OUT_OF_WINDOW"


def test_timeline_and_backtest_so_far(eng):
    e, db = eng
    e.seek(db, "control-2021", date(2021, 8, 2))
    tl = e.timeline(db, "control-2021")
    assert [d["date"] for d in tl["days"]] == [date(2021, 8, 1), date(2021, 8, 2)]
    assert len(tl["days"][0]["districts"]) == 5
    bt = e.backtest(db, "control-2021")
    assert bt["complete"] is False and bt["basis"] == "tier_crossing" and bt["forecast_is_proxy"] is True


def test_manifest_detects_tampering(tmp_path):
    d = tmp_path / "k" / "nasa_power"
    d.mkdir(parents=True)
    f = d / "X.json"
    f.write_text(json.dumps({"properties": {"parameter": {"PRECTOTCORR": {"20220801": 1.0}}}}))
    write_manifest("k", root=tmp_path)
    assert verify_manifest("k", root=tmp_path)["files"]["nasa_power/X.json"]["rows"] == 1
    f.write_text(f.read_text() + " ")
    with pytest.raises(DomainError) as exc:
        verify_manifest("k", root=tmp_path)
    assert exc.value.code == "REPLAY_CACHE_INVALID" and "X.json" in exc.value.detail


def test_committed_manifests_verify():
    for key in ("sindh-2022", "buner-2025", "control-2021"):
        assert verify_manifest(key)["files"]


def test_reset_covers_every_operational_table():
    from app.core.db import Base

    covered = set(scenario_tables()) | set(CHILDREN) | NEVER_PURGE
    assert {t.name for t in Base.metadata.sorted_tables} <= covered
    assert "audit_log" not in scenario_tables()

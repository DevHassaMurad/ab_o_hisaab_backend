from datetime import date

import pytest
from sqlalchemy import func, select

from app.core.clock import FixedClock
from app.core.config import settings
from app.core.scenario import use_scenario
from app.integrations.earth_engine import EEUnavailable, TehsilRain
from app.models.core import AuditLog, ReplayScenario
from app.models.geography import District, Tehsil
from app.models.ingestion import ClimateObservation as CO
from app.models.ingestion import DataSourceStatus
from app.services.ingestion.imerg_ingestor import ImergIngestor
from app.services.stubs import LogAlertService


class FakeGateway:
    """Stands in for Earth Engine: 48 images per day unless told otherwise."""

    def __init__(self, counts=None, rain=12.5, burst=6.0, missing=()):
        self.counts = counts or {}
        self.rain, self.burst = rain, burst
        self.missing = set(missing)
        self.down = False
        self.reduce_calls = 0

    def image_count(self, day):
        if self.down:
            raise EEUnavailable("EEException: Too many concurrent aggregations")
        return self.counts.get(day, 48)

    def reduce_imerg(self, day, features):
        self.reduce_calls += 1
        return [TehsilRain(f.pcode, self.rain, self.burst, ["centroid_fallback"] if f.pcode in self.missing else [])
                for f in features]


def tehsils_in(db, *names):
    return list(db.scalars(select(Tehsil).join(District).where(District.name.in_(names))))


def ids(db, *names):
    return list(db.scalars(select(District.id).where(District.name.in_(names))))


def imerg_rows(db, **kw):
    q = select(func.count()).select_from(CO).where(CO.source == "GPM_IMERG", CO.scenario_id.is_(None))
    for k, v in kw.items():
        q = q.where(getattr(CO, k) == v)
    return db.scalar(q)


def test_rows_per_tehsil_per_day_with_both_values(seeded):
    db = seeded
    ing = ImergIngestor(FakeGateway(), clock=FixedClock(date(2022, 8, 27)), alerts=LogAlertService())
    res = ing.ingest(db, start=date(2022, 8, 24), end=date(2022, 8, 26), district_ids=ids(db, "Dadu"))
    assert res.is_healthy and res.tehsils_requested == 4 and res.rows_fresh == 12
    row = db.scalar(select(CO).where(CO.source == "GPM_IMERG").limit(1))
    assert row.tehsil_id is not None and row.district_id is not None
    assert row.rainfall_mm == 12.5 and row.rainfall_max_3h_mm == 6.0


def test_incomplete_recent_day_skipped_partial_overdue_day_flagged(seeded):
    db = seeded
    gw = FakeGateway(counts={date(2022, 8, 26): 31, date(2022, 8, 24): 44})
    ing = ImergIngestor(gw, clock=FixedClock(date(2022, 8, 27)), alerts=LogAlertService())
    res = ing.ingest(db, start=date(2022, 8, 24), end=date(2022, 8, 26), district_ids=ids(db, "Dadu"))
    status = {d["date"]: d["status"] for d in res.days}
    assert status["2022-08-26"] == "NOT_YET_PUBLISHED" and status["2022-08-24"] == "PARTIAL"
    assert imerg_rows(db, obs_date=date(2022, 8, 26)) == 0
    flagged = db.scalars(select(CO).where(CO.obs_date == date(2022, 8, 24), CO.source == "GPM_IMERG")).all()
    assert flagged and all("imerg_partial:44/48" in r.quality_flags for r in flagged)


def test_idempotent(seeded):
    db = seeded
    ing = ImergIngestor(FakeGateway(), clock=FixedClock(date(2022, 8, 27)), alerts=LogAlertService())
    for _ in range(2):
        ing.ingest(db, start=date(2022, 8, 24), end=date(2022, 8, 26), district_ids=ids(db, "Dadu"))
    assert imerg_rows(db) == 12


def test_outage_uses_shared_stale_path(seeded):
    db = seeded
    gw = FakeGateway()
    ImergIngestor(gw, clock=FixedClock(date(2022, 8, 27)), alerts=LogAlertService()).ingest(
        db, start=date(2022, 8, 22), end=date(2022, 8, 26), district_ids=ids(db, "Dadu"))
    gw.down = True
    alerts = LogAlertService()
    res = ImergIngestor(gw, clock=FixedClock(date(2022, 8, 31)), alerts=alerts).ingest(
        db, start=date(2022, 8, 27), end=date(2022, 8, 30), district_ids=ids(db, "Dadu"))
    assert not res.is_healthy and res.errors[0]["scope"] == "earth_engine"
    assert res.rows_stale == 4 * 3  # 27, 28, 29 Aug overdue on 31 Aug (IMERG lag is 2 days), 4 tehsils
    assert db.scalar(select(DataSourceStatus.is_healthy).where(DataSourceStatus.source == "GPM_IMERG")) is False
    assert len(alerts.sent) == 1


def test_replay_reads_csv_cache_with_cutoff(seeded, tmp_path, monkeypatch):
    db = seeded
    sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == "buner-2025"))
    teh = tehsils_in(db, "Buner", "Swat", "Shangla")
    lines = ["date,tehsil_pcode,rainfall_mm,rainfall_max_3h_mm,n_images"]
    for day in ("2025-08-13", "2025-08-14", "2025-08-15"):
        lines += [f"{day},{t.pcode},{40.0 if t.name == 'Daggar' else 3.0},18.0,48" for t in teh]
    (tmp_path / "buner-2025").mkdir()
    (tmp_path / "buner-2025" / "imerg_daily.csv").write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(settings, "REPLAY_CACHE_ROOT", str(tmp_path))
    ing = ImergIngestor(FakeGateway(), clock=FixedClock(date(2025, 8, 14)), alerts=LogAlertService())
    with use_scenario(sc.id):
        res = ing.ingest(db, start=date(2025, 8, 13), end=date(2025, 8, 14))
    assert res.rows_fresh == len(teh)  # on simulated 14 Aug only data up to 13 Aug exists (1-day replay lag)
    got = db.scalars(select(CO).where(CO.scenario_id == sc.id)).all()
    assert {r.obs_date for r in got} == {date(2025, 8, 13)}
    assert imerg_rows(db) == 0
    assert db.scalar(select(func.count()).select_from(DataSourceStatus)) == 0


def test_audit_payload_records_dataset(seeded):
    db = seeded
    ImergIngestor(FakeGateway(), clock=FixedClock(date(2022, 8, 27)), alerts=LogAlertService()).ingest(
        db, start=date(2022, 8, 25), end=date(2022, 8, 26), district_ids=ids(db, "Dadu"))
    a = db.scalar(select(AuditLog).where(AuditLog.event_type == "DATA_INGESTED"))
    assert a.payload["dataset"] == settings.IMERG_COLLECTION and a.payload["source"] == "GPM_IMERG"


@pytest.mark.parametrize("missing", [("PK-SD-DAD-JOH",)])
def test_centroid_fallback_flag_is_stored(seeded, missing):
    db = seeded
    ImergIngestor(FakeGateway(missing=missing), clock=FixedClock(date(2022, 8, 27)),
                  alerts=LogAlertService()).ingest(db, start=date(2022, 8, 26), end=date(2022, 8, 26),
                                                   district_ids=ids(db, "Dadu"))
    johi = db.scalar(select(Tehsil).where(Tehsil.pcode == missing[0]))
    row = db.scalar(select(CO).where(CO.tehsil_id == johi.id))
    assert "centroid_fallback" in row.quality_flags

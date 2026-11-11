from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.core.clock import FixedClock
from app.core.errors import DomainError
from app.integrations.forecast_rain import OpenMeteoClient
from app.models.core import AuditLog
from app.models.forecasting import FloodForecast, RiskIndex
from app.models.geography import District, Tehsil
from app.models.ingestion import ClimateObservation as CO
from app.models.ingestion import DataSourceStatus
from app.services.forecasting.modes import apply_flood_mode_seed, set_district_mode
from app.services.forecasting.rain_ingest import ForecastRainIngestService
from app.services.forecasting.service import ForecastService
from app.services.ingestion.observations import upsert_observations
from app.services.stubs import LogAlertService, NoopTriggerEngine, set_trigger_engine

RUN = date(2022, 8, 20)


def district(db, name):
    return db.scalar(select(District).where(District.name == name))


def put_obs(db, d, rain=10.0, soil=0.6, days=40, stale_on=None):
    rows = []
    for i in range(days):
        day = RUN - timedelta(days=i)
        st = stale_on is not None and day >= stale_on
        rows.append({"district_id": d.id, "obs_date": day, "source": "NASA_POWER", "rainfall_mm": rain,
                     "soil_wetness": soil, "soil_wetness_top": soil, "temp_c": 30.0, "is_stale": st,
                     "stale_since": stale_on - timedelta(days=1) if st else None})
    upsert_observations(db, rows)


def put_forecast(db, d, rain=5.0, burst=2.0):
    rows = []
    for t in db.scalars(select(Tehsil).where(Tehsil.district_id == d.id)):
        for lead in range(1, 8):
            rows.append({"district_id": d.id, "tehsil_id": t.id, "obs_date": RUN + timedelta(days=lead),
                         "lead_day": lead, "source": "FORECAST_RAIN", "rainfall_mm": rain,
                         "rainfall_max_3h_mm": burst, "provenance": "LIVE_FORECAST", "forecast_model": "test"})
    upsert_observations(db, rows)


@pytest.fixture
def trig():
    t = NoopTriggerEngine()
    set_trigger_engine(t)
    yield t
    set_trigger_engine(NoopTriggerEngine())


def run(db, ids):
    return ForecastService().run(db, RUN, ids)


def test_scores_seven_leads_per_tehsil_and_district_in_their_modes(seeded, trig):
    db = seeded
    apply_flood_mode_seed(db)
    dadu, buner = district(db, "Dadu"), district(db, "Buner")
    for d in (dadu, buner):
        put_obs(db, d)
        put_forecast(db, d)
    res = run(db, [dadu.id, buner.id])
    assert res["districts_scored"] == 2 and res["tehsils_scored"] == 8
    assert db.scalar(select(func.count()).select_from(FloodForecast).where(FloodForecast.district_id == dadu.id)) \
        == (4 + 1) * 7
    ri_d = db.scalar(select(RiskIndex).where(RiskIndex.district_id == dadu.id, RiskIndex.tehsil_id.is_(None)))
    ri_b = db.scalar(select(RiskIndex).where(RiskIndex.district_id == buner.id, RiskIndex.tehsil_id.is_(None)))
    assert ri_d.mode == "SLOW_ONSET" and ri_b.mode == "FLASH"
    assert ri_d.is_fallback and ri_d.model_version == "rules-fallback-1"  # no trained artifact yet
    assert 0 <= ri_d.score <= 100 and ri_d.tier in ("NONE", "WATCH", "WARNING", "ACTION")
    assert ri_d.explanation_en.startswith("Dadu: flood risk") and ri_d.top_factors
    ff = db.scalar(select(FloodForecast).where(FloodForecast.district_id == dadu.id,
                                               FloodForecast.tehsil_id.is_not(None), FloodForecast.lead_day == 3))
    assert "rain_30d_mm" in ff.features and ff.features["window_sources"]
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.event_type == "RISK_SCORED")) == 2
    assert len(trig.calls) == 2


def test_rerun_is_idempotent(seeded, trig):
    db = seeded
    dadu = district(db, "Dadu")
    put_obs(db, dadu)
    put_forecast(db, dadu)
    run(db, [dadu.id])
    first = db.scalar(select(RiskIndex.score).where(RiskIndex.district_id == dadu.id, RiskIndex.tehsil_id.is_(None)))
    run(db, [dadu.id])
    assert db.scalar(select(func.count()).select_from(RiskIndex).where(RiskIndex.district_id == dadu.id)) == 5
    assert db.scalar(select(RiskIndex.score).where(RiskIndex.district_id == dadu.id,
                                                   RiskIndex.tehsil_id.is_(None))) == first


def test_inactive_district_skipped(seeded, trig):
    db = seeded
    res = ForecastService().run(db, RUN)
    pesh = district(db, "Peshawar")
    assert db.scalar(select(func.count()).select_from(RiskIndex).where(RiskIndex.district_id == pesh.id)) == 0
    assert res["districts_scored"] > 0


def test_heavy_rain_on_wet_soil_scores_higher(seeded, trig):
    db = seeded
    dadu, larkana = district(db, "Dadu"), district(db, "Larkana")
    put_obs(db, dadu, rain=25.0, soil=0.95)
    put_obs(db, larkana, rain=1.0, soil=0.2)
    for d in (dadu, larkana):
        put_forecast(db, d, rain=30.0)
    run(db, [dadu.id, larkana.id])
    s = dict(db.execute(select(RiskIndex.district_id, RiskIndex.score).where(RiskIndex.tehsil_id.is_(None))).all())
    assert s[dadu.id] > s[larkana.id]


def test_stale_inputs_and_missing_forecast_are_flagged(seeded, trig):
    db = seeded
    dadu = district(db, "Dadu")
    put_obs(db, dadu, stale_on=RUN - timedelta(days=2))  # last 3 days copied forward
    res = run(db, [dadu.id])  # and no forecast rows at all
    ri = db.scalar(select(RiskIndex).where(RiskIndex.district_id == dadu.id, RiskIndex.tehsil_id.is_(None)))
    assert ri.is_stale_input and ri.stale_since == RUN - timedelta(days=3) and ri.is_fallback
    assert "Some data is old" in ri.explanation_en
    audit = db.scalar(select(AuditLog).where(AuditLog.event_type == "RISK_SCORED"))
    assert audit.payload["is_stale_input"] and audit.payload["is_fallback"]
    assert res["stale_districts"] == ["PK-SD-DAD"]


def test_trigger_failure_is_isolated(seeded):
    class Boom:
        def evaluate(self, db, did, ri):
            raise RuntimeError("trigger down")

    set_trigger_engine(Boom())
    try:
        db = seeded
        dadu = district(db, "Dadu")
        res = run(db, [dadu.id])
        assert res["trigger_errors"] and "trigger down" in res["trigger_errors"][0]["error"]
        assert db.scalar(select(func.count()).select_from(RiskIndex).where(RiskIndex.district_id == dadu.id)) == 5
    finally:
        set_trigger_engine(NoopTriggerEngine())


def test_mode_change_is_audited_and_used_next_run(seeded, trig):
    db = seeded
    apply_flood_mode_seed(db)
    nowshera = district(db, "Nowshera")
    with pytest.raises(DomainError) as e:
        set_district_mode(db, nowshera.id, "SLOW_ONSET", "short", "officer")
    assert e.value.code == "REASON_REQUIRED"
    out = set_district_mode(db, nowshera.id, "SLOW_ONSET", "Riverine floods from the Kabul river", "officer")
    assert out["changed"] and out["previous_mode"] == "FLASH"
    assert db.scalar(select(AuditLog).where(AuditLog.event_type == "DISTRICT_FLOOD_MODE_CHANGED")).payload["to"] \
        == "SLOW_ONSET"
    assert not set_district_mode(db, nowshera.id, "SLOW_ONSET", "same mode again ok", "officer")["changed"]
    run(db, [nowshera.id])
    assert db.scalar(select(RiskIndex.mode).where(RiskIndex.district_id == nowshera.id).limit(1)) == "SLOW_ONSET"


def _om_handler(state):
    def handler(request):
        if state.get("down"):
            return httpx.Response(503)
        n = len(request.url.params["latitude"].split(","))
        start = RUN - timedelta(days=3)
        times = [f"{(start + timedelta(days=h // 24)).isoformat()}T{h % 24:02d}:00" for h in range(11 * 24)]
        body = {"hourly": {"time": times, "precipitation": [1.0] * len(times)}}
        return httpx.Response(200, json=[body] * n if n > 1 else body)
    return handler


def test_forecast_rain_ingest_live_then_outage_shifts_previous(seeded):
    db = seeded
    state = {}
    client = OpenMeteoClient(http=httpx.Client(transport=httpx.MockTransport(_om_handler(state))),
                             sleep=lambda _: None)
    dadu = district(db, "Dadu")
    svc = ForecastRainIngestService(live=client, clock=FixedClock(RUN), alerts=LogAlertService())
    out = svc.ingest(db, RUN, [dadu.id])
    assert out["is_healthy"] and out["provenance"] == ["LIVE_FORECAST"]
    fc = db.scalars(select(CO).where(CO.source == "FORECAST_RAIN", CO.lead_day.between(1, 7))).all()
    assert len(fc) == 4 * 7 and all(r.rainfall_mm == 24.0 and r.rainfall_max_3h_mm == 3.0 for r in fc)

    state["down"] = True
    alerts = LogAlertService()
    nxt = RUN + timedelta(days=1)
    out2 = ForecastRainIngestService(live=client, clock=FixedClock(nxt), alerts=alerts).ingest(db, nxt, [dadu.id])
    assert not out2["is_healthy"] and out2["stale_rows"] == 4 * 7
    shifted = db.scalars(select(CO).where(CO.source == "FORECAST_RAIN", CO.obs_date == nxt + timedelta(days=1),
                                          CO.lead_day == 1)).all()
    assert shifted and all(r.is_stale and r.stale_since == RUN for r in shifted)
    assert db.scalar(select(DataSourceStatus.is_healthy).where(DataSourceStatus.source == "FORECAST_RAIN")) is False
    assert len(alerts.sent) == 1

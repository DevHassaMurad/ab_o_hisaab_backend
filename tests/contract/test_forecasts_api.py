from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.main import create_app
from app.models.geography import District
from app.services.forecasting.modes import apply_flood_mode_seed
from app.services.forecasting.service import ForecastService
from tests.integration.test_forecasting import RUN, put_forecast, put_obs


@pytest.fixture
def client(seeded):
    apply_flood_mode_seed(seeded)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: seeded
    return TestClient(app)


def login(c, email):
    r = c.post("/api/v1/auth/token", data={"username": email, "password": "demo"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def scored(seeded):
    dadu = seeded.scalar(select(District).where(District.name == "Dadu"))
    put_obs(seeded, dadu)
    put_forecast(seeded, dadu)
    ForecastService().run(seeded, RUN, [dadu.id])
    return dadu


def test_district_detail_has_series_flags_and_explanation(client, scored):
    r = client.get(f"/api/v1/forecasts/districts/{scored.id}", headers=login(client, "officer@pdma-sindh.demo"))
    b = r.json()
    assert r.status_code == 200 and len(b["series"]) == 7 and b["mode"] == "SLOW_ONSET"
    assert b["is_fallback"] is True and b["forecast_is_proxy"] is False
    assert b["thresholds"] == {"WATCH": 40, "WARNING": 60, "ACTION": 80}
    assert b["explanation_en"].startswith("Dadu: flood risk")


def test_dc_scope_and_donor_refused(client, scored, seeded):
    buner = seeded.scalar(select(District).where(District.name == "Buner"))
    assert client.get(f"/api/v1/forecasts/districts/{scored.id}",
                      headers=login(client, "dc.dadu@sindh.demo")).status_code == 200
    r = client.get(f"/api/v1/forecasts/districts/{buner.id}", headers=login(client, "dc.dadu@sindh.demo"))
    assert r.status_code == 403
    assert client.get(f"/api/v1/forecasts/districts/{scored.id}",
                      headers=login(client, "donor@fund.demo")).status_code == 403


def test_risk_map_history_and_trace(client, scored):
    h = login(client, "officer@pdma-sindh.demo")
    m = client.get("/api/v1/forecasts/risk-map", params={"level": "tehsil"}, headers=h).json()
    assert m["run_date"] == RUN.isoformat() and len(m["features"]) == 4
    hist = client.get(f"/api/v1/forecasts/districts/{scored.id}/history", headers=h).json()
    assert hist["total"] == 1
    rid = client.get(f"/api/v1/forecasts/districts/{scored.id}", headers=h).json()["risk_index_id"]
    trace = client.get(f"/api/v1/forecasts/risk-index/{rid}", headers=h).json()
    assert trace["forecasts"][0]["features"]["window_sources"] and trace["aggregation"]["rule"] == "max_pop_share"
    assert trace["audit_entry_seq"] is not None


def test_no_forecast_404(client):
    r = client.get("/api/v1/forecasts/risk-map", headers=login(client, "officer@pdma-sindh.demo"))
    assert r.status_code == 404 and r.json()["code"] == "NO_FORECAST_FOR_DATE"


def test_runs_admin_only_and_no_future(client):
    assert client.post("/api/v1/forecasts/runs", json={},
                       headers=login(client, "officer@pdma-sindh.demo")).status_code == 403
    r = client.post("/api/v1/forecasts/runs", json={"run_date": "2999-01-01"}, headers=login(client, "admin@aab.demo"))
    assert r.status_code == 422 and r.json()["code"] == "INVALID_RUN_DATE"


def test_modes_list_and_model_info(client):
    h = login(client, "officer@pdma-sindh.demo")
    items = {m["mode"]: m for m in client.get("/api/v1/forecasting/modes", headers=h).json()["items"]}
    assert set(items) == {"SLOW_ONSET", "FLASH"}
    assert items["FLASH"]["lead_weights"]["6"] == 0.6 and items["FLASH"]["model_loaded"] is False
    assert items["SLOW_ONSET"]["validated_provinces"] == ["SINDH"]
    assert client.get("/api/v1/forecasting/modes/FLASH/calibration", headers=h).json()["code"] == \
        "CALIBRATION_NOT_AVAILABLE"
    info = client.get("/api/v1/forecasts/model-info", headers=h).json()
    assert {m["loaded"] for m in info["modes"]} == {False}


def test_mode_change_api(client, seeded):
    nowshera = seeded.scalar(select(District).where(District.name == "Nowshera"))
    url = f"/api/v1/districts/{nowshera.id}/flood-mode"
    body = {"flood_mode": "SLOW_ONSET", "reason": "Riverine flooding from the Kabul river"}
    assert client.patch(url, json=body, headers=login(client, "dc.buner@kp.demo")).status_code == 403
    h = login(client, "officer@pdma-sindh.demo")
    assert client.patch(url, json={**body, "reason": "x"}, headers=h).json()["code"] == "REASON_REQUIRED"
    assert client.patch(url, json={**body, "flood_mode": "GLOF"}, headers=h).json()["code"] == "INVALID_MODE"
    r = client.patch(url, json=body, headers={**h, "X-Scenario-Id": "00000000-0000-0000-0000-000000000001"})
    assert r.status_code == 409
    r = client.patch(url, json=body, headers=h)
    assert r.status_code == 200 and r.json()["previous_mode"] == "FLASH" and r.json()["audit_seq"]
    assert date.fromisoformat(r.json()["effective_from"])

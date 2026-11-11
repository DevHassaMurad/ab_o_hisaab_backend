import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.main import create_app, register_all
from app.services.triggers.fund_settings import seed_live_funds


@pytest.fixture
def client(seeded):
    register_all()
    seed_live_funds(seeded)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: seeded
    c = TestClient(app)
    c.h = lambda email: {"Authorization": "Bearer " + c.post(
        "/api/v1/auth/token", data={"username": email, "password": "demo"}).json()["access_token"]}
    return c


def test_drive_and_watch_roles(client):
    off, dc, donor = (client.h(e) for e in ("officer@pdma-sindh.demo", "dc.dadu@sindh.demo", "donor@fund.demo"))
    assert client.post("/api/v1/replay/scenarios/control-2021/start", json={}, headers=dc).status_code == 403
    assert client.post("/api/v1/replay/scenarios/control-2021/start", json={}, headers=donor).status_code == 403
    r = client.post("/api/v1/replay/scenarios/control-2021/start", json={"approval_mode": "AUTO_SIM"}, headers=off)
    assert r.status_code == 201 and r.json()["simulated"] is True
    assert client.post("/api/v1/replay/scenarios/control-2021/start", json={}, headers=off).status_code == 200
    r = client.post("/api/v1/replay/scenarios/control-2021/step", json={"days": 1}, headers=off)
    assert r.status_code == 200 and r.json()["stepped"] == ["2021-08-01"]
    st = client.get("/api/v1/replay/scenarios/control-2021/state", headers=dc).json()
    assert [d["name"] for d in st["districts"]] == ["Dadu"]  # DC sees only own district
    assert client.get("/api/v1/replay/scenarios/control-2021/timeline", headers=donor).status_code == 200
    assert client.get("/api/v1/replay/scenarios/control-2021/backtest", headers=dc).status_code == 403
    items = client.get("/api/v1/replay/scenarios", headers=donor).json()["items"]
    assert {i["key"] for i in items} == {"sindh-2022", "buner-2025", "control-2021"}


def test_errors(client):
    off = client.h("officer@pdma-sindh.demo")
    assert client.post("/api/v1/replay/scenarios/nope/start", json={}, headers=off).json()["code"] == \
        "SCENARIO_NOT_FOUND"
    assert client.post("/api/v1/replay/scenarios/buner-2025/step", json={"days": 1},
                       headers=off).json()["code"] == "REPLAY_NOT_STARTED"
    assert client.post("/api/v1/replay/scenarios/buner-2025/start", json={"approval_mode": "MAGIC"},
                       headers=off).json()["code"] == "INVALID_APPROVAL_MODE"
    assert client.post("/api/v1/replay/scenarios/buner-2025/play", json={"interval_seconds": 1},
                       headers=off).json()["code"] == "INVALID_INTERVAL"

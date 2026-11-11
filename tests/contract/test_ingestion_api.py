from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.clock import FixedClock
from app.core.db import get_db
from app.main import create_app
from app.models.geography import District
from app.services.ingestion.nasa_power_ingestor import NasaPowerIngestor
from app.services.ingestion.registry import register_ingestor
from app.services.stubs import LogAlertService
from tests.conftest import PowerStub


@pytest.fixture
def client(seeded):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: seeded
    register_ingestor(NasaPowerIngestor(clock=FixedClock(date(2022, 8, 30)), alerts=LogAlertService(),
                                        live_client=PowerStub().client()))
    yield TestClient(app)
    register_ingestor(NasaPowerIngestor())


def login(c, email):
    r = c.post("/api/v1/auth/token", data={"username": email, "password": "demo"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_bad_password_is_generic(client):
    r = client.post("/api/v1/auth/token", data={"username": "admin@aab.demo", "password": "nope"})
    assert r.status_code == 401 and r.json()["detail"] == "Email or password is incorrect."


def test_run_is_admin_only(client):
    body = {"source": "NASA_POWER", "start": "2022-08-20", "end": "2022-08-26"}
    assert client.post("/api/v1/ingestion/run", json=body).status_code == 401
    r = client.post("/api/v1/ingestion/run", json=body, headers=login(client, "officer@pdma-sindh.demo"))
    assert r.status_code == 403
    r = client.post("/api/v1/ingestion/run", json=body, headers=login(client, "admin@aab.demo"))
    assert r.status_code == 200 and r.json()["is_healthy"] and r.json()["rows_fresh"] > 0


def test_run_validation_errors(client):
    h = login(client, "admin@aab.demo")
    r = client.post("/api/v1/ingestion/run", json={"source": "NASA_POWER", "start": "2022-07-01",
                                                   "end": "2022-08-26"}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "RANGE_TOO_LARGE"
    r = client.post("/api/v1/ingestion/run", json={"source": "NOPE"}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "UNKNOWN_SOURCE"
    r = client.post("/api/v1/ingestion/run", json={"source": "NASA_POWER", "district_pcodes": ["XX"]}, headers=h)
    assert r.status_code == 404


def test_run_conflicts_when_lock_held(client, engine):
    with engine.connect() as other:
        other.begin()
        other.execute(text("SELECT pg_advisory_xact_lock(hashtext('ingest:NASA_POWER'))"))
        r = client.post("/api/v1/ingestion/run", json={"source": "NASA_POWER", "start": "2022-08-20",
                                                       "end": "2022-08-26"}, headers=login(client, "admin@aab.demo"))
        other.rollback()
    assert r.status_code == 409 and r.json()["code"] == "INGEST_ALREADY_RUNNING"


def test_observations_access_and_page_shape(client, seeded):
    h = login(client, "admin@aab.demo")
    client.post("/api/v1/ingestion/run", json={"source": "NASA_POWER", "start": "2022-08-20", "end": "2022-08-26"},
                headers=h)
    dadu = seeded.scalar(select(District).where(District.name == "Dadu"))
    buner = seeded.scalar(select(District).where(District.name == "Buner"))
    q = {"district_id": str(dadu.id), "from": "2022-08-01", "to": "2022-08-31", "page_size": 5}
    r = client.get("/api/v1/ingestion/observations", params=q, headers=login(client, "officer@pdma-sindh.demo"))
    body = r.json()
    assert r.status_code == 200 and body["total"] == 7 and len(body["items"]) == 5 and body["page"] == 1
    assert client.get("/api/v1/ingestion/observations", params=q,
                      headers=login(client, "dc.dadu@sindh.demo")).status_code == 200
    q["district_id"] = str(buner.id)
    r = client.get("/api/v1/ingestion/observations", params=q, headers=login(client, "dc.dadu@sindh.demo"))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_DISTRICT"
    assert client.get("/api/v1/ingestion/observations", params={},
                      headers=h).json()["code"] == "MISSING_FILTER"


def test_donor_cannot_read_observations(client, seeded):
    dadu = seeded.scalar(select(District).where(District.name == "Dadu"))
    r = client.get("/api/v1/ingestion/observations", params={"district_id": str(dadu.id)},
                   headers=login(client, "donor@fund.demo"))
    assert r.status_code == 403


def test_data_source_health(client):
    h = login(client, "admin@aab.demo")
    client.post("/api/v1/ingestion/run", json={"source": "NASA_POWER", "start": "2022-08-20", "end": "2022-08-26"},
                headers=h)
    r = client.get("/api/v1/health/data-sources", headers=h)
    assert r.status_code == 200 and r.json()[0]["source"] == "NASA_POWER" and r.json()[0]["is_healthy"]

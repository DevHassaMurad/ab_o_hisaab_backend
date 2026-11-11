"""Test harness. DB tests use TEST_DATABASE_URL (default: the aab_test database from docker-compose), migrate it
once, and run each test inside an outer transaction that is rolled back (service commits become savepoints)."""
from __future__ import annotations

import os
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://aab:aab@localhost:5432/aab_test")


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test database not reachable ({exc}); run `docker compose up -d db`")
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; CREATE EXTENSION postgis"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield session
    session.close()
    trans.rollback()
    conn.close()


@pytest.fixture
def seeded(db):
    from app.services.forecasting.modes import apply_flood_mode_seed
    from app.services.replay.scenarios import ensure_scenarios
    from scripts.seed import seed_districts, seed_users
    seed_districts(db)
    apply_flood_mode_seed(db)
    seed_users(db)
    ensure_scenarios(db)
    db.flush()
    return db


def power_doc(start: date, end: date, rain: float = 5.0, units: str = "mm/day") -> dict:
    """A synthetic NASA POWER response for any date range."""
    days, d = {}, start
    while d <= end:
        days[d.strftime("%Y%m%d")] = d
        d += timedelta(days=1)
    return {"header": {"fill_value": -999.0},
            "parameters": {"PRECTOTCORR": {"units": units}, "T2M": {"units": "C"}, "GWETROOT": {"units": "1"},
                           "GWETTOP": {"units": "1"}},
            "properties": {"parameter": {"PRECTOTCORR": {k: rain for k in days}, "T2M": {k: 30.0 for k in days},
                                         "GWETROOT": {k: 0.6 for k in days}, "GWETTOP": {k: 0.7 for k in days}}}}


class PowerStub:
    """httpx transport serving synthetic POWER data; `down=True` simulates an outage."""

    def __init__(self, rain: float = 5.0) -> None:
        self.down = False
        self.rain = rain
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.down:
            return httpx.Response(503)
        p = request.url.params
        start = date(int(p["start"][:4]), int(p["start"][4:6]), int(p["start"][6:]))
        end = date(int(p["end"][:4]), int(p["end"][4:6]), int(p["end"][6:]))
        return httpx.Response(200, json=power_doc(start, end, self.rain))

    def client(self):
        from app.core.config import Settings
        from app.integrations.nasa_power import HttpNasaPowerClient

        s = Settings(POWER_MAX_RETRIES=1, POWER_RETRY_BASE_S=0)
        return HttpNasaPowerClient(httpx.Client(transport=httpx.MockTransport(self.handler)), s, sleep=lambda _: None)

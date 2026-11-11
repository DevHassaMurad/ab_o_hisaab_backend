"""Quick backtest view: run a replay scenario day by day through FR1 ingest -> FR3 forecast rain -> FR3 scoring and
print each district's daily score. Everything happens inside one transaction that is rolled back (nothing kept).
FR5 will own the real replay engine; this is a development check of FR3/FR4 on real cached data.

    python -m scripts.replay_scores --scenario sindh-2022 [--districts Dadu Larkana]
"""
from __future__ import annotations

import argparse
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import FixedClock, clock_scope
from app.core.db import engine
from app.core.scenario import use_scenario
from app.models.core import ReplayScenario
from app.models.forecasting import RiskIndex
from app.models.geography import District
from app.services.forecasting.rain_ingest import ForecastRainIngestService
from app.services.forecasting.service import ForecastService
from app.services.ingestion.nasa_power_ingestor import NasaPowerIngestor
from app.services.stubs import LogAlertService


def run(key: str, names: list[str] | None) -> dict[str, list[tuple[str, int, str]]]:
    conn = engine.connect()
    trans = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == key))
        ids = sc.districts
        if names:
            ids = list(db.scalars(select(District.id).where(District.id.in_(ids), District.name.in_(names))))
        power, rain, score = NasaPowerIngestor(alerts=LogAlertService()), ForecastRainIngestService(
            alerts=LogAlertService()), ForecastService()
        out: dict[str, list[tuple[str, int, str]]] = {}
        with use_scenario(sc.id):
            with clock_scope(FixedClock(sc.start_date)):
                power.ingest(db, start=sc.start_date - timedelta(days=30), end=sc.start_date - timedelta(days=1),
                             district_ids=ids)
            day = sc.start_date
            while day <= sc.end_date:
                with clock_scope(FixedClock(day)):
                    power.ingest(db, district_ids=ids)
                    rain.ingest(db, day, ids)
                    score.run(db, day, ids)
                for ri, name in db.execute(select(RiskIndex, District.name).join(District)
                                           .where(RiskIndex.scenario_id == sc.id, RiskIndex.run_date == day,
                                                  RiskIndex.tehsil_id.is_(None))):
                    out.setdefault(name, []).append((day.isoformat(), ri.score, ri.tier))
                day += timedelta(days=1)
        return out
    finally:
        db.close()
        trans.rollback()
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--districts", nargs="*")
    a = ap.parse_args()
    for name, series in run(a.scenario, a.districts).items():
        peak = max(series, key=lambda x: x[1])
        order = ["NONE", "WATCH", "WARNING", "ACTION"]
        first = {t: next((d for d, _, tt in series if order.index(tt) >= order.index(t)), None)
                 for t in ("WATCH", "WARNING", "ACTION")}
        print(f"{name}: peak {peak[1]} on {peak[0]} | first Watch {first['WATCH']}, Warning {first['WARNING']}, "
              f"Action {first['ACTION']}")
        print("   " + " ".join(f"{d[5:]}:{s}" for d, s, _ in series))

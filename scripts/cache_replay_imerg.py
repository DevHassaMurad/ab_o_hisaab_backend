"""FR2: build data/replay/<key>/imerg_daily.csv from Earth Engine (needs credentials once; then commit the CSV).

    python -m scripts.cache_replay_imerg --scenario sindh-2022
"""
from __future__ import annotations

import argparse
import csv
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.integrations.earth_engine import EEGateway
from app.models.core import ReplayScenario
from app.models.geography import Tehsil
from app.services.ingestion.ee_boundaries import tehsil_features

ROOT = Path(__file__).resolve().parents[1]


def build(key: str) -> Path:
    gw = EEGateway()
    with SessionLocal() as db:
        sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == key))
        if sc is None:
            raise SystemExit(f"scenario {key} not seeded (run python -m scripts.seed)")
        tehsils = list(db.scalars(select(Tehsil).where(Tehsil.district_id.in_(sc.districts)).order_by(Tehsil.pcode)))
        feats = tehsil_features(db, tehsils)
        start = sc.start_date - timedelta(days=settings.REPLAY_SPINUP_DAYS)
        out = ROOT / settings.REPLAY_CACHE_ROOT / key / "imerg_daily.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "tehsil_pcode", "rainfall_mm", "rainfall_max_3h_mm", "n_images"])
            d = start
            while d <= sc.end_date:
                n = gw.image_count(d)
                for r in gw.reduce_imerg(d, feats):
                    w.writerow([d.isoformat(), r.pcode, r.rainfall_mm, r.rainfall_max_3h_mm, n])
                print(f"  {key} {d}: {n} images")
                d += timedelta(days=1)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    print(build(ap.parse_args().scenario))

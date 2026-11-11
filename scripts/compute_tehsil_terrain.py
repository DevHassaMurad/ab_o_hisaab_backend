"""FR2: SRTM elevation + slope per tehsil -> DB and data/seeds/tehsil_terrain.csv (run once, needs EE).

    python -m scripts.compute_tehsil_terrain
"""
from pathlib import Path

from sqlalchemy import select

from app.core.db import SessionLocal
from app.integrations.earth_engine import EEGateway
from app.models.geography import District, Tehsil
from app.services.ingestion.ee_boundaries import tehsil_features
from app.services.ingestion.terrain import load_terrain_seed, write_terrain_csv

OUT = Path(__file__).resolve().parents[1] / "data/seeds/tehsil_terrain.csv"

if __name__ == "__main__":
    with SessionLocal() as db:
        tehsils = list(db.scalars(select(Tehsil).join(District).where(District.is_active.is_(True))))
        missing = [t.pcode for t in tehsils if t.geom is None]
        if missing:
            raise SystemExit(f"{len(missing)} tehsils have no boundary yet; load COD-AB boundaries first")
        rows = EEGateway().compute_terrain(tehsil_features(db, tehsils))
        write_terrain_csv(rows, OUT)
        print("rows:", load_terrain_seed(db, OUT))
        db.commit()

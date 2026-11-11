"""SRTM elevation and slope per tehsil (FR2 US3). Computed once with Earth Engine into a seed CSV; seeding reads
the CSV so no credentials are needed elsewhere."""
from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.geography import Tehsil

COLUMNS = ("tehsil_pcode", "elevation_mean_m", "slope_mean_deg", "source", "computed_at")


def write_terrain_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["tehsil_pcode"]):
            w.writerow({**r, "source": "USGS/SRTMGL1_003", "computed_at": now})


def load_terrain_seed(db: Session, path: Path) -> int:
    if not path.exists():
        return 0
    tehsils = {t.pcode: t for t in db.scalars(select(Tehsil)).all()}
    n = 0
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t = tehsils.get(r["tehsil_pcode"])
            if t is None:
                continue
            t.elevation_mean_m = float(r["elevation_mean_m"]) if r["elevation_mean_m"] else None
            t.slope_mean_deg = float(r["slope_mean_deg"]) if r["slope_mean_deg"] else None
            n += 1
    db.flush()
    return n

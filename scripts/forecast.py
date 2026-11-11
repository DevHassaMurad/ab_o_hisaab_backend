"""Forecasting data jobs.

  python scripts/forecast.py climatology   # one-off: 2000-2014 NASA POWER baseline the model needs (resumable)
  python scripts/forecast.py refresh       # observations -> model scores -> weather forecast -> events
  python scripts/forecast.py imerg         # NASA GPM IMERG rainfall (needs EARTHDATA_TOKEN)
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from config.database import SessionLocal  # noqa: E402
from controller import forecasting as f  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if __name__ == "__main__":
    job = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    with SessionLocal() as db:
        if job == "climatology":
            runs = [f.build_climatology(db, "manual")]
        elif job == "imerg":
            runs = [f.refresh_imerg(db, "manual")]
        else:
            runs = f.refresh_all(db, "manual")
        for r in runs:
            print(f"{r.kind:12} {r.status:8} records={r.records} {r.error or ''}")

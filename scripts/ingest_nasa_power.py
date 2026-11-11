"""FR1 backfill and replay-cache builder.

Backfill live rows (for FR3 training), one request per district per calendar year:
    python -m scripts.ingest_nasa_power --start 2015-01-01 --end 2025-12-31 [--districts PK-SD-DAD PK-KP-BNR]

Build the replay cache for a scenario (raw POWER JSON; needs internet once, then commit the files):
    python -m scripts.ingest_nasa_power --scenario sindh-2022 --to-cache
    python -m scripts.ingest_nasa_power --all-scenarios --to-cache
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from app.core.config import settings
from app.integrations.nasa_power import HttpNasaPowerClient

ROOT = Path(__file__).resolve().parents[1]


def _seed_index() -> dict[tuple[str, str], dict]:
    from scripts.seed import load_district_seed
    return {(r["name"].lower(), r["province"]): r for r in load_district_seed()}


def write_replay_cache(key: str, client: HttpNasaPowerClient | None = None, root: Path | None = None) -> list[Path]:
    """Writes data/replay/<key>/nasa_power/<pcode>.json for every scenario district (spin-up + window).
    Reads districts from the seed files, so it needs no database."""
    from scripts.seed import load_scenario_definitions

    defs = {s["key"]: s for s in load_scenario_definitions()}
    if key not in defs:
        raise SystemExit(f"unknown scenario {key}; known: {', '.join(defs)}")
    s = defs[key]
    start = date.fromisoformat(str(s["start_date"])) - timedelta(days=settings.REPLAY_SPINUP_DAYS)
    end = date.fromisoformat(str(s["end_date"]))
    client = client or HttpNasaPowerClient()
    out_dir = Path(root or ROOT / settings.REPLAY_CACHE_ROOT) / key / "nasa_power"
    out_dir.mkdir(parents=True, exist_ok=True)
    seeds = _seed_index()
    written = []
    for x in s["districts"]:
        row = seeds[(x["name"].lower(), x["province"])]
        doc = client.fetch_raw(row["lat"], row["lon"], start, end)
        doc.setdefault("aab_cache", {}).update({"district_pcode": row["pcode"], "district": row["name"],
                                                "start": str(start), "end": str(end), "scenario": key})
        p = out_dir / f"{row['pcode']}.json"
        p.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
        written.append(p)
        print(f"  {key}: {row['name']} -> {p.relative_to(ROOT) if p.is_relative_to(ROOT) else p}")
    return written


def backfill(start: date, end: date, pcodes: list[str] | None) -> None:
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models.geography import District
    from app.services.ingestion.nasa_power_ingestor import NasaPowerIngestor

    with SessionLocal() as db:
        q = select(District.id)
        q = q.where(District.pcode.in_(pcodes)) if pcodes else q.where(District.is_active.is_(True))
        ids = list(db.scalars(q).all())
        ing = NasaPowerIngestor()
        y = start.year
        while y <= end.year:
            a, b = max(start, date(y, 1, 1)), min(end, date(y, 12, 31))
            res = ing.ingest(db, start=a, end=b, district_ids=ids)
            print(f"{a}..{b}: fresh={res.rows_fresh} stale={res.rows_stale} errors={len(res.errors)}")
            y += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--districts", nargs="*")
    ap.add_argument("--scenario")
    ap.add_argument("--all-scenarios", action="store_true")
    ap.add_argument("--to-cache", action="store_true")
    a = ap.parse_args()
    if a.to_cache:
        from scripts.seed import load_scenario_definitions
        keys = [s["key"] for s in load_scenario_definitions()] if a.all_scenarios else [a.scenario]
        for k in keys:
            write_replay_cache(k)
    elif a.start and a.end:
        backfill(a.start, a.end, a.districts)
    else:
        ap.error("give --start/--end, or --scenario/--all-scenarios with --to-cache")


if __name__ == "__main__":
    main()

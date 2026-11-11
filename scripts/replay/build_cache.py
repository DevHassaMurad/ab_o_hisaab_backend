"""FR5: build (or refresh) a scenario's replay cache and write its manifest.

    python -m scripts.replay.build_cache --all              # NASA POWER caches + manifests (needs internet once)
    python -m scripts.replay.build_cache --all --manifest-only

IMERG (FR2) and archived forecast rain (FR3) caches need Earth Engine credentials and the archive APIs; until then
replays use the labelled NASA POWER perfect-forecast proxy and the manifest lists only the files that exist.
"""
from __future__ import annotations

import argparse

from app.services.replay.cache import write_manifest
from app.services.replay.scenarios import load_definitions
from scripts.ingest_nasa_power import write_replay_cache


def build(key: str, manifest_only: bool = False) -> dict:
    if not manifest_only:
        write_replay_cache(key)  # FR1
    defs = load_definitions()[key]
    return write_manifest(key, {"window": {"start": str(defs["start_date"]), "end": str(defs["end_date"]),
                                           "spinup_days": defs["spinup_days"]},
                                "builders": {"fr1": "nasa_power_raw_json"},
                                "notes": "IMERG and archived forecast caches not built yet; forecast rain in replay "
                                         "is a PERFECT_FORECAST_PROXY from observed NASA POWER rain."})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--manifest-only", action="store_true")
    a = ap.parse_args()
    for k in (list(load_definitions()) if a.all else [a.scenario]):
        m = build(k, a.manifest_only)
        print(f"{k}: {len(m['files'])} files in manifest")

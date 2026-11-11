"""FR5 replay cache manifest: SHA-256 and row counts of every committed cache file, verified before a start."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import Unprocessable

MANIFEST = "manifest.json"


def _root(key: str, root: Path | None = None) -> Path:
    return Path(root or settings.REPLAY_CACHE_ROOT) / key


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rows(p: Path) -> int | None:
    if p.suffix == ".csv":
        return max(0, sum(1 for _ in p.open(encoding="utf-8")) - 1)
    if p.suffix == ".json" and p.parent.name == "nasa_power":
        doc = json.loads(p.read_text(encoding="utf-8"))
        return len((doc.get("properties") or {}).get("parameter", {}).get("PRECTOTCORR", {}))
    return None


def cache_files(key: str, root: Path | None = None) -> list[Path]:
    base = _root(key, root)
    return sorted(p for p in base.rglob("*") if p.is_file() and p.name != MANIFEST)


def write_manifest(key: str, extra: dict[str, Any] | None = None, root: Path | None = None) -> dict[str, Any]:
    base = _root(key, root)
    files = {p.relative_to(base).as_posix(): {"sha256": _sha(p), "rows": _rows(p)} for p in cache_files(key, root)}
    if not files:
        raise Unprocessable("REPLAY_CACHE_INVALID", f"No cache files for {key}.")
    doc = {"key": key, "built_at": datetime.now(UTC).isoformat(timespec="seconds"), "files": files, **(extra or {})}
    (base / MANIFEST).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return doc


def verify_manifest(key: str, root: Path | None = None) -> dict[str, Any]:
    """Every file listed must exist with the same hash. Returns the manifest plus its own sha256."""
    base = _root(key, root)
    mp = base / MANIFEST
    if not mp.exists():
        raise Unprocessable("REPLAY_CACHE_INVALID", f"{mp.as_posix()} is missing; build the cache first.")
    doc = json.loads(mp.read_text(encoding="utf-8"))
    for rel, meta in doc["files"].items():
        p = base / rel
        if not p.exists():
            raise Unprocessable("REPLAY_CACHE_INVALID", f"Cache file missing: {key}/{rel}")
        if _sha(p) != meta["sha256"]:
            raise Unprocessable("REPLAY_CACHE_INVALID", f"Cache file changed since the manifest: {key}/{rel}")
    doc["sha256"] = _sha(mp)
    return doc

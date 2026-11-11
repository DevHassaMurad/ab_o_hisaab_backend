"""FR5 scenario definitions (data/replay/scenarios.yaml) -> ReplayScenario rows. Districts resolve by name +
province to exactly one row, or loading fails loudly."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NotFound
from app.models.core import ReplayScenario
from app.models.geography import District

DEFAULTS = {"spinup_days": 30, "funds": {"allocated_pkr": "50000000.00"}, "approval_mode": "AUTO_SIM"}


def load_definitions(path: Path | None = None) -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(Path(path or settings.REPLAY_DEFINITIONS).read_text(encoding="utf-8"))
    defaults = {**DEFAULTS, **(doc.get("defaults") or {})}
    return {s["key"]: {**defaults, **s} for s in doc["scenarios"]}


def _d(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def resolve_district(db: Session, name: str, province: str) -> District:
    rows = db.scalars(select(District).where(District.name.ilike(name), District.province == province)).all()
    if len(rows) != 1:
        raise ValueError(f"scenario district {name}/{province} resolves to {len(rows)} rows")
    return rows[0]


def config_json(db: Session, s: dict[str, Any]) -> dict[str, Any]:
    """Static scenario config stored on the row (hero, onsets, lead rules, fund amount)."""
    out: dict[str, Any] = {"min_lead_days": s.get("min_lead_days"), "stretch_lead_days": s.get("stretch_lead_days"),
                           "spinup_days": s["spinup_days"], "funds": s["funds"]}
    if s.get("hero_district"):
        hero = next(x for x in s["districts"] if x["name"] == s["hero_district"])
        out["hero_district_id"] = str(resolve_district(db, hero["name"], hero["province"]).id)
        out["hero_district"] = s["hero_district"]
    if s.get("reference_onset"):
        onset = {}
        for name, d in s["reference_onset"].items():
            prov = next(x["province"] for x in s["districts"] if x["name"] == name)
            onset[str(resolve_district(db, name, prov).id)] = _d(d).isoformat()
        out["reference_onset"] = onset
    return out


def ensure_scenarios(db: Session, path: Path | None = None) -> int:
    defs = load_definitions(path)
    for key, s in defs.items():
        sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == key))
        if sc is None:
            sc = ReplayScenario(key=key, status="IDLE", run_no=1, config={})
            db.add(sc)
        sc.name, sc.expected_outcome = s["name"], s["expected_outcome"]
        sc.start_date, sc.end_date = _d(s["start_date"]), _d(s["end_date"])
        sc.districts = [resolve_district(db, x["name"], x["province"]).id for x in s["districts"]]
        if sc.status == "IDLE":  # never rewrite a running scenario's config
            sc.config = config_json(db, s)
    db.flush()
    return len(defs)


def get_scenario_by_key(db: Session, key: str) -> ReplayScenario:
    sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == key))
    if sc is None:
        raise NotFound("SCENARIO_NOT_FOUND", f"Unknown replay scenario {key}.")
    return sc

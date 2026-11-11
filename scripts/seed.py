"""Seed districts, tehsils, demo users and replay scenarios. Idempotent (upsert by pcode / email / key).

    python -m scripts.seed
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.core import ReplayScenario, User
from app.models.enums import FloodMode, Province
from app.models.geography import District, Tehsil
from app.services.forecasting.modes import apply_flood_mode_seed
from app.services.ingestion.terrain import load_terrain_seed
from app.services.replay.scenarios import ensure_scenarios
from app.services.triggers.fund_settings import seed_live_funds

ROOT = Path(__file__).resolve().parents[1]


def tehsil_pcode(district_pcode: str, name: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Z]", "", name.upper())[:3] or "TEH"
    code, n = f"{district_pcode}-{base}", 2
    while code in taken:
        code, n = f"{district_pcode}-{base}{n}", n + 1
    taken.add(code)
    return code


def load_district_seed(path: Path | None = None) -> list[dict]:
    return yaml.safe_load((path or ROOT / "data/seeds/districts.yaml").read_text(encoding="utf-8"))["districts"]


def seed_districts(db: Session, path: Path | None = None) -> int:
    n = 0
    for row in load_district_seed(path):
        d = db.scalar(select(District).where(District.pcode == row["pcode"]))
        if d is None:
            d = District(pcode=row["pcode"])
            db.add(d)
        d.name, d.province = row["name"], Province(row["province"]).value
        d.flood_mode = (FloodMode.FLASH if d.province == Province.KP else FloodMode.SLOW_ONSET).value
        d.centroid_lat, d.centroid_lon, d.is_active = row["lat"], row["lon"], bool(row.get("active", True))
        if d.vulnerability_weight is None:
            d.vulnerability_weight = 0.5
        db.flush()
        taken = set(db.scalars(select(Tehsil.pcode).where(Tehsil.district_id == d.id)).all())
        existing = {t.name: t for t in db.scalars(select(Tehsil).where(Tehsil.district_id == d.id)).all()}
        for name in row.get("tehsils", []):
            if name not in existing:
                db.add(Tehsil(pcode=tehsil_pcode(d.pcode, name, taken), district_id=d.id, name=name))
        n += 1
    db.flush()
    return n


def seed_users(db: Session, path: Path | None = None) -> int:
    pw = os.environ.get("SEED_USER_PASSWORD", "demo")
    rows = yaml.safe_load((path or ROOT / "data/seeds/users.yaml").read_text(encoding="utf-8"))["users"]
    for row in rows:
        district_id = (db.scalar(select(District.id).where(District.name == row["district"]))
                       if row.get("district") else None)
        u = db.scalar(select(User).where(User.email == row["email"]))
        if u is None:
            u = User(email=row["email"], password_hash=hash_password(pw))
            db.add(u)
        u.full_name, u.role, u.district_id = row["full_name"], row["role"], district_id
    db.flush()
    return len(rows)


def load_scenario_definitions(path: Path | None = None) -> list[dict]:
    return yaml.safe_load((path or ROOT / settings.REPLAY_DEFINITIONS).read_text(encoding="utf-8"))["scenarios"]


def resolve_district(db: Session, name: str, province: str) -> District:
    rows = db.scalars(select(District).where(District.name.ilike(name), District.province == province)).all()
    if len(rows) != 1:
        raise ValueError(f"scenario district {name}/{province} resolves to {len(rows)} rows")
    return rows[0]


def seed_scenarios(db: Session, path: Path | None = None) -> int:
    defs = load_scenario_definitions(path)
    for s in defs:
        sc = db.scalar(select(ReplayScenario).where(ReplayScenario.key == s["key"]))
        if sc is None:
            sc = ReplayScenario(key=s["key"], status="IDLE", run_no=1)
            db.add(sc)
        sc.name, sc.expected_outcome = s["name"], s["expected_outcome"]
        sc.start_date, sc.end_date = _d(s["start_date"]), _d(s["end_date"])
        sc.districts = [resolve_district(db, x["name"], x["province"]).id for x in s["districts"]]
        sc.config = {k: v for k, v in s.items() if k in ("hero_district", "min_lead_days", "stretch_lead_days")}
    db.flush()
    return len(defs)


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def seed_all(db: Session) -> dict[str, int]:
    out = {"districts": seed_districts(db), "modes_changed": apply_flood_mode_seed(db), "users": seed_users(db),
           "funds_created": seed_live_funds(db),
           "scenarios": ensure_scenarios(db),
           "terrain_rows": load_terrain_seed(db, ROOT / "data/seeds/tehsil_terrain.csv")}
    db.commit()
    return out


if __name__ == "__main__":
    with SessionLocal() as session:
        print(seed_all(session))

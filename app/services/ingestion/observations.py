"""Reads and idempotent writes of ClimateObservation (shared by FR1 and FR2)."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.core import ReplayScenario
from app.models.geography import District, Tehsil
from app.models.ingestion import ClimateObservation as CO

VALUE_COLS = ("rainfall_mm", "rainfall_max_3h_mm", "temp_c", "soil_wetness", "soil_wetness_top", "provenance",
              "forecast_model", "quality_flags", "is_stale", "stale_since")


def _scope(stmt, scenario_id: UUID | None):
    return stmt.where(CO.scenario_id.is_(None) if scenario_id is None else CO.scenario_id == scenario_id)


def upsert_observations(db: Session, rows: list[dict[str, Any]]) -> int:
    """INSERT ... ON CONFLICT on the NULLS NOT DISTINCT key, so re-runs update instead of duplicating."""
    if not rows:
        return 0
    payload = []
    for r in rows:
        base = {"id": uuid.uuid4(), "tehsil_id": None, "lead_day": None, "scenario_id": None,
                "rainfall_max_3h_mm": None, "temp_c": None, "soil_wetness": None, "soil_wetness_top": None,
                "provenance": None, "forecast_model": None, "quality_flags": [], "is_stale": False, "stale_since": None}
        base.update(r)
        payload.append(base)
    stmt = insert(CO).values(payload)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_climate_observation",
        set_={c: getattr(stmt.excluded, c) for c in VALUE_COLS} | {"updated_at": func.now()},
    )
    db.execute(stmt)
    return len(payload)


def latest_valid_date(db: Session, source: str, scenario_id: UUID | None, *, district_id: UUID | None = None,
                      tehsil_id: UUID | None = None, on_or_before: date | None = None) -> date | None:
    q = select(func.max(CO.obs_date)).where(CO.source == str(source), CO.is_stale.is_(False),
                                            CO.rainfall_mm.is_not(None), CO.lead_day.is_(None))
    if district_id is not None:
        q = q.where(CO.district_id == district_id)
    q = q.where(CO.tehsil_id == tehsil_id) if tehsil_id is not None else q.where(CO.tehsil_id.is_(None))
    if on_or_before is not None:
        q = q.where(CO.obs_date <= on_or_before)
    return db.scalar(_scope(q, scenario_id))


def fresh_dates_in_db(db: Session, source: str, scenario_id: UUID | None, start: date, end: date, *,
                      district_id: UUID | None = None, tehsil_id: UUID | None = None) -> set[date]:
    q = select(CO.obs_date).where(CO.source == str(source), CO.is_stale.is_(False), CO.rainfall_mm.is_not(None),
                                  CO.obs_date.between(start, end), CO.lead_day.is_(None))
    if district_id is not None:
        q = q.where(CO.district_id == district_id)
    q = q.where(CO.tehsil_id == tehsil_id) if tehsil_id is not None else q.where(CO.tehsil_id.is_(None))
    return set(db.scalars(_scope(q, scenario_id)).all())


def get_valid_row(db: Session, source: str, scenario_id: UUID | None, obs_date: date, *,
                  district_id: UUID | None, tehsil_id: UUID | None = None) -> CO | None:
    q = select(CO).where(CO.source == str(source), CO.obs_date == obs_date, CO.lead_day.is_(None),
                         CO.is_stale.is_(False))
    q = q.where(CO.district_id == district_id) if district_id is not None else q
    q = q.where(CO.tehsil_id == tehsil_id) if tehsil_id is not None else q.where(CO.tehsil_id.is_(None))
    return db.scalar(_scope(q, scenario_id))


def copy_forward_row(src: CO, obs_date: date) -> dict[str, Any]:
    return {"district_id": src.district_id, "tehsil_id": src.tehsil_id, "obs_date": obs_date, "source": src.source,
            "rainfall_mm": src.rainfall_mm, "rainfall_max_3h_mm": src.rainfall_max_3h_mm, "temp_c": src.temp_c,
            "soil_wetness": src.soil_wetness, "soil_wetness_top": src.soil_wetness_top,
            "quality_flags": ["copied_forward"], "is_stale": True, "stale_since": src.obs_date,
            "scenario_id": src.scenario_id}


def active_districts(db: Session, district_ids: list[UUID] | None, scenario_id: UUID | None) -> list[District]:
    q = select(District).order_by(District.pcode)
    if district_ids:
        q = q.where(District.id.in_(district_ids))
    elif scenario_id is not None:
        ids = db.scalar(select(ReplayScenario.districts).where(ReplayScenario.id == scenario_id)) or []
        q = q.where(District.id.in_(ids))
    else:
        q = q.where(District.is_active.is_(True))
    return list(db.scalars(q).all())


def active_tehsils(db: Session, district_ids: list[UUID] | None, scenario_id: UUID | None) -> list[Tehsil]:
    ds = [d.id for d in active_districts(db, district_ids, scenario_id)]
    if not ds:
        return []
    return list(db.scalars(select(Tehsil).where(Tehsil.district_id.in_(ds)).order_by(Tehsil.pcode)).all())


def scenario_key(db: Session, scenario_id: UUID) -> str:
    return db.scalar(select(ReplayScenario.key).where(ReplayScenario.id == scenario_id))


def list_observations(db: Session, *, scenario_id: UUID | None, district_id: UUID, tehsil_id: UUID | None,
                      source: str | None, start: date, end: date, page: int, page_size: int) -> tuple[list[CO], int]:
    q = select(CO).where(CO.district_id == district_id, CO.obs_date.between(start, end))
    if tehsil_id is not None:
        q = q.where(CO.tehsil_id == tehsil_id)
    if source:
        q = q.where(CO.source == source)
    q = _scope(q, scenario_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    items = db.scalars(q.order_by(CO.obs_date.desc(), CO.source, CO.tehsil_id)
                       .offset((page - 1) * page_size).limit(page_size)).all()
    return list(items), total

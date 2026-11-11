"""FR3 read APIs and on-demand run. Every response carries mode, fallback, stale and proxy flags (FR3-013)."""
from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import get_clock
from app.core.db import get_db
from app.core.errors import NotFound, Unprocessable
from app.core.scenario import use_scenario
from app.core.security import assert_district_access, require_role
from app.models.core import AuditLog, User
from app.models.enums import Role
from app.models.forecasting import FloodForecast, RiskIndex
from app.models.geography import District, Tehsil
from app.services.forecasting.features import PROXY_PROVENANCES
from app.services.forecasting.model_registry import ModelRegistry
from app.services.forecasting.modes import ModeRegistry
from app.services.forecasting.scoring import round_half_up_score
from app.services.forecasting.service import ForecastService, load_thresholds

router = APIRouter(prefix="/forecasts", tags=["Forecasting"])
READERS = (Role.PDMA_OFFICER, Role.ADMIN, Role.DC)


def _scope(q, model, sid: UUID | None):
    return q.where(model.scenario_id.is_(None) if sid is None else model.scenario_id == sid)


def _flags(ri: RiskIndex) -> dict[str, Any]:
    return {"mode": ri.mode, "model_version": ri.model_version, "is_fallback": ri.is_fallback,
            "is_stale_input": ri.is_stale_input, "stale_since": ri.stale_since,
            "forecast_provenance": ri.forecast_provenance,
            "forecast_is_proxy": ri.forecast_provenance in PROXY_PROVENANCES}


def _latest_run(db: Session, sid: UUID | None) -> date:
    d = db.scalar(_scope(select(func.max(RiskIndex.run_date)), RiskIndex, sid))
    if d is None:
        raise NotFound("NO_FORECAST_FOR_DATE", "No forecast run exists yet.")
    return d


def _index(db: Session, district_id: UUID, tehsil_id: UUID | None, run_date: date | None,
           sid: UUID | None) -> RiskIndex:
    run_date = run_date or _latest_run(db, sid)
    q = select(RiskIndex).where(RiskIndex.district_id == district_id, RiskIndex.run_date == run_date)
    q = q.where(RiskIndex.tehsil_id == tehsil_id) if tehsil_id else q.where(RiskIndex.tehsil_id.is_(None))
    ri = db.scalar(_scope(q, RiskIndex, sid))
    if ri is None:
        raise NotFound("NO_FORECAST_FOR_DATE", f"No forecast for {run_date}.")
    return ri


def _series(db: Session, ri: RiskIndex, sid: UUID | None, full: bool = False) -> list[dict[str, Any]]:
    q = select(FloodForecast).where(FloodForecast.district_id == ri.district_id,
                                    FloodForecast.run_date == ri.run_date)
    q = q.where(FloodForecast.tehsil_id == ri.tehsil_id) if ri.tehsil_id else q.where(
        FloodForecast.tehsil_id.is_(None))
    rows = db.scalars(_scope(q, FloodForecast, sid).order_by(FloodForecast.lead_day)).all()
    out = []
    for f in rows:
        item = {"lead_day": f.lead_day, "target_date": f.target_date, "probability": round(f.probability, 4),
                "score": round_half_up_score(f.probability)}
        if full:
            item["features"] = f.features
        out.append(item)
    return out


def _detail(db: Session, ri: RiskIndex, name: str, sid: UUID | None) -> dict[str, Any]:
    return {"district_id": ri.district_id, "tehsil_id": ri.tehsil_id, "name": name, "run_date": ri.run_date,
            "score": ri.score, "tier": ri.tier, "peak_lead_day": ri.peak_lead_day, "series": _series(db, ri, sid),
            "explanation_en": ri.explanation_en, "explanation_ur": ri.explanation_ur, "top_factors": ri.top_factors,
            "thresholds": load_thresholds(db, ri.district_id), "fund_remaining_pkr": None,  # FR6/FR17
            "risk_index_id": ri.id, **_flags(ri)}


@router.get("/risk-map")
def risk_map(db: Session = Depends(get_db), _: User = Depends(require_role(*READERS)),
             run_date: date | None = None, level: str = Query("tehsil", pattern="^(tehsil|district)$"),
             province: str | None = None, x_scenario_id: UUID | None = Header(default=None)) -> dict:
    run_date = run_date or _latest_run(db, x_scenario_id)
    q = select(RiskIndex).where(RiskIndex.run_date == run_date)
    q = q.where(RiskIndex.tehsil_id.is_not(None)) if level == "tehsil" else q.where(RiskIndex.tehsil_id.is_(None))
    if province:
        q = q.join(District, District.id == RiskIndex.district_id).where(District.province == province)
    feats, proxy = [], False
    for ri in db.scalars(_scope(q, RiskIndex, x_scenario_id)).all():
        model = Tehsil if ri.tehsil_id else District
        obj_id = ri.tehsil_id or ri.district_id
        row = db.execute(select(model.pcode, model.name, func.ST_AsGeoJSON(func.ST_SimplifyPreserveTopology(
            model.geom, 0.005))).where(model.id == obj_id)).one()
        proxy = proxy or ri.forecast_provenance in PROXY_PROVENANCES
        feats.append({"type": "Feature", "geometry": json.loads(row[2]) if row[2] else None, "properties": {
            "tehsil_id": ri.tehsil_id, "district_id": ri.district_id, "pcode": row[0], "name": row[1],
            "score": ri.score, "tier": ri.tier, "peak_lead_day": ri.peak_lead_day, **_flags(ri)}})
    return {"type": "FeatureCollection", "run_date": run_date, "level": level, "forecast_is_proxy": proxy,
            "features": feats}


@router.get("/districts/{district_id}")
def district_detail(district_id: UUID, db: Session = Depends(get_db), user: User = Depends(require_role(*READERS)),
                    run_date: date | None = None, x_scenario_id: UUID | None = Header(default=None)) -> dict:
    d = db.get(District, district_id)
    if d is None:
        raise NotFound("DISTRICT_NOT_FOUND", "District not found.")
    assert_district_access(user, d.id)
    return _detail(db, _index(db, d.id, None, run_date, x_scenario_id), d.name, x_scenario_id)


@router.get("/districts/{district_id}/history")
def district_history(district_id: UUID, db: Session = Depends(get_db), user: User = Depends(require_role(*READERS)),
                     from_: date | None = Query(default=None, alias="from"), to: date | None = None,
                     page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=400),
                     x_scenario_id: UUID | None = Header(default=None)) -> dict:
    assert_district_access(user, district_id)
    q = select(RiskIndex).where(RiskIndex.district_id == district_id, RiskIndex.tehsil_id.is_(None))
    if from_:
        q = q.where(RiskIndex.run_date >= from_)
    if to:
        q = q.where(RiskIndex.run_date <= to)
    q = _scope(q, RiskIndex, x_scenario_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(RiskIndex.run_date).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"run_date": r.run_date, "score": r.score, "tier": r.tier, "peak_lead_day": r.peak_lead_day,
                       "is_fallback": r.is_fallback, "is_stale_input": r.is_stale_input,
                       "forecast_is_proxy": r.forecast_provenance in PROXY_PROVENANCES} for r in items],
            "total": total, "page": page, "page_size": page_size}


@router.get("/tehsils/{tehsil_id}")
def tehsil_detail(tehsil_id: UUID, db: Session = Depends(get_db), user: User = Depends(require_role(*READERS)),
                  run_date: date | None = None, x_scenario_id: UUID | None = Header(default=None)) -> dict:
    t = db.get(Tehsil, tehsil_id)
    if t is None:
        raise NotFound("TEHSIL_NOT_FOUND", "Tehsil not found.")
    assert_district_access(user, t.district_id)
    out = _detail(db, _index(db, t.district_id, t.id, run_date, x_scenario_id), t.name, x_scenario_id)
    out.pop("fund_remaining_pkr")
    return out


@router.get("/risk-index/{risk_index_id}")
def risk_index_trace(risk_index_id: UUID, db: Session = Depends(get_db), user: User = Depends(require_role(*READERS)),
                     x_scenario_id: UUID | None = Header(default=None)) -> dict:
    ri = db.scalar(_scope(select(RiskIndex).where(RiskIndex.id == risk_index_id), RiskIndex, x_scenario_id))
    if ri is None:
        raise NotFound("RISK_INDEX_NOT_FOUND", "Risk index not found.")
    assert_district_access(user, ri.district_id)
    name = db.scalar(select(District.name).where(District.id == ri.district_id))
    out = _detail(db, ri, name, x_scenario_id)
    out["forecasts"] = _series(db, ri, x_scenario_id, full=True)
    out["aggregation"] = ri.aggregation
    out["audit_entry_seq"] = db.scalar(select(AuditLog.seq).where(AuditLog.event_type == "RISK_SCORED",
                                                                  AuditLog.entity_id == ri.id)
                                       .order_by(AuditLog.seq.desc()).limit(1))
    return out


class RunIn(BaseModel):
    run_date: date | None = None
    district_ids: list[UUID] | None = None


@router.post("/runs")
def run_forecast(body: RunIn, db: Session = Depends(get_db), _: User = Depends(require_role(Role.ADMIN)),
                 x_scenario_id: UUID | None = Header(default=None)) -> dict:
    today = get_clock().today()
    run_date = body.run_date or today
    if run_date > today:
        raise Unprocessable("INVALID_RUN_DATE", "Run date cannot be in the future.")
    with use_scenario(x_scenario_id):
        return ForecastService().run(db, run_date, body.district_ids)


@router.get("/model-info")
def model_info(_: User = Depends(require_role(*READERS))) -> dict:
    reg = ModelRegistry()
    return {"modes": [reg.info(s) for s in ModeRegistry.all()]}


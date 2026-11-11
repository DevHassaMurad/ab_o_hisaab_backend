"""FR1 router; FR2 uses the same endpoints through the ingestor registry."""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import DomainError, NotFound, Unprocessable
from app.core.scenario import use_scenario
from app.core.security import assert_district_access, require_role
from app.models.core import User
from app.models.enums import Role
from app.models.geography import District
from app.models.ingestion import ContextIndicator
from app.schemas.ingestion import (
    ContextIndicatorOut,
    ContextIndicatorsOut,
    ContextRefreshOut,
    IngestRunRequest,
    IngestRunResult,
    ObservationOut,
    ObservationPage,
)
from app.services.ingestion.context_indicators import ContextIndicatorService
from app.services.ingestion.observations import list_observations
from app.services.ingestion.registry import get_ingestor

router = APIRouter(prefix="/ingestion", tags=["Data"])
READERS = (Role.PDMA_OFFICER, Role.ADMIN, Role.DC)
MAX_RUN_DAYS = 31


def _district(db: Session, district_id: UUID | None, pcode: str | None) -> District:
    if district_id is None and not pcode:
        raise DomainError("MISSING_FILTER", "Give district_id or district_pcode.", status=400)
    q = select(District).where(District.id == district_id) if district_id else select(District).where(
        District.pcode == pcode)
    d = db.scalar(q)
    if d is None:
        raise NotFound("DISTRICT_NOT_FOUND", "District not found.")
    return d


@router.post("/run", response_model=IngestRunResult)
def run(body: IngestRunRequest, db: Session = Depends(get_db), _: User = Depends(require_role(Role.ADMIN)),
        x_scenario_id: UUID | None = Header(default=None)) -> dict:
    if body.start and body.end and (body.end - body.start).days + 1 > MAX_RUN_DAYS:
        raise Unprocessable("RANGE_TOO_LARGE", f"At most {MAX_RUN_DAYS} days per request; use the backfill script.")
    ingestor = get_ingestor(body.source)
    ids = None
    if body.district_pcodes:
        found = {d.pcode: d.id for d in db.scalars(select(District).where(District.pcode.in_(body.district_pcodes)))}
        missing = sorted(set(body.district_pcodes) - set(found))
        if missing:
            raise NotFound("DISTRICT_NOT_FOUND", f"Unknown district pcodes: {', '.join(missing)}")
        ids = list(found.values())
    with use_scenario(x_scenario_id):
        return ingestor.ingest(db, start=body.start, end=body.end, district_ids=ids).summary()


@router.get("/observations", response_model=ObservationPage)
def observations(db: Session = Depends(get_db), user: User = Depends(require_role(*READERS)),
                 district_id: UUID | None = None, district_pcode: str | None = None, tehsil_id: UUID | None = None,
                 source: str | None = None, from_: date | None = Query(default=None, alias="from"),
                 to: date | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                 x_scenario_id: UUID | None = Header(default=None)) -> dict:
    d = _district(db, district_id, district_pcode)
    assert_district_access(user, d.id)
    to = to or date.today()
    from_ = from_ or to - timedelta(days=30)
    if (to - from_).days > 366:
        raise Unprocessable("RANGE_TOO_LARGE", "At most 366 days per request.")
    items, total = list_observations(db, scenario_id=x_scenario_id, district_id=d.id, tehsil_id=tehsil_id,
                                     source=source, start=from_, end=to, page=page, page_size=page_size)
    return {"items": [ObservationOut.model_validate(i) for i in items], "total": total, "page": page,
            "page_size": page_size}


@router.get("/context-indicators", response_model=ContextIndicatorsOut)
def context_indicators(district_id: UUID, db: Session = Depends(get_db),
                       user: User = Depends(require_role(*READERS))) -> dict:
    d = _district(db, district_id, None)
    assert_district_access(user, d.id)
    items = db.scalars(select(ContextIndicator).where(ContextIndicator.district_id == d.id)
                       .order_by(ContextIndicator.indicator, ContextIndicator.period)).all()
    return {"district_id": d.id, "vulnerability_weight": d.vulnerability_weight,
            "vulnerability_inputs": d.vulnerability_inputs,
            "items": [ContextIndicatorOut(indicator=i.indicator, value=i.value, period=i.period, source=i.source)
                      for i in items]}


@router.post("/context-indicators/refresh", response_model=ContextRefreshOut)
def refresh_context(db: Session = Depends(get_db), _: User = Depends(require_role(Role.ADMIN))) -> dict:
    res = ContextIndicatorService().refresh(db)
    return res.__dict__

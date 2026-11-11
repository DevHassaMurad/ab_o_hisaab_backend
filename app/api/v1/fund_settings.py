"""FR6 fund settings API. Writes: PDMA_OFFICER only. Reads: officer and admin (all), DC (own district). Donor: 403.
Honours X-Scenario-Id, so a replay's fund can be edited without touching live data."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.scenario import use_scenario
from app.core.security import assert_district_access, require_role
from app.models.core import User
from app.models.enums import Role
from app.schemas.fund_settings import FundSettingsIn
from app.services.triggers import fund_settings as svc

router = APIRouter(tags=["Triggers"])


@router.get("/districts/{district_id}/fund-settings")
def get_fund_settings(district_id: UUID, db: Session = Depends(get_db),
                      user: User = Depends(require_role(Role.PDMA_OFFICER, Role.ADMIN, Role.DC)),
                      x_scenario_id: UUID | None = Header(default=None)) -> dict:
    assert_district_access(user, district_id)
    with use_scenario(x_scenario_id):
        return svc.get_settings(db, district_id)


@router.put("/districts/{district_id}/fund-settings")
def put_fund_settings(district_id: UUID, body: FundSettingsIn, db: Session = Depends(get_db),
                      user: User = Depends(require_role(Role.PDMA_OFFICER)),
                      x_scenario_id: UUID | None = Header(default=None)) -> dict:
    with use_scenario(x_scenario_id):
        return svc.update_settings(db, district_id, body, actor=str(user.id))


@router.get("/districts/{district_id}/fund-settings/draft")
def copy_draft(district_id: UUID, copy_from: UUID, include_amount: bool = False, db: Session = Depends(get_db),
               _: User = Depends(require_role(Role.PDMA_OFFICER)),
               x_scenario_id: UUID | None = Header(default=None)) -> dict:
    with use_scenario(x_scenario_id):
        return svc.build_copy_draft(db, district_id, copy_from, include_amount).model_dump(mode="json")


@router.get("/fund-settings")
def list_fund_settings(db: Session = Depends(get_db),
                       user: User = Depends(require_role(Role.PDMA_OFFICER, Role.ADMIN, Role.DC)),
                       province: str | None = None, page: int = Query(1, ge=1),
                       page_size: int = Query(50, ge=1, le=200),
                       x_scenario_id: UUID | None = Header(default=None)) -> dict:
    with use_scenario(x_scenario_id):
        items = svc.list_settings(db, province, user.district_id if user.role == Role.DC else None)
    return {"items": items[(page - 1) * page_size: page * page_size], "total": len(items), "page": page,
            "page_size": page_size}

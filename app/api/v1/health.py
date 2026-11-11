from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import require_role
from app.models.enums import Role
from app.models.ingestion import DataSourceStatus
from app.schemas.ingestion import DataSourceOut

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/data-sources", response_model=list[DataSourceOut],
            dependencies=[Depends(require_role(Role.PDMA_OFFICER, Role.DC, Role.ADMIN))])
def data_sources(db: Session = Depends(get_db)) -> list[DataSourceStatus]:
    return list(db.scalars(select(DataSourceStatus).order_by(DataSourceStatus.source)).all())

"""Copy-forward of last valid data and source health (NFR9). Shared by FR1 and FR2."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.core import User
from app.models.enums import Role
from app.models.ingestion import DataSourceStatus

log = logging.getLogger("aab.ingestion")


@dataclass(frozen=True)
class StaleRow:
    obs_date: date
    copy_of: date  # = stale_since


def plan_copy_forward(*, fresh_dates: set[date], last_valid: date | None, window: tuple[date, date],
                      today: date, expected_lag: int, max_days: int) -> list[StaleRow]:
    """Days that are overdue (older than the expected publication lag) and have no fresh value get a copy of the
    last valid row, for at most max_days after it. Days not yet due are simply skipped."""
    if last_valid is None:
        return []
    due_until = today - timedelta(days=expected_lag)
    rows: list[StaleRow] = []
    d = window[0]
    while d <= min(window[1], due_until):
        if d not in fresh_dates and d > last_valid and (d - last_valid).days <= max_days:
            rows.append(StaleRow(d, last_valid))
        d += timedelta(days=1)
    return rows


def count_not_due(start: date, end: date, today: date, expected_lag: int, fresh_dates: set[date]) -> int:
    due_until = today - timedelta(days=expected_lag)
    n, d = 0, max(start, due_until + timedelta(days=1))
    while d <= end:
        if d not in fresh_dates:
            n += 1
        d += timedelta(days=1)
    return n


class Transition(StrEnum):
    DOWN = "DOWN"
    UP = "UP"
    NONE = "NONE"


def update_source_status(db: Session, *, source: str, healthy: bool, last_valid_date: date | None,
                         error: str | None, now: datetime, scenario_id: UUID | None) -> Transition:
    if scenario_id is not None:
        return Transition.NONE  # replay never touches the live status table
    st = db.scalar(select(DataSourceStatus).where(DataSourceStatus.source == str(source)).with_for_update())
    was = st.is_healthy if st else True
    if st is None:
        st = DataSourceStatus(source=str(source))
        db.add(st)
    st.is_healthy, st.last_error = healthy, error
    if last_valid_date:
        st.last_valid_date = last_valid_date
    if healthy:
        st.last_success_at = now
    db.flush()
    if was and not healthy:
        return Transition.DOWN
    if not was and healthy:
        return Transition.UP
    return Transition.NONE


def source_status_id(db: Session, source: str) -> UUID | None:
    return db.scalar(select(DataSourceStatus.id).where(DataSourceStatus.source == str(source)))


def notify_admin_source_down(db: Session, alerts: Any, source: str, last_valid_date: date | None,
                             error: str | None) -> None:
    admins = db.scalars(select(User).where(User.role == Role.ADMIN, User.is_active.is_(True))).all()
    ctx = {"source": str(source), "last_valid_date": str(last_valid_date) if last_valid_date else None,
           "error": error}
    if not admins:
        log.error("data source %s is down and no admin user exists to alert: %s", source, ctx)
        return
    for admin in admins:
        alerts.enqueue(db, recipient={"user_id": str(admin.id), "type": "ADMIN"}, purpose="DATA_SOURCE_DOWN",
                       template_key="data_source_down", context=ctx)

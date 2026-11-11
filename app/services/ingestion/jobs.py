"""FR1 pipeline step (order 10) and monthly context refresh. Steps never raise, so FR3 always runs (UC03-6)."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.scenario import current_scenario
from app.models.enums import DataSource
from app.services.ingestion.context_indicators import ContextIndicatorService
from app.services.ingestion.nasa_power_ingestor import NasaPowerIngestor
from app.services.ingestion.registry import get_ingestor, register_ingestor
from app.services.ingestion.staleness import (
    Transition,
    notify_admin_source_down,
    source_status_id,
    update_source_status,
)
from app.services.stubs import get_alerts, get_audit

log = logging.getLogger("aab.ingestion")


def mark_unhealthy_and_alert(db: Session, source: str, error: str, clock: Clock) -> None:
    """Own small transaction after an unexpected failure."""
    try:
        sid = current_scenario()
        transition = update_source_status(db, source=source, healthy=False, last_valid_date=None, error=error,
                                          now=clock.now(), scenario_id=sid)
        if transition is Transition.DOWN:
            get_audit().record(db, event_type="DATA_SOURCE_STALE", entity_type="DataSourceStatus",
                               entity_id=source_status_id(db, source), actor="system",
                               payload={"source": source, "error": error})
            notify_admin_source_down(db, get_alerts(), source, None, error)
        db.commit()
    except Exception:  # pragma: no cover - defensive
        log.exception("could not record %s outage", source)
        db.rollback()


def run_source_step(db: Session, clock: Clock, source: str) -> dict[str, Any]:
    try:
        return get_ingestor(source).ingest(db).summary()
    except Exception as exc:
        log.exception("%s ingest step failed", source)
        db.rollback()
        mark_unhealthy_and_alert(db, source, str(exc), clock)
        return {"source": source, "is_healthy": False, "error": str(exc)}


def run_nasa_power_step(db: Session, clock: Clock) -> dict[str, Any]:
    return run_source_step(db, clock, DataSource.NASA_POWER)


def run_context_refresh() -> None:
    with SessionLocal() as db:
        try:
            ContextIndicatorService().refresh(db)
        except Exception:
            log.exception("context indicator refresh failed")
            db.rollback()


def register_ingestion_jobs() -> None:
    from app.core import scheduler
    from app.jobs.daily_pipeline import register_step

    register_ingestor(NasaPowerIngestor())
    register_step(10, "ingest.nasa_power", run_nasa_power_step)
    scheduler.register_job("refresh_context_indicators", run_context_refresh, settings.CONTEXT_REFRESH_CRON)

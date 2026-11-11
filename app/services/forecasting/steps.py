"""FR3 pipeline steps: 30 forecast rain, 40 scoring (after FR1 = 10 and FR2 = 20). Neither raises."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.services.forecasting.rain_ingest import ForecastRainIngestService
from app.services.forecasting.service import ForecastService

log = logging.getLogger("aab.forecasting")


def forecast_rain_step(db: Session, clock: Clock) -> dict[str, Any]:
    try:
        return ForecastRainIngestService(clock=clock).ingest(db, clock.today())
    except Exception as exc:
        log.exception("forecast rain step failed")
        db.rollback()
        return {"source": "FORECAST_RAIN", "is_healthy": False, "error": str(exc)}


def forecast_score_step(db: Session, clock: Clock) -> dict[str, Any]:
    try:
        return ForecastService(clock=clock).run(db, clock.today())
    except Exception as exc:
        log.exception("forecast score step failed")
        db.rollback()
        return {"step": "forecast_score", "error": str(exc)}


def register_forecast_steps() -> None:
    from app.jobs.daily_pipeline import register_step
    from app.services.forecasting.modes import ModeRegistry

    ModeRegistry.load_all()  # invalid mode YAML stops start-up (FR4-008)
    register_step(30, "forecast_rain_ingest", forecast_rain_step)
    register_step(40, "forecast_score", forecast_score_step)

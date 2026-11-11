from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.api.v1 import auth, forecast_modes, forecasts, fund_settings, health, ingestion, replay
from app.core import scheduler
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.services.forecasting.steps import register_forecast_steps
from app.services.ingestion.ee_jobs import register_ee_jobs
from app.services.ingestion.jobs import register_ingestion_jobs
from app.services.triggers.registry import register_fund_rules


def register_all() -> None:
    register_ingestion_jobs()  # FR1: NASA POWER step 10 + monthly context refresh
    register_ee_jobs()         # FR2: IMERG step 20
    register_forecast_steps()  # FR3: forecast rain 30, scoring 40 (FR4 modes validated here)
    register_fund_rules()      # FR6: scoring reads each district's configured thresholds


@asynccontextmanager
async def lifespan(_: FastAPI):
    register_all()
    if settings.SCHEDULER_ENABLED:
        from app.core.db import SessionLocal
        from app.jobs.daily_pipeline import run_daily_pipeline

        def _daily() -> None:
            with SessionLocal() as db:
                run_daily_pipeline(db)

        scheduler.register_job("daily_pipeline", _daily, settings.PIPELINE_CRON)
        scheduler.start()
    yield
    scheduler.shutdown()


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(title="Aab o Hisaab AI", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    api = APIRouter(prefix="/api/v1")
    for r in (auth.router, health.router, ingestion.router, forecasts.router, forecast_modes.router,
              fund_settings.router, replay.router):
        api.include_router(r)
    app.include_router(api)
    return app


app = create_app()

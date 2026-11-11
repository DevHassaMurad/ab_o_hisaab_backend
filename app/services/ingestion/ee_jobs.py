"""FR2 pipeline step (order 20), after NASA POWER (10) and before scoring (30). Never raises."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.models.enums import DataSource
from app.services.ingestion.imerg_ingestor import ImergIngestor
from app.services.ingestion.jobs import run_source_step
from app.services.ingestion.registry import register_ingestor


def run_imerg_step(db: Session, clock: Clock) -> dict[str, Any]:
    return run_source_step(db, clock, DataSource.GPM_IMERG)


def register_ee_jobs() -> None:
    from app.jobs.daily_pipeline import register_step

    register_ingestor(ImergIngestor())
    register_step(20, "ingest.gpm_imerg", run_imerg_step)

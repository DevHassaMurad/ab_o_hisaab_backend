"""FR5 autoplay: an APScheduler interval job steps one day per interval and removes itself at the end, on failure,
when awaiting a live approval, or when `until` is reached."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.db import SessionLocal
from app.core.errors import DomainError

log = logging.getLogger("aab.replay")
_scheduler = BackgroundScheduler(timezone="UTC")
_status: dict[str, dict[str, Any]] = {}


def _job_id(key: str) -> str:
    return f"replay.autoplay.{key}"


def _tick(key: str, until: date | None) -> None:
    from app.services.replay.engine import ReplayEngine

    with SessionLocal() as db:
        try:
            st = ReplayEngine().step(db, key, 1)
        except DomainError as exc:
            log.info("autoplay %s stopped: %s", key, exc.code)
            pause(key)
            return
        if st["status"] != "RUNNING" or (until and st["sim_date"] and st["sim_date"] >= until):
            pause(key)


def play(key: str, interval_seconds: int, until: date | None = None) -> dict[str, Any]:
    if not _scheduler.running:
        _scheduler.start()
    _scheduler.add_job(_tick, "interval", seconds=interval_seconds, args=[key, until], id=_job_id(key),
                       replace_existing=True, max_instances=1, coalesce=True)
    _status[key] = {"running": True, "interval_seconds": interval_seconds, "until": until}
    return autoplay_status(key)


def pause(key: str) -> dict[str, Any]:
    try:
        _scheduler.remove_job(_job_id(key))
    except JobLookupError:
        pass
    _status[key] = {"running": False, "interval_seconds": None, "until": None}
    return autoplay_status(key)


def autoplay_status(key: str) -> dict[str, Any]:
    return _status.get(key, {"running": False, "interval_seconds": None, "until": None})

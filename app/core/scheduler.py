"""Thin APScheduler wrapper. Started only when SCHEDULER_ENABLED=true (avoids double start under --reload)."""
from __future__ import annotations

from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

_scheduler = BackgroundScheduler(timezone="UTC")


def register_job(job_id: str, func: Callable[[], object], cron: str) -> None:
    _scheduler.add_job(func, CronTrigger.from_crontab(cron, timezone="UTC"), id=job_id, replace_existing=True,
                       coalesce=True, max_instances=1)


def start() -> None:
    if not _scheduler.running:
        _scheduler.start()


def shutdown() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)

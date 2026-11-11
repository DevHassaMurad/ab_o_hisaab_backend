"""Daily pipeline: ordered steps registered by FRs (FR1 = 10, FR2 = 20, FR3 = 30 ...). A failing step never
stops later steps (UC03-6)."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock

log = logging.getLogger("aab.pipeline")

Step = Callable[[Session, Clock], dict[str, Any]]
_steps: dict[str, tuple[int, Step]] = {}


def register_step(order: int, name: str, func: Step) -> None:
    _steps[name] = (order, func)  # idempotent: re-registering replaces


def steps() -> list[tuple[int, str, Step]]:
    return sorted(((o, n, f) for n, (o, f) in _steps.items()), key=lambda x: x[0])


def run_daily_pipeline(db: Session, clock: Clock | None = None) -> list[dict[str, Any]]:
    clock = clock or get_clock()
    results = []
    for order, name, func in steps():
        try:
            out = func(db, clock)
        except Exception as exc:  # last line of defence; steps should not raise
            log.exception("pipeline step %s failed", name)
            db.rollback()
            out = {"error": str(exc)}
        results.append({"order": order, "step": name, **(out or {})})
    return results

"""Replay scoping: a context variable holds the active scenario id (None = live)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

_current: ContextVar[UUID | None] = ContextVar("scenario_id", default=None)


def current_scenario() -> UUID | None:
    return _current.get()


@contextmanager
def use_scenario(scenario_id: UUID | None) -> Iterator[None]:
    token = _current.set(scenario_id)
    try:
        yield
    finally:
        _current.reset(token)


def scoped(stmt, model):
    """Filter a select() to the current scenario (live rows when no scenario is set)."""
    sid = current_scenario()
    return stmt.where(model.scenario_id.is_(None) if sid is None else model.scenario_id == sid)

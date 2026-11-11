"""Swappable clock. Live code uses SystemClock; FR5 replay swaps in a fixed clock. Never call date.today() directly."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, time
from typing import Protocol


class Clock(Protocol):
    def today(self) -> date: ...
    def now(self) -> datetime: ...


class SystemClock:
    def today(self) -> date:
        return datetime.now(UTC).date()

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock pinned to one simulated day (used by replay and tests)."""

    def __init__(self, day: date, at: time = time(1, 0)) -> None:
        self.day = day
        self.at = at

    def today(self) -> date:
        return self.day

    def now(self) -> datetime:
        return datetime.combine(self.day, self.at, tzinfo=UTC)


_SYSTEM = SystemClock()
_clock: ContextVar[Clock | None] = ContextVar("clock", default=None)


def get_clock() -> Clock:
    return _clock.get() or _SYSTEM


@contextmanager
def clock_scope(clock: Clock) -> Iterator[None]:
    token = _clock.set(clock)
    try:
        yield
    finally:
        _clock.reset(token)

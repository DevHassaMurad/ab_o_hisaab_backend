"""FR5 ReplayClock: today() = the simulated date; now() = that date at the pipeline time plus wall time elapsed in
the step (monotonic within a step)."""
from __future__ import annotations

import time as time_mod
from datetime import UTC, date, datetime, time, timedelta

from app.core.config import settings


def pipeline_time() -> time:
    h, m = settings.REPLAY_PIPELINE_TIME_UTC.split(":")
    return time(int(h), int(m))


class ReplayClock:
    def __init__(self, sim_date: date, at: time | None = None) -> None:
        self.sim_date = sim_date
        self._base = datetime.combine(sim_date, at or pipeline_time(), tzinfo=UTC)
        self._wall0 = time_mod.monotonic()

    def today(self) -> date:
        return self.sim_date

    def now(self) -> datetime:
        return self._base + timedelta(seconds=time_mod.monotonic() - self._wall0)

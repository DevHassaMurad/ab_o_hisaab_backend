"""Pure-Python reference for the IMERG daily maths (FR2). The Earth Engine version computes the same thing
per pixel; tests use this to check the arithmetic. IMERG `precipitation` is a RATE in mm/hr per 30-min slot."""
from __future__ import annotations

from collections.abc import Sequence

SLOT_HOURS = 0.5
SLOTS_PER_3H = 6


def daily_total(rates_mm_hr: Sequence[float]) -> float:
    return round(sum(rates_mm_hr) * SLOT_HOURS, 2)


def rolling_max_3h(rates_mm_hr: Sequence[float]) -> float | None:
    """Largest rainfall over any 6 consecutive half-hour slots (a rolling 3-hour window)."""
    if len(rates_mm_hr) < SLOTS_PER_3H:
        return None
    acc = [r * SLOT_HOURS for r in rates_mm_hr]
    return round(max(sum(acc[i:i + SLOTS_PER_3H]) for i in range(len(acc) - SLOTS_PER_3H + 1)), 2)

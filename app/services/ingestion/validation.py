"""Validation of NASA POWER daily values (UC03-5). Every fix is recorded as a quality flag (Principle IV)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.core.config import settings
from app.integrations.nasa_power import PowerDay


def ranges() -> dict[str, tuple[float, float]]:
    return {"PRECTOTCORR": (0.0, settings.POWER_RAIN_MAX_MM), "T2M": (-30.0, 55.0),
            "GWETROOT": (0.0, 1.0), "GWETTOP": (0.0, 1.0)}


@dataclass
class ValidatedDay:
    obs_date: date
    rainfall_mm: float | None
    temp_c: float | None
    soil_wetness: float | None
    soil_wetness_top: float | None
    flags: list[str] = field(default_factory=list)

    @property
    def is_fresh(self) -> bool:
        return self.rainfall_mm is not None


def validate_power_day(day: PowerDay) -> ValidatedDay:
    flags: list[str] = []
    out: dict[str, float | None] = {}
    rng = ranges()
    for p in ("PRECTOTCORR", "T2M", "GWETROOT", "GWETTOP"):
        v = day.values.get(p)
        lo, hi = rng[p]
        if v is None:
            out[p] = None
            continue
        if p == "PRECTOTCORR" and -0.5 <= v < 0:
            v = 0.0
            flags.append("rain_neg_clamped")
        if p in ("GWETROOT", "GWETTOP") and 1.0 < v <= 100.0:
            v = v / 100.0
            flags.append(f"{p.lower()}_pct_to_fraction")
        if not lo <= v <= hi:
            flags.append(f"{p.lower()}_out_of_range:{v}")
            v = None
        out[p] = v
    return ValidatedDay(day.obs_date, out["PRECTOTCORR"], out["T2M"], out["GWETROOT"], out["GWETTOP"], flags)

"""FR3 feature library (shared by scoring and, later, ml/ training so there is no train/serve skew).

Rows are (tehsil, run_date t, lead d), target date T = t + d. Rain windows end at T and mix sources day by day
(clarify.md): IMERG if the day is <= the tehsil's last IMERG date, else the district's NASA POWER rain, else the
forecast provider's past-days value (stored as FORECAST_RAIN lead_day 0) for days <= t, else the forecast issued on
t for days > t. The source of every day is recorded in `window_sources`."""
from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.scenario import current_scenario
from app.models.geography import District, Tehsil
from app.models.ingestion import ClimateObservation as CO

LOOKBACK_DAYS = 37  # 30-day window + 7 leads
PROVENANCE_RANK = {None: 0, "LIVE_FORECAST": 1, "ARCHIVED_FORECAST": 2, "ARCHIVED_SHORT_LEAD_PROXY": 3,
                   "PERFECT_FORECAST_PROXY": 4}
PROXY_PROVENANCES = {"ARCHIVED_SHORT_LEAD_PROXY", "PERFECT_FORECAST_PROXY"}
RAIN_FEATURES = {"rain_3d_mm", "rain_7d_mm", "rain_14d_mm", "rain_30d_mm", "rain_fcst_target_mm",
                 "rain_24h_fcst_mm", "rain_48h_fcst_mm", "max_3h_fcst_mm", "max_3h_obs_mm"}
PERCENT_FEATURES = {"soil_wetness_root", "soil_wetness_top", "vulnerability_weight"}


@dataclass
class Val:
    rain: float | None
    max3h: float | None = None
    stale: bool = False
    stale_since: date | None = None
    provenance: str | None = None
    soil_root: float | None = None
    soil_top: float | None = None


@dataclass
class Indexes:
    imerg: dict[tuple[UUID, date], Val] = field(default_factory=dict)
    imerg_last: dict[UUID, date] = field(default_factory=dict)
    power: dict[tuple[UUID, date], Val] = field(default_factory=dict)
    power_last: dict[UUID, date] = field(default_factory=dict)
    fc_lead: dict[tuple[UUID, date], Val] = field(default_factory=dict)   # issued on run_date, target date key
    fc_past: dict[tuple[UUID, date], Val] = field(default_factory=dict)   # provider past days (lead 0)


def load_indexes(db: Session, run_date: date, tehsils: list[Tehsil]) -> Indexes:
    sid = current_scenario()
    tids = [t.id for t in tehsils]
    dids = list({t.district_id for t in tehsils})
    lo, hi = run_date - timedelta(days=LOOKBACK_DAYS), run_date + timedelta(days=7)
    q = select(CO).where(CO.obs_date.between(lo, hi),
                         CO.scenario_id.is_(None) if sid is None else CO.scenario_id == sid)
    ix = Indexes()
    rows = db.scalars(q.where((CO.tehsil_id.in_(tids)) | ((CO.tehsil_id.is_(None)) & CO.district_id.in_(dids)))).all()
    for r in rows:
        v = Val(r.rainfall_mm, r.rainfall_max_3h_mm, r.is_stale, r.stale_since, r.provenance, r.soil_wetness,
                r.soil_wetness_top)
        if r.source == "GPM_IMERG" and r.tehsil_id and r.obs_date <= run_date and r.rainfall_mm is not None:
            ix.imerg[(r.tehsil_id, r.obs_date)] = v
            ix.imerg_last[r.tehsil_id] = max(ix.imerg_last.get(r.tehsil_id, date.min), r.obs_date)
        elif r.source == "NASA_POWER" and r.tehsil_id is None and r.obs_date <= run_date:
            ix.power[(r.district_id, r.obs_date)] = v
            if r.rainfall_mm is not None:
                ix.power_last[r.district_id] = max(ix.power_last.get(r.district_id, date.min), r.obs_date)
        elif r.source == "FORECAST_RAIN" and r.tehsil_id:
            if r.lead_day == 0 and r.obs_date <= run_date:
                ix.fc_past[(r.tehsil_id, r.obs_date)] = v
            elif r.lead_day and r.obs_date - timedelta(days=r.lead_day) == run_date:
                ix.fc_lead[(r.tehsil_id, r.obs_date)] = v
    return ix


class RowCtx:
    def __init__(self, ix: Indexes, tehsil: Tehsil, district: District, run_date: date, lead: int,
                 slope_median: float | None) -> None:
        self.ix, self.tehsil, self.district = ix, tehsil, district
        self.run_date, self.lead = run_date, lead
        self.target = run_date + timedelta(days=lead)
        self.slope_median = slope_median
        self.sources: dict[str, str] = {}
        self.missing: set[date] = set()
        self.stale_dates: list[date] = []
        self.provenances: set[str] = set()
        self.max_missing_in_window = 0

    def _use(self, v: Val) -> None:
        if v.stale:
            self.stale_dates.append(v.stale_since or self.run_date)
        if v.provenance:
            self.provenances.add(v.provenance)

    def daily_rain(self, day: date) -> float:
        tid, did, ix = self.tehsil.id, self.tehsil.district_id, self.ix
        candidates: list[tuple[str, Val | None]] = []
        if day <= ix.imerg_last.get(tid, date.min):
            candidates.append(("IMERG", ix.imerg.get((tid, day))))
        if day <= ix.power_last.get(did, date.min):
            candidates.append(("NASA_POWER", ix.power.get((did, day))))
        if day <= self.run_date:
            candidates.append(("OM_PAST", ix.fc_past.get((tid, day))))
        else:
            candidates.append(("FORECAST", ix.fc_lead.get((tid, day))))
        for name, v in candidates:
            if v is not None and v.rain is not None:
                self.sources[day.isoformat()] = name
                self._use(v)
                return float(v.rain)
        self.sources[day.isoformat()] = "MISSING"
        self.missing.add(day)
        return 0.0

    def window(self, n: int) -> float:
        days = [self.target - timedelta(days=i) for i in range(n)]
        total = sum(self.daily_rain(d) for d in days)
        n_missing = sum(1 for d in days if self.sources[d.isoformat()] == "MISSING")
        self.max_missing_in_window = max(self.max_missing_in_window, n_missing)
        return round(total, 2)

    def fc(self, target: date, attr: str = "rain") -> float:
        v = self.ix.fc_lead.get((self.tehsil.id, target))
        if v is None:
            self.missing.add(target)
            return 0.0
        self._use(v)
        x = getattr(v, attr)
        return float(x) if x is not None else 0.0

    def obs_max3h_last(self) -> float:
        last = self.ix.imerg_last.get(self.tehsil.id)
        v = self.ix.imerg.get((self.tehsil.id, last)) if last else None
        return float(v.max3h) if v and v.max3h is not None else 0.0

    def power_last_valid(self, attr: str) -> float:
        last = self.ix.power_last.get(self.tehsil.district_id)
        v = self.ix.power.get((self.tehsil.district_id, last)) if last else None
        if v is None:
            return 0.0
        self._use(v)
        x = getattr(v, attr)
        return float(x) if x is not None else 0.0

    def slope(self) -> float:
        if self.tehsil.slope_mean_deg is not None:
            return float(self.tehsil.slope_mean_deg)
        return float(self.slope_median or 0.0)


FEATURES: dict[str, Callable[[RowCtx], float]] = {
    "rain_3d_mm": lambda c: c.window(3),
    "rain_7d_mm": lambda c: c.window(7),
    "rain_14d_mm": lambda c: c.window(14),
    "rain_30d_mm": lambda c: c.window(30),
    "rain_fcst_target_mm": lambda c: c.fc(c.target),
    "rain_24h_fcst_mm": lambda c: c.fc(c.run_date + timedelta(days=1)),
    "rain_48h_fcst_mm": lambda c: c.fc(c.run_date + timedelta(days=1)) + c.fc(c.run_date + timedelta(days=2)),
    "max_3h_fcst_mm": lambda c: c.fc(c.target, "max3h"),
    "max_3h_obs_mm": lambda c: c.obs_max3h_last(),
    "soil_wetness_root": lambda c: c.power_last_valid("soil_root"),
    "soil_wetness_top": lambda c: c.power_last_valid("soil_top"),
    "elevation_mean_m": lambda c: float(c.tehsil.elevation_mean_m or 0.0),
    "slope_mean_deg": lambda c: c.slope(),
    "vulnerability_weight": lambda c: float(c.district.vulnerability_weight or 0.0),
    "lead_day": lambda c: float(c.lead),
}


@dataclass
class FeatureRow:
    tehsil_id: UUID
    district_id: UUID
    lead_day: int
    target_date: date
    features: dict[str, float]
    window_sources: dict[str, str]
    is_stale: bool
    stale_since: date | None
    provenance: str | None
    has_forecast: bool

    def as_json(self) -> dict[str, Any]:
        return {**self.features, "window_sources": self.window_sources,
                "forecast_provenance": self.provenance, "obs_source": _obs_source(self.window_sources)}


def _obs_source(sources: dict[str, str]) -> str | None:
    obs = [s for s in sources.values() if s in ("IMERG", "NASA_POWER")]
    return max(set(obs), key=obs.count) if obs else None


def worst_provenance(provs: set[str] | list[str | None]) -> str | None:
    return max(provs, key=lambda p: PROVENANCE_RANK.get(p, 0), default=None) if provs else None


class FeatureBuilder:
    def build(self, db: Session, run_date: date, tehsils: list[Tehsil], district: District,
              feature_names: list[str]) -> list[FeatureRow]:
        ix = load_indexes(db, run_date, tehsils)
        slopes = [t.slope_mean_deg for t in tehsils if t.slope_mean_deg is not None]
        median = statistics.median(slopes) if slopes else None
        out: list[FeatureRow] = []
        for t in tehsils:
            for lead in range(1, 8):
                c = RowCtx(ix, t, district, run_date, lead, median)
                feats = {name: round(FEATURES[name](c), 4) for name in feature_names}
                too_many_missing = c.max_missing_in_window > settings.FORECAST_MAX_MISSING_WINDOW_DAYS
                has_fc = any((t.id, run_date + timedelta(days=d)) in ix.fc_lead for d in range(1, 8))
                stale = bool(c.stale_dates) or too_many_missing or not has_fc
                out.append(FeatureRow(t.id, t.district_id, lead, c.target, feats, c.sources, stale,
                                      min(c.stale_dates) if c.stale_dates else None,
                                      worst_provenance(c.provenances), has_fc))
        return out

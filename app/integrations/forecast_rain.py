"""FR3 forecast rainfall from Open-Meteo (live). Response format checked against the live API: multi-location
requests return a JSON list; one location returns an object; `hourly.time` is ISO 'YYYY-MM-DDTHH:MM' in UTC and
`hourly.precipitation` is mm per hour."""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from app.core.config import Settings, settings

LIVE = "LIVE_FORECAST"


class ForecastSourceError(Exception):
    pass


@dataclass(frozen=True)
class GeoPoint:
    tehsil_id: UUID
    district_id: UUID
    lat: float
    lon: float


@dataclass
class ForecastRainRecord:
    tehsil_id: UUID
    district_id: UUID
    target_date: date
    lead_day: int  # 1..7 forecast; 0 = the provider's value for a past day (used only to fill observation gaps)
    rainfall_mm: float
    rainfall_max_3h_mm: float | None
    provenance: str
    forecast_model: str
    is_stale: bool = False
    stale_since: date | None = None


def daily_from_hourly(times: list[str], precip: list[float | None]) -> dict[date, tuple[float, float]]:
    """UTC daily total and largest rolling 3-hour sum (windows ending in that day)."""
    vals = [float(v or 0.0) for v in precip]
    out: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for i, t in enumerate(times):
        d = datetime.fromisoformat(t).date()
        out[d][0] += vals[i]
        out[d][1] = max(out[d][1], sum(vals[max(0, i - 2):i + 1]))
    return {d: (round(a, 2), round(b, 2)) for d, (a, b) in out.items()}


def records_for(point: GeoPoint, daily: dict[date, tuple[float, float]], issue: date, prov: str, model: str,
                past_days: int = 3) -> list[ForecastRainRecord]:
    recs: list[ForecastRainRecord] = []
    for k in range(past_days - 1, -1, -1):  # issue-2 .. issue as lead 0 (past/now)
        d = issue - timedelta(days=k)
        if d in daily:
            recs.append(ForecastRainRecord(point.tehsil_id, point.district_id, d, 0, daily[d][0], daily[d][1],
                                           prov, model))
    last: tuple[float, float] | None = None
    for lead in range(1, 8):
        d = issue + timedelta(days=lead)
        if d in daily:
            last = daily[d]
            recs.append(ForecastRainRecord(point.tehsil_id, point.district_id, d, lead, last[0], last[1], prov, model))
        elif last is not None:  # shorter forecast: persistence, flagged stale (spec edge case)
            recs.append(ForecastRainRecord(point.tehsil_id, point.district_id, d, lead, last[0], last[1], prov, model,
                                           is_stale=True, stale_since=issue))
    return recs


class OpenMeteoClient:
    def __init__(self, s: Settings = settings, http: httpx.Client | None = None, sleep=time.sleep) -> None:
        self.s = s
        self.http = http or httpx.Client(timeout=s.FORECAST_HTTP_TIMEOUT_S)
        self.sleep = sleep

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        last: Exception | None = None
        for attempt in range(self.s.FORECAST_HTTP_RETRIES + 1):
            try:
                r = self.http.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                if attempt < self.s.FORECAST_HTTP_RETRIES:
                    self.sleep((1, 3, 9)[min(attempt, 2)])
        raise ForecastSourceError(f"Open-Meteo unavailable: {last}")

    def fetch(self, points: list[GeoPoint], issue_date: date) -> list[ForecastRainRecord]:
        # tehsils without boundaries share their district centroid: request each distinct point once
        by_loc: dict[tuple[float, float], list[GeoPoint]] = defaultdict(list)
        for p in points:
            by_loc[(round(p.lat, 4), round(p.lon, 4))].append(p)
        locs = list(by_loc)
        model = f"open-meteo:{self.s.OPEN_METEO_MODEL}"
        out: list[ForecastRainRecord] = []
        for i in range(0, len(locs), self.s.FORECAST_BATCH_SIZE):
            batch = locs[i:i + self.s.FORECAST_BATCH_SIZE]
            params: dict[str, Any] = {
                "latitude": ",".join(f"{a:.4f}" for a, _ in batch),
                "longitude": ",".join(f"{b:.4f}" for _, b in batch),
                "hourly": "precipitation", "forecast_days": 8, "past_days": 3, "timezone": "UTC",
                "models": self.s.OPEN_METEO_MODEL,
            }
            if self.s.OPEN_METEO_API_KEY:
                params["apikey"] = self.s.OPEN_METEO_API_KEY
            data = self._get(f"{self.s.OPEN_METEO_BASE}/v1/forecast", params)
            bodies = data if isinstance(data, list) else [data]
            if len(bodies) != len(batch):
                raise ForecastSourceError(f"expected {len(batch)} locations, got {len(bodies)}")
            for loc, body in zip(batch, bodies, strict=True):
                h = body.get("hourly") or {}
                if "time" not in h or "precipitation" not in h:
                    raise ForecastSourceError("Open-Meteo response has no hourly precipitation")
                daily = daily_from_hourly(h["time"], h["precipitation"])
                for p in by_loc[loc]:
                    out += records_for(p, daily, issue_date, LIVE, model)
        return out

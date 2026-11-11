"""NASA POWER daily point API client (FR1) plus the replay cache reader."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.core.config import Settings, settings

POWER_PARAMS = ("PRECTOTCORR", "T2M", "GWETROOT", "GWETTOP")
# Units as POWER reports them (checked against a live response for Dadu, Aug 2022).
EXPECTED_UNITS = {"PRECTOTCORR": {"mm/day"}, "T2M": {"C"}, "GWETROOT": {"1"}, "GWETTOP": {"1"}}


@dataclass(frozen=True)
class PowerDay:
    obs_date: date
    values: dict[str, float | None]  # None = fill value or missing


@dataclass(frozen=True)
class PowerResponse:
    days: list[PowerDay]
    units: dict[str, str | None]


class SourceUnavailable(Exception):
    """Upstream could not be reached or returned no data after retries."""


class UnitMismatch(Exception):
    """Upstream changed units; refuse to store values we would misread."""


class NasaPowerClient(Protocol):
    def fetch_daily(self, lat: float, lon: float, start: date, end: date) -> PowerResponse: ...


def parse_power_json(doc: dict[str, Any]) -> PowerResponse:
    props = (doc.get("properties") or {}).get("parameter")
    if not props:
        msgs = doc.get("messages") or doc.get("detail") or "no properties.parameter in response"
        raise SourceUnavailable(f"NASA POWER returned no data: {msgs}")
    fill = float((doc.get("header") or {}).get("fill_value", -999.0))
    units = {p: ((doc.get("parameters") or {}).get(p) or {}).get("units") for p in POWER_PARAMS}
    keys = sorted(k for k in props.get("PRECTOTCORR", {}) if len(k) == 8 and k.isdigit())
    days: list[PowerDay] = []
    for key in keys:
        vals: dict[str, float | None] = {}
        for p in POWER_PARAMS:
            v = props.get(p, {}).get(key)
            vals[p] = None if v is None or float(v) <= fill + 0.5 else float(v)
        days.append(PowerDay(datetime.strptime(key, "%Y%m%d").date(), vals))
    return PowerResponse(days=days, units=units)


def check_units(units: dict[str, str | None]) -> None:
    for p, allowed in EXPECTED_UNITS.items():
        u = units.get(p)
        if u is not None and u not in allowed:
            raise UnitMismatch(f"{p} unit is {u!r}, expected one of {sorted(allowed)}")


class HttpNasaPowerClient:
    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, http: httpx.Client | None = None, s: Settings = settings, sleep=time.sleep) -> None:
        self.http = http or httpx.Client(timeout=s.POWER_TIMEOUT_S)
        self.s = s
        self.sleep = sleep

    def fetch_daily(self, lat: float, lon: float, start: date, end: date) -> PowerResponse:
        params = {
            "parameters": ",".join(POWER_PARAMS), "community": "AG",
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
            "format": "JSON", "time-standard": "UTC",
        }
        last: Exception | None = None
        for attempt in range(self.s.POWER_MAX_RETRIES + 1):
            try:
                r = self.http.get(self.s.NASA_POWER_BASE_URL, params=params)
                if r.status_code in self.RETRY_STATUS:
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                resp = parse_power_json(r.json())
                check_units(resp.units)
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                if attempt < self.s.POWER_MAX_RETRIES:
                    self.sleep(self.s.POWER_RETRY_BASE_S * 2 ** attempt)
        raise SourceUnavailable(f"{last} after {self.s.POWER_MAX_RETRIES} retries")

    def fetch_raw(self, lat: float, lon: float, start: date, end: date) -> dict[str, Any]:
        """Raw JSON for the replay cache (same request, no parsing)."""
        r = self.http.get(self.s.NASA_POWER_BASE_URL, params={
            "parameters": ",".join(POWER_PARAMS), "community": "AG",
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
            "format": "JSON", "time-standard": "UTC"})
        r.raise_for_status()
        return r.json()


def _load_outages(path: Path) -> list[tuple[date, date]]:
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in doc.get("outages", [])]


class CachedNasaPowerClient:
    """Replay: raw POWER JSON from data/replay/<key>/nasa_power/<pcode>.json with no lookahead."""

    def __init__(self, scenario_key: str, replay_today: date, lag_days: int, root: Path | None = None) -> None:
        self.dir = Path(root or settings.REPLAY_CACHE_ROOT) / scenario_key / "nasa_power"
        self.replay_today = replay_today
        self.cutoff = replay_today - timedelta(days=lag_days)
        self.outages = _load_outages(self.dir / "outages.json")

    def fetch_for(self, pcode: str, start: date, end: date) -> PowerResponse:
        if any(a <= self.replay_today <= b for a, b in self.outages):
            raise SourceUnavailable("simulated outage (replay)")
        path = self.dir / f"{pcode}.json"
        if not path.exists():
            raise SourceUnavailable(f"replay cache missing: {path}")
        resp = parse_power_json(json.loads(path.read_text(encoding="utf-8")))
        keep = [d for d in resp.days if start <= d.obs_date <= min(end, self.cutoff)]
        return PowerResponse(days=keep, units=resp.units)

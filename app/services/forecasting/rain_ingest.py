"""FR3 forecast-rain step (pipeline order 30). Live: Open-Meteo. Replay: a PERFECT_FORECAST_PROXY built from the
cached observed NASA POWER district rain (labelled; the spec prefers archived forecasts or IMERG, which are not
cached yet). Provider down: yesterday's forecast shifted one day, marked stale, admin alerted (UC03-2a)."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.scenario import current_scenario
from app.integrations.forecast_rain import (
    ForecastRainRecord,
    ForecastSourceError,
    GeoPoint,
    OpenMeteoClient,
    records_for,
)
from app.integrations.nasa_power import CachedNasaPowerClient, SourceUnavailable
from app.models.enums import DataSource
from app.models.geography import District
from app.models.ingestion import ClimateObservation as CO
from app.services.ingestion.observations import active_tehsils, scenario_key, upsert_observations
from app.services.ingestion.staleness import (
    Transition,
    notify_admin_source_down,
    source_status_id,
    update_source_status,
)
from app.services.stubs import get_alerts, get_audit

log = logging.getLogger("aab.forecasting")
PROXY = "PERFECT_FORECAST_PROXY"

POINTS_SQL = text("""
  SELECT t.id, t.district_id,
         COALESCE(ST_Y(ST_PointOnSurface(t.geom)), d.centroid_lat) AS lat,
         COALESCE(ST_X(ST_PointOnSurface(t.geom)), d.centroid_lon) AS lon
  FROM tehsil t JOIN district d ON d.id = t.district_id WHERE t.id = ANY(:ids)
""")


class ForecastRainSource(Protocol):
    def fetch(self, points: list[GeoPoint], issue_date: date) -> list[ForecastRainRecord]: ...


class ReplayProxyRainSource:
    """Forecast for target T = observed NASA POWER rain on T for the tehsil's district (hindsight, labelled)."""

    def __init__(self, db: Session, key: str) -> None:
        self.db, self.key = db, key

    def fetch(self, points: list[GeoPoint], issue_date: date) -> list[ForecastRainRecord]:
        pcodes = dict(self.db.execute(select(District.id, District.pcode)).all())
        cache = CachedNasaPowerClient(self.key, replay_today=date.max - timedelta(days=10), lag_days=0)
        series: dict[UUID, dict[date, tuple[float, None]]] = {}
        out: list[ForecastRainRecord] = []
        for p in points:
            if p.district_id not in series:
                try:
                    resp = cache.fetch_for(pcodes[p.district_id], issue_date - timedelta(days=3),
                                           issue_date + timedelta(days=7))
                except SourceUnavailable as exc:
                    raise ForecastSourceError(str(exc)) from exc
                series[p.district_id] = {d.obs_date: (d.values["PRECTOTCORR"], None) for d in resp.days
                                         if d.values["PRECTOTCORR"] is not None}
            out += records_for(p, series[p.district_id], issue_date, PROXY, "proxy:nasa_power_observed")
        return out


def tehsil_points(db: Session, district_ids: list[UUID] | None = None) -> list[GeoPoint]:
    tehsils = active_tehsils(db, district_ids, current_scenario())
    if not tehsils:
        return []
    rows = db.execute(POINTS_SQL, {"ids": [t.id for t in tehsils]}).all()
    return [GeoPoint(r.id, r.district_id, float(r.lat), float(r.lon)) for r in rows]


def shift_previous(db: Session, issue_date: date, points: list[GeoPoint]) -> list[ForecastRainRecord]:
    """Yesterday's lead d+1 becomes today's lead d; lead 7 repeats lead 6. All stale."""
    sid = current_scenario()
    prev_issue = issue_date - timedelta(days=1)
    by_tid = {p.tehsil_id: p for p in points}
    q = select(CO).where(CO.source == "FORECAST_RAIN", CO.tehsil_id.in_(list(by_tid)), CO.lead_day.between(1, 7),
                         CO.obs_date.between(prev_issue + timedelta(days=1), prev_issue + timedelta(days=7)),
                         CO.scenario_id.is_(None) if sid is None else CO.scenario_id == sid)
    prev = [r for r in db.scalars(q) if r.obs_date - timedelta(days=r.lead_day) == prev_issue]
    out: list[ForecastRainRecord] = []
    by_t: dict[UUID, dict[int, CO]] = {}
    for r in prev:
        by_t.setdefault(r.tehsil_id, {})[r.lead_day] = r
    for tid, leads in by_t.items():
        for lead in range(1, 8):
            src = leads.get(lead + 1) if lead < 7 else leads.get(7)  # lead 7 repeats lead 6
            if src is None:
                continue
            out.append(ForecastRainRecord(tid, by_tid[tid].district_id, issue_date + timedelta(days=lead), lead,
                                          src.rainfall_mm or 0.0, src.rainfall_max_3h_mm, src.provenance or "",
                                          src.forecast_model or "", is_stale=True,
                                          stale_since=src.stale_since or prev_issue))
    return out


class ForecastRainIngestService:
    source = DataSource.FORECAST_RAIN

    def __init__(self, live: ForecastRainSource | None = None, clock: Clock | None = None, alerts: Any = None) -> None:
        self.live, self._clock, self._alerts = live, clock, alerts

    def source_for(self, db: Session) -> ForecastRainSource:
        sid = current_scenario()
        if sid is not None:
            return ReplayProxyRainSource(db, scenario_key(db, sid))
        self.live = self.live or OpenMeteoClient()
        return self.live

    def ingest(self, db: Session, issue_date: date | None = None,
               district_ids: list[UUID] | None = None) -> dict[str, Any]:
        clock = self._clock or get_clock()
        issue_date = issue_date or clock.today()
        sid = current_scenario()
        points = tehsil_points(db, district_ids)
        error = None
        try:
            recs = self.source_for(db).fetch(points, issue_date)
        except ForecastSourceError as exc:
            error = str(exc)
            recs = shift_previous(db, issue_date, points)
        rows = [{"district_id": r.district_id, "tehsil_id": r.tehsil_id, "obs_date": r.target_date,
                 "lead_day": r.lead_day, "source": str(self.source), "rainfall_mm": r.rainfall_mm,
                 "rainfall_max_3h_mm": r.rainfall_max_3h_mm, "provenance": r.provenance,
                 "forecast_model": r.forecast_model, "is_stale": r.is_stale, "stale_since": r.stale_since,
                 "scenario_id": sid} for r in recs]
        upsert_observations(db, rows)
        healthy = error is None
        transition = update_source_status(db, source=self.source, healthy=healthy,
                                          last_valid_date=issue_date if healthy else None, error=error,
                                          now=clock.now(), scenario_id=sid)
        summary = {"source": str(self.source), "issue_date": str(issue_date), "rows": len(rows),
                   "tehsils": len(points), "is_healthy": healthy, "error": error,
                   "stale_rows": sum(1 for r in recs if r.is_stale),
                   "provenance": sorted({r.provenance for r in recs if r.provenance})}
        entity = ("ReplayScenario", sid) if sid else ("DataSourceStatus", source_status_id(db, self.source))
        get_audit().record(db, event_type="DATA_INGESTED", entity_type=entity[0], entity_id=entity[1],
                           actor="system", payload=summary)
        if transition is Transition.DOWN:
            get_audit().record(db, event_type="DATA_SOURCE_STALE", entity_type=entity[0], entity_id=entity[1],
                               actor="system", payload={"source": str(self.source), "error": error})
            notify_admin_source_down(db, self._alerts or get_alerts(), str(self.source), issue_date - timedelta(1),
                                     error)
        elif transition is Transition.UP:
            get_audit().record(db, event_type="DATA_SOURCE_RECOVERED", entity_type=entity[0], entity_id=entity[1],
                               actor="system", payload={"source": str(self.source)})
        db.commit()
        return summary


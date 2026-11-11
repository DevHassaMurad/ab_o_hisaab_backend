"""FR2: GPM IMERG rainfall per tehsil (daily total + max 3-hour burst), UC03 step 3. Reuses FR1's framework and
stale path. Live data comes from Earth Engine; replays read data/replay/<key>/imerg_daily.csv."""
from __future__ import annotations

import csv
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.config import settings
from app.core.db import try_advisory_xact_lock
from app.core.errors import Conflict
from app.core.scenario import current_scenario
from app.integrations.earth_engine import EarthEngineGateway, EEGateway, EEUnavailable, TehsilRain
from app.models.enums import DataSource
from app.models.geography import Tehsil
from app.services.ingestion.base import IngestResult
from app.services.ingestion.ee_boundaries import tehsil_features
from app.services.ingestion.observations import (
    active_tehsils,
    copy_forward_row,
    fresh_dates_in_db,
    get_valid_row,
    latest_valid_date,
    scenario_key,
    upsert_observations,
)
from app.services.ingestion.staleness import (
    Transition,
    notify_admin_source_down,
    plan_copy_forward,
    source_status_id,
    update_source_status,
)
from app.services.stubs import get_alerts, get_audit

FULL_DAY = 48


class ImergSource(Protocol):
    def image_count(self, day: date) -> int: ...
    def reduce(self, db: Session, day: date, tehsils: list[Tehsil]) -> list[TehsilRain]: ...


class LiveImergSource:
    def __init__(self, gateway: EarthEngineGateway) -> None:
        self.gw = gateway

    def image_count(self, day: date) -> int:
        return self.gw.image_count(day)

    def reduce(self, db: Session, day: date, tehsils: list[Tehsil]) -> list[TehsilRain]:
        return self.gw.reduce_imerg(day, tehsil_features(db, tehsils))


class CachedImergSource:
    """Replay: committed CSV (date,tehsil_pcode,rainfall_mm,rainfall_max_3h_mm,n_images); no lookahead."""

    def __init__(self, scenario_key: str, replay_today: date, lag_days: int, root: Path | None = None) -> None:
        self.path = Path(root or settings.REPLAY_CACHE_ROOT) / scenario_key / "imerg_daily.csv"
        self.cutoff = replay_today - timedelta(days=lag_days)
        self._rows: dict[date, dict[str, dict[str, str]]] | None = None

    def _load(self) -> dict[date, dict[str, dict[str, str]]]:
        if self._rows is None:
            if not self.path.exists():
                raise EEUnavailable(f"replay cache missing: {self.path}")
            out: dict[date, dict[str, dict[str, str]]] = {}
            with self.path.open(encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    out.setdefault(date.fromisoformat(r["date"]), {})[r["tehsil_pcode"]] = r
            self._rows = out
        return self._rows

    def image_count(self, day: date) -> int:
        if day > self.cutoff:
            return 0
        rows = self._load().get(day)
        return max(int(r.get("n_images") or FULL_DAY) for r in rows.values()) if rows else 0

    def reduce(self, db: Session, day: date, tehsils: list[Tehsil]) -> list[TehsilRain]:
        rows = self._load().get(day, {})
        out = []
        for t in tehsils:
            r = rows.get(t.pcode)
            if r is None or r.get("rainfall_mm") in (None, ""):
                continue
            mx = r.get("rainfall_max_3h_mm")
            out.append(TehsilRain(t.pcode, float(r["rainfall_mm"]), float(mx) if mx not in (None, "") else None, []))
        return out


class ImergIngestor:
    source = DataSource.GPM_IMERG

    def __init__(self, gateway: EarthEngineGateway | None = None, clock: Clock | None = None, audit: Any = None,
                 alerts: Any = None) -> None:
        self.gateway = gateway
        self._clock, self._audit, self._alerts = clock, audit, alerts

    @property
    def clock(self) -> Clock:
        return self._clock or get_clock()

    def source_for(self, db: Session, scenario_id: UUID | None, today: date) -> ImergSource:
        if scenario_id is None:
            self.gateway = self.gateway or EEGateway()
            return LiveImergSource(self.gateway)
        return CachedImergSource(scenario_key(db, scenario_id), today, settings.REPLAY_IMERG_LAG_DAYS)

    def ingest(self, db: Session, *, start: date | None = None, end: date | None = None,
               district_ids: list[UUID] | None = None) -> IngestResult:
        t0 = time.monotonic()
        audit, alerts = self._audit or get_audit(), self._alerts or get_alerts()
        today = self.clock.today()
        end = end or today - timedelta(days=1)
        start = start or end - timedelta(days=settings.IMERG_LOOKBACK_DAYS - 1)
        scenario_id = current_scenario()
        tehsils = active_tehsils(db, district_ids, scenario_id)
        src = self.source_for(db, scenario_id, today)
        by_pcode = {t.pcode: t for t in tehsils}

        # 1) Earth Engine first, sequential (quota), no DB writes yet
        per_day: dict[date, list[TehsilRain]] = {}
        days_info: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        d = start
        while d <= end and tehsils:
            try:
                n = src.image_count(d)
                due = d <= today - timedelta(days=settings.IMERG_EXPECTED_LAG_DAYS)
                if n >= FULL_DAY or (due and n >= settings.IMERG_MIN_IMAGES_PARTIAL):
                    rows = src.reduce(db, d, tehsils)
                    if n < FULL_DAY:
                        for r in rows:
                            r.flags.append(f"imerg_partial:{n}/{FULL_DAY}")
                    per_day[d] = rows
                    days_info.append({"date": str(d), "n_images": n,
                                      "status": "COMPLETE" if n >= FULL_DAY else "PARTIAL"})
                else:
                    days_info.append({"date": str(d), "n_images": n,
                                      "status": "OVERDUE_INCOMPLETE" if due else "NOT_YET_PUBLISHED"})
            except EEUnavailable as exc:
                errors.append({"scope": "earth_engine", "date": str(d), "error": str(exc)})
                break  # EE is down; don't hammer it
            d += timedelta(days=1)

        # 2) one transaction: lock, upsert, per-tehsil copy-forward, status, audit
        if not try_advisory_xact_lock(db, f"ingest:{self.source}"):
            raise Conflict("INGEST_ALREADY_RUNNING", "An IMERG ingest is already running.")
        res = IngestResult(source=str(self.source), start=start, end=end, scenario_id=scenario_id,
                           districts_requested=len({t.district_id for t in tehsils}), tehsils_requested=len(tehsils),
                           errors=errors, days=days_info)
        fresh_by_tehsil: dict[str, set[date]] = {}
        for day, rows in per_day.items():
            batch = []
            for r in rows:
                t = by_pcode.get(r.pcode)
                if t is None or r.rainfall_mm is None:
                    continue
                batch.append({"district_id": t.district_id, "tehsil_id": t.id, "obs_date": day,
                              "source": str(self.source), "rainfall_mm": max(r.rainfall_mm, 0.0),
                              "rainfall_max_3h_mm": r.rainfall_max_3h_mm, "quality_flags": r.flags,
                              "scenario_id": scenario_id})
                fresh_by_tehsil.setdefault(t.pcode, set()).add(day)
            res.rows_fresh += upsert_observations(db, batch)
        res.rows_not_yet_published = sum(len(tehsils) for x in days_info if x["status"] == "NOT_YET_PUBLISHED")

        latest: list[date] = []
        for t in tehsils:
            last_valid = latest_valid_date(db, self.source, scenario_id, district_id=t.district_id, tehsil_id=t.id,
                                           on_or_before=end)
            stale = plan_copy_forward(fresh_dates=fresh_by_tehsil.get(t.pcode, set()), last_valid=last_valid,
                                      window=(start, end), today=today,
                                      expected_lag=settings.IMERG_EXPECTED_LAG_DAYS,
                                      max_days=settings.IMERG_MAX_COPY_FORWARD_DAYS)
            existing = fresh_dates_in_db(db, self.source, scenario_id, start, end, district_id=t.district_id,
                                         tehsil_id=t.id)
            stale = [s for s in stale if s.obs_date not in existing]
            if stale:
                src_row = get_valid_row(db, self.source, scenario_id, last_valid, district_id=t.district_id,
                                        tehsil_id=t.id)
                upsert_observations(db, [copy_forward_row(src_row, s.obs_date) for s in stale])
            res.rows_stale += len(stale)
            if last_valid:
                latest.append(last_valid)

        res.is_healthy = not errors and res.rows_stale == 0
        res.last_valid_date = min(latest) if latest else None
        err_text = "; ".join(e["error"] for e in errors[:3]) or None
        transition = update_source_status(db, source=self.source, healthy=res.is_healthy,
                                          last_valid_date=res.last_valid_date, error=err_text, now=self.clock.now(),
                                          scenario_id=scenario_id)
        entity_type, entity_id = (("ReplayScenario", scenario_id) if scenario_id
                                  else ("DataSourceStatus", source_status_id(db, self.source)))
        res.duration_s = round(time.monotonic() - t0, 2)
        res.extra = {"dataset": settings.IMERG_COLLECTION, "band": settings.IMERG_BAND,
                     "scale_m": settings.IMERG_REDUCE_SCALE_M}
        audit.record(db, event_type="DATA_INGESTED", entity_type=entity_type, entity_id=entity_id, actor="system",
                     payload=res.audit_payload())
        if transition is Transition.DOWN:
            audit.record(db, event_type="DATA_SOURCE_STALE", entity_type=entity_type, entity_id=entity_id,
                         actor="system", payload={"source": str(self.source),
                                                  "last_valid_date": str(res.last_valid_date)})
            notify_admin_source_down(db, alerts, str(self.source), res.last_valid_date, err_text)
        elif transition is Transition.UP:
            audit.record(db, event_type="DATA_SOURCE_RECOVERED", entity_type=entity_type, entity_id=entity_id,
                         actor="system", payload={"source": str(self.source),
                                                  "last_valid_date": str(res.last_valid_date)})
        db.commit()
        return res

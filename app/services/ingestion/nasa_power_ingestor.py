"""FR1: daily NASA POWER ingest for every active district (UC03 steps 1, 2, 5, 6 and alt 2a)."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.core.clock import Clock, get_clock
from app.core.config import settings
from app.core.db import try_advisory_xact_lock
from app.core.errors import Conflict
from app.core.scenario import current_scenario
from app.integrations.nasa_power import (
    CachedNasaPowerClient,
    HttpNasaPowerClient,
    PowerResponse,
    SourceUnavailable,
    UnitMismatch,
)
from app.models.enums import DataSource
from app.models.geography import District
from app.services.ingestion.base import IngestResult
from app.services.ingestion.observations import (
    active_districts,
    copy_forward_row,
    fresh_dates_in_db,
    get_valid_row,
    latest_valid_date,
    scenario_key,
    upsert_observations,
)
from app.services.ingestion.staleness import (
    Transition,
    count_not_due,
    notify_admin_source_down,
    plan_copy_forward,
    source_status_id,
    update_source_status,
)
from app.services.ingestion.validation import ValidatedDay, validate_power_day
from app.services.stubs import get_alerts, get_audit


class _LiveAdapter:
    def __init__(self, client: HttpNasaPowerClient) -> None:
        self.client = client

    def fetch(self, d: District, start: date, end: date) -> PowerResponse:
        return self.client.fetch_daily(d.centroid_lat, d.centroid_lon, start, end)


class _CachedAdapter:
    def __init__(self, client: CachedNasaPowerClient) -> None:
        self.client = client

    def fetch(self, d: District, start: date, end: date) -> PowerResponse:
        return self.client.fetch_for(d.pcode, start, end)


class NasaPowerIngestor:
    source = DataSource.NASA_POWER

    def __init__(self, clock: Clock | None = None, audit: Any = None, alerts: Any = None,
                 http: httpx.Client | None = None, live_client: HttpNasaPowerClient | None = None) -> None:
        self._clock = clock
        self._audit = audit
        self._alerts = alerts
        self._live = live_client or (HttpNasaPowerClient(http) if http else None)

    @property
    def clock(self) -> Clock:
        return self._clock or get_clock()

    @property
    def audit(self) -> Any:
        return self._audit or get_audit()

    @property
    def alerts(self) -> Any:
        return self._alerts or get_alerts()

    def client_for(self, db: Session, scenario_id: UUID | None, today: date):
        if scenario_id is None:
            self._live = self._live or HttpNasaPowerClient()
            return _LiveAdapter(self._live)
        return _CachedAdapter(CachedNasaPowerClient(scenario_key(db, scenario_id), today,
                                                    settings.REPLAY_POWER_LAG_DAYS))

    def ingest(self, db: Session, *, start: date | None = None, end: date | None = None,
               district_ids: list[UUID] | None = None) -> IngestResult:
        t0 = time.monotonic()
        today = self.clock.today()
        end = end or today - timedelta(days=1)
        start = start or end - timedelta(days=settings.POWER_LOOKBACK_DAYS - 1)
        scenario_id = current_scenario()
        districts = active_districts(db, district_ids, scenario_id)
        client = self.client_for(db, scenario_id, today)

        # 1) network first, outside any write
        fetched: dict[UUID, PowerResponse] = {}
        errors: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=settings.POWER_MAX_WORKERS) as pool:
            futs = {pool.submit(client.fetch, d, start, end): d for d in districts}
            for f in as_completed(futs):
                d = futs[f]
                try:
                    fetched[d.id] = f.result()
                except (SourceUnavailable, UnitMismatch) as exc:
                    errors.append({"district_pcode": d.pcode, "error": str(exc)})

        # 2) one transaction: lock, upsert, copy-forward, status, audit
        if not try_advisory_xact_lock(db, f"ingest:{self.source}"):
            raise Conflict("INGEST_ALREADY_RUNNING", "A NASA POWER ingest is already running.")
        res = IngestResult(source=str(self.source), start=start, end=end, scenario_id=scenario_id,
                           districts_requested=len(districts), errors=sorted(errors, key=lambda e: e["district_pcode"]))
        latest: list[date] = []
        for d in districts:
            days: list[ValidatedDay] = [validate_power_day(x) for x in fetched[d.id].days] if d.id in fetched else []
            fresh = [v for v in days if v.is_fresh]
            res.values_rejected += sum(1 for v in days for fl in v.flags if "out_of_range" in fl)
            upsert_observations(db, [self._row(d, v, scenario_id) for v in fresh])
            res.rows_fresh += len(fresh)
            last_valid = latest_valid_date(db, self.source, scenario_id, district_id=d.id, on_or_before=end)
            fresh_set = {v.obs_date for v in fresh}
            stale = plan_copy_forward(fresh_dates=fresh_set, last_valid=last_valid, window=(start, end),
                                      today=today, expected_lag=settings.POWER_EXPECTED_LAG_DAYS,
                                      max_days=settings.POWER_MAX_COPY_FORWARD_DAYS)
            existing = fresh_dates_in_db(db, self.source, scenario_id, start, end, district_id=d.id)
            stale = [s for s in stale if s.obs_date not in existing]  # never overwrite fresh with stale
            if stale:
                src_row = get_valid_row(db, self.source, scenario_id, last_valid, district_id=d.id)
                upsert_observations(db, [copy_forward_row(src_row, s.obs_date) for s in stale])
            res.rows_stale += len(stale)
            res.rows_not_yet_published += count_not_due(start, end, today, settings.POWER_EXPECTED_LAG_DAYS,
                                                        fresh_set | existing)
            if last_valid:
                latest.append(last_valid)

        res.is_healthy = not errors and res.rows_stale == 0
        res.last_valid_date = min(latest) if latest else None
        err_text = "; ".join(f"{e['district_pcode']}: {e['error']}" for e in res.errors[:3]) or None
        transition = update_source_status(db, source=self.source, healthy=res.is_healthy,
                                          last_valid_date=res.last_valid_date, error=err_text,
                                          now=self.clock.now(), scenario_id=scenario_id)
        entity_type, entity_id = (("ReplayScenario", scenario_id) if scenario_id
                                  else ("DataSourceStatus", source_status_id(db, self.source)))
        res.duration_s = round(time.monotonic() - t0, 2)
        self.audit.record(db, event_type="DATA_INGESTED", entity_type=entity_type, entity_id=entity_id,
                          actor="system", payload=res.audit_payload())
        if transition is Transition.DOWN:
            self.audit.record(db, event_type="DATA_SOURCE_STALE", entity_type=entity_type, entity_id=entity_id,
                              actor="system", payload={"source": str(self.source),
                                                       "last_valid_date": str(res.last_valid_date)})
            notify_admin_source_down(db, self.alerts, str(self.source), res.last_valid_date, err_text)
        elif transition is Transition.UP:
            self.audit.record(db, event_type="DATA_SOURCE_RECOVERED", entity_type=entity_type, entity_id=entity_id,
                              actor="system", payload={"source": str(self.source),
                                                       "last_valid_date": str(res.last_valid_date)})
        db.commit()
        return res

    def _row(self, d: District, v: ValidatedDay, scenario_id: UUID | None) -> dict[str, Any]:
        return {"district_id": d.id, "obs_date": v.obs_date, "source": str(self.source), "rainfall_mm": v.rainfall_mm,
                "temp_c": v.temp_c, "soil_wetness": v.soil_wetness, "soil_wetness_top": v.soil_wetness_top,
                "quality_flags": v.flags, "is_stale": False, "stale_since": None, "scenario_id": scenario_id}

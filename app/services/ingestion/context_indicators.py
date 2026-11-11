"""FR1 US4: load curated district indicators (CSV), refresh the World Bank national reference, recompute
District.vulnerability_weight with an audit entry for every change."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import try_advisory_xact_lock
from app.core.errors import Conflict, Unprocessable
from app.integrations import world_bank
from app.models.enums import DataSource
from app.models.geography import District
from app.models.ingestion import ContextIndicator
from app.services.ingestion.staleness import update_source_status
from app.services.ingestion.vulnerability import compute_vulnerability
from app.services.stubs import get_audit

REQUIRED = ("district_pcode", "indicator", "value", "period", "source")


@dataclass
class RefreshResult:
    indicators_upserted: int = 0
    districts_updated: int = 0
    imputed_districts: list[str] = field(default_factory=list)
    world_bank_healthy: bool = True
    changes: list[dict[str, Any]] = field(default_factory=list)


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise Unprocessable("INDICATOR_FILE_INVALID", f"Indicator file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if rows and not set(REQUIRED) <= set(rows[0]):
        raise Unprocessable("INDICATOR_FILE_INVALID", f"Columns must be {', '.join(REQUIRED)}")
    return rows


class ContextIndicatorService:
    def __init__(self, audit: Any = None, http: httpx.Client | None = None) -> None:
        self.audit = audit or get_audit()
        self.http = http

    def refresh(self, db: Session, path: Path | None = None, now: datetime | None = None) -> RefreshResult:
        now = now or datetime.now(UTC)
        if not try_advisory_xact_lock(db, "ingest:CONTEXT"):
            raise Conflict("INGEST_ALREADY_RUNNING", "A context refresh is already running.")
        res = RefreshResult()
        districts = {d.pcode: d for d in db.scalars(select(District)).all()}
        rows = load_csv(Path(path or settings.CONTEXT_INDICATORS_FILE))
        for r in rows:
            d = districts.get(r["district_pcode"])
            if d is None:
                continue
            stmt = insert(ContextIndicator).values(district_id=d.id, indicator=r["indicator"],
                                                   value=float(r["value"]), period=r["period"], source=r["source"])
            db.execute(stmt.on_conflict_do_update(constraint="uq_context_indicator",
                                                  set_={"value": stmt.excluded.value}))
            res.indicators_upserted += 1

        try:  # national reference value, informational only
            ref = world_bank.fetch_indicator(settings.WORLD_BANK_POVERTY_CODE, now.year - 10, now.year, http=self.http)
            update_source_status(db, source=DataSource.WORLD_BANK, healthy=True, last_valid_date=None, error=None,
                                 now=now, scenario_id=None)
            reference = {"code": settings.WORLD_BANK_POVERTY_CODE, "values": ref}
        except (httpx.HTTPError, ValueError) as exc:
            res.world_bank_healthy = False
            update_source_status(db, source=DataSource.WORLD_BANK, healthy=False, last_valid_date=None,
                                 error=str(exc), now=now, scenario_id=None)
            reference = None

        active = [d for d in districts.values() if d.is_active]
        latest: dict[Any, dict[str, float | None]] = {d.id: {} for d in active}
        for ci in db.scalars(select(ContextIndicator).where(ContextIndicator.district_id.in_([d.id for d in active]))
                             .order_by(ContextIndicator.period)).all():
            latest[ci.district_id][ci.indicator] = ci.value  # latest period wins (ordered ascending)
        if not active:
            db.commit()
            return res
        results = compute_vulnerability(latest, {d.id: d.province for d in active}, settings.VULNERABILITY_WEIGHTS,
                                        settings.VULNERABILITY_INVERTED)
        for d in active:
            vr = results[d.id]
            if vr.imputed:
                res.imputed_districts.append(d.pcode)
            old = d.vulnerability_weight
            d.vulnerability_inputs = {"computed_at": now.isoformat(), "weights": settings.VULNERABILITY_WEIGHTS,
                                      "indicators": vr.inputs, "imputed": vr.imputed, "reference": reference}
            if old is None or abs(old - vr.weight) > 1e-9:
                d.vulnerability_weight = vr.weight
                res.districts_updated += 1
                res.changes.append({"district_pcode": d.pcode, "old": old, "new": vr.weight})
                self.audit.record(db, event_type="VULNERABILITY_WEIGHT_UPDATED", entity_type="District",
                                  entity_id=d.id, actor="system",
                                  payload={"district_pcode": d.pcode, "old": old, "new": vr.weight,
                                           "imputed": vr.imputed})
        self.audit.record(db, event_type="CONTEXT_INDICATORS_REFRESHED", entity_type="ContextIndicator",
                          entity_id=None, actor="system",
                          payload={"indicators_upserted": res.indicators_upserted,
                                   "districts_updated": res.districts_updated,
                                   "world_bank_healthy": res.world_bank_healthy})
        db.commit()
        return res

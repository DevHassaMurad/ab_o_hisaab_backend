"""Shared ingestion framework (owned by FR1, used by FR2)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.enums import DataSource


class IngestError(Exception):
    pass


@dataclass
class IngestResult:
    source: str
    start: date
    end: date
    scenario_id: UUID | None = None
    districts_requested: int = 0
    tehsils_requested: int | None = None
    rows_fresh: int = 0
    rows_stale: int = 0
    rows_not_yet_published: int = 0
    values_rejected: int = 0
    last_valid_date: date | None = None
    is_healthy: bool = True
    errors: list[dict[str, Any]] = field(default_factory=list)
    days: list[dict[str, Any]] | None = None
    duration_s: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("extra")
        return {k: v for k, v in d.items() if v is not None}

    def audit_payload(self) -> dict[str, Any]:
        p = self.summary()
        p["errors"] = p.get("errors", [])[:5]
        p.update(self.extra)
        return p


class Ingestor(Protocol):
    source: DataSource

    def ingest(self, db: Session, *, start: date | None = None, end: date | None = None,
               district_ids: list[UUID] | None = None) -> IngestResult: ...

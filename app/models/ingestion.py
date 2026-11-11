from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, ScenarioMixin, TimestampMixin


class ClimateObservation(IdMixin, TimestampMixin, ScenarioMixin, Base):
    __tablename__ = "climate_observation"
    # The real constraint is UNIQUE NULLS NOT DISTINCT (created in the migration); this declaration names it.
    __table_args__ = (
        UniqueConstraint("district_id", "tehsil_id", "obs_date", "source", "lead_day", "scenario_id",
                         name="uq_climate_observation"),
    )

    district_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("district.id"), index=True)
    tehsil_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tehsil.id"), index=True)
    obs_date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    rainfall_mm: Mapped[float | None] = mapped_column(Float)
    rainfall_max_3h_mm: Mapped[float | None] = mapped_column(Float)
    temp_c: Mapped[float | None] = mapped_column(Float)
    soil_wetness: Mapped[float | None] = mapped_column(Float)
    soil_wetness_top: Mapped[float | None] = mapped_column(Float)
    lead_day: Mapped[int | None] = mapped_column(Integer)
    provenance: Mapped[str | None] = mapped_column(String(32))
    forecast_model: Mapped[str | None] = mapped_column(String(64))
    quality_flags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    stale_since: Mapped[date | None] = mapped_column(Date)


class ContextIndicator(IdMixin, TimestampMixin, Base):
    __tablename__ = "context_indicator"
    __table_args__ = (UniqueConstraint("district_id", "indicator", "period", "source", name="uq_context_indicator"),)

    district_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("district.id"), index=True)
    indicator: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    period: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))


class DataSourceStatus(IdMixin, TimestampMixin, Base):
    __tablename__ = "data_source_status"

    source: Mapped[str] = mapped_column(String(16), unique=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_valid_date: Mapped[date | None] = mapped_column(Date)
    last_error: Mapped[str | None] = mapped_column(Text)
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

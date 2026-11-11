from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, ScenarioMixin, TimestampMixin


class FloodForecast(IdMixin, TimestampMixin, ScenarioMixin, Base):
    """Probability per tehsil (or district aggregate when tehsil_id is NULL) per run date and lead day.
    `probability` is the lead-weighted value that drives the score (p_adj); the raw model value is in features."""

    __tablename__ = "flood_forecast"
    __table_args__ = (UniqueConstraint("district_id", "tehsil_id", "run_date", "lead_day", "scenario_id",
                                       name="uq_flood_forecast"),)

    district_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("district.id"), index=True)
    tehsil_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tehsil.id"))
    run_date: Mapped[date] = mapped_column(Date, index=True)
    lead_day: Mapped[int] = mapped_column(Integer)
    target_date: Mapped[date] = mapped_column(Date)
    probability: Mapped[float] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(16))
    model_version: Mapped[str] = mapped_column(String(64))
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    forecast_provenance: Mapped[str | None] = mapped_column(String(32))
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    is_stale_input: Mapped[bool] = mapped_column(Boolean, default=False)


class RiskIndex(IdMixin, TimestampMixin, ScenarioMixin, Base):
    __tablename__ = "risk_index"
    __table_args__ = (UniqueConstraint("district_id", "tehsil_id", "run_date", "scenario_id", name="uq_risk_index"),)

    district_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("district.id"), index=True)
    tehsil_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tehsil.id"))
    run_date: Mapped[date] = mapped_column(Date, index=True)
    score: Mapped[int] = mapped_column(Integer)
    peak_lead_day: Mapped[int] = mapped_column(Integer)
    tier: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))
    model_version: Mapped[str] = mapped_column(String(64))
    forecast_provenance: Mapped[str | None] = mapped_column(String(32))
    explanation_en: Mapped[str] = mapped_column(Text, default="")
    explanation_ur: Mapped[str | None] = mapped_column(Text)
    top_factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    aggregation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    is_stale_input: Mapped[bool] = mapped_column(Boolean, default=False)
    stale_since: Mapped[date | None] = mapped_column(Date)

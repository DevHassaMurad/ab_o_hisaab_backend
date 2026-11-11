from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, IdMixin, ScenarioMixin, TimestampMixin

Money = Numeric(14, 2)


class DisasterFund(IdMixin, TimestampMixin, ScenarioMixin, Base):
    """One fund per district per scenario (live = scenario_id NULL)."""

    __tablename__ = "disaster_fund"
    __table_args__ = (UniqueConstraint("district_id", "scenario_id", name="uq_disaster_fund_district"),)

    district_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("district.id"), index=True)
    allocated_pkr: Mapped[Decimal] = mapped_column(Money, default=Decimal("0.00"))
    reserved_pkr: Mapped[Decimal] = mapped_column(Money, default=Decimal("0.00"))
    disbursed_pkr: Mapped[Decimal] = mapped_column(Money, default=Decimal("0.00"))
    remaining_pkr: Mapped[Decimal] = mapped_column(Money, default=Decimal("0.00"))
    household_share_pct: Mapped[int] = mapped_column(Integer, default=70)
    pool_spillover_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    household_amount_by_tier: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    shopkeeper_cap_pkr: Mapped[Decimal] = mapped_column(Money, default=Decimal("50000.00"))
    shopkeeper_stock_pct: Mapped[int] = mapped_column(Integer, default=40)
    ranking_weights: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    event_close_days: Mapped[int] = mapped_column(Integer, default=14)
    version: Mapped[int] = mapped_column(Integer, default=1)

    tiers: Mapped[list[TriggerTier]] = relationship(back_populates="fund", order_by="TriggerTier.min_score")


class TriggerTier(IdMixin, TimestampMixin, Base):
    __tablename__ = "trigger_tier"
    __table_args__ = (UniqueConstraint("fund_id", "name", name="uq_trigger_tier_name"),)

    fund_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("disaster_fund.id", ondelete="CASCADE"),
                                               index=True)
    name: Mapped[str] = mapped_column(String(16))
    min_score: Mapped[int] = mapped_column(Integer)
    release_pct: Mapped[int] = mapped_column(Integer)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)  # never writable via API (Principle II)

    fund: Mapped[DisasterFund] = relationship(back_populates="tiers")

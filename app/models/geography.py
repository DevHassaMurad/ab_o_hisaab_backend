from __future__ import annotations

import uuid
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, IdMixin, TimestampMixin


class District(IdMixin, TimestampMixin, Base):
    __tablename__ = "district"

    pcode: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    province: Mapped[str] = mapped_column(String(16), index=True)
    flood_mode: Mapped[str] = mapped_column(String(16))
    vulnerability_weight: Mapped[float] = mapped_column(Float, default=0.5)
    vulnerability_inputs: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=4326), nullable=True)
    centroid_lat: Mapped[float] = mapped_column(Float)
    centroid_lon: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    tehsils: Mapped[list[Tehsil]] = relationship(back_populates="district")


class Tehsil(IdMixin, TimestampMixin, Base):
    __tablename__ = "tehsil"

    pcode: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    district_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("district.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    population: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elevation_mean_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    slope_mean_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    geom = mapped_column(Geometry("MULTIPOLYGON", srid=4326), nullable=True)

    district: Mapped[District] = relationship(back_populates="tehsils")

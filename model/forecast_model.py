"""PDMA forecasting tables (see migration 0012). Geographic risk only: no beneficiary data lives here."""
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base


class ForecastDistrict(Base):
    __tablename__ = "forecast_districts"
    pcode: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    province: Mapped[str] = mapped_column(String(100))
    province_pcode: Mapped[str | None] = mapped_column(String(12), nullable=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    area_km2: Mapped[float] = mapped_column(Float)
    flood_mode: Mapped[str] = mapped_column(String(10))
    hist_floods_train: Mapped[int] = mapped_column(Integer, default=0)
    gwt_q99: Mapped[float | None] = mapped_column(Float, nullable=True)
    gwr_q99: Mapped[float | None] = mapped_column(Float, nullable=True)
    gwp_q99: Mapped[float | None] = mapped_column(Float, nullable=True)
    clim_rain_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    clim_rain_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    climatology_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    __table_args__ = (Index("ix_forecast_runs_kind", "kind", "started_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10))
    trigger: Mapped[str] = mapped_column(String(10), default="schedule")
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    records: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class HazardObservation(Base):
    __tablename__ = "hazard_observations"
    __table_args__ = (UniqueConstraint("district_pcode", "variable", "observation_date", "source",
                                       name="uq_hazard_obs_once"),
                      Index("ix_hazard_obs_date", "observation_date"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district_pcode: Mapped[str] = mapped_column(ForeignKey("forecast_districts.pcode", name="fk_hazard_obs_district"))
    variable: Mapped[str] = mapped_column(String(30))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(40))
    observation_date: Mapped[date] = mapped_column(Date)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime)


class HazardForecast(Base):
    __tablename__ = "hazard_forecasts"
    __table_args__ = (UniqueConstraint("district_pcode", "variable", "forecast_date", "source",
                                       name="uq_hazard_fc_once"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district_pcode: Mapped[str] = mapped_column(ForeignKey("forecast_districts.pcode", name="fk_hazard_fc_district"))
    variable: Mapped[str] = mapped_column(String(30))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(40))
    forecast_date: Mapped[date] = mapped_column(Date)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime)


class DistrictRiskAssessment(Base):
    __tablename__ = "district_risk_assessments"
    __table_args__ = (Index("ix_risk_district_time", "district_pcode", "calculated_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    district_pcode: Mapped[str] = mapped_column(ForeignKey("forecast_districts.pcode", name="fk_risk_district"))
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_category: Mapped[str] = mapped_column(String(15))
    model_version: Mapped[str] = mapped_column(String(60))
    primary_indicator: Mapped[str | None] = mapped_column(String(60), nullable=True)
    data_completeness: Mapped[float] = mapped_column(Float)
    calculation_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_runs.id", name="fk_risk_run"), nullable=True)


class HazardEvent(Base):
    __tablename__ = "hazard_events"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_hazard_events_once"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    event_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    district_pcode: Mapped[str | None] = mapped_column(String(12), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime)

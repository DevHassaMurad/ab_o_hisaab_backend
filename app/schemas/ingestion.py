from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IngestRunRequest(BaseModel):
    source: str
    start: date | None = None
    end: date | None = None
    district_pcodes: list[str] | None = None

    @model_validator(mode="after")
    def _order(self) -> IngestRunRequest:
        if self.start and self.end and self.start > self.end:
            raise ValueError("start must be on or before end")
        return self


class IngestRunResult(BaseModel):
    source: str
    start: date
    end: date
    scenario_id: UUID | None = None
    districts_requested: int
    tehsils_requested: int | None = None
    rows_fresh: int
    rows_stale: int
    rows_not_yet_published: int
    values_rejected: int = 0
    days: list[dict[str, Any]] | None = None
    last_valid_date: date | None = None
    is_healthy: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)
    duration_s: float


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    district_id: UUID | None
    tehsil_id: UUID | None
    obs_date: date
    source: str
    rainfall_mm: float | None
    rainfall_max_3h_mm: float | None
    temp_c: float | None
    soil_wetness: float | None
    soil_wetness_top: float | None
    quality_flags: list[str]
    is_stale: bool
    stale_since: date | None


class ObservationPage(BaseModel):
    items: list[ObservationOut]
    total: int
    page: int
    page_size: int


class ContextIndicatorOut(BaseModel):
    indicator: str
    value: float
    period: str
    source: str


class ContextIndicatorsOut(BaseModel):
    district_id: UUID
    vulnerability_weight: float
    vulnerability_inputs: dict[str, Any] | None
    items: list[ContextIndicatorOut]


class ContextRefreshOut(BaseModel):
    indicators_upserted: int
    districts_updated: int
    imputed_districts: list[str]
    world_bank_healthy: bool
    changes: list[dict[str, Any]]


class DataSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    is_healthy: bool
    last_success_at: datetime | None
    last_valid_date: date | None
    last_error: str | None

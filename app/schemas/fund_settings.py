from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TierIn(BaseModel):
    name: str
    min_score: int
    release_pct: int
    requires_approval: Any = None  # present only to catch attempts to change it (422 REQUIRES_APPROVAL_LOCKED)


class FundSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    allocated_pkr: Decimal
    tiers: list[TierIn]
    household_share_pct: int
    pool_spillover_enabled: bool = True
    shopkeeper_cap_pkr: Decimal
    shopkeeper_stock_pct: int
    household_amount_by_tier: dict[str, Decimal]
    event_close_days: int = 14


class CopiedFrom(BaseModel):
    district_id: UUID
    district_name: str
    version: int


class FundSettingsDraft(FundSettingsIn):
    copied_from: CopiedFrom

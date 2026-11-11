"""FR6 shared tier rule: FR3 (RiskIndex.tier) and FR7 (firing) must agree exactly. score >= min_score enters a tier."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.scenario import current_scenario
from app.models.funds import DisasterFund, TriggerTier

TierName = Literal["WATCH", "WARNING", "ACTION"]
TierLevel = Literal["NONE", "WATCH", "WARNING", "ACTION"]
TIER_ORDER: tuple[TierName, ...] = ("WATCH", "WARNING", "ACTION")
_RANK = {"NONE": 0, "WATCH": 1, "WARNING": 2, "ACTION": 3}


def tier_rank(level: str) -> int:
    return _RANK[level]


@dataclass(frozen=True)
class TierRule:
    name: TierName
    min_score: int
    release_pct: int
    requires_approval: bool


@dataclass(frozen=True)
class TierConfig:
    rules: tuple[TierRule, ...]  # in TIER_ORDER

    def rule(self, name: str) -> TierRule:
        return next(r for r in self.rules if r.name == name)

    def cumulative_pct(self, name: str) -> int:
        return sum(r.release_pct for r in self.rules[: TIER_ORDER.index(name) + 1])  # type: ignore[arg-type]

    def tiers_up_to(self, level: str) -> list[TierRule]:
        return [r for r in self.rules if tier_rank(r.name) <= tier_rank(level)]

    def thresholds(self) -> dict[str, int]:
        return {r.name: r.min_score for r in self.rules}


def tier_for_score(score: int, config: TierConfig) -> TierLevel:
    level: TierLevel = "NONE"
    for rule in config.rules:
        if score >= rule.min_score:
            level = rule.name
    return level


def get_fund(db: Session, district_id: UUID, *, for_update: bool = False) -> DisasterFund | None:
    sid = current_scenario()
    q = select(DisasterFund).where(DisasterFund.district_id == district_id,
                                   DisasterFund.scenario_id.is_(None) if sid is None
                                   else DisasterFund.scenario_id == sid)
    return db.scalar(q.with_for_update() if for_update else q)


def load_tier_config(db: Session, fund: DisasterFund) -> TierConfig:
    rows = {t.name: t for t in db.scalars(select(TriggerTier).where(TriggerTier.fund_id == fund.id))}
    return TierConfig(tuple(TierRule(n, rows[n].min_score, rows[n].release_pct, rows[n].requires_approval)
                            for n in TIER_ORDER))


def thresholds_for_district(db: Session, district_id: UUID) -> dict[str, int] | None:
    """Registered with FR3 so scores map to the district's configured thresholds (scenario-scoped)."""
    fund = get_fund(db, district_id)
    return load_tier_config(db, fund).thresholds() if fund else None

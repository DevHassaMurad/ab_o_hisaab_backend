"""FR3 pure scoring helpers: fallback rule (UC04-3a), rounding, lead weights, tiers, top factors, aggregation."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from app.services.forecasting.modes import ModeSpec

FALLBACK_VERSION = "rules-fallback-1"


def round_half_up_score(p: float) -> int:
    """Python's round() is banker's rounding (round(0.5) == 0); 0.405 must give 41."""
    s = (Decimal(str(p)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(min(100, max(0, s)))


def fallback_score(features: dict[str, float], spec: ModeSpec) -> tuple[float, dict[str, float]]:
    """p = clip(sum_k w_k * min(1, x_k / ref_k), 0, 1). Returns p and each term's contribution."""
    contrib: dict[str, float] = {}
    for t in spec.fallback.terms:
        x = features.get(t.feature, 0.0) or 0.0
        if t.alt_feature:
            x = max(x, features.get(t.alt_feature, 0.0) or 0.0)
        contrib[t.feature] = t.weight * min(1.0, max(0.0, x) / t.ref)
    return min(1.0, max(0.0, sum(contrib.values()))), contrib


def lead_weight(spec: ModeSpec, lead: int) -> float:
    return spec.lead_weights[int(lead)]


def peak(lead_probs: dict[int, float]) -> tuple[int, float]:
    best = max(lead_probs.values())
    return min(lead for lead, v in lead_probs.items() if v == best), best  # earliest lead on ties


def tier_for(score: int, thresholds: dict[str, int]) -> str:
    tier = "NONE"
    for name in ("WATCH", "WARNING", "ACTION"):
        if score >= thresholds[name]:
            tier = name
    return tier


def top_factors(contrib: dict[str, float], features: dict[str, float], k: int = 3) -> list[dict[str, Any]]:
    pos = {f: v for f, v in contrib.items() if v > 0 and f != "lead_day"}
    total = sum(pos.values())
    if not total:
        return []
    ranked = sorted(pos, key=lambda f: (-pos[f], f))[:k]
    return [{"factor": f, "value": round(float(features.get(f, 0.0)), 2), "contribution": round(pos[f] / total, 2)}
            for f in ranked]


@dataclass
class DistrictAgg:
    p_by_lead: dict[int, float]
    source_by_lead: dict[int, UUID]
    eligible: list[UUID]
    rule: str = "max_pop_share"
    min_share: float = 0.10


def aggregate_district(tehsil_p: list[tuple[UUID, int, float]], pops: dict[UUID, int | None],
                       min_share: float) -> DistrictAgg:
    """Per lead day: max over tehsils holding >= min_share of the district population; all tehsils if none
    qualifies or populations are missing (FR3-007)."""
    total = sum(v for v in pops.values() if v)
    eligible = [t for t, v in pops.items() if total and v and v / total >= min_share] or list(pops)
    p_by_lead: dict[int, float] = {}
    src: dict[int, UUID] = {}
    for tid, lead, p in sorted(tehsil_p, key=lambda x: (x[1], str(x[0]))):
        if tid in eligible and (lead not in p_by_lead or p > p_by_lead[lead]):
            p_by_lead[lead], src[lead] = p, tid
    return DistrictAgg(p_by_lead, src, eligible, min_share=min_share)

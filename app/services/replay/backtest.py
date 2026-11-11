"""FR5 backtest evaluation (NFR3). TRIGGER: the hero district must fire, at least min_lead_days before the
reference onset. NO_TRIGGER: no release anywhere and every district below its Watch threshold every day.

Until FR7 (fund releases) exists, the "fire" event is the first day a district's score reached each tier
(`basis: tier_crossing`); afterwards it is the first FundRelease (`basis: fund_release`). The report says which."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.scenario import use_scenario
from app.models.core import ReplayScenario
from app.models.forecasting import RiskIndex
from app.models.geography import District
from app.services.forecasting.features import PROXY_PROVENANCES
from app.services.forecasting.service import load_thresholds
from app.services.triggers.tiers import tier_rank

TIERS = ("WATCH", "WARNING", "ACTION")


def _scores(db: Session, sid: UUID) -> dict[UUID, list[RiskIndex]]:
    out: dict[UUID, list[RiskIndex]] = defaultdict(list)
    for ri in db.scalars(select(RiskIndex).where(RiskIndex.scenario_id == sid, RiskIndex.tehsil_id.is_(None))
                         .order_by(RiskIndex.run_date)):
        out[ri.district_id].append(ri)
    return out


def _releases(db: Session, sid: UUID) -> tuple[str, list[dict[str, Any]]]:
    try:
        from app.models.triggers import FundRelease, TriggerEvent  # type: ignore[import-not-found]
    except ImportError:
        return "tier_crossing", []
    rows = db.execute(select(FundRelease, TriggerEvent.district_id, RiskIndex.run_date)
                      .join(TriggerEvent, TriggerEvent.id == FundRelease.event_id)
                      .join(RiskIndex, RiskIndex.id == FundRelease.risk_index_id)
                      .where(FundRelease.scenario_id == sid)).all()
    return "fund_release", [{"district_id": did, "tier": fr.tier, "run_date": rd, "release_id": fr.id,
                             "approved": getattr(fr, "status", "") != "PENDING_APPROVAL"} for fr, did, rd in rows]


def evaluate_backtest(db: Session, sc: ReplayScenario) -> dict[str, Any]:
    cfg = sc.config or {}
    names = dict(db.execute(select(District.id, District.name).where(District.id.in_(sc.districts))).all())
    scores = _scores(db, sc.id)
    with use_scenario(sc.id):
        watch = {did: load_thresholds(db, did)["WATCH"] for did in sc.districts}
    basis, rel = _releases(db, sc.id)
    if basis == "tier_crossing":  # first day each district's score reached each tier
        for did, rows in scores.items():
            for t in TIERS:
                first = next((r for r in rows if tier_rank(r.tier) >= tier_rank(t)), None)
                if first:
                    rel.append({"district_id": did, "tier": t, "run_date": first.run_date, "release_id": None,
                                "approved": True})
    failures: list[str] = []
    actions = [r for r in rel if r["tier"] == "ACTION"]
    if basis == "fund_release" and any(not r["approved"] for r in actions):
        failures.append("Action release(s) without an approval record")
    first_release, tier_first = None, {}
    if sc.expected_outcome == "TRIGGER":
        hero = UUID(cfg["hero_district_id"])
        onset = date.fromisoformat(cfg["reference_onset"][str(hero)])
        hero_rel = sorted((r for r in rel if r["district_id"] == hero), key=lambda r: r["run_date"])
        tier_first = {t: next((str(r["run_date"]) for r in hero_rel if r["tier"] == t), None) for t in TIERS}
        if not hero_rel:
            failures.append(f"{names[hero]} never triggered")
        else:
            f = hero_rel[0]
            lead = (onset - f["run_date"]).days
            first_release = {"tier": f["tier"], "run_date": str(f["run_date"]), "lead_days": lead}
            if lead < (cfg.get("min_lead_days") or 0):
                failures.append(f"first trigger {f['run_date']} has lead {lead} < {cfg['min_lead_days']} days")
    else:
        if basis == "fund_release" and rel:
            failures.append(f"{len(rel)} release(s) in a NO_TRIGGER scenario")
        over = [(names[did], str(r.run_date), r.score) for did, rows in scores.items() for r in rows
                if r.score >= watch[did]]
        if over:
            failures.append(f"{len(over)} district-day(s) at or above Watch, first {over[0]}")
    all_rows = [r for rows in scores.values() for r in rows]
    best = max(all_rows, key=lambda r: r.score, default=None)
    provs = defaultdict(int)
    for r in all_rows:
        provs[r.forecast_provenance or "NONE"] += 1
    n = len(all_rows) or 1
    report = {"key": sc.key, "run_no": sc.run_no, "complete": sc.status == "COMPLETED",
              "expected": sc.expected_outcome, "passed": not failures and sc.status == "COMPLETED", "basis": basis,
              "hero_district": cfg.get("hero_district"), "min_lead_days": cfg.get("min_lead_days"),
              "reference_onset": next(iter((cfg.get("reference_onset") or {}).values()), None),
              "first_release": first_release, "tier_first_dates": tier_first,
              "max_score": {"district": names.get(best.district_id), "value": best.score,
                            "run_date": str(best.run_date)} if best else None,
              "districts": [{"name": names[did], "max_score": max((r.score for r in rows), default=0),
                             "days_at_or_above_watch": sum(1 for r in rows if r.score >= watch[did])}
                            for did, rows in sorted(scores.items(), key=lambda x: names[x[0]])],
              "watch_margin": (min(watch.values()) - best.score) if best and sc.expected_outcome == "NO_TRIGGER"
              else None,
              "forecast_provenance": {k: round(v / n, 2) for k, v in provs.items()},
              "forecast_is_proxy": any(k in PROXY_PROVENANCES for k in provs),
              "model_versions": sorted({r.model_version for r in all_rows}),
              "failures": failures, "simulated": True}
    return report

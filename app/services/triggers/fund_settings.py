"""FR6 fund settings: validate the whole form (all errors at once), save fund + tiers atomically with exactly one
FUND_SETTINGS_UPDATED audit entry, optimistic locking on version, no-op detection, copy drafts."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, DomainError, NotFound, Unprocessable
from app.core.scenario import current_scenario
from app.models.funds import DisasterFund, TriggerTier
from app.models.geography import District
from app.schemas.fund_settings import CopiedFrom, FundSettingsDraft, FundSettingsIn, TierIn
from app.services.stubs import get_audit
from app.services.triggers.tiers import TIER_ORDER, get_fund, load_tier_config

MAX_PKR = Decimal("999999999999.99")
FUNDS_SEED = Path("data/seeds/funds.yaml")


@dataclass
class FieldError:
    field: str
    code: str
    message: str


class FundSettingsInvalid(DomainError):
    status = 422
    title = "Fund settings are invalid"

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__(errors[0].code, f"{len(errors)} problem(s) found. Nothing was saved.")
        self.extra = {"errors": [e.__dict__ for e in errors]}


@lru_cache
def fund_defaults() -> dict[str, Any]:
    return yaml.safe_load(FUNDS_SEED.read_text(encoding="utf-8"))["fund_defaults"]


def _money_ok(v: Decimal) -> bool:
    return v.is_finite() and Decimal(0) <= v <= MAX_PKR and v.as_tuple().exponent >= -2


def validate_split(share: int) -> list[FieldError]:
    """FR9 owns the split rules; FR6 calls this from the settings form."""
    if not 0 <= share <= 100:
        return [FieldError("household_share_pct", "SPLIT_PCT_INVALID", "Household share must be 0 to 100.")]
    return []


def validate_fund_settings(body: FundSettingsIn, fund: DisasterFund | None) -> list[FieldError]:
    errs: list[FieldError] = []
    if any(t.requires_approval is not None for t in body.tiers):
        errs.append(FieldError("tiers[].requires_approval", "REQUIRES_APPROVAL_LOCKED",
                               "Approval rules are fixed: Action always needs the DC."))
    names = [t.name for t in body.tiers]
    if sorted(names) != sorted(TIER_ORDER):
        errs.append(FieldError("tiers", "TIER_SET_INVALID", "Give exactly one WATCH, WARNING and ACTION tier."))
    else:
        by = {t.name: t for t in body.tiers}
        scores = [by[n].min_score for n in TIER_ORDER]
        pcts = [by[n].release_pct for n in TIER_ORDER]
        if any(not 1 <= s <= 100 for s in scores):
            errs.append(FieldError("tiers[].min_score", "TIER_THRESHOLD_RANGE_INVALID",
                                   "Thresholds must be 1 to 100."))
        elif not scores[0] < scores[1] < scores[2]:
            errs.append(FieldError("tiers[].min_score", "TIER_THRESHOLD_ORDER_INVALID",
                                   "Thresholds must increase: Watch < Warning < Action."))
        if any(not 0 <= p <= 100 for p in pcts):
            errs.append(FieldError("tiers[].release_pct", "TIER_PCT_RANGE_INVALID", "Each increment must be 0 to 100."))
        elif sum(pcts) != 100:
            errs.append(FieldError("tiers[].release_pct", "TIER_PCT_SUM_INVALID",
                                   f"Release increments add up to {sum(pcts)}; they must add up to 100."))
    if not _money_ok(body.allocated_pkr):
        errs.append(FieldError("allocated_pkr", "FUND_AMOUNT_INVALID",
                               "Fund amount must be 0 or more, with at most 2 decimals."))
    elif fund and body.allocated_pkr < fund.reserved_pkr + fund.disbursed_pkr:
        errs.append(FieldError("allocated_pkr", "FUND_BELOW_COMMITTED",
                               f"PKR {fund.reserved_pkr + fund.disbursed_pkr} is already reserved or paid."))
    errs += validate_split(body.household_share_pct)
    if not _money_ok(body.shopkeeper_cap_pkr):
        errs.append(FieldError("shopkeeper_cap_pkr", "SHOPKEEPER_CAP_INVALID", "Cap must be zero or more."))
    if not 0 <= body.shopkeeper_stock_pct <= 100:
        errs.append(FieldError("shopkeeper_stock_pct", "STOCK_PCT_INVALID", "Stock share must be 0 to 100."))
    h = body.household_amount_by_tier
    if set(h) != set(TIER_ORDER) or any(not _money_ok(v) for v in h.values()):
        errs.append(FieldError("household_amount_by_tier", "HOUSEHOLD_AMOUNTS_INVALID",
                               "Give WATCH, WARNING and ACTION amounts, each 0 or more."))
    if not 1 <= body.event_close_days <= 60:
        errs.append(FieldError("event_close_days", "EVENT_CLOSE_DAYS_INVALID", "Close days must be 1 to 60."))
    return errs


def create_fund_with_tiers(db: Session, district_id: UUID, *, allocated: Decimal | None = None,
                           defaults: dict[str, Any] | None = None) -> DisasterFund:
    """Also used by seeding and by FR5 to create scenario funds. requires_approval: ACTION only, forever."""
    d = defaults or fund_defaults()
    alloc = Decimal(str(allocated if allocated is not None else d["allocated_pkr"]))
    fund = DisasterFund(district_id=district_id, allocated_pkr=alloc, reserved_pkr=Decimal(0),
                        disbursed_pkr=Decimal(0), remaining_pkr=alloc, household_share_pct=d["household_share_pct"],
                        pool_spillover_enabled=d["pool_spillover_enabled"],
                        household_amount_by_tier={k: str(Decimal(str(v))) for k, v in
                                                  d["household_amount_by_tier"].items()},
                        shopkeeper_cap_pkr=Decimal(str(d["shopkeeper_cap_pkr"])),
                        shopkeeper_stock_pct=d["shopkeeper_stock_pct"], event_close_days=d["event_close_days"],
                        version=1, scenario_id=current_scenario())
    db.add(fund)
    db.flush()
    for name in TIER_ORDER:
        db.add(TriggerTier(fund_id=fund.id, name=name, min_score=d["thresholds"][name],
                           release_pct=d["increments"][name], requires_approval=(name == "ACTION")))
    db.flush()
    return fund


def snapshot(db: Session, fund: DisasterFund) -> dict[str, Any]:
    tiers = {t.name: [t.min_score, t.release_pct] for t in db.scalars(select(TriggerTier)
                                                                     .where(TriggerTier.fund_id == fund.id))}
    return {"allocated_pkr": f"{fund.allocated_pkr:.2f}", "tiers": {n: tiers.get(n) for n in TIER_ORDER},
            "household_share_pct": fund.household_share_pct, "pool_spillover_enabled": fund.pool_spillover_enabled,
            "shopkeeper_cap_pkr": f"{fund.shopkeeper_cap_pkr:.2f}",
            "shopkeeper_stock_pct": fund.shopkeeper_stock_pct,
            "household_amount_by_tier": {k: f"{Decimal(v):.2f}" for k, v in
                                         sorted(fund.household_amount_by_tier.items())},
            "event_close_days": fund.event_close_days}


def snapshot_from_body(body: FundSettingsIn) -> dict[str, Any]:
    by = {t.name: t for t in body.tiers}
    return {"allocated_pkr": f"{body.allocated_pkr:.2f}",
            "tiers": {n: [by[n].min_score, by[n].release_pct] for n in TIER_ORDER},
            "household_share_pct": body.household_share_pct, "pool_spillover_enabled": body.pool_spillover_enabled,
            "shopkeeper_cap_pkr": f"{body.shopkeeper_cap_pkr:.2f}", "shopkeeper_stock_pct": body.shopkeeper_stock_pct,
            "household_amount_by_tier": {k: f"{v:.2f}" for k, v in sorted(body.household_amount_by_tier.items())},
            "event_close_days": body.event_close_days}


def diff_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    out = []
    for k in after:
        if k == "tiers":
            for n in TIER_ORDER:
                for i, f in enumerate(("min_score", "release_pct")):
                    if (before["tiers"][n] or [None, None])[i] != after["tiers"][n][i]:
                        out.append(f"tiers.{n}.{f}")
        elif before.get(k) != after[k]:
            out.append(k)
    return out


def open_event(db: Session, district_id: UUID) -> dict[str, Any] | None:
    """FR7 owns TriggerEvent; until it exists there is never an open event."""
    try:
        from app.models.triggers import TriggerEvent  # type: ignore[import-not-found]
    except ImportError:
        return None
    sid = current_scenario()
    ev = db.scalar(select(TriggerEvent).where(TriggerEvent.district_id == district_id, TriggerEvent.status == "OPEN",
                                              TriggerEvent.scenario_id.is_(None) if sid is None
                                              else TriggerEvent.scenario_id == sid))
    return {"event_id": ev.id, "opened_at": ev.opened_at, "highest_tier": ev.highest_tier} if ev else None


def settings_out(db: Session, district: District, fund: DisasterFund, **extra: Any) -> dict[str, Any]:
    cfg = load_tier_config(db, fund)
    return {"district_id": district.id, "district_name": district.name, "fund_id": fund.id,
            "scenario_id": fund.scenario_id, "allocated_pkr": f"{fund.allocated_pkr:.2f}",
            "reserved_pkr": f"{fund.reserved_pkr:.2f}", "disbursed_pkr": f"{fund.disbursed_pkr:.2f}",
            "remaining_pkr": f"{fund.remaining_pkr:.2f}",
            "tiers": [{"name": r.name, "min_score": r.min_score, "release_pct": r.release_pct,
                       "cumulative_pct": cfg.cumulative_pct(r.name), "requires_approval": r.requires_approval}
                      for r in cfg.rules],
            "household_share_pct": fund.household_share_pct, "shopkeeper_share_pct": 100 - fund.household_share_pct,
            "pool_spillover_enabled": fund.pool_spillover_enabled,
            "shopkeeper_cap_pkr": f"{fund.shopkeeper_cap_pkr:.2f}", "shopkeeper_stock_pct": fund.shopkeeper_stock_pct,
            "household_amount_by_tier": {k: f"{Decimal(v):.2f}" for k, v in fund.household_amount_by_tier.items()},
            "event_close_days": fund.event_close_days, "version": fund.version, "updated_at": fund.updated_at,
            "applies_from": "next_daily_run", "open_event": open_event(db, district.id), **extra}


def _district(db: Session, district_id: UUID) -> District:
    d = db.get(District, district_id)
    if d is None:
        raise NotFound("DISTRICT_NOT_FOUND", "District not found.")
    return d


def get_settings(db: Session, district_id: UUID) -> dict[str, Any]:
    d = _district(db, district_id)
    fund = get_fund(db, district_id)
    if fund is None:
        raise NotFound("FUND_NOT_FOUND", "This district has no fund yet. Save settings to create it.")
    return settings_out(db, d, fund)


def update_settings(db: Session, district_id: UUID, body: FundSettingsIn, actor: str) -> dict[str, Any]:
    d = _district(db, district_id)
    fund = get_fund(db, district_id, for_update=True)
    errors = validate_fund_settings(body, fund)
    if errors:
        raise FundSettingsInvalid(errors)
    current = fund.version if fund else 0
    if body.version != current:
        raise Conflict("VERSION_CONFLICT", f"Someone else saved first. Current version is {current}.")
    created = fund is None
    if created:
        fund = create_fund_with_tiers(db, district_id)
    before = snapshot(db, fund)
    after = snapshot_from_body(body)
    changed = diff_fields(before, after)
    if not changed and not created:
        return settings_out(db, d, fund, changed_fields=[], audit_seq=None)  # FR06-008: nothing written
    with db.no_autoflush:
        fund.allocated_pkr = body.allocated_pkr
        fund.remaining_pkr = body.allocated_pkr - fund.reserved_pkr - fund.disbursed_pkr
        fund.household_share_pct = body.household_share_pct
        fund.pool_spillover_enabled = body.pool_spillover_enabled
        fund.shopkeeper_cap_pkr = body.shopkeeper_cap_pkr
        fund.shopkeeper_stock_pct = body.shopkeeper_stock_pct
        fund.household_amount_by_tier = {k: f"{v:.2f}" for k, v in body.household_amount_by_tier.items()}
        fund.event_close_days = body.event_close_days
        by = {t.name: t for t in body.tiers}
        for t in db.scalars(select(TriggerTier).where(TriggerTier.fund_id == fund.id)):
            t.min_score, t.release_pct = by[t.name].min_score, by[t.name].release_pct  # never requires_approval
    db.flush()
    fund.version = current + 1
    entry = get_audit().record(db, event_type="FUND_SETTINGS_UPDATED", entity_type="DisasterFund", entity_id=fund.id,
                               actor=actor, payload={"district_id": str(district_id), "created": created,
                                                     "version_before": current, "version_after": fund.version,
                                                     "changed_fields": changed, "before": before, "after": after,
                                                     "open_event_id": (open_event(db, district_id) or {}).get(
                                                         "event_id")})
    db.commit()
    return settings_out(db, d, fund, changed_fields=changed, audit_seq=entry.seq)


def build_copy_draft(db: Session, target_id: UUID, source_id: UUID, include_amount: bool = False) -> FundSettingsDraft:
    if target_id == source_id:
        raise Unprocessable("COPY_SOURCE_SAME_AS_TARGET", "Choose a different district to copy from.")
    _district(db, target_id)
    src_d = _district(db, source_id)
    src = get_fund(db, source_id)
    if src is None:
        raise NotFound("SOURCE_FUND_NOT_FOUND", f"{src_d.name} has no fund to copy.")
    tgt = get_fund(db, target_id)
    cfg = load_tier_config(db, src)
    return FundSettingsDraft(
        version=tgt.version if tgt else 0,
        allocated_pkr=src.allocated_pkr if include_amount else (tgt.allocated_pkr if tgt else Decimal("0.00")),
        tiers=[TierIn(name=r.name, min_score=r.min_score, release_pct=r.release_pct) for r in cfg.rules],
        household_share_pct=src.household_share_pct, pool_spillover_enabled=src.pool_spillover_enabled,
        shopkeeper_cap_pkr=src.shopkeeper_cap_pkr, shopkeeper_stock_pct=src.shopkeeper_stock_pct,
        household_amount_by_tier={k: Decimal(v) for k, v in src.household_amount_by_tier.items()},
        event_close_days=src.event_close_days,
        copied_from=CopiedFrom(district_id=src_d.id, district_name=src_d.name, version=src.version))


def list_settings(db: Session, province: str | None, only_district: UUID | None) -> list[dict[str, Any]]:
    sid = current_scenario()
    q = (select(District, DisasterFund).join(DisasterFund, DisasterFund.district_id == District.id)
         .where(DisasterFund.scenario_id.is_(None) if sid is None else DisasterFund.scenario_id == sid)
         .order_by(District.province, District.name))
    if province:
        q = q.where(District.province == province)
    if only_district:
        q = q.where(District.id == only_district)
    else:
        q = q.where(District.is_active.is_(True))
    out = []
    for d, f in db.execute(q).all():
        cfg = load_tier_config(db, f)
        ev = open_event(db, d.id)
        out.append({"district_id": d.id, "district_name": d.name, "province": d.province,
                    "allocated_pkr": f"{f.allocated_pkr:.2f}", "remaining_pkr": f"{f.remaining_pkr:.2f}",
                    "thresholds": [r.min_score for r in cfg.rules], "increments": [r.release_pct for r in cfg.rules],
                    "household_share_pct": f.household_share_pct,
                    "open_event_tier": ev["highest_tier"] if ev else None, "version": f.version})
    return out


def seed_live_funds(db: Session) -> int:
    """Create the default live fund for every active district that has none (idempotent)."""
    n = 0
    for d in db.scalars(select(District).where(District.is_active.is_(True))):
        if get_fund(db, d.id) is None:
            create_fund_with_tiers(db, d.id)
            n += 1
    return n

"""FR5 replay engine: run the real daily pipeline one simulated day at a time, inside the scenario context, with a
ReplayClock. Start creates scenario funds (FR6), snapshots modes (FR4) and loads 30 spin-up days (FR1/FR2)."""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.clock import clock_scope
from app.core.config import settings
from app.core.errors import Conflict, DomainError, Unprocessable
from app.core.scenario import use_scenario
from app.jobs.daily_pipeline import run_daily_pipeline
from app.models.core import ReplayScenario
from app.models.forecasting import RiskIndex
from app.models.funds import DisasterFund
from app.models.geography import District
from app.services.forecasting.features import PROXY_PROVENANCES
from app.services.forecasting.modes import snapshot_modes
from app.services.ingestion.registry import all_ingestors
from app.services.replay.backtest import evaluate_backtest
from app.services.replay.cache import verify_manifest
from app.services.replay.clock import ReplayClock
from app.services.replay.reset import purge_scenario_rows
from app.services.replay.scenarios import config_json, get_scenario_by_key, load_definitions
from app.services.stubs import get_audit
from app.services.triggers.fund_settings import create_fund_with_tiers

log = logging.getLogger("aab.replay")
LOCK_NS = 5005
APPROVAL_MODES = ("AUTO_SIM", "LIVE_WHATSAPP", "DASHBOARD")
# FR8 registers: resolver(db, scenario, mode) -> list of pending approval ids the replay must wait for.
APPROVAL_RESOLVERS: list[Callable[[Session, ReplayScenario, str], list[str]]] = []
# FR17/FR14 register jobs that must finish inside each simulated day (payments.dispatch, messaging.dispatch).
DRAIN_JOBS: list[Callable[[Session], Any]] = []


class StepFailed(DomainError):
    status = 500
    title = "Replay step failed"


@contextmanager
def scenario_lock(db: Session, key: str) -> Iterator[None]:
    if not db.scalar(text("SELECT pg_try_advisory_lock(:ns, hashtext(:k))"), {"ns": LOCK_NS, "k": key}):
        raise Conflict("REPLAY_BUSY", f"Replay {key} is busy; try again in a moment.")
    try:
        yield
    finally:
        db.execute(text("SELECT pg_advisory_unlock(:ns, hashtext(:k))"), {"ns": LOCK_NS, "k": key})


def _now() -> datetime:
    return datetime.now(UTC)


class ReplayEngine:
    # ---------------------------------------------------------------- start
    def start(self, db: Session, key: str, approval_mode: str = "AUTO_SIM", actor: str = "system") -> dict[str, Any]:
        if approval_mode not in APPROVAL_MODES:
            raise Unprocessable("INVALID_APPROVAL_MODE", f"Approval mode must be one of {', '.join(APPROVAL_MODES)}.")
        sc = get_scenario_by_key(db, key)
        if sc.status in ("RUNNING", "AWAITING_APPROVAL", "COMPLETED"):
            return self.state(db, key)  # idempotent
        with scenario_lock(db, key):
            manifest = verify_manifest(key)
            defs = load_definitions()[key]
            with use_scenario(sc.id), clock_scope(ReplayClock(sc.start_date - timedelta(days=1))):
                alloc = Decimal(str(defs["funds"]["allocated_pkr"]))
                for did in sc.districts:
                    if db.scalar(select(DisasterFund.id).where(DisasterFund.district_id == did,
                                                               DisasterFund.scenario_id == sc.id)) is None:
                        create_fund_with_tiers(db, did, allocated=alloc)
                spin_start = sc.start_date - timedelta(days=defs["spinup_days"])
                spinup = {}
                for ing in all_ingestors():
                    res = ing.ingest(db, start=spin_start, end=sc.start_date - timedelta(days=1),
                                     district_ids=list(sc.districts))
                    spinup[str(ing.source)] = {"rows_fresh": res.rows_fresh, "errors": len(res.errors)}
                sc.config = {**config_json(db, defs), "approval_mode": approval_mode,
                             "modes": snapshot_modes(db, list(sc.districts)), "manifest_sha256": manifest["sha256"],
                             "spinup": spinup}
                sc.status, sc.sim_date, sc.started_at, sc.last_error = ("RUNNING", sc.start_date - timedelta(days=1),
                                                                        _now(), None)
                get_audit().record(db, event_type="REPLAY_STARTED", entity_type="ReplayScenario", entity_id=sc.id,
                                   actor=actor, payload={"key": key, "run_no": sc.run_no,
                                                         "approval_mode": approval_mode,
                                                         "manifest_sha256": manifest["sha256"]})
            db.commit()
        return self.state(db, key)

    # ---------------------------------------------------------------- step
    def step(self, db: Session, key: str, days: int = 1) -> dict[str, Any]:
        if not 1 <= days <= settings.REPLAY_MAX_STEP_DAYS:
            raise Unprocessable("INVALID_DAYS", f"Step 1 to {settings.REPLAY_MAX_STEP_DAYS} days at a time.")
        sc = get_scenario_by_key(db, key)
        if sc.status == "IDLE":
            raise Conflict("REPLAY_NOT_STARTED", "Start the replay first.")
        if sc.status == "COMPLETED":
            raise Conflict("REPLAY_COMPLETED", "This replay has finished. Reset to run it again.")
        stepped: list[str] = []
        with scenario_lock(db, key):
            for _ in range(days):
                db.refresh(sc)
                if sc.status == "AWAITING_APPROVAL":
                    pending = self._pending(db, sc)
                    if pending:
                        if not stepped:
                            err = Conflict("REPLAY_AWAITING_APPROVAL", "The DC must decide the Action release first.")
                            err.extra = {"approval_ids": pending}
                            raise err
                        break
                    sc.status = "RUNNING"
                if sc.sim_date >= sc.end_date:
                    self._complete(db, sc)
                    break
                day = sc.sim_date + timedelta(days=1)
                try:
                    clk = ReplayClock(day)
                    with use_scenario(sc.id), clock_scope(clk):
                        run_daily_pipeline(db, clk)
                        for job in DRAIN_JOBS:
                            job(db)
                        waiting = self._pending(db, sc)
                        for job in DRAIN_JOBS:
                            job(db)
                    sc.sim_date, sc.last_step_at = day, _now()
                    sc.status = "AWAITING_APPROVAL" if waiting else "RUNNING"
                    db.commit()
                    stepped.append(day.isoformat())
                except Exception as exc:
                    db.rollback()
                    sc = get_scenario_by_key(db, key)
                    sc.status, sc.last_error = "FAILED", f"{day}: {exc!r}"[:2000]
                    db.commit()
                    raise StepFailed("REPLAY_STEP_FAILED", f"Day {day} failed: {exc}") from exc
                if waiting:
                    break
            if sc.status == "RUNNING" and sc.sim_date >= sc.end_date:
                self._complete(db, sc)
        return self.state(db, key, stepped=stepped)

    def _pending(self, db: Session, sc: ReplayScenario) -> list[str]:
        mode = (sc.config or {}).get("approval_mode", "AUTO_SIM")
        out: list[str] = []
        for resolver in APPROVAL_RESOLVERS:
            out += resolver(db, sc, mode)
        return out

    def _complete(self, db: Session, sc: ReplayScenario) -> None:
        sc.status, sc.completed_at = "COMPLETED", _now()
        with use_scenario(sc.id):
            get_audit().record(db, event_type="REPLAY_COMPLETED", entity_type="ReplayScenario", entity_id=sc.id,
                               actor="system", payload={"key": sc.key, "run_no": sc.run_no,
                                                        "last_day": str(sc.sim_date)})
        db.commit()

    # ---------------------------------------------------------------- seek / reset
    def seek(self, db: Session, key: str, target: date, reset: bool = False, actor: str = "system") -> dict[str, Any]:
        sc = get_scenario_by_key(db, key)
        if not sc.start_date <= target <= sc.end_date:
            raise Unprocessable("DATE_OUT_OF_WINDOW", f"Pick a date from {sc.start_date} to {sc.end_date}.")
        if sc.sim_date is not None and target <= sc.sim_date:
            if not reset:
                raise Conflict("REPLAY_SEEK_BACKWARD", "Going back needs a reset; past effects cannot be undone.")
            mode = (sc.config or {}).get("approval_mode", "AUTO_SIM")
            self.reset(db, key, actor)
            self.start(db, key, mode, actor)
        elif sc.status == "IDLE":
            self.start(db, key, actor=actor)
        sc = get_scenario_by_key(db, key)
        days = (target - sc.sim_date).days
        return self.step(db, key, days) if days > 0 else self.state(db, key)

    def reset(self, db: Session, key: str, actor: str = "system") -> dict[str, Any]:
        from app.services.replay.autoplay import pause

        pause(key)
        sc = get_scenario_by_key(db, key)
        with scenario_lock(db, key):
            with use_scenario(sc.id):  # written to the old chain, before run_no moves (FR20)
                get_audit().record(db, event_type="REPLAY_RESET", entity_type="ReplayScenario", entity_id=sc.id,
                                   actor=actor, payload={"key": key, "run_no": sc.run_no})
            db.commit()
            deleted = purge_scenario_rows(db, sc.id)
            sc.run_no += 1
            sc.status, sc.sim_date, sc.last_error = "IDLE", None, None
            sc.started_at = sc.completed_at = sc.last_step_at = None
            sc.config = config_json(db, load_definitions()[key])
            db.commit()
        return {"key": key, "status": "IDLE", "run_no": sc.run_no, "deleted": {k: v for k, v in deleted.items() if v},
                "simulated": True}

    # ---------------------------------------------------------------- reads
    def state(self, db: Session, key: str, stepped: list[str] | None = None,
              only_district: UUID | None = None) -> dict[str, Any]:
        from app.services.replay.autoplay import autoplay_status

        sc = get_scenario_by_key(db, key)
        districts = []
        ids = [only_district] if only_district else sc.districts
        names = dict(db.execute(select(District.id, District.name).where(District.id.in_(ids))).all())
        for did in ids:
            if did not in names:
                continue
            ri = db.scalar(select(RiskIndex).where(RiskIndex.scenario_id == sc.id, RiskIndex.district_id == did,
                                                   RiskIndex.tehsil_id.is_(None), RiskIndex.run_date == sc.sim_date))
            fund = db.scalar(select(DisasterFund).where(DisasterFund.scenario_id == sc.id,
                                                        DisasterFund.district_id == did))
            districts.append({"district_id": did, "name": names[did], "score": ri.score if ri else None,
                              "tier": ri.tier if ri else None, "is_fallback": ri.is_fallback if ri else None,
                              "forecast_is_proxy": (ri.forecast_provenance in PROXY_PROVENANCES) if ri else None,
                              "fund_remaining_pkr": f"{fund.remaining_pkr:.2f}" if fund else None})
        bt = evaluate_backtest(db, sc) if sc.status != "IDLE" else None
        return {"key": sc.key, "scenario_id": sc.id, "name": sc.name, "status": sc.status, "sim_date": sc.sim_date,
                "start_date": sc.start_date, "end_date": sc.end_date, "run_no": sc.run_no,
                "approval_mode": (sc.config or {}).get("approval_mode"), "districts": districts,
                "pending_approvals": self._pending(db, sc) if sc.status == "AWAITING_APPROVAL" else [],
                "forecast_provenance": bt["forecast_provenance"] if bt else {},
                "backtest_so_far": {"expected": sc.expected_outcome, "basis": bt["basis"],
                                    "first_release": bt["first_release"]} if bt else None,
                "last_error": sc.last_error, "autoplay": autoplay_status(key), "stepped": stepped or [],
                "simulated": True}

    def timeline(self, db: Session, key: str, only_district: UUID | None = None) -> dict[str, Any]:
        sc = get_scenario_by_key(db, key)
        q = (select(RiskIndex, District.name).join(District, District.id == RiskIndex.district_id)
             .where(RiskIndex.scenario_id == sc.id, RiskIndex.tehsil_id.is_(None)).order_by(RiskIndex.run_date,
                                                                                          District.name))
        if only_district:
            q = q.where(RiskIndex.district_id == only_district)
        days: dict[date, list[dict[str, Any]]] = {}
        for ri, name in db.execute(q).all():
            days.setdefault(ri.run_date, []).append({
                "district_id": ri.district_id, "name": name, "score": ri.score, "tier": ri.tier,
                "is_fallback": ri.is_fallback, "forecast_is_proxy": ri.forecast_provenance in PROXY_PROVENANCES,
                "releases": []})  # FR7 adds releases per day
        return {"key": sc.key, "run_no": sc.run_no,
                "days": [{"date": d, "districts": v} for d, v in sorted(days.items())], "simulated": True}

    def backtest(self, db: Session, key: str) -> dict[str, Any]:
        return evaluate_backtest(db, get_scenario_by_key(db, key))

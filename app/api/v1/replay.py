"""FR5 replay API. Drive (start/step/seek/reset/play/pause): PDMA_OFFICER, ADMIN. Watch (list/state/timeline/
backtest): also DC (own district only) and DONOR (district aggregates). Every response says simulated: true."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import Unprocessable
from app.core.security import require_role
from app.models.core import ReplayScenario, User
from app.models.enums import Role
from app.services.replay import autoplay
from app.services.replay.engine import ReplayEngine
from app.services.replay.scenarios import get_scenario_by_key

router = APIRouter(prefix="/replay", tags=["Forecasting"])
DRIVERS = (Role.PDMA_OFFICER, Role.ADMIN)
WATCHERS = (Role.PDMA_OFFICER, Role.ADMIN, Role.DC, Role.DONOR)
engine = ReplayEngine()


def _scope(user: User):
    return user.district_id if user.role == Role.DC else None


@router.get("/scenarios")
def list_scenarios(db: Session = Depends(get_db), _: User = Depends(require_role(*WATCHERS))) -> dict:
    items = [{"key": s.key, "scenario_id": s.id, "name": s.name, "start_date": s.start_date, "end_date": s.end_date,
              "expected_outcome": s.expected_outcome, "status": s.status, "sim_date": s.sim_date, "run_no": s.run_no,
              "simulated": True} for s in db.scalars(select(ReplayScenario).order_by(ReplayScenario.start_date))]
    return {"items": items, "total": len(items), "page": 1, "page_size": 50}


class StartIn(BaseModel):
    approval_mode: str = "AUTO_SIM"


@router.post("/scenarios/{key}/start")
def start(key: str, body: StartIn, response: Response, db: Session = Depends(get_db),
          user: User = Depends(require_role(*DRIVERS))) -> dict:
    was_idle = get_scenario_by_key(db, key).status == "IDLE"
    st = engine.start(db, key, body.approval_mode, actor=str(user.id))
    response.status_code = 201 if was_idle else 200
    return st


class StepIn(BaseModel):
    days: int = Field(1, ge=1)


@router.post("/scenarios/{key}/step")
def step(key: str, body: StepIn, db: Session = Depends(get_db), _: User = Depends(require_role(*DRIVERS))) -> dict:
    return engine.step(db, key, body.days)


class SeekIn(BaseModel):
    date: date
    reset: bool = False


@router.post("/scenarios/{key}/seek")
def seek(key: str, body: SeekIn, db: Session = Depends(get_db), user: User = Depends(require_role(*DRIVERS))) -> dict:
    return engine.seek(db, key, body.date, body.reset, actor=str(user.id))


@router.post("/scenarios/{key}/reset")
def reset(key: str, db: Session = Depends(get_db), user: User = Depends(require_role(*DRIVERS))) -> dict:
    return engine.reset(db, key, actor=str(user.id))


@router.get("/scenarios/{key}/state")
def state(key: str, db: Session = Depends(get_db), user: User = Depends(require_role(*WATCHERS))) -> dict:
    return engine.state(db, key, only_district=_scope(user))


@router.get("/scenarios/{key}/timeline")
def timeline(key: str, db: Session = Depends(get_db), user: User = Depends(require_role(*WATCHERS))) -> dict:
    return engine.timeline(db, key, only_district=_scope(user))


@router.get("/scenarios/{key}/backtest")
def backtest(key: str, db: Session = Depends(get_db),
             _: User = Depends(require_role(Role.PDMA_OFFICER, Role.ADMIN, Role.DONOR))) -> dict:
    return engine.backtest(db, key)


class PlayIn(BaseModel):
    interval_seconds: int = 3
    until: date | None = None


@router.post("/scenarios/{key}/play")
def play(key: str, body: PlayIn, db: Session = Depends(get_db), _: User = Depends(require_role(*DRIVERS))) -> dict:
    if not settings.REPLAY_AUTOPLAY_MIN_INTERVAL_S <= body.interval_seconds <= 30:
        raise Unprocessable("INVALID_INTERVAL", f"Interval must be {settings.REPLAY_AUTOPLAY_MIN_INTERVAL_S}-30 s.")
    get_scenario_by_key(db, key)
    autoplay.play(key, body.interval_seconds, body.until)
    return engine.state(db, key)


@router.post("/scenarios/{key}/pause")
def pause(key: str, db: Session = Depends(get_db), _: User = Depends(require_role(*DRIVERS))) -> dict:
    autoplay.pause(key)
    return engine.state(db, key)

"""Foundation stubs. StubAuditService appends rows without a real hash chain (FR20 replaces it).
LogAlertService only logs (FR14 replaces it)."""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.scenario import current_scenario
from app.models.core import AuditLog, ReplayScenario

log = logging.getLogger("aab")
GENESIS = "0" * 64


def chain_key_for(db: Session, scenario_id: UUID | None) -> str:
    if scenario_id is None:
        return "live"
    run_no = db.scalar(select(ReplayScenario.run_no).where(ReplayScenario.id == scenario_id)) or 1
    return f"replay:{scenario_id}:{run_no}"


class StubAuditService:
    def record(self, db: Session, *, event_type: str, entity_type: str, entity_id: UUID | None, actor: str,
               payload: dict[str, Any]) -> AuditLog:
        sid = current_scenario()
        key = chain_key_for(db, sid)
        seq = (db.scalar(select(func.max(AuditLog.seq)).where(AuditLog.chain_key == key)) or 0) + 1
        row = AuditLog(chain_key=key, seq=seq, event_type=event_type, entity_type=entity_type, entity_id=entity_id,
                       actor=actor, payload=json.loads(json.dumps(payload, default=str)),
                       prev_hash=GENESIS, hash=GENESIS, scenario_id=sid)
        db.add(row)
        db.flush()
        return row


class LogAlertService:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def enqueue(self, db: Session, *, recipient: Any, purpose: str, template_key: str, context: dict[str, Any],
                related: dict[str, Any] | None = None) -> dict[str, Any]:
        msg = {"recipient": recipient, "purpose": purpose, "template_key": template_key, "context": context,
               "related": related or {}}
        self.sent.append(msg)
        log.warning("alert (stub, not sent): %s", json.dumps(msg, default=str))
        return msg


class NoopTriggerEngine:
    """FR7 replaces this. FR3 calls evaluate() for every district after its scores commit."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def evaluate(self, db: Session, district_id: UUID, risk_index: Any) -> list[Any]:
        self.calls.append((district_id, getattr(risk_index, "id", None)))
        return []


_trigger: Any = NoopTriggerEngine()


def get_trigger_engine() -> Any:
    return _trigger


def set_trigger_engine(svc: Any) -> None:
    global _trigger
    _trigger = svc


_audit: Any = StubAuditService()
_alerts: Any = LogAlertService()


def get_audit() -> Any:
    return _audit


def get_alerts() -> Any:
    return _alerts


def set_audit(svc: Any) -> None:
    global _audit
    _audit = svc


def set_alerts(svc: Any) -> None:
    global _alerts
    _alerts = svc

"""Shared service seams (000-foundation/plan.md). The owning FR replaces each stub."""
from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session


class AuditService(Protocol):
    def record(self, db: Session, *, event_type: str, entity_type: str, entity_id: UUID | None, actor: str,
               payload: dict[str, Any]) -> Any: ...


class AlertService(Protocol):
    def enqueue(self, db: Session, *, recipient: Any, purpose: str, template_key: str, context: dict[str, Any],
                related: dict[str, Any] | None = None) -> Any: ...

"""Engine, session factory, declarative base and shared column mixins."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, create_engine, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, declared_attr, mapped_column, sessionmaker

from app.core.config import settings
from app.core.scenario import current_scenario


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScenarioMixin:
    """scenario_id NULL = live data; a value = row belongs to a replay run (FR5)."""

    @declared_attr
    def scenario_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("replay_scenario.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
            default=current_scenario,
        )


engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def try_advisory_xact_lock(db: Session, key: str) -> bool:
    """Transaction-scoped advisory lock keyed by a stable text hash (not Python hash())."""
    return bool(db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": key}))

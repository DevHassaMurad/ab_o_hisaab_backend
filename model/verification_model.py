"""Capability-protected pre-registration state; passwords are never stored here."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base


class VerificationProcess(Base):
    __tablename__ = "verification_processes"
    __table_args__ = (
        CheckConstraint(
            "verification_attempts BETWEEN 0 AND 3", name="ck_process_attempts"
        ),
        CheckConstraint(
            "document_type IN ('cnic', 'passport', 'home_address', 'ngo_registration')",
            name="ck_process_document_type",
        ),
        CheckConstraint(
            "user_type IN ('donor', 'ngo', 'householder', 'shopkeeper')",
            name="ck_process_user_type",
        ),
        Index("ix_process_owner_created", "owner_key", "created_at"),
        Index("ix_process_stage", "stage"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_key: Mapped[str] = mapped_column(String(64), nullable=False)
    user_type: Mapped[str] = mapped_column(String(11), nullable=False)
    document_type: Mapped[str] = mapped_column(String(16), nullable=False)
    document_url: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(30), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    verification_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    stage: Mapped[str] = mapped_column(
        String(20), nullable=False, default="uploaded", server_default="uploaded"
    )
    outcome: Mapped[str | None] = mapped_column(String(20))
    completed_stages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    verification_result: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    extracted_data: Mapped[str | None] = mapped_column(Text)
    document_status: Mapped[str] = mapped_column(
        String(8), nullable=False, default="Pending", server_default="Pending"
    )
    review_requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    claimed_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

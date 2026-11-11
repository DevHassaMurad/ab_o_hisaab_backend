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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from config.database import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('Pending', 'Approved', 'Rejected')", name="ck_document_status"
        ),
        CheckConstraint(
            "user_type IN ('donor', 'ngo', 'shopkeeper', 'householder')",
            name="ck_document_user_type",
        ),
        CheckConstraint(
            "document_type IN ('passport', 'cnic', 'home_address', 'ngo_registration', 'ngo_certificate', 'authorization', 'business_proof', 'shop_photo')",
            name="ck_document_type",
        ),
        CheckConstraint(
            "approval_type IS NULL OR approval_type IN ('ai', 'manual')",
            name="ck_document_approval_type",
        ),
        CheckConstraint(
            "(status = 'Pending' AND approval_type IS NULL AND verified_at IS NULL) OR (status != 'Pending' AND approval_type IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_document_review_state",
        ),
        Index("ix_documents_user_id_created_at", "user_id", "created_at"),
        Index("ix_documents_status", "status"),
        CheckConstraint(
            "verification_attempts BETWEEN 0 AND 3", name="ck_document_attempts"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    document_url: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(8), nullable=False, default="Pending", server_default="Pending"
    )
    user_type: Mapped[str] = mapped_column(String(11), nullable=False)
    document_type: Mapped[str] = mapped_column(String(16), nullable=False)
    document_slot: Mapped[str] = mapped_column(String(20), nullable=False)
    content_type: Mapped[str] = mapped_column(String(30), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_type: Mapped[str | None] = mapped_column(String(6))
    verification_result: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    verification_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    extracted_data: Mapped[str | None] = mapped_column(Text)
    review_requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_notification_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    verified_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user = relationship("User", foreign_keys=[user_id], back_populates="documents")
    reviewer = relationship("User", foreign_keys=[verified_by])

"""Fund requests from registered householders and shopkeepers to an NGO (see migration 0014)."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base

BFR_STATUSES = ("pending", "on_hold", "rejected", "paid", "received", "cancelled")
PURPOSES = ("food", "shelter", "health", "education", "livelihood", "business_recovery", "stock_replacement",
            "utilities", "other")


class BeneficiaryFundRequest(Base):
    __tablename__ = "beneficiary_fund_requests"
    __table_args__ = (
        CheckConstraint("requested_amount > 0", name="ck_bfr_amount_positive"),
        CheckConstraint("paid_amount IS NULL OR (paid_amount > 0 AND paid_amount <= requested_amount)",
                        name="ck_bfr_paid_amount"),
        CheckConstraint("status IN ({})".format(", ".join(f"'{s}'" for s in BFR_STATUSES)), name="ck_bfr_status"),
        CheckConstraint("beneficiary_type IN ('householder', 'shopkeeper')", name="ck_bfr_type"),
        Index("ix_bfr_ngo_status", "ngo_id", "status"),
        Index("ix_bfr_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_bfr_user_id"), nullable=False)
    beneficiary_type: Mapped[str] = mapped_column(String(12), nullable=False)
    ngo_id: Mapped[int] = mapped_column(ForeignKey("ngos.id", name="fk_bfr_ngo_id"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False)
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", server_default="pending")
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    payment_no: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_bfr_decided_by"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

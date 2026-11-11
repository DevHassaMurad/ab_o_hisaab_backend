"""PDMA fund ledger.

Donor -> PDMA: `DonorDonation` (DD-IN-26-00001). Counts as received only when payment_status = verified.
PDMA -> recipient: `DonationDisbursement` (DD-OUT-26-00001) to an NGO, shopkeeper or householder.
Each disbursement draws from one verified incoming donation (`source_batch_id`), so every rupee out
traces back to the payment that funded it. A donation can fund many disbursements.

Valid (counted) disbursements: pending, sent, received. Draft and rejected ones do not use funds.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base

MONEY = Numeric(14, 2)
RECIPIENT_TYPES = ("ngo", "shopkeeper", "householder")
DISBURSEMENT_STATUSES = ("draft", "pending", "sent", "received", "rejected")
COUNTED_DISBURSEMENT = ("pending", "sent", "received")
PAYMENT_STATUSES = ("pending", "verified", "rejected")


class DonorDonation(Base):
    """Money a donor sends to PDMA (only to PDMA)."""

    __tablename__ = "donor_donations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_donor_donations_amount_positive"),
        CheckConstraint("payment_status IN ('pending', 'verified', 'rejected')", name="ck_donor_donations_payment_status"),
        Index("ix_donor_donations_status", "payment_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    donor_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_donor_donations_donor_id"), nullable=False, index=True)
    # Optional link to the PDMA request this payment answers.
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("donation_requests.id", name="fk_donor_donations_request_id"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # future: disaster event
    payment_status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending", server_default="pending")
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_donor_donations_verified_by"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class DonationDisbursement(Base):
    """Money PDMA sends to an NGO, shopkeeper or householder, drawn from one verified incoming donation."""

    __tablename__ = "donation_disbursements"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_disbursements_amount_positive"),
        CheckConstraint("recipient_type IN ('ngo', 'shopkeeper', 'householder')", name="ck_disbursements_recipient_type"),
        CheckConstraint("status IN ('draft', 'pending', 'sent', 'received', 'rejected')", name="ck_disbursements_status"),
        Index("ix_disbursements_source", "source_batch_id"),
        Index("ix_disbursements_recipient", "recipient_type", "recipient_id"),
        Index("ix_disbursements_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    sent_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_disbursements_sent_by"), nullable=False)
    recipient_type: Mapped[str] = mapped_column(String(12), nullable=False)
    recipient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_batch_id: Mapped[int] = mapped_column(
        ForeignKey("donor_donations.id", name="fk_disbursements_source_batch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="sent", server_default="sent")
    status_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class EmailOutbox(Base):
    """Emails waiting to be sent. Saved with the business change, delivered after, retried on failure."""

    __tablename__ = "email_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_email_outbox_status"),
        Index("ix_email_outbox_due", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipient: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    text_body: Mapped[str] = mapped_column(Text, nullable=False)
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class NgoExpenditure(Base):
    """Money an NGO spent out of one disbursement (future NGO phase). Only `verified` rows count as spent."""

    __tablename__ = "ngo_expenditures"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_ngo_expenditures_amount_positive"),
        CheckConstraint("verification_status IN ('pending', 'verified', 'rejected')", name="ck_ngo_expenditures_status"),
        Index("ix_ngo_expenditures_disbursement", "disbursement_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disbursement_id: Mapped[int] = mapped_column(
        ForeignKey("donation_disbursements.id", name="fk_ngo_expenditures_disbursement_id", ondelete="RESTRICT"),
        nullable=False,
    )
    reference: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending", server_default="pending")
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_ngo_expenditures_verified_by"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

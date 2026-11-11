"""Donor management and fund tracking.

A donation request is a *batch* with a document number (DT-DONOR-26-00001). Money actually received
against it is recorded as `donations` (counted only once verified). Verified money is passed to NGOs
as `fund_allocations`. Remaining balance = verified received - active allocations, never below zero.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base

MONEY = Numeric(14, 2)

REQUEST_TYPES = ("donor", "ngo")
REQUEST_STATUSES = ("SENT", "PARTIALLY_FUNDED", "FUNDED", "CANCELLED")
DONATION_STATUSES = ("PENDING", "VERIFIED", "REJECTED")
PAYMENT_METHODS = ("BANK_TRANSFER", "CHEQUE", "ONLINE", "CASH", "OTHER")
ALLOCATION_STATUSES = ("ACTIVE", "CANCELLED")


class DonorProfile(Base):
    """Donor details beyond the login account (users row with role DONOR)."""

    __tablename__ = "donor_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="fk_donor_profiles_user_id", ondelete="CASCADE"), unique=True, nullable=False
    )
    organization: Mapped[str | None] = mapped_column(String(150), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Ngo(Base):
    """An NGO that receives part of a donation batch."""

    __tablename__ = "ngos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    registration_no: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    district_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Login account for the NGO (future NGO portal); allocations are visible only to that account.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_ngos_user_id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class DocumentSequence(Base):
    """Last issued number per document prefix and year. The row is locked while a number is issued."""

    __tablename__ = "document_sequences"

    prefix: Mapped[str] = mapped_column(String(40), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DonationRequest(Base):
    """A donation batch requested from a donor (or, later, an NGO)."""

    __tablename__ = "donation_requests"
    __table_args__ = (
        CheckConstraint("type IN ('donor', 'ngo')", name="ck_donation_requests_type"),
        CheckConstraint("requested_amount > 0", name="ck_donation_requests_amount_positive"),
        CheckConstraint(
            "donor_response IN ('PENDING', 'ACCEPTED', 'REJECTED')", name="ck_donation_requests_donor_response"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(10), nullable=False, default="donor")
    donor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_donation_requests_donor_id"), nullable=True, index=True
    )
    ngo_id: Mapped[int | None] = mapped_column(
        ForeignKey("ngos.id", name="fk_donation_requests_ngo_id"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="PKR", server_default="PKR")
    requested_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SENT", server_default="SENT")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_donation_requests_created_by"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The donor's answer. ACCEPTED requests can receive further amounts until fully covered.
    donor_response: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING", server_default="PENDING")
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Commitment details (accepting is a promise to contribute, not proof of payment).
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_donation_requests_accepted_by"), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Donation(Base):
    """Money received against a batch. Counts towards the batch only when VERIFIED."""

    __tablename__ = "donations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_donations_amount_positive"),
        CheckConstraint(
            "verification_status IN ('PENDING', 'VERIFIED', 'REJECTED')", name="ck_donations_verification_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("donation_requests.id", name="fk_donations_batch_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    donor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_donations_donor_id"), nullable=True, index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    payment_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="PENDING", server_default="PENDING"
    )
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_donations_verified_by"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_donations_recorded_by"), nullable=False)
    remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class FundAllocation(Base):
    """Verified batch money passed to an NGO. Never more than the batch's remaining balance."""

    __tablename__ = "fund_allocations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_fund_allocations_amount_positive"),
        CheckConstraint("status IN ('ACTIVE', 'CANCELLED')", name="ck_fund_allocations_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("donation_requests.id", name="fk_fund_allocations_batch_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ngo_id: Mapped[int] = mapped_column(ForeignKey("ngos.id", name="fk_fund_allocations_ngo_id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    allocation_date: Mapped[date] = mapped_column(Date, nullable=False)
    remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVE", server_default="ACTIVE")
    allocated_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_fund_allocations_allocated_by"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

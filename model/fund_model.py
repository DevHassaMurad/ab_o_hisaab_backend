"""PDMA fund ledger.

Donor -> PDMA: `DonorDonation` (DD-IN-26-00001). Counts as received only when payment_status = verified.
PDMA -> recipient: `DonationDisbursement` (DD-OUT-26-00001) to an NGO, shopkeeper or householder.
Each disbursement draws from one verified incoming donation (`source_batch_id`), so every rupee out
traces back to the payment that funded it. A donation can fund many disbursements.

Valid (counted) disbursements: pending, sent, received. Draft and rejected ones do not use funds.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
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
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Receipt voucher number (RCPT-26-00001); assigned once, on first voucher generation, and reused on reprints.
    receipt_no: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True)
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
        Index("ix_disbursements_ngo_request", "ngo_request_id"),
        Index("ix_disbursements_ngo_allocation", "ngo_allocation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    sent_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_disbursements_sent_by"), nullable=False)
    recipient_type: Mapped[str] = mapped_column(String(12), nullable=False)
    recipient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Direct transfers draw from one donor contribution. Transfers under an NGO allocation leave this empty:
    # they are paid from the allocation's reserved funding sources (see funding_disbursements).
    source_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("donor_donations.id", name="fk_disbursements_source_batch_id", ondelete="RESTRICT"),
        nullable=True,
    )
    ngo_allocation_id: Mapped[int | None] = mapped_column(
        ForeignKey("ngo_allocations.id", name="fk_disbursements_ngo_allocation_id"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="sent", server_default="sent")
    status_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # NGO portal: the NGO request this transfer answers, the bank/transfer reference and the NGO's own
    # confirmation of receipt (separate from PDMA approval and from the transfer itself).
    ngo_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("ngo_donation_requests.id", name="fk_disbursements_ngo_request_id"), nullable=True
    )
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supporting_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    receipt_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    receipt_confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_disbursements_receipt_confirmed_by"), nullable=True
    )
    # Direct transfers: when the source donor was told the NGO received this money (sent once).
    donor_receipt_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
        Index("ix_ngo_expenditures_ngo", "ngo_id"),
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
    ngo_id: Mapped[int | None] = mapped_column(ForeignKey("ngos.id", name="fk_ngo_expenditures_ngo_id"), nullable=True)
    expense_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    supporting_document: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_ngo_expenditures_recorded_by"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class NgoDonationRequest(Base):
    """An NGO asks PDMA for funds. Approval sets approved_amount; money moves only through disbursements."""

    __tablename__ = "ngo_donation_requests"
    __table_args__ = (
        CheckConstraint("requested_amount > 0", name="ck_ngo_requests_requested_positive"),
        CheckConstraint(
            "approved_amount IS NULL OR (approved_amount > 0 AND approved_amount <= requested_amount)",
            name="ck_ngo_requests_approved_range",
        ),
        CheckConstraint(
            "status IN ('pending', 'under_review', 'approved', 'partially_approved', 'rejected')",
            name="ck_ngo_requests_status",
        ),
        Index("ix_ngo_requests_ngo_status", "ngo_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    ngo_id: Mapped[int] = mapped_column(ForeignKey("ngos.id", name="fk_ngo_requests_ngo_id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    requested_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    approved_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    supporting_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    info_request: Mapped[str | None] = mapped_column(String(500), nullable=True)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_ngo_requests_submitted_by"), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_ngo_requests_reviewed_by"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_ngo_requests_approved_by"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejected_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_ngo_requests_rejected_by"), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())



class NgoAllocation(Base):
    """PDMA's approved allocation for one NGO request (ALLOC-26-00001). A commitment funded by reserved donor money;
    money moves only through transfers (donation_disbursements with ngo_allocation_id)."""

    __tablename__ = "ngo_allocations"
    __table_args__ = (
        CheckConstraint("approved_amount > 0", name="ck_ngo_allocations_amount_positive"),
        CheckConstraint("status IN ('approved', 'partially_disbursed', 'disbursed', 'cancelled')",
                        name="ck_ngo_allocations_status"),
        Index("ix_ngo_allocations_ngo", "ngo_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    ngo_id: Mapped[int] = mapped_column(ForeignKey("ngos.id", name="fk_ngo_allocations_ngo_id"), nullable=False)
    ngo_request_id: Mapped[int] = mapped_column(
        ForeignKey("ngo_donation_requests.id", name="fk_ngo_allocations_request_id"), unique=True, nullable=False
    )
    approved_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="approved", server_default="approved")
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_ngo_allocations_approved_by"), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_ngo_allocations_cancelled_by"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class AllocationFundingSource(Base):
    """How much of one donor contribution funds one allocation. allocated = reserved at approval;
    disbursed grows with actual transfers; released returns unused money to the contribution."""

    __tablename__ = "allocation_funding_sources"
    __table_args__ = (
        CheckConstraint("allocated_amount > 0", name="ck_funding_sources_allocated_positive"),
        CheckConstraint("disbursed_amount >= 0 AND released_amount >= 0", name="ck_funding_sources_non_negative"),
        CheckConstraint("disbursed_amount + released_amount <= allocated_amount", name="ck_funding_sources_within_allocation"),
        CheckConstraint("funding_status IN ('reserved', 'partially_disbursed', 'disbursed', 'released')",
                        name="ck_funding_sources_status"),
        UniqueConstraint("allocation_id", "donor_donation_id", name="uq_funding_source_allocation_donation"),
        Index("ix_funding_sources_donation", "donor_donation_id"),
        Index("ix_funding_sources_donor", "donor_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    allocation_id: Mapped[int] = mapped_column(ForeignKey("ngo_allocations.id", name="fk_funding_sources_allocation_id"),
                                               nullable=False)
    donor_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_funding_sources_donor_id"), nullable=False)
    donor_donation_id: Mapped[int] = mapped_column(ForeignKey("donor_donations.id", name="fk_funding_sources_donation_id"),
                                                   nullable=False)
    donation_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("donation_requests.id", name="fk_funding_sources_batch_id"), nullable=True)
    allocated_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    disbursed_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0, server_default="0")
    released_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0, server_default="0")
    funding_status: Mapped[str] = mapped_column(String(20), nullable=False, default="reserved", server_default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class FundingDisbursement(Base):
    """Which funding source paid which part of an actual transfer. Voided when the transfer fails."""

    __tablename__ = "funding_disbursements"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_funding_disb_amount_positive"),
        CheckConstraint("status IN ('active', 'voided')", name="ck_funding_disb_status"),
        UniqueConstraint("disbursement_id", "funding_source_id", name="uq_funding_disb_pair"),
        Index("ix_funding_disb_source", "funding_source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disbursement_id: Mapped[int] = mapped_column(
        ForeignKey("donation_disbursements.id", name="fk_funding_disb_disbursement_id"), nullable=False)
    funding_source_id: Mapped[int] = mapped_column(
        ForeignKey("allocation_funding_sources.id", name="fk_funding_disb_source_id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active", server_default="active")
    donor_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # When this donor share was reported as received by the NGO (sent once, after receipt confirmation).
    donor_receipt_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class FundLedgerEvent(Base):
    """Append-only audit trail of every reservation, release, approval and disbursement."""

    __tablename__ = "fund_ledger_events"
    __table_args__ = (
        CheckConstraint("event_type IN ('approved', 'reserved', 'released', 'disbursed', 'disbursement_voided', "
                        "'cancelled')", name="ck_ledger_event_type"),
        Index("ix_ledger_allocation", "allocation_id"),
        Index("ix_ledger_donation", "donor_donation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    allocation_id: Mapped[int | None] = mapped_column(ForeignKey("ngo_allocations.id", name="fk_ledger_allocation_id"),
                                                      nullable=True)
    funding_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("allocation_funding_sources.id", name="fk_ledger_funding_source_id"), nullable=True)
    disbursement_id: Mapped[int | None] = mapped_column(
        ForeignKey("donation_disbursements.id", name="fk_ledger_disbursement_id"), nullable=True)
    donor_donation_id: Mapped[int | None] = mapped_column(
        ForeignKey("donor_donations.id", name="fk_ledger_donor_donation_id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_ledger_actor_id"), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

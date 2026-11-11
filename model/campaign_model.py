"""Donation campaigns. Separate from general donor donations, NGO requests and allocations:
campaign money lives only in campaign_contributions and is never a general donation batch."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base

MONEY = Numeric(14, 2)
CAMPAIGN_STATUSES = ("draft", "pending_review", "under_review", "changes_requested", "approved", "scheduled",
                     "active", "goal_reached", "completed", "rejected", "paused", "cancelled")
# Visible to donors once published (scheduled campaigns are visible as "upcoming").
DONOR_VISIBLE = ("scheduled", "active", "goal_reached", "completed", "paused", "cancelled")
_STATUS_SQL = ", ".join(f"'{s}'" for s in CAMPAIGN_STATUSES)


class DonationCampaign(Base):
    __tablename__ = "donation_campaigns"
    __table_args__ = (
        CheckConstraint("source_type IN ('PDMA', 'NGO')", name="ck_campaigns_source"),
        CheckConstraint("target_amount > 0", name="ck_campaigns_target_positive"),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="ck_campaigns_status"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_campaigns_dates"),
        CheckConstraint("source_type = 'PDMA' OR ngo_id IS NOT NULL", name="ck_campaigns_ngo_owner"),
        CheckConstraint("overfunding_limit IS NULL OR overfunding_limit >= target_amount",
                        name="ck_campaigns_overfunding_limit"),
        Index("ix_campaigns_status", "status"),
        Index("ix_campaigns_ngo", "ngo_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(10), nullable=False)
    ngo_id: Mapped[int | None] = mapped_column(ForeignKey("ngos.id", name="fk_campaigns_ngo_id"), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_campaigns_created_by"), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    short_description: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    target_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    allow_overfunding: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    overfunding_limit: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    district: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_info: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cover_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaign_documents.id", name="fk_campaigns_cover_document_id", use_alter=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_campaigns_verified_by"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_campaigns_approved_by"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_campaigns_published_by"), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changes_requested: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # PDMA-internal
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class CampaignDocument(Base):
    __tablename__ = "campaign_documents"
    __table_args__ = (
        CheckConstraint("document_type IN ('cover', 'supporting')", name="ck_campaign_docs_type"),
        CheckConstraint("visibility IN ('internal', 'donor_public')", name="ck_campaign_docs_visibility"),
        Index("ix_campaign_docs_campaign", "campaign_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("donation_campaigns.id", name="fk_campaign_docs_campaign_id"),
                                             nullable=False)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_campaign_docs_uploaded_by"), nullable=False)
    document_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Random server-side file name; real paths are never sent to clients.
    storage_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    document_type: Mapped[str] = mapped_column(String(20), nullable=False)
    visibility: Mapped[str] = mapped_column(String(15), nullable=False, default="internal", server_default="internal")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class CampaignContribution(Base):
    __tablename__ = "campaign_contributions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_campaign_contrib_amount_positive"),
        CheckConstraint("payment_status IN ('pending', 'verified', 'rejected')", name="ck_campaign_contrib_status"),
        Index("ix_campaign_contrib_campaign", "campaign_id", "payment_status"),
        Index("ix_campaign_contrib_donor", "donor_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("donation_campaigns.id", name="fk_campaign_contrib_campaign_id"),
                                             nullable=False)
    donor_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_campaign_contrib_donor_id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending", server_default="pending")
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_campaign_contrib_verified_by"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class CampaignEvent(Base):
    """Append-only history of every campaign status change."""

    __tablename__ = "campaign_events"
    __table_args__ = (Index("ix_campaign_events_campaign", "campaign_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("donation_campaigns.id", name="fk_campaign_events_campaign_id"),
                                             nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_campaign_events_actor_id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class CampaignNotice(Base):
    """A one-off broadcast already sent for a campaign (prevents duplicate notifications)."""

    __tablename__ = "campaign_notices"
    __table_args__ = (UniqueConstraint("campaign_id", "notice_key", name="uq_campaign_notices_once"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("donation_campaigns.id", name="fk_campaign_notices_campaign_id"),
                                             nullable=False)
    notice_key: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

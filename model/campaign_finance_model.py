"""Campaign finance (migration 0015): master batch -> NGO collaboration -> NGO sub-batch -> releases (exact donor
sources) -> NGO distribution campaign -> beneficiary distributions (exact sources), plus a double-entry ledger."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (JSON, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String,  # noqa: F401
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base

MONEY = Numeric(14, 2)


def _ts():
    return (mapped_column(DateTime, nullable=False, server_default=func.now()),
            mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now()))


class CampaignMasterBatch(Base):
    __tablename__ = "campaign_master_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("donation_campaigns.id", name="fk_cmb_campaign"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class CollaborationCampaign(Base):
    """PDMA-created parent campaign; selected NGOs participate through campaign_collaborations rows."""
    __tablename__ = "collaboration_campaigns"
    __table_args__ = (CheckConstraint("status IN ('draft', 'launched', 'completed', 'cancelled')", name="ck_colc_status"),
                      CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_colc_dates"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(4000))
    objective: Mapped[str] = mapped_column(String(1000))
    districts: Mapped[str] = mapped_column(String(500))
    operational_areas: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_budget: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    available_funding: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    instructions: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    required_capabilities: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    funding_campaign_id: Mapped[int | None] = mapped_column(ForeignKey("donation_campaigns.id", name="fk_colc_funding"),
                                                            nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="draft", server_default="draft")
    launched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_colc_created_by"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class CampaignCollaboration(Base):
    """One NGO's participation / invitation in a collaboration campaign (funding link optional)."""
    __tablename__ = "campaign_collaborations"
    __table_args__ = (
        CheckConstraint("assigned_amount IS NULL OR assigned_amount > 0", name="ck_ccol_amount"),
        Index("ix_ccol_parent", "collaboration_campaign_id"),
        CheckConstraint("status IN ('draft', 'invited', 'accepted', 'rejected', 'cancelled', 'completed')",
                        name="ck_ccol_status"),
        Index("ix_ccol_campaign", "campaign_id"),
        Index("ix_ccol_ngo", "ngo_id", "status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    collaboration_campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("collaboration_campaigns.id", name="fk_ccol_parent"), nullable=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("donation_campaigns.id", name="fk_ccol_campaign"),
                                                    nullable=True)  # funding (fundraising) campaign, optional
    ngo_id: Mapped[int] = mapped_column(ForeignKey("ngos.id", name="fk_ccol_ngo"))
    assigned_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # proposed / funded allocation
    scope_of_work: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    instructions: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    district: Mapped[str] = mapped_column(String(100))
    operational_area: Mapped[str | None] = mapped_column(String(200), nullable=True)
    responsibility: Mapped[str] = mapped_column(String(1000))
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_completion: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    invited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    responded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_ccol_responded_by"), nullable=True)
    response_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_ccol_created_by"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class NgoCampaignBatch(Base):
    __tablename__ = "ngo_campaign_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    collaboration_id: Mapped[int] = mapped_column(ForeignKey("campaign_collaborations.id", name="fk_ncb_collab"), unique=True)
    master_batch_id: Mapped[int] = mapped_column(ForeignKey("campaign_master_batches.id", name="fk_ncb_master"))
    ngo_id: Mapped[int] = mapped_column(ForeignKey("ngos.id", name="fk_ncb_ngo"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class CampaignFundRelease(Base):
    __tablename__ = "campaign_fund_releases"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_cfr_amount"),
        CheckConstraint("status IN ('in_transit', 'acknowledged', 'cancelled')", name="ck_cfr_status"),
        Index("ix_cfr_batch", "batch_id", "status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("ngo_campaign_batches.id", name="fk_cfr_batch"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(12), default="in_transit", server_default="in_transit")
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    transfer_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)
    released_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_cfr_released_by"))
    released_at: Mapped[datetime] = mapped_column(DateTime)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_cfr_ack_by"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    donors_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class CampaignReleaseSource(Base):
    __tablename__ = "campaign_release_sources"
    __table_args__ = (UniqueConstraint("release_id", "contribution_id", name="uq_crs_once"),
                      CheckConstraint("amount > 0", name="ck_crs_amount"),
                      Index("ix_crs_contribution", "contribution_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("campaign_fund_releases.id", name="fk_crs_release"))
    contribution_id: Mapped[int] = mapped_column(ForeignKey("campaign_contributions.id", name="fk_crs_contribution"))
    amount: Mapped[Decimal] = mapped_column(MONEY)


class NgoDistributionCampaign(Base):
    __tablename__ = "ngo_distribution_campaigns"
    __table_args__ = (CheckConstraint("planned_amount IS NULL OR planned_amount > 0", name="ck_ndc_amount"),
                      CheckConstraint("status IN ('draft', 'active', 'completed', 'cancelled')", name="ck_ndc_status"),
                      Index("ix_ndc_ngo", "ngo_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    ngo_id: Mapped[int] = mapped_column(ForeignKey("ngos.id", name="fk_ndc_ngo"))
    collaboration_id: Mapped[int] = mapped_column(ForeignKey("campaign_collaborations.id", name="fk_ndc_collab"))
    collaboration_campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("collaboration_campaigns.id", name="fk_ndc_parent"), nullable=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("ngo_campaign_batches.id", name="fk_ndc_batch"), nullable=True)
    target_beneficiaries: Mapped[str | None] = mapped_column(String(500), nullable=True)
    locations: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    expected_beneficiaries: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    name: Mapped[str] = mapped_column(String(150))
    district: Mapped[str] = mapped_column(String(100))
    operational_area: Mapped[str | None] = mapped_column(String(200), nullable=True)
    objective: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    planned_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="active", server_default="active")
    donors_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_ndc_created_by"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class DistributionBeneficiary(Base):
    __tablename__ = "distribution_beneficiaries"
    __table_args__ = (UniqueConstraint("distribution_campaign_id", "user_id", name="uq_dben_once"),
                      CheckConstraint("beneficiary_type IN ('householder', 'shopkeeper')", name="ck_dben_type"),
                      CheckConstraint("status IN ('selected', 'partially_paid', 'paid', 'removed')", name="ck_dben_status"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    distribution_campaign_id: Mapped[int] = mapped_column(ForeignKey("ngo_distribution_campaigns.id", name="fk_dben_campaign"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_dben_user"))
    beneficiary_type: Mapped[str] = mapped_column(String(12))
    planned_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    assistance_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12), default="selected", server_default="selected")
    added_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_dben_added_by"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class BeneficiaryDistribution(Base):
    __tablename__ = "beneficiary_distributions"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_bdis_amount"),
                      CheckConstraint("status IN ('recorded', 'reversed')", name="ck_bdis_status"),
                      Index("ix_bdis_campaign", "distribution_campaign_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    distribution_campaign_id: Mapped[int] = mapped_column(ForeignKey("ngo_distribution_campaigns.id", name="fk_bdis_campaign"))
    beneficiary_id: Mapped[int] = mapped_column(ForeignKey("distribution_beneficiaries.id", name="fk_bdis_ben"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    distribution_date: Mapped[date] = mapped_column(Date)
    payment_method: Mapped[str] = mapped_column(String(20))
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    proof_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="recorded", server_default="recorded")
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id", name="fk_bdis_recorded_by"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class DistributionFundingSource(Base):
    __tablename__ = "distribution_funding_sources"
    __table_args__ = (UniqueConstraint("distribution_id", "release_id", "contribution_id", name="uq_dfs_once"),
                      CheckConstraint("amount > 0", name="ck_dfs_amount"),
                      Index("ix_dfs_contribution", "contribution_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    distribution_id: Mapped[int] = mapped_column(ForeignKey("beneficiary_distributions.id", name="fk_dfs_dist"))
    release_id: Mapped[int] = mapped_column(ForeignKey("campaign_fund_releases.id", name="fk_dfs_release"))
    contribution_id: Mapped[int] = mapped_column(ForeignKey("campaign_contributions.id", name="fk_dfs_contribution"))
    amount: Mapped[Decimal] = mapped_column(MONEY)


class FinanceAccount(Base):
    __tablename__ = "finance_accounts"
    __table_args__ = (CheckConstraint("kind IN ('donor_receipts', 'campaign_funds', 'ngo_in_transit', 'ngo_batch_funds', "
                                      "'beneficiary_payments')", name="ck_facc_kind"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(200))
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("donation_campaigns.id", name="fk_facc_campaign"), nullable=True)
    ngo_id: Mapped[int | None] = mapped_column(ForeignKey("ngos.id", name="fk_facc_ngo"), nullable=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("ngo_campaign_batches.id", name="fk_facc_batch"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class FinanceLedgerEntry(Base):
    __tablename__ = "finance_ledger_entries"
    __table_args__ = (
        UniqueConstraint("txn_ref", "account_id", name="uq_fle_once"),
        CheckConstraint("debit >= 0 AND credit >= 0 AND (debit = 0 OR credit = 0) AND debit + credit > 0",
                        name="ck_fle_one_side"),
        Index("ix_fle_account", "account_id", "posted_at"),
        Index("ix_fle_campaign", "campaign_id"),
        Index("ix_fle_source", "source_type", "source_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    txn_ref: Mapped[str] = mapped_column(String(40))
    account_id: Mapped[int] = mapped_column(ForeignKey("finance_accounts.id", name="fk_fle_account"))
    debit: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    credit: Mapped[Decimal] = mapped_column(MONEY, default=0, server_default="0")
    event_type: Mapped[str] = mapped_column(String(30))
    source_type: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[int] = mapped_column(Integer)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memo: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reversal_of: Mapped[str | None] = mapped_column(String(40), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime)
    posted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

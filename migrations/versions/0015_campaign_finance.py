"""Campaign finance: master batches, NGO collaborations, NGO sub-batches with partial releases (exact donor
attribution), NGO distribution campaigns, beneficiary distributions (exact attribution) and a double-entry ledger.
Additive only; no existing table is changed."""
from alembic import op
import sqlalchemy as sa

revision = "0015_campaign_finance"
down_revision = "0014_beneficiaries"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)


def ts():
    return [sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now())]


def upgrade():
    op.create_table(
        "campaign_master_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference_no", sa.String(30), nullable=False, unique=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("donation_campaigns.id", name="fk_cmb_campaign"),
                  nullable=False, unique=True),
        *ts(),
    )
    op.create_table(
        "campaign_collaborations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference_no", sa.String(30), nullable=False, unique=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("donation_campaigns.id", name="fk_ccol_campaign"), nullable=False),
        sa.Column("ngo_id", sa.Integer(), sa.ForeignKey("ngos.id", name="fk_ccol_ngo"), nullable=False),
        sa.Column("assigned_amount", MONEY, nullable=False),
        sa.Column("district", sa.String(100), nullable=False),
        sa.Column("operational_area", sa.String(200), nullable=True),
        sa.Column("responsibility", sa.String(1000), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("expected_completion", sa.Date(), nullable=True),
        sa.Column("remarks", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("invited_at", sa.DateTime(), nullable=True),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.Column("responded_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_ccol_responded_by"), nullable=True),
        sa.Column("response_note", sa.String(1000), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_ccol_created_by"), nullable=False),
        *ts(),
        sa.CheckConstraint("assigned_amount > 0", name="ck_ccol_amount"),
        sa.CheckConstraint("status IN ('draft', 'invited', 'accepted', 'rejected', 'cancelled', 'completed')",
                           name="ck_ccol_status"),
    )
    op.create_index("ix_ccol_campaign", "campaign_collaborations", ["campaign_id"])
    op.create_index("ix_ccol_ngo", "campaign_collaborations", ["ngo_id", "status"])
    op.create_table(
        "ngo_campaign_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference_no", sa.String(30), nullable=False, unique=True),
        sa.Column("collaboration_id", sa.Integer(), sa.ForeignKey("campaign_collaborations.id", name="fk_ncb_collab"),
                  nullable=False, unique=True),
        sa.Column("master_batch_id", sa.Integer(), sa.ForeignKey("campaign_master_batches.id", name="fk_ncb_master"),
                  nullable=False),
        sa.Column("ngo_id", sa.Integer(), sa.ForeignKey("ngos.id", name="fk_ncb_ngo"), nullable=False),
        *ts(),
    )
    op.create_table(
        "campaign_fund_releases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference_no", sa.String(30), nullable=False, unique=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("ngo_campaign_batches.id", name="fk_cfr_batch"), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="in_transit"),
        sa.Column("payment_method", sa.String(20), nullable=True),
        sa.Column("transfer_reference", sa.String(100), nullable=True),
        sa.Column("remarks", sa.String(500), nullable=True),
        sa.Column("released_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_cfr_released_by"), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=False),
        sa.Column("acknowledged_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_cfr_ack_by"), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_reason", sa.String(500), nullable=True),
        sa.Column("donors_notified_at", sa.DateTime(), nullable=True),
        *ts(),
        sa.CheckConstraint("amount > 0", name="ck_cfr_amount"),
        sa.CheckConstraint("status IN ('in_transit', 'acknowledged', 'cancelled')", name="ck_cfr_status"),
    )
    op.create_index("ix_cfr_batch", "campaign_fund_releases", ["batch_id", "status"])
    op.create_table(
        "campaign_release_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("release_id", sa.Integer(), sa.ForeignKey("campaign_fund_releases.id", name="fk_crs_release"), nullable=False),
        sa.Column("contribution_id", sa.Integer(), sa.ForeignKey("campaign_contributions.id", name="fk_crs_contribution"),
                  nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.UniqueConstraint("release_id", "contribution_id", name="uq_crs_once"),
        sa.CheckConstraint("amount > 0", name="ck_crs_amount"),
    )
    op.create_index("ix_crs_contribution", "campaign_release_sources", ["contribution_id"])
    op.create_table(
        "ngo_distribution_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference_no", sa.String(30), nullable=False, unique=True),
        sa.Column("ngo_id", sa.Integer(), sa.ForeignKey("ngos.id", name="fk_ndc_ngo"), nullable=False),
        sa.Column("collaboration_id", sa.Integer(), sa.ForeignKey("campaign_collaborations.id", name="fk_ndc_collab"),
                  nullable=False),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("ngo_campaign_batches.id", name="fk_ndc_batch"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("district", sa.String(100), nullable=False),
        sa.Column("operational_area", sa.String(200), nullable=True),
        sa.Column("objective", sa.String(500), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("planned_amount", MONEY, nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("remarks", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("donors_notified_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_ndc_created_by"), nullable=False),
        *ts(),
        sa.CheckConstraint("planned_amount > 0", name="ck_ndc_amount"),
        sa.CheckConstraint("status IN ('active', 'completed', 'cancelled')", name="ck_ndc_status"),
    )
    op.create_index("ix_ndc_ngo", "ngo_distribution_campaigns", ["ngo_id"])
    op.create_table(
        "distribution_beneficiaries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("distribution_campaign_id", sa.Integer(),
                  sa.ForeignKey("ngo_distribution_campaigns.id", name="fk_dben_campaign"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_dben_user"), nullable=False),
        sa.Column("beneficiary_type", sa.String(12), nullable=False),
        sa.Column("planned_amount", MONEY, nullable=True),
        sa.Column("assistance_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="selected"),
        sa.Column("added_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_dben_added_by"), nullable=False),
        *ts(),
        sa.UniqueConstraint("distribution_campaign_id", "user_id", name="uq_dben_once"),
        sa.CheckConstraint("beneficiary_type IN ('householder', 'shopkeeper')", name="ck_dben_type"),
        sa.CheckConstraint("status IN ('selected', 'partially_paid', 'paid', 'removed')", name="ck_dben_status"),
    )
    op.create_table(
        "beneficiary_distributions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference_no", sa.String(30), nullable=False, unique=True),
        sa.Column("distribution_campaign_id", sa.Integer(),
                  sa.ForeignKey("ngo_distribution_campaigns.id", name="fk_bdis_campaign"), nullable=False),
        sa.Column("beneficiary_id", sa.Integer(), sa.ForeignKey("distribution_beneficiaries.id", name="fk_bdis_ben"),
                  nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("distribution_date", sa.Date(), nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=False),
        sa.Column("payment_reference", sa.String(100), nullable=True),
        sa.Column("proof_reference", sa.String(200), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="recorded"),
        sa.Column("idempotency_key", sa.String(64), nullable=True, unique=True),
        sa.Column("reversal_reason", sa.String(500), nullable=True),
        sa.Column("reversed_at", sa.DateTime(), nullable=True),
        sa.Column("recorded_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_bdis_recorded_by"), nullable=False),
        *ts(),
        sa.CheckConstraint("amount > 0", name="ck_bdis_amount"),
        sa.CheckConstraint("status IN ('recorded', 'reversed')", name="ck_bdis_status"),
    )
    op.create_index("ix_bdis_campaign", "beneficiary_distributions", ["distribution_campaign_id"])
    op.create_table(
        "distribution_funding_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("distribution_id", sa.Integer(), sa.ForeignKey("beneficiary_distributions.id", name="fk_dfs_dist"),
                  nullable=False),
        sa.Column("release_id", sa.Integer(), sa.ForeignKey("campaign_fund_releases.id", name="fk_dfs_release"), nullable=False),
        sa.Column("contribution_id", sa.Integer(), sa.ForeignKey("campaign_contributions.id", name="fk_dfs_contribution"),
                  nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.UniqueConstraint("distribution_id", "release_id", "contribution_id", name="uq_dfs_once"),
        sa.CheckConstraint("amount > 0", name="ck_dfs_amount"),
    )
    op.create_index("ix_dfs_contribution", "distribution_funding_sources", ["contribution_id"])
    op.create_table(
        "finance_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("donation_campaigns.id", name="fk_facc_campaign"), nullable=True),
        sa.Column("ngo_id", sa.Integer(), sa.ForeignKey("ngos.id", name="fk_facc_ngo"), nullable=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("ngo_campaign_batches.id", name="fk_facc_batch"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('donor_receipts', 'campaign_funds', 'ngo_in_transit', 'ngo_batch_funds', "
                           "'beneficiary_payments')", name="ck_facc_kind"),
    )
    op.create_table(
        "finance_ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("txn_ref", sa.String(40), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("finance_accounts.id", name="fk_fle_account"), nullable=False),
        sa.Column("debit", MONEY, nullable=False, server_default="0"),
        sa.Column("credit", MONEY, nullable=False, server_default="0"),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=True),
        sa.Column("memo", sa.String(300), nullable=True),
        sa.Column("reversal_of", sa.String(40), nullable=True),
        sa.Column("posted_at", sa.DateTime(), nullable=False),
        sa.Column("posted_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("txn_ref", "account_id", name="uq_fle_once"),
        sa.CheckConstraint("debit >= 0 AND credit >= 0 AND (debit = 0 OR credit = 0) AND debit + credit > 0",
                           name="ck_fle_one_side"),
    )
    op.create_index("ix_fle_account", "finance_ledger_entries", ["account_id", "posted_at"])
    op.create_index("ix_fle_campaign", "finance_ledger_entries", ["campaign_id"])
    op.create_index("ix_fle_source", "finance_ledger_entries", ["source_type", "source_id"])


def downgrade():
    for idx, table in (("ix_fle_source", "finance_ledger_entries"), ("ix_fle_campaign", "finance_ledger_entries"),
                       ("ix_fle_account", "finance_ledger_entries")):
        op.drop_index(idx, table_name=table)
    op.drop_table("finance_ledger_entries")
    op.drop_table("finance_accounts")
    op.drop_index("ix_dfs_contribution", table_name="distribution_funding_sources")
    op.drop_table("distribution_funding_sources")
    op.drop_index("ix_bdis_campaign", table_name="beneficiary_distributions")
    op.drop_table("beneficiary_distributions")
    op.drop_table("distribution_beneficiaries")
    op.drop_index("ix_ndc_ngo", table_name="ngo_distribution_campaigns")
    op.drop_table("ngo_distribution_campaigns")
    op.drop_index("ix_crs_contribution", table_name="campaign_release_sources")
    op.drop_table("campaign_release_sources")
    op.drop_index("ix_cfr_batch", table_name="campaign_fund_releases")
    op.drop_table("campaign_fund_releases")
    op.drop_table("ngo_campaign_batches")
    op.drop_index("ix_ccol_ngo", table_name="campaign_collaborations")
    op.drop_index("ix_ccol_campaign", table_name="campaign_collaborations")
    op.drop_table("campaign_collaborations")
    op.drop_table("campaign_master_batches")

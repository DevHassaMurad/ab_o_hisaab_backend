"""Donation campaigns (PDMA-created and NGO-proposed), campaign contributions, campaign documents and a
campaign event log. Kept separate from general donor donations, NGO requests and allocations."""
from alembic import op
import sqlalchemy as sa

revision = "0011_campaigns"
down_revision = "0010_donor_ledger"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)
STATUSES = ("draft", "pending_review", "under_review", "changes_requested", "approved", "scheduled", "active",
            "goal_reached", "completed", "rejected", "paused", "cancelled")


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    ]


def upgrade():
    status_list = ", ".join(f"'{s}'" for s in STATUSES)
    op.create_table(
        "donation_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference_no", sa.String(30), nullable=False),
        sa.Column("source_type", sa.String(10), nullable=False),
        sa.Column("ngo_id", sa.Integer(), sa.ForeignKey("ngos.id", name="fk_campaigns_ngo_id"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_campaigns_created_by"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("short_description", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("objective", sa.String(500), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=True),
        sa.Column("target_amount", MONEY, nullable=False),
        sa.Column("allow_overfunding", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("overfunding_limit", MONEY, nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("district", sa.String(100), nullable=True),
        sa.Column("contact_info", sa.String(300), nullable=True),
        sa.Column("cover_document_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("verified_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_campaigns_verified_by"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_campaigns_approved_by"), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_campaigns_published_by"), nullable=True),
        sa.Column("rejected_at", sa.DateTime(), nullable=True),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("changes_requested", sa.String(1000), nullable=True),
        sa.Column("cancellation_reason", sa.String(500), nullable=True),
        sa.Column("review_notes", sa.String(1000), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("reference_no", name="uq_campaigns_reference_no"),
        sa.CheckConstraint("source_type IN ('PDMA', 'NGO')", name="ck_campaigns_source"),
        sa.CheckConstraint("target_amount > 0", name="ck_campaigns_target_positive"),
        sa.CheckConstraint(f"status IN ({status_list})", name="ck_campaigns_status"),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_campaigns_dates"),
        sa.CheckConstraint("source_type = 'PDMA' OR ngo_id IS NOT NULL", name="ck_campaigns_ngo_owner"),
        sa.CheckConstraint("overfunding_limit IS NULL OR overfunding_limit >= target_amount",
                           name="ck_campaigns_overfunding_limit"),
    )
    op.create_index("ix_campaigns_status", "donation_campaigns", ["status"])
    op.create_index("ix_campaigns_ngo", "donation_campaigns", ["ngo_id"])

    op.create_table(
        "campaign_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("donation_campaigns.id", name="fk_campaign_docs_campaign_id"),
                  nullable=False),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_campaign_docs_uploaded_by"), nullable=False),
        sa.Column("document_name", sa.String(200), nullable=False),
        sa.Column("storage_key", sa.String(200), nullable=False),
        sa.Column("content_type", sa.String(50), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(20), nullable=False),
        sa.Column("visibility", sa.String(15), nullable=False, server_default="internal"),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("storage_key", name="uq_campaign_docs_storage_key"),
        sa.CheckConstraint("document_type IN ('cover', 'supporting')", name="ck_campaign_docs_type"),
        sa.CheckConstraint("visibility IN ('internal', 'donor_public')", name="ck_campaign_docs_visibility"),
    )
    op.create_index("ix_campaign_docs_campaign", "campaign_documents", ["campaign_id"])
    with op.batch_alter_table("donation_campaigns") as batch:
        batch.create_foreign_key("fk_campaigns_cover_document_id", "campaign_documents", ["cover_document_id"], ["id"])

    op.create_table(
        "campaign_contributions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference_no", sa.String(30), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("donation_campaigns.id", name="fk_campaign_contrib_campaign_id"),
                  nullable=False),
        sa.Column("donor_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_campaign_contrib_donor_id"), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=True),
        sa.Column("payment_reference", sa.String(100), nullable=True),
        sa.Column("payment_status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("verified_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_campaign_contrib_verified_by"), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("remarks", sa.String(500), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("reference_no", name="uq_campaign_contrib_reference_no"),
        sa.CheckConstraint("amount > 0", name="ck_campaign_contrib_amount_positive"),
        sa.CheckConstraint("payment_status IN ('pending', 'verified', 'rejected')", name="ck_campaign_contrib_status"),
    )
    op.create_index("ix_campaign_contrib_campaign", "campaign_contributions", ["campaign_id", "payment_status"])
    op.create_index("ix_campaign_contrib_donor", "campaign_contributions", ["donor_id"])

    op.create_table(
        "campaign_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("donation_campaigns.id", name="fk_campaign_events_campaign_id"),
                  nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_campaign_events_actor_id"), nullable=True),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_campaign_events_campaign", "campaign_events", ["campaign_id"])
    # One-off notices (published, started, goal_reached, completed ...) are sent once per campaign.
    op.create_table(
        "campaign_notices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("donation_campaigns.id", name="fk_campaign_notices_campaign_id"),
                  nullable=False),
        sa.Column("notice_key", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("campaign_id", "notice_key", name="uq_campaign_notices_once"),
    )


def downgrade():
    op.drop_table("campaign_notices")
    op.drop_index("ix_campaign_events_campaign", table_name="campaign_events")
    op.drop_table("campaign_events")
    op.drop_index("ix_campaign_contrib_donor", table_name="campaign_contributions")
    op.drop_index("ix_campaign_contrib_campaign", table_name="campaign_contributions")
    op.drop_table("campaign_contributions")
    with op.batch_alter_table("donation_campaigns") as batch:
        batch.drop_constraint("fk_campaigns_cover_document_id", type_="foreignkey")
    op.drop_index("ix_campaign_docs_campaign", table_name="campaign_documents")
    op.drop_table("campaign_documents")
    op.drop_index("ix_campaigns_ngo", table_name="donation_campaigns")
    op.drop_index("ix_campaigns_status", table_name="donation_campaigns")
    op.drop_table("donation_campaigns")

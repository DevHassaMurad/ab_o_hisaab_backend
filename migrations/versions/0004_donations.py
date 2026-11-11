"""Donor management: donor profiles, NGOs, donation requests (batches), donations, fund allocations,
document numbering and persistent notifications. Also records when each user registered."""
from alembic import op
import sqlalchemy as sa

revision = "0004_donations"
down_revision = "0003_auth_verification"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    ]


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))

    op.create_table(
        "donor_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", name="fk_donor_profiles_user_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("organization", sa.String(150), nullable=True),
        sa.Column("phone", sa.String(30), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("user_id", name="uq_donor_profiles_user_id"),
    )
    op.create_table(
        "ngos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("registration_no", sa.String(50), nullable=True),
        sa.Column("contact_email", sa.String(100), nullable=True),
        sa.Column("contact_phone", sa.String(30), nullable=True),
        sa.Column("district_id", sa.String(36), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_ngos_user_id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *timestamps(),
        sa.UniqueConstraint("registration_no", name="uq_ngos_registration_no"),
    )
    op.create_table(
        "document_sequences",
        sa.Column("prefix", sa.String(40), primary_key=True),
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("last_no", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "donation_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_no", sa.String(30), nullable=False),
        sa.Column("type", sa.String(10), nullable=False, server_default="donor"),
        sa.Column("donor_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_donation_requests_donor_id"), nullable=True),
        sa.Column("ngo_id", sa.Integer(), sa.ForeignKey("ngos.id", name="fk_donation_requests_ngo_id"), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PKR"),
        sa.Column("requested_amount", MONEY, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="SENT"),
        sa.Column(
            "created_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_donation_requests_created_by"), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("document_no", name="uq_donation_requests_document_no"),
        sa.CheckConstraint("type IN ('donor', 'ngo')", name="ck_donation_requests_type"),
        sa.CheckConstraint("requested_amount > 0", name="ck_donation_requests_amount_positive"),
    )
    op.create_index("ix_donation_requests_donor_id", "donation_requests", ["donor_id"])
    op.create_index("ix_donation_requests_ngo_id", "donation_requests", ["ngo_id"])

    op.create_table(
        "donations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id", sa.Integer(),
            sa.ForeignKey("donation_requests.id", name="fk_donations_batch_id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("donor_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_donations_donor_id"), nullable=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("payment_reference", sa.String(100), nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("verification_status", sa.String(10), nullable=False, server_default="PENDING"),
        sa.Column("verified_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_donations_verified_by"), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column(
            "recorded_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_donations_recorded_by"), nullable=False
        ),
        sa.Column("remarks", sa.String(500), nullable=True),
        *timestamps(),
        sa.CheckConstraint("amount > 0", name="ck_donations_amount_positive"),
        sa.CheckConstraint(
            "verification_status IN ('PENDING', 'VERIFIED', 'REJECTED')", name="ck_donations_verification_status"
        ),
    )
    op.create_index("ix_donations_batch_id", "donations", ["batch_id"])
    op.create_index("ix_donations_donor_id", "donations", ["donor_id"])

    op.create_table(
        "fund_allocations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id", sa.Integer(),
            sa.ForeignKey("donation_requests.id", name="fk_fund_allocations_batch_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ngo_id", sa.Integer(), sa.ForeignKey("ngos.id", name="fk_fund_allocations_ngo_id"), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("allocation_date", sa.Date(), nullable=False),
        sa.Column("remarks", sa.String(500), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "allocated_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_fund_allocations_allocated_by"),
            nullable=False,
        ),
        *timestamps(),
        sa.CheckConstraint("amount > 0", name="ck_fund_allocations_amount_positive"),
        sa.CheckConstraint("status IN ('ACTIVE', 'CANCELLED')", name="ck_fund_allocations_status"),
    )
    op.create_index("ix_fund_allocations_batch_id", "fund_allocations", ["batch_id"])
    op.create_index("ix_fund_allocations_ngo_id", "fund_allocations", ["ngo_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", name="fk_notifications_user_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("related_id", sa.Integer(), nullable=True),
        sa.Column("related_type", sa.String(40), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        *timestamps(),
    )
    op.create_index("ix_notifications_user_read", "notifications", ["user_id", "read_at"])


def downgrade():
    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_fund_allocations_ngo_id", table_name="fund_allocations")
    op.drop_index("ix_fund_allocations_batch_id", table_name="fund_allocations")
    op.drop_table("fund_allocations")
    op.drop_index("ix_donations_donor_id", table_name="donations")
    op.drop_index("ix_donations_batch_id", table_name="donations")
    op.drop_table("donations")
    op.drop_index("ix_donation_requests_ngo_id", table_name="donation_requests")
    op.drop_index("ix_donation_requests_donor_id", table_name="donation_requests")
    op.drop_table("donation_requests")
    op.drop_table("document_sequences")
    op.drop_table("ngos")
    op.drop_table("donor_profiles")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("created_at")

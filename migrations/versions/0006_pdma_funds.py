"""PDMA fund ledger: incoming donor donations (DD-IN), outgoing disbursements (DD-OUT) to NGOs,
shopkeepers and householders, request acceptance details and an email outbox with retries.
Existing tables are kept unchanged. Shopkeeper and household registers are owned by the
beneficiary-register work and are not created here."""
from alembic import op
import sqlalchemy as sa

revision = "0006_pdma_funds"
down_revision = "0005_donor_response"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    ]


def upgrade():
    with op.batch_alter_table("donation_requests") as batch:
        batch.add_column(sa.Column("accepted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("accepted_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("rejected_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key("fk_donation_requests_accepted_by", "users", ["accepted_by"], ["id"])

    op.create_table(
        "donor_donations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_no", sa.String(30), nullable=False),
        sa.Column("donor_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_donor_donations_donor_id"), nullable=False),
        sa.Column(
            "request_id", sa.Integer(),
            sa.ForeignKey("donation_requests.id", name="fk_donor_donations_request_id"), nullable=True,
        ),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("remarks", sa.String(500), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("payment_status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("payment_reference", sa.String(100), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("verified_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_donor_donations_verified_by"), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("document_no", name="uq_donor_donations_document_no"),
        sa.CheckConstraint("amount > 0", name="ck_donor_donations_amount_positive"),
        sa.CheckConstraint(
            "payment_status IN ('pending', 'verified', 'rejected')", name="ck_donor_donations_payment_status"
        ),
    )
    op.create_index("ix_donor_donations_donor_id", "donor_donations", ["donor_id"])
    op.create_index("ix_donor_donations_status", "donor_donations", ["payment_status"])

    op.create_table(
        "donation_disbursements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_no", sa.String(30), nullable=False),
        sa.Column("sent_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_disbursements_sent_by"), nullable=False),
        sa.Column("recipient_type", sa.String(12), nullable=False),
        sa.Column("recipient_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_batch_id", sa.Integer(),
            sa.ForeignKey("donor_donations.id", name="fk_disbursements_source_batch_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("remarks", sa.String(500), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="sent"),
        sa.Column("status_reason", sa.String(500), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("document_no", name="uq_disbursements_document_no"),
        sa.CheckConstraint("amount > 0", name="ck_disbursements_amount_positive"),
        sa.CheckConstraint(
            "recipient_type IN ('ngo', 'shopkeeper', 'householder')", name="ck_disbursements_recipient_type"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending', 'sent', 'received', 'rejected')", name="ck_disbursements_status"
        ),
    )
    op.create_index("ix_disbursements_source", "donation_disbursements", ["source_batch_id"])
    op.create_index("ix_disbursements_recipient", "donation_disbursements", ["recipient_type", "recipient_id"])
    op.create_index("ix_disbursements_status", "donation_disbursements", ["status"])

    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("recipient", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        *timestamps(),
        sa.CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_email_outbox_status"),
    )
    op.create_index("ix_email_outbox_due", "email_outbox", ["status", "next_attempt_at"])


def downgrade():
    op.drop_index("ix_email_outbox_due", table_name="email_outbox")
    op.drop_table("email_outbox")
    op.drop_index("ix_disbursements_status", table_name="donation_disbursements")
    op.drop_index("ix_disbursements_recipient", table_name="donation_disbursements")
    op.drop_index("ix_disbursements_source", table_name="donation_disbursements")
    op.drop_table("donation_disbursements")
    op.drop_index("ix_donor_donations_status", table_name="donor_donations")
    op.drop_index("ix_donor_donations_donor_id", table_name="donor_donations")
    op.drop_table("donor_donations")
    with op.batch_alter_table("donation_requests") as batch:
        batch.drop_constraint("fk_donation_requests_accepted_by", type_="foreignkey")
        batch.drop_column("rejected_at")
        batch.drop_column("accepted_by")
        batch.drop_column("accepted_at")

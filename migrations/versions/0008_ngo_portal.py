"""NGO portal: NGO role, NGO funding requests, and extensions to existing tables (no duplicate ledgers):
- donation_disbursements: link to the NGO request it answers, payment reference, receipt confirmation
  (kept separate from approval and transfer);
- ngo_expenditures: owning NGO, category and supporting document reference."""
from alembic import op
import sqlalchemy as sa

revision = "0008_ngo_portal"
down_revision = "0007_ngo_expenditures"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)
NGO_ROLE_ID = 5


def upgrade():
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("name", sa.String))
    op.bulk_insert(roles, [{"id": NGO_ROLE_ID, "name": "NGO"}])

    op.create_table(
        "ngo_donation_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_no", sa.String(30), nullable=False),
        sa.Column("ngo_id", sa.Integer(), sa.ForeignKey("ngos.id", name="fk_ngo_requests_ngo_id"), nullable=False),
        sa.Column("subject", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("requested_amount", MONEY, nullable=False),
        sa.Column("approved_amount", MONEY, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("supporting_reference", sa.String(200), nullable=True),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("info_request", sa.String(500), nullable=True),
        sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_ngo_requests_submitted_by"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_ngo_requests_reviewed_by"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_ngo_requests_approved_by"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("rejected_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_ngo_requests_rejected_by"), nullable=True),
        sa.Column("rejected_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_no", name="uq_ngo_requests_document_no"),
        sa.CheckConstraint("requested_amount > 0", name="ck_ngo_requests_requested_positive"),
        sa.CheckConstraint(
            "approved_amount IS NULL OR (approved_amount > 0 AND approved_amount <= requested_amount)",
            name="ck_ngo_requests_approved_range",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'under_review', 'approved', 'partially_approved', 'rejected')",
            name="ck_ngo_requests_status",
        ),
    )
    op.create_index("ix_ngo_requests_ngo_status", "ngo_donation_requests", ["ngo_id", "status"])

    with op.batch_alter_table("donation_disbursements") as batch:
        batch.add_column(sa.Column("ngo_request_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("payment_reference", sa.String(100), nullable=True))
        batch.add_column(sa.Column("supporting_reference", sa.String(200), nullable=True))
        batch.add_column(sa.Column("receipt_confirmed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("receipt_confirmed_by", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_disbursements_ngo_request_id", "ngo_donation_requests", ["ngo_request_id"], ["id"])
        batch.create_foreign_key("fk_disbursements_receipt_confirmed_by", "users", ["receipt_confirmed_by"], ["id"])
        batch.create_index("ix_disbursements_ngo_request", ["ngo_request_id"])

    with op.batch_alter_table("ngo_expenditures") as batch:
        batch.add_column(sa.Column("ngo_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("expense_category", sa.String(50), nullable=True))
        batch.add_column(sa.Column("supporting_document", sa.String(200), nullable=True))
        batch.add_column(sa.Column("recorded_by", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_ngo_expenditures_ngo_id", "ngos", ["ngo_id"], ["id"])
        batch.create_foreign_key("fk_ngo_expenditures_recorded_by", "users", ["recorded_by"], ["id"])
        batch.create_index("ix_ngo_expenditures_ngo", ["ngo_id"])


def downgrade():
    with op.batch_alter_table("ngo_expenditures") as batch:
        batch.drop_index("ix_ngo_expenditures_ngo")
        batch.drop_constraint("fk_ngo_expenditures_recorded_by", type_="foreignkey")
        batch.drop_constraint("fk_ngo_expenditures_ngo_id", type_="foreignkey")
        batch.drop_column("recorded_by")
        batch.drop_column("supporting_document")
        batch.drop_column("expense_category")
        batch.drop_column("ngo_id")
    with op.batch_alter_table("donation_disbursements") as batch:
        batch.drop_index("ix_disbursements_ngo_request")
        batch.drop_constraint("fk_disbursements_receipt_confirmed_by", type_="foreignkey")
        batch.drop_constraint("fk_disbursements_ngo_request_id", type_="foreignkey")
        batch.drop_column("receipt_confirmed_by")
        batch.drop_column("receipt_confirmed_at")
        batch.drop_column("supporting_reference")
        batch.drop_column("payment_reference")
        batch.drop_column("ngo_request_id")
    op.drop_index("ix_ngo_requests_ngo_status", table_name="ngo_donation_requests")
    op.drop_table("ngo_donation_requests")
    op.execute(sa.text(f"DELETE FROM roles WHERE id = {NGO_ROLE_ID}"))

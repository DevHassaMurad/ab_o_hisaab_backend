"""NGO expenditure records (future NGO phase). Each record spends part of one disbursement, so donor
batch reports can show where allocated money was spent. Empty until NGOs submit expenditure;
reports show "Not available yet" rather than assuming nothing was spent."""
from alembic import op
import sqlalchemy as sa

revision = "0007_ngo_expenditures"
down_revision = "0006_pdma_funds"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ngo_expenditures",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "disbursement_id", sa.Integer(),
            sa.ForeignKey("donation_disbursements.id", name="fk_ngo_expenditures_disbursement_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("evidence_reference", sa.String(200), nullable=True),
        sa.Column("verification_status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("verified_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_ngo_expenditures_verified_by"), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("reference", name="uq_ngo_expenditures_reference"),
        sa.CheckConstraint("amount > 0", name="ck_ngo_expenditures_amount_positive"),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'verified', 'rejected')", name="ck_ngo_expenditures_status"
        ),
    )
    op.create_index("ix_ngo_expenditures_disbursement", "ngo_expenditures", ["disbursement_id"])


def downgrade():
    op.drop_index("ix_ngo_expenditures_disbursement", table_name="ngo_expenditures")
    op.drop_table("ngo_expenditures")

"""Householder and shopkeeper portals: beneficiary roles, household composition and income/revenue ranges, and
beneficiary fund requests addressed to NGOs (paid from the NGO's net balance). Additive only."""
from alembic import op
import sqlalchemy as sa

revision = "0014_beneficiaries"
down_revision = "0013_merge_registration"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)
HOUSEHOLDER_ROLE_ID, SHOPKEEPER_ROLE_ID = 6, 7
STATUSES = ("pending", "on_hold", "rejected", "paid", "received", "cancelled")


def upgrade():
    roles = sa.table("roles", sa.column("id", sa.Integer), sa.column("name", sa.String))
    op.bulk_insert(roles, [{"id": HOUSEHOLDER_ROLE_ID, "name": "HOUSEHOLDER"},
                           {"id": SHOPKEEPER_ROLE_ID, "name": "SHOPKEEPER"}])
    with op.batch_alter_table("householder_details") as b:
        b.add_column(sa.Column("adults", sa.Integer(), nullable=True))
        b.add_column(sa.Column("children", sa.Integer(), nullable=True))
        b.add_column(sa.Column("males", sa.Integer(), nullable=True))
        b.add_column(sa.Column("females", sa.Integer(), nullable=True))
        b.add_column(sa.Column("income_min", MONEY, nullable=True))
        b.add_column(sa.Column("income_max", MONEY, nullable=True))
        b.create_check_constraint("ck_household_income_range",
                                  "income_min IS NULL OR income_max IS NULL OR income_max >= income_min")
    with op.batch_alter_table("shopkeeper_details") as b:
        b.add_column(sa.Column("revenue_min", MONEY, nullable=True))
        b.add_column(sa.Column("revenue_max", MONEY, nullable=True))
        b.create_check_constraint("ck_shop_revenue_range",
                                  "revenue_min IS NULL OR revenue_max IS NULL OR revenue_max >= revenue_min")
    status_list = ", ".join(f"'{s}'" for s in STATUSES)
    op.create_table(
        "beneficiary_fund_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_no", sa.String(30), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_bfr_user_id"), nullable=False),
        sa.Column("beneficiary_type", sa.String(12), nullable=False),
        sa.Column("ngo_id", sa.Integer(), sa.ForeignKey("ngos.id", name="fk_bfr_ngo_id"), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.Column("requested_amount", MONEY, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("paid_amount", MONEY, nullable=True),
        sa.Column("payment_no", sa.String(30), nullable=True),
        sa.Column("payment_method", sa.String(20), nullable=True),
        sa.Column("payment_reference", sa.String(100), nullable=True),
        sa.Column("decision_reason", sa.String(1000), nullable=True),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_bfr_decided_by"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_no", name="uq_bfr_document_no"),
        sa.UniqueConstraint("payment_no", name="uq_bfr_payment_no"),
        sa.CheckConstraint("requested_amount > 0", name="ck_bfr_amount_positive"),
        sa.CheckConstraint("paid_amount IS NULL OR (paid_amount > 0 AND paid_amount <= requested_amount)",
                           name="ck_bfr_paid_amount"),
        sa.CheckConstraint(f"status IN ({status_list})", name="ck_bfr_status"),
        sa.CheckConstraint("beneficiary_type IN ('householder', 'shopkeeper')", name="ck_bfr_type"),
    )
    op.create_index("ix_bfr_ngo_status", "beneficiary_fund_requests", ["ngo_id", "status"])
    op.create_index("ix_bfr_user", "beneficiary_fund_requests", ["user_id"])


def downgrade():
    op.drop_index("ix_bfr_user", table_name="beneficiary_fund_requests")
    op.drop_index("ix_bfr_ngo_status", table_name="beneficiary_fund_requests")
    op.drop_table("beneficiary_fund_requests")
    with op.batch_alter_table("shopkeeper_details") as b:
        b.drop_constraint("ck_shop_revenue_range", type_="check")
        b.drop_column("revenue_max")
        b.drop_column("revenue_min")
    with op.batch_alter_table("householder_details") as b:
        b.drop_constraint("ck_household_income_range", type_="check")
        for c in ("income_max", "income_min", "females", "males", "children", "adults"):
            b.drop_column(c)
    op.execute("UPDATE users SET role_id = 0 WHERE role_id IN (6, 7)")
    op.execute("DELETE FROM roles WHERE id IN (6, 7)")

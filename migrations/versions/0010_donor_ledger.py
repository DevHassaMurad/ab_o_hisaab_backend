"""Donor ledger and receipt vouchers (smallest changes on existing tables):
- donor_donations.payment_method: how the donor paid (shown on the ledger and voucher).
- donor_donations.receipt_no: unique voucher number (RCPT-26-00001), assigned once on first voucher generation.
- funding_disbursements.donor_receipt_notified_at / donation_disbursements.donor_receipt_notified_at:
  "your donation has reached an NGO" is sent once per donor share, only after the NGO confirms receipt."""
from alembic import op
import sqlalchemy as sa

revision = "0010_donor_ledger"
down_revision = "0009_fund_traceability"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("donor_donations") as batch:
        batch.add_column(sa.Column("payment_method", sa.String(20), nullable=True))
        batch.add_column(sa.Column("receipt_no", sa.String(30), nullable=True))
        batch.create_unique_constraint("uq_donor_donations_receipt_no", ["receipt_no"])
    with op.batch_alter_table("funding_disbursements") as batch:
        batch.add_column(sa.Column("donor_receipt_notified_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("donation_disbursements") as batch:
        batch.add_column(sa.Column("donor_receipt_notified_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("donation_disbursements") as batch:
        batch.drop_column("donor_receipt_notified_at")
    with op.batch_alter_table("funding_disbursements") as batch:
        batch.drop_column("donor_receipt_notified_at")
    with op.batch_alter_table("donor_donations") as batch:
        batch.drop_constraint("uq_donor_donations_receipt_no", type_="unique")
        batch.drop_column("receipt_no")
        batch.drop_column("payment_method")

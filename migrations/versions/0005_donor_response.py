"""Donor's answer to a donation request: accepted (with an amount) or rejected (with a reason)."""
from alembic import op
import sqlalchemy as sa

revision = "0005_donor_response"
down_revision = "0004_donations"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("donation_requests") as batch:
        batch.add_column(sa.Column("donor_response", sa.String(10), nullable=False, server_default="PENDING"))
        batch.add_column(sa.Column("responded_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("response_note", sa.String(500), nullable=True))
        batch.create_check_constraint(
            "ck_donation_requests_donor_response", "donor_response IN ('PENDING', 'ACCEPTED', 'REJECTED')"
        )


def downgrade():
    with op.batch_alter_table("donation_requests") as batch:
        batch.drop_constraint("ck_donation_requests_donor_response", type_="check")
        batch.drop_column("response_note")
        batch.drop_column("responded_at")
        batch.drop_column("donor_response")

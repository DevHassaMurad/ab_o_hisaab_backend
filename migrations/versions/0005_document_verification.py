"""Persisted pre-registration analysis, retry limits and manual review state."""

from alembic import op
import sqlalchemy as sa

revision = "0005_document_verification"
down_revision = "0004_registration_documents"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("registration_status", sa.String(24), nullable=True))
    with op.batch_alter_table("documents") as batch:
        batch.add_column(
            sa.Column(
                "verification_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("extracted_data", sa.Text(), nullable=True))
        batch.add_column(sa.Column("review_requested_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column("review_notification_sent_at", sa.DateTime(), nullable=True)
        )
        batch.drop_constraint("ck_document_type", type_="check")
        batch.create_check_constraint(
            "ck_document_type",
            "document_type IN ('passport', 'cnic', 'home_address', 'ngo_registration', 'ngo_certificate', 'authorization', 'business_proof', 'shop_photo')",
        )
        batch.create_check_constraint(
            "ck_document_attempts", "verification_attempts BETWEEN 0 AND 3"
        )
    op.create_table(
        "verification_processes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("user_type", sa.String(11), nullable=False),
        sa.Column("document_type", sa.String(16), nullable=False),
        sa.Column("document_url", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(30), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "verification_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("stage", sa.String(20), nullable=False, server_default="uploaded"),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("completed_stages", sa.JSON(), nullable=False),
        sa.Column("verification_result", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("extracted_data", sa.Text(), nullable=True),
        sa.Column(
            "document_status", sa.String(8), nullable=False, server_default="Pending"
        ),
        sa.Column("review_requested_at", sa.DateTime(), nullable=True),
        sa.Column(
            "claimed_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "verification_attempts BETWEEN 0 AND 3", name="ck_process_attempts"
        ),
        sa.CheckConstraint(
            "document_type IN ('cnic', 'passport', 'home_address', 'ngo_registration')",
            name="ck_process_document_type",
        ),
        sa.CheckConstraint(
            "user_type IN ('donor', 'ngo', 'householder', 'shopkeeper')",
            name="ck_process_user_type",
        ),
    )
    op.create_index(
        "ix_process_owner_created",
        "verification_processes",
        ["owner_key", "created_at"],
    )
    op.create_index("ix_process_stage", "verification_processes", ["stage"])


def downgrade():
    op.drop_index("ix_process_stage", table_name="verification_processes")
    op.drop_index("ix_process_owner_created", table_name="verification_processes")
    op.drop_table("verification_processes")
    # Map only the new category to its existing legacy equivalent before restoring
    # the older constraint; do not delete existing user or document records.
    op.execute(
        sa.text(
            "UPDATE documents SET document_type='ngo_certificate' WHERE document_type='ngo_registration'"
        )
    )
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("ck_document_attempts", type_="check")
        batch.drop_constraint("ck_document_type", type_="check")
        batch.create_check_constraint(
            "ck_document_type",
            "document_type IN ('passport', 'cnic', 'home_address', 'ngo_certificate', 'authorization', 'business_proof', 'shop_photo')",
        )
        batch.drop_column("review_requested_at")
        batch.drop_column("review_notification_sent_at")
        batch.drop_column("extracted_data")
        batch.drop_column("verification_attempts")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("registration_status")

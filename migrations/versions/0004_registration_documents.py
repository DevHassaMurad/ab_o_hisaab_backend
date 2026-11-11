"""Role-specific registration profiles and shared private verification documents."""

from alembic import op
import sqlalchemy as sa

revision = "0004_registration_documents"
down_revision = "0003_auth_verification"
branch_labels = None
depends_on = None


def profile_columns():
    return [
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("identity_type", sa.String(8), nullable=False),
        sa.Column("identity_number", sa.String(20), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("state", sa.String(100), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("postal_code", sa.String(20), nullable=False),
        sa.Column("address1", sa.String(255), nullable=False),
        sa.Column("address2", sa.String(255), nullable=True),
        sa.Column(
            "address_confirmed", sa.Boolean(), nullable=False, server_default="0"
        ),
        sa.Column(
            "consent_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade():
    op.create_table(
        "donor_details",
        *profile_columns(),
        sa.Column("donor_type", sa.String(13), nullable=False),
        sa.CheckConstraint(
            "identity_type IN ('cnic', 'passport')", name="ck_donor_identity_type"
        ),
        sa.CheckConstraint(
            "donor_type IN ('national', 'international')", name="ck_donor_type"
        ),
    )
    op.create_table(
        "ngo_details",
        *profile_columns(),
        sa.Column("organization_name", sa.String(200), nullable=False),
        sa.Column("registration_number", sa.String(100), nullable=False),
        sa.Column("designation", sa.String(100), nullable=False),
        sa.CheckConstraint(
            "identity_type IN ('cnic', 'passport')", name="ck_ngo_identity_type"
        ),
    )
    op.create_table(
        "householder_details",
        *profile_columns(),
        sa.Column("household_size", sa.Integer(), nullable=False),
        sa.Column("dependents", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "identity_type IN ('cnic', 'passport')", name="ck_householder_identity_type"
        ),
        sa.CheckConstraint(
            "household_size >= 1 AND dependents >= 0 AND dependents < household_size",
            name="ck_household_counts",
        ),
    )
    op.create_table(
        "shopkeeper_details",
        *profile_columns(),
        sa.Column("shop_name", sa.String(200), nullable=False),
        sa.Column("business_number", sa.String(100), nullable=False),
        sa.Column("business_category", sa.String(100), nullable=False),
        sa.CheckConstraint(
            "identity_type IN ('cnic', 'passport')", name="ck_shopkeeper_identity_type"
        ),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_url", sa.String(255), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="Pending"),
        sa.Column("user_type", sa.String(11), nullable=False),
        sa.Column("document_type", sa.String(16), nullable=False),
        sa.Column("document_slot", sa.String(20), nullable=False),
        sa.Column("content_type", sa.String(30), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("approval_type", sa.String(6), nullable=True),
        sa.Column("verification_result", sa.JSON(none_as_null=True), nullable=True),
        sa.Column(
            "verified_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('Pending', 'Approved', 'Rejected')", name="ck_document_status"
        ),
        sa.CheckConstraint(
            "user_type IN ('donor', 'ngo', 'shopkeeper', 'householder')",
            name="ck_document_user_type",
        ),
        sa.CheckConstraint(
            "document_type IN ('passport', 'cnic', 'home_address', 'ngo_certificate', 'authorization', 'business_proof', 'shop_photo')",
            name="ck_document_type",
        ),
        sa.CheckConstraint(
            "approval_type IS NULL OR approval_type IN ('ai', 'manual')",
            name="ck_document_approval_type",
        ),
        sa.CheckConstraint(
            "(status = 'Pending' AND approval_type IS NULL AND verified_at IS NULL) OR (status != 'Pending' AND approval_type IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_document_review_state",
        ),
    )
    op.create_index(
        "ix_documents_user_id_created_at", "documents", ["user_id", "created_at"]
    )
    op.create_index("ix_documents_status", "documents", ["status"])


def downgrade():
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_user_id_created_at", table_name="documents")
    op.drop_table("documents")
    for table in (
        "shopkeeper_details",
        "householder_details",
        "ngo_details",
        "donor_details",
    ):
        op.drop_table(table)

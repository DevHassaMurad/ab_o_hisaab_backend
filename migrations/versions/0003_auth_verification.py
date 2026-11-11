"""Email confirmation, six-digit recovery challenges and session revocation."""
from alembic import op
import sqlalchemy as sa

revision = "0003_auth_verification"
down_revision = "0002_user_roles_and_image"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "auth_challenges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("send_count", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("reset_token_hash", sa.String(64), nullable=True),
        sa.Column("reset_expires_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "purpose", name="uq_auth_challenge_user_purpose"),
    )


def downgrade():
    op.drop_table("auth_challenges")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("token_version")
        batch.drop_column("email_verified_at")

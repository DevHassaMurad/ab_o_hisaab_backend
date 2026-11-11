"""Add authenticatable user roles, optional image and district, and active state."""
from alembic import op
import sqlalchemy as sa

revision = "0002_user_roles_and_image"
down_revision = "0001_create_users"
branch_labels = None
depends_on = None


def upgrade():
    roles = op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(30), nullable=False, unique=True),
    )
    # Required reference data; demo user accounts are optional and separate.
    op.bulk_insert(roles, [
        {"id": 0, "name": "UNASSIGNED"},
        {"id": 1, "name": "PDMA_OFFICER"},
        {"id": 2, "name": "DC"},
        {"id": 3, "name": "DONOR"},
        {"id": 4, "name": "ADMIN"},
    ])
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("image", sa.String(255), nullable=True))
        batch.add_column(sa.Column("role_id", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("district_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.create_foreign_key("fk_users_role_id", "roles", ["role_id"], ["id"])


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_role_id", type_="foreignkey")
        batch.drop_column("is_active")
        batch.drop_column("district_id")
        batch.drop_column("role_id")
        batch.drop_column("image")
    op.drop_table("roles")

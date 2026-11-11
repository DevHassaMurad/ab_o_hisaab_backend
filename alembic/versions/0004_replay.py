"""FR5: replay scenario run bookkeeping (data-model v1.1; run_no, config and sim_date already exist).

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE replay_scenario
      ADD COLUMN started_at timestamptz,
      ADD COLUMN completed_at timestamptz,
      ADD COLUMN last_step_at timestamptz,
      ADD COLUMN last_error text,
      ADD CONSTRAINT ck_replay_status CHECK (status IN ('IDLE','RUNNING','AWAITING_APPROVAL','COMPLETED','FAILED'));
    """)


def downgrade() -> None:
    op.execute("""
    ALTER TABLE replay_scenario DROP CONSTRAINT ck_replay_status, DROP COLUMN started_at, DROP COLUMN completed_at,
      DROP COLUMN last_step_at, DROP COLUMN last_error;
    """)

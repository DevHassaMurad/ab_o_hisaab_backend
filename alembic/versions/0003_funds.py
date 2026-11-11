"""FR6: disaster_fund and trigger_tier (data-model v1.1).

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TS = "created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()"


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE disaster_fund (
      id uuid PRIMARY KEY, {TS},
      district_id uuid NOT NULL REFERENCES district(id),
      allocated_pkr numeric(14,2) NOT NULL DEFAULT 0 CHECK (allocated_pkr >= 0),
      reserved_pkr numeric(14,2) NOT NULL DEFAULT 0 CHECK (reserved_pkr >= 0),
      disbursed_pkr numeric(14,2) NOT NULL DEFAULT 0 CHECK (disbursed_pkr >= 0),
      remaining_pkr numeric(14,2) NOT NULL DEFAULT 0,
      household_share_pct integer NOT NULL DEFAULT 70 CHECK (household_share_pct BETWEEN 0 AND 100),
      pool_spillover_enabled boolean NOT NULL DEFAULT true,
      household_amount_by_tier jsonb NOT NULL DEFAULT '{{}}',
      shopkeeper_cap_pkr numeric(14,2) NOT NULL DEFAULT 50000 CHECK (shopkeeper_cap_pkr >= 0),
      shopkeeper_stock_pct integer NOT NULL DEFAULT 40 CHECK (shopkeeper_stock_pct BETWEEN 0 AND 100),
      ranking_weights jsonb,
      event_close_days integer NOT NULL DEFAULT 14 CHECK (event_close_days BETWEEN 1 AND 60),
      version integer NOT NULL DEFAULT 1,
      scenario_id uuid REFERENCES replay_scenario(id) ON DELETE CASCADE,
      CONSTRAINT uq_disaster_fund_district UNIQUE NULLS NOT DISTINCT (district_id, scenario_id),
      CONSTRAINT ck_fund_remaining CHECK (remaining_pkr = allocated_pkr - reserved_pkr - disbursed_pkr)
    );

    CREATE TABLE trigger_tier (
      id uuid PRIMARY KEY, {TS},
      fund_id uuid NOT NULL REFERENCES disaster_fund(id) ON DELETE CASCADE,
      name varchar(16) NOT NULL CHECK (name IN ('WATCH', 'WARNING', 'ACTION')),
      min_score integer NOT NULL CHECK (min_score BETWEEN 1 AND 100),
      release_pct integer NOT NULL CHECK (release_pct BETWEEN 0 AND 100),
      requires_approval boolean NOT NULL DEFAULT false,
      CONSTRAINT uq_trigger_tier_name UNIQUE (fund_id, name),
      CONSTRAINT ck_action_needs_approval CHECK ((name = 'ACTION') = requires_approval)
    );
    CREATE INDEX ix_trigger_tier_fund ON trigger_tier (fund_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trigger_tier, disaster_fund")

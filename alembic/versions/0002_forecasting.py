"""FR3/FR4: flood_forecast and risk_index (data-model v1.1, FR13 LLM columns come later).

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = "created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()"


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE flood_forecast (
      id uuid PRIMARY KEY, {TS},
      district_id uuid NOT NULL REFERENCES district(id),
      tehsil_id uuid REFERENCES tehsil(id),
      run_date date NOT NULL,
      lead_day integer NOT NULL CHECK (lead_day BETWEEN 1 AND 7),
      target_date date NOT NULL,
      probability double precision NOT NULL CHECK (probability BETWEEN 0 AND 1),
      mode varchar(16) NOT NULL,
      model_version varchar(64) NOT NULL,
      features jsonb NOT NULL DEFAULT '{{}}',
      forecast_provenance varchar(32),
      is_fallback boolean NOT NULL DEFAULT false,
      is_stale_input boolean NOT NULL DEFAULT false,
      scenario_id uuid REFERENCES replay_scenario(id) ON DELETE CASCADE,
      CONSTRAINT uq_flood_forecast UNIQUE NULLS NOT DISTINCT (district_id, tehsil_id, run_date, lead_day, scenario_id)
    );
    CREATE INDEX ix_flood_forecast_lookup ON flood_forecast (district_id, run_date);

    CREATE TABLE risk_index (
      id uuid PRIMARY KEY, {TS},
      district_id uuid NOT NULL REFERENCES district(id),
      tehsil_id uuid REFERENCES tehsil(id),
      run_date date NOT NULL,
      score integer NOT NULL CHECK (score BETWEEN 0 AND 100),
      peak_lead_day integer NOT NULL,
      tier varchar(16) NOT NULL,
      mode varchar(16) NOT NULL,
      model_version varchar(64) NOT NULL,
      forecast_provenance varchar(32),
      explanation_en text NOT NULL DEFAULT '',
      explanation_ur text,
      top_factors jsonb NOT NULL DEFAULT '[]',
      aggregation jsonb,
      is_fallback boolean NOT NULL DEFAULT false,
      is_stale_input boolean NOT NULL DEFAULT false,
      stale_since date,
      scenario_id uuid REFERENCES replay_scenario(id) ON DELETE CASCADE,
      CONSTRAINT uq_risk_index UNIQUE NULLS NOT DISTINCT (district_id, tehsil_id, run_date, scenario_id)
    );
    CREATE INDEX ix_risk_index_lookup ON risk_index (district_id, run_date);
    CREATE INDEX ix_risk_index_run_date ON risk_index (run_date);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS risk_index, flood_forecast")

"""Foundation core tables plus FR1/FR2 ingestion tables (data-model v1.1 subset).

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = "created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute(f"""
    CREATE TABLE district (
      id uuid PRIMARY KEY, {TS},
      pcode varchar(32) NOT NULL UNIQUE,
      name varchar(120) NOT NULL,
      province varchar(16) NOT NULL,
      flood_mode varchar(16) NOT NULL,
      vulnerability_weight double precision NOT NULL DEFAULT 0.5,
      vulnerability_inputs jsonb,
      geom geometry(MULTIPOLYGON, 4326),
      centroid_lat double precision NOT NULL,
      centroid_lon double precision NOT NULL,
      is_active boolean NOT NULL DEFAULT true
    );
    CREATE INDEX ix_district_province ON district (province);
    CREATE INDEX ix_district_is_active ON district (is_active);

    CREATE TABLE tehsil (
      id uuid PRIMARY KEY, {TS},
      pcode varchar(32) NOT NULL UNIQUE,
      district_id uuid NOT NULL REFERENCES district(id),
      name varchar(120) NOT NULL,
      population integer,
      elevation_mean_m double precision,
      slope_mean_deg double precision,
      geom geometry(MULTIPOLYGON, 4326)
    );
    CREATE INDEX ix_tehsil_district_id ON tehsil (district_id);

    CREATE TABLE replay_scenario (
      id uuid PRIMARY KEY, {TS},
      key varchar(32) NOT NULL UNIQUE,
      name varchar(120) NOT NULL,
      start_date date NOT NULL,
      end_date date NOT NULL,
      districts uuid[] NOT NULL DEFAULT '{{}}',
      expected_outcome varchar(16) NOT NULL,
      status varchar(24) NOT NULL DEFAULT 'IDLE',
      sim_date date,
      run_no integer NOT NULL DEFAULT 1,
      config jsonb NOT NULL DEFAULT '{{}}'
    );

    CREATE TABLE app_user (
      id uuid PRIMARY KEY, {TS},
      email varchar(254) NOT NULL UNIQUE,
      password_hash varchar(100) NOT NULL,
      full_name varchar(120) NOT NULL,
      role varchar(16) NOT NULL,
      district_id uuid REFERENCES district(id),
      is_active boolean NOT NULL DEFAULT true,
      CONSTRAINT ck_dc_has_district CHECK (role <> 'DC' OR district_id IS NOT NULL)
    );

    CREATE TABLE climate_observation (
      id uuid PRIMARY KEY, {TS},
      district_id uuid REFERENCES district(id),
      tehsil_id uuid REFERENCES tehsil(id),
      obs_date date NOT NULL,
      source varchar(16) NOT NULL,
      rainfall_mm double precision,
      rainfall_max_3h_mm double precision,
      temp_c double precision,
      soil_wetness double precision,
      soil_wetness_top double precision,
      lead_day integer,
      provenance varchar(32),
      forecast_model varchar(64),
      quality_flags jsonb NOT NULL DEFAULT '[]',
      is_stale boolean NOT NULL DEFAULT false,
      stale_since date,
      scenario_id uuid REFERENCES replay_scenario(id) ON DELETE CASCADE,
      CONSTRAINT uq_climate_observation UNIQUE NULLS NOT DISTINCT
        (district_id, tehsil_id, obs_date, source, lead_day, scenario_id)
    );
    CREATE INDEX ix_climate_observation_lookup ON climate_observation (source, district_id, obs_date);
    CREATE INDEX ix_climate_observation_tehsil ON climate_observation (tehsil_id, obs_date);
    CREATE INDEX ix_climate_observation_scenario ON climate_observation (scenario_id);

    CREATE TABLE context_indicator (
      id uuid PRIMARY KEY, {TS},
      district_id uuid NOT NULL REFERENCES district(id),
      indicator varchar(64) NOT NULL,
      value double precision NOT NULL,
      period varchar(16) NOT NULL,
      source varchar(64) NOT NULL,
      CONSTRAINT uq_context_indicator UNIQUE (district_id, indicator, period, source)
    );

    CREATE TABLE data_source_status (
      id uuid PRIMARY KEY, {TS},
      source varchar(16) NOT NULL UNIQUE,
      last_success_at timestamptz,
      last_valid_date date,
      last_error text,
      is_healthy boolean NOT NULL DEFAULT true,
      details jsonb
    );

    CREATE TABLE audit_log (
      id uuid PRIMARY KEY,
      chain_key varchar(80) NOT NULL,
      seq bigint NOT NULL,
      event_type varchar(64) NOT NULL,
      entity_type varchar(64) NOT NULL,
      entity_id uuid,
      actor varchar(120) NOT NULL,
      payload jsonb NOT NULL DEFAULT '{{}}',
      timestamp timestamptz NOT NULL DEFAULT now(),
      prev_hash varchar(64) NOT NULL,
      hash varchar(64) NOT NULL,
      scenario_id uuid REFERENCES replay_scenario(id) ON DELETE RESTRICT,
      CONSTRAINT uq_audit_chain_seq UNIQUE (chain_key, seq)
    );
    CREATE INDEX ix_audit_log_chain_key ON audit_log (chain_key);
    CREATE INDEX ix_audit_log_event_type ON audit_log (event_type);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS audit_log, data_source_status, context_indicator, climate_observation,
      app_user, replay_scenario, tehsil, district CASCADE;
    """)

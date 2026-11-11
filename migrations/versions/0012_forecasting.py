"""PDMA weather, flood and hazard forecasting: districts with model climatology, observations, forecasts,
risk assessments, hazard events and a data-run log. Additive only."""
from alembic import op
import sqlalchemy as sa

revision = "0012_forecasting"
down_revision = "0011_campaigns"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forecast_districts",
        sa.Column("pcode", sa.String(12), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("province", sa.String(100), nullable=False),
        sa.Column("province_pcode", sa.String(12), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("area_km2", sa.Float(), nullable=False),
        sa.Column("flood_mode", sa.String(10), nullable=False),
        sa.Column("hist_floods_train", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gwt_q99", sa.Float(), nullable=True),
        sa.Column("gwr_q99", sa.Float(), nullable=True),
        sa.Column("gwp_q99", sa.Float(), nullable=True),
        sa.Column("clim_rain_7d", sa.Float(), nullable=True),
        sa.Column("clim_rain_30d", sa.Float(), nullable=True),
        sa.Column("climatology_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "forecast_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False, server_default="schedule"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.CheckConstraint("status IN ('running', 'success', 'partial', 'failed')", name="ck_forecast_runs_status"),
    )
    op.create_index("ix_forecast_runs_kind", "forecast_runs", ["kind", "started_at"])
    op.create_table(
        "hazard_observations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("district_pcode", sa.String(12), sa.ForeignKey("forecast_districts.pcode", name="fk_hazard_obs_district"),
                  nullable=False),
        sa.Column("variable", sa.String(30), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("district_pcode", "variable", "observation_date", "source", name="uq_hazard_obs_once"),
    )
    op.create_index("ix_hazard_obs_date", "hazard_observations", ["observation_date"])
    op.create_table(
        "hazard_forecasts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("district_pcode", sa.String(12), sa.ForeignKey("forecast_districts.pcode", name="fk_hazard_fc_district"),
                  nullable=False),
        sa.Column("variable", sa.String(30), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("forecast_date", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("district_pcode", "variable", "forecast_date", "source", name="uq_hazard_fc_once"),
    )
    op.create_table(
        "district_risk_assessments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("district_pcode", sa.String(12), sa.ForeignKey("forecast_districts.pcode", name="fk_risk_district"),
                  nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("risk_category", sa.String(15), nullable=False),
        sa.Column("model_version", sa.String(60), nullable=False),
        sa.Column("primary_indicator", sa.String(60), nullable=True),
        sa.Column("data_completeness", sa.Float(), nullable=False),
        sa.Column("calculation_details", sa.JSON(), nullable=True),
        sa.Column("calculated_at", sa.DateTime(), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("forecast_runs.id", name="fk_risk_run"), nullable=True),
        sa.CheckConstraint("risk_category IN ('low', 'watch', 'warning', 'immediate', 'insufficient')",
                           name="ck_risk_category"),
        sa.CheckConstraint("risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)", name="ck_risk_score_range"),
    )
    op.create_index("ix_risk_district_time", "district_risk_assessments", ["district_pcode", "calculated_at"])
    op.create_table(
        "hazard_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("event_date", sa.DateTime(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("district_pcode", sa.String(12), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_hazard_events_once"),
    )


def downgrade():
    op.drop_table("hazard_events")
    op.drop_index("ix_risk_district_time", table_name="district_risk_assessments")
    op.drop_table("district_risk_assessments")
    op.drop_table("hazard_forecasts")
    op.drop_index("ix_hazard_obs_date", table_name="hazard_observations")
    op.drop_table("hazard_observations")
    op.drop_index("ix_forecast_runs_kind", table_name="forecast_runs")
    op.drop_table("forecast_runs")
    op.drop_table("forecast_districts")

"""PDMA Collaboration Campaigns as the parent entity.

collaboration_campaigns (new, parent)
  -> campaign_collaborations (existing; now one NGO participation/invitation row per selected NGO)
     -> ngo_distribution_campaigns (existing; created by the NGO from its accepted participation)
Funding stays separate and optional: a participation may later be linked to a fundraising campaign
(campaign_id) with an allocation (assigned_amount); releases / ledger work exactly as before.
Existing collaborations are wrapped in one generated parent campaign per fundraising campaign. Additive only.
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = "0016_collaboration_campaigns"
down_revision = "0015_campaign_finance"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)


def upgrade():
    op.create_table(
        "collaboration_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference_no", sa.String(30), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.String(4000), nullable=False),
        sa.Column("objective", sa.String(1000), nullable=False),
        sa.Column("districts", sa.String(500), nullable=False),
        sa.Column("operational_areas", sa.String(1000), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("total_budget", MONEY, nullable=True),
        sa.Column("available_funding", MONEY, nullable=True),
        sa.Column("instructions", sa.String(4000), nullable=True),
        sa.Column("required_capabilities", sa.String(1000), nullable=True),
        sa.Column("funding_campaign_id", sa.Integer(), sa.ForeignKey("donation_campaigns.id", name="fk_colc_funding"),
                  nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("launched_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_colc_created_by"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft', 'launched', 'completed', 'cancelled')", name="ck_colc_status"),
        sa.CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_colc_dates"),
    )
    with op.batch_alter_table("campaign_collaborations") as b:
        b.add_column(sa.Column("collaboration_campaign_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("scope_of_work", sa.String(1000), nullable=True))
        b.add_column(sa.Column("instructions", sa.String(2000), nullable=True))
        b.create_foreign_key("fk_ccol_parent", "collaboration_campaigns", ["collaboration_campaign_id"], ["id"])
        b.alter_column("campaign_id", existing_type=sa.Integer(), nullable=True)
        b.alter_column("assigned_amount", existing_type=MONEY, nullable=True)
        b.drop_constraint("ck_ccol_amount", type_="check")
        b.create_check_constraint("ck_ccol_amount", "assigned_amount IS NULL OR assigned_amount > 0")
        b.create_index("ix_ccol_parent", ["collaboration_campaign_id"])
    with op.batch_alter_table("ngo_distribution_campaigns") as b:
        b.add_column(sa.Column("collaboration_campaign_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("target_beneficiaries", sa.String(500), nullable=True))
        b.add_column(sa.Column("locations", sa.String(1000), nullable=True))
        b.add_column(sa.Column("expected_beneficiaries", sa.Integer(), nullable=True))
        b.add_column(sa.Column("strategy", sa.String(2000), nullable=True))
        b.create_foreign_key("fk_ndc_parent", "collaboration_campaigns", ["collaboration_campaign_id"], ["id"])
        b.alter_column("batch_id", existing_type=sa.Integer(), nullable=True)
        b.alter_column("planned_amount", existing_type=MONEY, nullable=True)
        b.drop_constraint("ck_ndc_amount", type_="check")
        b.create_check_constraint("ck_ndc_amount", "planned_amount IS NULL OR planned_amount > 0")
        b.drop_constraint("ck_ndc_status", type_="check")
        b.create_check_constraint("ck_ndc_status", "status IN ('draft', 'active', 'completed', 'cancelled')")

    # Wrap existing collaborations in one generated parent per fundraising campaign (keeps history intact).
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT c.campaign_id, MIN(c.created_by), d.name, d.description, d.objective, d.start_date, d.end_date "
        "FROM campaign_collaborations c JOIN donation_campaigns d ON d.id = c.campaign_id "
        "GROUP BY c.campaign_id, d.name, d.description, d.objective, d.start_date, d.end_date")).fetchall()
    for campaign_id, created_by, name, description, objective, start, end in rows:
        districts = conn.execute(sa.text("SELECT DISTINCT district FROM campaign_collaborations WHERE campaign_id = :c"),
                                 {"c": campaign_id}).scalars().all()
        conn.execute(sa.text(
            "INSERT INTO collaboration_campaigns (reference_no, title, description, objective, districts, start_date, "
            "end_date, funding_campaign_id, status, launched_at, created_by, created_at, updated_at) VALUES "
            "(:ref, :title, :desc, :obj, :dist, :start, :end, :cid, 'launched', :now, :by, :now, :now)"),
            {"ref": f"COLC-LEGACY-{campaign_id}", "title": f"{name} - NGO collaboration"[:200], "desc": description[:4000],
             "obj": objective[:1000], "dist": ", ".join(districts)[:500] or "-", "start": start, "end": end,
             "cid": campaign_id, "now": datetime.utcnow(), "by": created_by})
        parent = conn.execute(sa.text("SELECT id FROM collaboration_campaigns WHERE reference_no = :r"),
                              {"r": f"COLC-LEGACY-{campaign_id}"}).scalar()
        conn.execute(sa.text("UPDATE campaign_collaborations SET collaboration_campaign_id = :p WHERE campaign_id = :c"),
                     {"p": parent, "c": campaign_id})
    conn.execute(sa.text(
        "UPDATE ngo_distribution_campaigns SET collaboration_campaign_id = (SELECT c.collaboration_campaign_id FROM "
        "campaign_collaborations c WHERE c.id = ngo_distribution_campaigns.collaboration_id)"))


def downgrade():
    with op.batch_alter_table("ngo_distribution_campaigns") as b:
        b.drop_constraint("ck_ndc_status", type_="check")
        b.create_check_constraint("ck_ndc_status", "status IN ('active', 'completed', 'cancelled')")
        b.drop_constraint("ck_ndc_amount", type_="check")
        b.create_check_constraint("ck_ndc_amount", "planned_amount > 0")
        b.drop_constraint("fk_ndc_parent", type_="foreignkey")
        for c in ("strategy", "expected_beneficiaries", "locations", "target_beneficiaries", "collaboration_campaign_id"):
            b.drop_column(c)
    with op.batch_alter_table("campaign_collaborations") as b:
        b.drop_index("ix_ccol_parent")
        b.drop_constraint("ck_ccol_amount", type_="check")
        b.create_check_constraint("ck_ccol_amount", "assigned_amount > 0")
        b.drop_constraint("fk_ccol_parent", type_="foreignkey")
        for c in ("instructions", "scope_of_work", "collaboration_campaign_id"):
            b.drop_column(c)
    op.drop_table("collaboration_campaigns")

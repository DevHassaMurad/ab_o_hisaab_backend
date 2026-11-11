"""Donor fund traceability for NGO allocations.

- ngo_allocations: one allocation (ALLOC-26-00001) per approved NGO request; a commitment, not money.
- allocation_funding_sources: which donor contribution (DD-IN) funds how much of an allocation
  (allocated = reserved at approval, disbursed as transfers happen, released if cancelled).
- funding_disbursements: which funding source paid which part of each actual transfer (DD-OUT).
- fund_ledger_events: append-only audit trail of reservations, releases, approvals and disbursements.
- donation_disbursements: link to the NGO allocation; source_batch_id becomes optional because a
  transfer under an allocation is paid from its reserved funding sources.
Existing donor contributions, requests and transfers are not duplicated or rewritten."""
from alembic import op
import sqlalchemy as sa

revision = "0009_fund_traceability"
down_revision = "0008_ngo_portal"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(14, 2)


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    ]


def upgrade():
    op.create_table(
        "ngo_allocations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_no", sa.String(30), nullable=False),
        sa.Column("ngo_id", sa.Integer(), sa.ForeignKey("ngos.id", name="fk_ngo_allocations_ngo_id"), nullable=False),
        sa.Column(
            "ngo_request_id", sa.Integer(),
            sa.ForeignKey("ngo_donation_requests.id", name="fk_ngo_allocations_request_id"), nullable=False,
        ),
        sa.Column("approved_amount", MONEY, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="approved"),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_ngo_allocations_approved_by"), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=False),
        sa.Column("cancelled_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_ngo_allocations_cancelled_by"), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("document_no", name="uq_ngo_allocations_document_no"),
        sa.UniqueConstraint("ngo_request_id", name="uq_ngo_allocations_request"),
        sa.CheckConstraint("approved_amount > 0", name="ck_ngo_allocations_amount_positive"),
        sa.CheckConstraint(
            "status IN ('approved', 'partially_disbursed', 'disbursed', 'cancelled')", name="ck_ngo_allocations_status"
        ),
    )
    op.create_index("ix_ngo_allocations_ngo", "ngo_allocations", ["ngo_id"])

    op.create_table(
        "allocation_funding_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "allocation_id", sa.Integer(),
            sa.ForeignKey("ngo_allocations.id", name="fk_funding_sources_allocation_id"), nullable=False,
        ),
        sa.Column("donor_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_funding_sources_donor_id"), nullable=False),
        sa.Column(
            "donor_donation_id", sa.Integer(),
            sa.ForeignKey("donor_donations.id", name="fk_funding_sources_donation_id"), nullable=False,
        ),
        sa.Column(
            "donation_batch_id", sa.Integer(),
            sa.ForeignKey("donation_requests.id", name="fk_funding_sources_batch_id"), nullable=True,
        ),
        sa.Column("allocated_amount", MONEY, nullable=False),
        sa.Column("disbursed_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("released_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("funding_status", sa.String(20), nullable=False, server_default="reserved"),
        *timestamps(),
        sa.UniqueConstraint("allocation_id", "donor_donation_id", name="uq_funding_source_allocation_donation"),
        sa.CheckConstraint("allocated_amount > 0", name="ck_funding_sources_allocated_positive"),
        sa.CheckConstraint("disbursed_amount >= 0 AND released_amount >= 0", name="ck_funding_sources_non_negative"),
        sa.CheckConstraint(
            "disbursed_amount + released_amount <= allocated_amount", name="ck_funding_sources_within_allocation"
        ),
        sa.CheckConstraint(
            "funding_status IN ('reserved', 'partially_disbursed', 'disbursed', 'released')",
            name="ck_funding_sources_status",
        ),
    )
    op.create_index("ix_funding_sources_donation", "allocation_funding_sources", ["donor_donation_id"])
    op.create_index("ix_funding_sources_donor", "allocation_funding_sources", ["donor_id"])

    with op.batch_alter_table("donation_disbursements") as batch:
        batch.add_column(sa.Column("ngo_allocation_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_disbursements_ngo_allocation_id", "ngo_allocations", ["ngo_allocation_id"], ["id"])
        batch.create_index("ix_disbursements_ngo_allocation", ["ngo_allocation_id"])
        batch.alter_column("source_batch_id", existing_type=sa.Integer(), nullable=True)

    op.create_table(
        "funding_disbursements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "disbursement_id", sa.Integer(),
            sa.ForeignKey("donation_disbursements.id", name="fk_funding_disb_disbursement_id"), nullable=False,
        ),
        sa.Column(
            "funding_source_id", sa.Integer(),
            sa.ForeignKey("allocation_funding_sources.id", name="fk_funding_disb_source_id"), nullable=False,
        ),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("donor_notified_at", sa.DateTime(), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("disbursement_id", "funding_source_id", name="uq_funding_disb_pair"),
        sa.CheckConstraint("amount > 0", name="ck_funding_disb_amount_positive"),
        sa.CheckConstraint("status IN ('active', 'voided')", name="ck_funding_disb_status"),
    )
    op.create_index("ix_funding_disb_source", "funding_disbursements", ["funding_source_id"])

    op.create_table(
        "fund_ledger_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("allocation_id", sa.Integer(), sa.ForeignKey("ngo_allocations.id", name="fk_ledger_allocation_id"), nullable=True),
        sa.Column(
            "funding_source_id", sa.Integer(),
            sa.ForeignKey("allocation_funding_sources.id", name="fk_ledger_funding_source_id"), nullable=True,
        ),
        sa.Column(
            "disbursement_id", sa.Integer(),
            sa.ForeignKey("donation_disbursements.id", name="fk_ledger_disbursement_id"), nullable=True,
        ),
        sa.Column(
            "donor_donation_id", sa.Integer(),
            sa.ForeignKey("donor_donations.id", name="fk_ledger_donor_donation_id"), nullable=True,
        ),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_ledger_actor_id"), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "event_type IN ('approved', 'reserved', 'released', 'disbursed', 'disbursement_voided', 'cancelled')",
            name="ck_ledger_event_type",
        ),
    )
    op.create_index("ix_ledger_allocation", "fund_ledger_events", ["allocation_id"])
    op.create_index("ix_ledger_donation", "fund_ledger_events", ["donor_donation_id"])


def downgrade():
    op.drop_index("ix_ledger_donation", table_name="fund_ledger_events")
    op.drop_index("ix_ledger_allocation", table_name="fund_ledger_events")
    op.drop_table("fund_ledger_events")
    op.drop_index("ix_funding_disb_source", table_name="funding_disbursements")
    op.drop_table("funding_disbursements")
    with op.batch_alter_table("donation_disbursements") as batch:
        batch.alter_column("source_batch_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_index("ix_disbursements_ngo_allocation")
        batch.drop_constraint("fk_disbursements_ngo_allocation_id", type_="foreignkey")
        batch.drop_column("ngo_allocation_id")
    op.drop_index("ix_funding_sources_donor", table_name="allocation_funding_sources")
    op.drop_index("ix_funding_sources_donation", table_name="allocation_funding_sources")
    op.drop_table("allocation_funding_sources")
    op.drop_index("ix_ngo_allocations_ngo", table_name="ngo_allocations")
    op.drop_table("ngo_allocations")

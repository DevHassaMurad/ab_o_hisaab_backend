"""FR5 reset: delete a scenario's operational rows, children first. Audit rows are never deleted (Principle III):
the scenario's run_no goes up instead, which starts a new audit chain."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import Base

NEVER_PURGE = {"audit_log", "replay_scenario", "app_user", "district", "tehsil", "beneficiary", "context_indicator",
               "data_source_status"}
# Tables without their own scenario_id, deleted through a parent that has one: table -> (fk column, parent table)
CHILDREN: dict[str, tuple[str, str]] = {"trigger_tier": ("fund_id", "disaster_fund")}


def scenario_tables() -> list[str]:
    """Tables with scenario_id, in child-before-parent order (reverse FK dependency order)."""
    return [t.name for t in reversed(Base.metadata.sorted_tables)
            if "scenario_id" in t.c and t.name not in NEVER_PURGE]


def purge_scenario_rows(db: Session, sid: UUID) -> dict[str, int]:
    out: dict[str, int] = {}
    for table, (fk, parent) in CHILDREN.items():
        out[table] = db.execute(text(f"DELETE FROM {table} WHERE {fk} IN "
                                     f"(SELECT id FROM {parent} WHERE scenario_id = :sid)"), {"sid": sid}).rowcount
    for table in scenario_tables():
        out[table] = db.execute(text(f"DELETE FROM {table} WHERE scenario_id = :sid"),
                                {"sid": sid}).rowcount
    return out

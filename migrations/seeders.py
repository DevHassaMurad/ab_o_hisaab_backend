"""Optional, repeatable demo users. Existing accounts are never overwritten."""
import os
from uuid import UUID

from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.database import engine
from model.user_model import User
from model.role_model import Role


def seed_users(password, bind=None):
    if not password or len(password) < 12:
        raise ValueError("Seed password must contain at least 12 characters.")
    # Supply actual district UUIDs; do not invent geography records for auth.
    districts = {}
    for district in ("DADU", "BUNER"):
        value = os.environ.get(f"SEED_{district}_DISTRICT_ID")
        if not value:
            raise ValueError(f"Set SEED_{district}_DISTRICT_ID to the district UUID before seeding.")
        districts[district] = str(UUID(value))

    users = [
        ("PDMA Officer", "officer@pdma-sindh.demo", 1, None),
        ("DC Dadu", "dc.dadu@sindh.demo", 2, districts["DADU"]),
        ("DC Buner", "dc.buner@kp.demo", 2, districts["BUNER"]),
        ("Donor", "donor@fund.demo", 3, None),
        ("Admin", "admin@aab.demo", 4, None),
    ]
    created = 0
    hasher = PasswordHash.recommended()
    with Session(bind or engine) as session, session.begin():
        if set(session.scalars(select(Role.id))) != {0, 1, 2, 3, 4}:
            raise ValueError("Run migrations before seeding users.")
        for name, email, role_id, district_id in users:
            if session.scalar(select(User.id).where(User.email == email)) is not None:
                continue
            session.add(User(
                name=name, email=email, password=hasher.hash(password),
                role_id=role_id, district_id=district_id,
            ))
            created += 1
    return created

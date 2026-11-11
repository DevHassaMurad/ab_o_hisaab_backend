"""Seed the PDMA administrator account only. Repeatable: an existing account is never overwritten.

Settings (from .env or the shell):
  SEED_ADMIN_EMAIL     default admin@pdma.gov.pk
  SEED_ADMIN_NAME      default "PDMA Admin"
  SEED_USER_PASSWORD   required, 12+ characters (artisan asks for it if not set)

Other users register through the portal and get their role from this administrator.
"""
import os

from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.database import engine
from model.role_model import Role
from model.user_model import User

ADMIN_ROLE = "ADMIN"


def seed_users(password, bind=None):
    """Create the PDMA admin if missing. Returns the number of accounts created (0 or 1)."""
    if not password or len(password) < 12:
        raise ValueError("Seed password must contain at least 12 characters.")
    email = (os.environ.get("SEED_ADMIN_EMAIL") or "admin@pdma.gov.pk").strip().lower()
    name = (os.environ.get("SEED_ADMIN_NAME") or "PDMA Admin").strip()

    with Session(bind or engine) as session, session.begin():
        role_id = session.scalar(select(Role.id).where(Role.name == ADMIN_ROLE))
        if role_id is None:
            raise ValueError("Run migrations before seeding users.")
        if session.scalar(select(User.id).where(func.lower(User.email) == email)) is not None:
            return 0
        session.add(User(
            name=name,
            email=email,
            password=PasswordHash.recommended().hash(password),
            role_id=role_id,
            district_id=None,
            is_active=True,
            # Seeded by the operator, so the address is trusted; the admin can sign in straight away.
            email_verified_at=func.now(),
        ))
    return 1

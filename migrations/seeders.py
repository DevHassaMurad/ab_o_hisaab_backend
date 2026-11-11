"""Seed accounts. Repeatable: existing accounts are never overwritten.

1. PDMA administrator (always)
   SEED_ADMIN_EMAIL     default admin@pdma.gov.pk
   SEED_ADMIN_NAME      default "PDMA Admin"
   SEED_USER_PASSWORD   required, 12+ characters (artisan asks for it if not set)

2. Demo donor and demo NGO (development only; skipped when APP_ENV=production)
   donor.admin@demo.com and ngo.admin@demo.com, each with a random 12-digit numeric password printed once
   when the account is created. Set SEED_DONOR_PASSWORD / SEED_NGO_PASSWORD to choose them instead.
   Passwords are never written to a file. The NGO login is linked to the "Demo Relief NGO" organisation.
"""
import os
import secrets

from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.database import engine
from model.donation_model import DonorProfile, Ngo
from model.role_model import Role
from model.user_model import User

DEMO_DONOR_EMAIL = "donor.admin@demo.com"
DEMO_NGO_EMAIL = "ngo.admin@demo.com"


def _create_user(session, hasher, email, name, password, role_name):
    role_id = session.scalar(select(Role.id).where(Role.name == role_name))
    if role_id is None:
        raise ValueError("Run migrations before seeding users.")
    if session.scalar(select(User.id).where(func.lower(User.email) == email)) is not None:
        return None
    user = User(
        name=name,
        email=email,
        password=hasher.hash(password),
        role_id=role_id,
        district_id=None,
        is_active=True,
        # Seeded by the operator, so the address is trusted and the account can sign in straight away.
        email_verified_at=func.now(),
    )
    session.add(user)
    session.flush()
    return user


def seed_users(password, bind=None, echo=print):
    """Create the PDMA admin and (outside production) the demo donor. Returns the number created."""
    if not password or len(password) < 12:
        raise ValueError("Seed password must contain at least 12 characters.")
    hasher = PasswordHash.recommended()
    created = 0
    with Session(bind or engine) as session, session.begin():
        admin_email = (os.environ.get("SEED_ADMIN_EMAIL") or "admin@pdma.gov.pk").strip().lower()
        admin_name = (os.environ.get("SEED_ADMIN_NAME") or "PDMA Admin").strip()
        if _create_user(session, hasher, admin_email, admin_name, password, "ADMIN"):
            created += 1

        if (os.environ.get("APP_ENV") or "development").lower() != "production":
            donor_password = os.environ.get("SEED_DONOR_PASSWORD") or f"{secrets.randbelow(10**12):012d}"
            if len(donor_password) < 12:
                raise ValueError("SEED_DONOR_PASSWORD must contain at least 12 characters.")
            donor = _create_user(session, hasher, DEMO_DONOR_EMAIL, "Demo Donor", donor_password, "DONOR")
            if donor:
                session.add(DonorProfile(user_id=donor.id, organization="Demo Relief Foundation"))
                created += 1
                if not os.environ.get("SEED_DONOR_PASSWORD"):
                    echo(f"Demo donor created: {DEMO_DONOR_EMAIL} / {donor_password} (shown once; store it safely)")

            ngo_password = os.environ.get("SEED_NGO_PASSWORD") or f"{secrets.randbelow(10**12):012d}"
            if len(ngo_password) < 12:
                raise ValueError("SEED_NGO_PASSWORD must contain at least 12 characters.")
            ngo_user = _create_user(session, hasher, DEMO_NGO_EMAIL, "Demo NGO Admin", ngo_password, "NGO")
            if ngo_user:
                org = session.scalar(select(Ngo).where(Ngo.name == "Demo Relief NGO"))
                if org is None:
                    org = Ngo(name="Demo Relief NGO", registration_no="NGO-DEMO-001", contact_email=DEMO_NGO_EMAIL)
                    session.add(org)
                org.user_id = ngo_user.id
                created += 1
                if not os.environ.get("SEED_NGO_PASSWORD"):
                    echo(f"Demo NGO created: {DEMO_NGO_EMAIL} / {ngo_password} (shown once; store it safely)")

            created += _seed_beneficiaries(session, hasher, echo)
    return created


DEMO_HOUSEHOLDER_EMAIL = "householder@demo.com"
DEMO_SHOPKEEPER_EMAIL = "shopkeeper@demo.com"


def _seed_beneficiaries(session, hasher, echo):
    """Demo householder and shopkeeper with complete registration profiles (outside production only)."""
    from datetime import datetime

    from model.registration_model import HouseholderDetails, ShopkeeperDetails

    address = dict(identity_type="cnic", country="PK", state="Punjab", city="Muzaffargarh", postal_code="34200",
                   address1="Ward 4, Main Bazaar Road", address_confirmed=True, consent_at=datetime.utcnow())
    created = 0
    pw = os.environ.get("SEED_HOUSEHOLDER_PASSWORD") or f"{secrets.randbelow(10**12):012d}"
    u = _create_user(session, hasher, DEMO_HOUSEHOLDER_EMAIL, "Demo Householder", pw, "HOUSEHOLDER")
    if u:
        u.registration_status = "approved"
        session.add(HouseholderDetails(user_id=u.id, phone="+92 300 1234567", identity_number="3210112345671",
                                       household_size=6, dependents=4, adults=2, children=4, males=3, females=3,
                                       income_min=15000, income_max=25000, **address))
        created += 1
        if not os.environ.get("SEED_HOUSEHOLDER_PASSWORD"):
            echo(f"Demo householder created: {DEMO_HOUSEHOLDER_EMAIL} / {pw} (shown once; store it safely)")
    pw = os.environ.get("SEED_SHOPKEEPER_PASSWORD") or f"{secrets.randbelow(10**12):012d}"
    u = _create_user(session, hasher, DEMO_SHOPKEEPER_EMAIL, "Demo Shopkeeper", pw, "SHOPKEEPER")
    if u:
        u.registration_status = "approved"
        session.add(ShopkeeperDetails(user_id=u.id, phone="+92 301 7654321", identity_number="3210198765431",
                                      shop_name="Al-Madina General Store", business_number="MZG-SHOP-0042",
                                      business_category="Grocery", revenue_min=100000, revenue_max=250000, **address))
        created += 1
        if not os.environ.get("SEED_SHOPKEEPER_PASSWORD"):
            echo(f"Demo shopkeeper created: {DEMO_SHOPKEEPER_EMAIL} / {pw} (shown once; store it safely)")
    return created

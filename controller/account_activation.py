"""What happens when a self-registered account becomes approved: it gets the role for its user type, and an NGO
also gets its organisation record so beneficiaries can address requests to it and PDMA can fund it.
Called from registration (automated checks passed) and from document review (PDMA approves the last document)."""
from sqlalchemy import select

from model.donation_model import Ngo
from model.role_model import Role

ROLE_FOR_TYPE = {"donor": "DONOR", "ngo": "NGO", "householder": "HOUSEHOLDER", "shopkeeper": "SHOPKEEPER"}


def activate(db, user, user_type):
    role_id = db.scalar(select(Role.id).where(Role.name == ROLE_FOR_TYPE.get(user_type)))
    if role_id is not None and not user.role_id:
        user.role_id = role_id  # never overwrite a role an administrator already set
    if user_type == "ngo":
        details = user.ngo_details
        ngo = db.scalar(select(Ngo).where(Ngo.user_id == user.id))
        if ngo is None and details is not None:
            db.add(Ngo(name=details.organization_name, registration_no=_free_reg_no(db, details.registration_number),
                       contact_email=user.email, contact_phone=details.phone, user_id=user.id, is_active=True))
        elif ngo is not None:
            ngo.is_active = True


def _free_reg_no(db, reg_no):
    """ngos.registration_no is unique; keep it if free, otherwise store NULL rather than fail the approval."""
    return None if db.scalar(select(Ngo.id).where(Ngo.registration_no == reg_no)) else reg_no

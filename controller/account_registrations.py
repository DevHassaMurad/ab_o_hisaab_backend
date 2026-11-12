"""ADMIN-only registration review, role activation and transactional email notification."""
from html import escape
from sqlalchemy import select, func, or_
from controller.authentication import AuthFlowError, now_utc
from controller.account_activation import activate, ROLE_FOR_TYPE
from controller.documents import Documents
from config.outbox import enqueue
from model.user_model import User
from model.role_model import Role
from model.registration_model import PROFILE_MODELS
from model.document_model import Document
from model.fund_model import EmailOutbox

SUBJECT = 'Your Aab o Hisaab account is verified'
PROFILE_FIELDS = ('phone', 'identity_type', 'identity_number', 'country', 'state', 'city', 'postal_code', 'address1', 'address2', 'address_confirmed', 'consent_at', 'created_at', 'donor_type', 'organization_name', 'registration_number', 'designation', 'household_size', 'dependents', 'adults', 'children', 'males', 'females', 'income_min', 'income_max', 'shop_name', 'business_number', 'business_category', 'revenue_min', 'revenue_max')

class AccountRegistrations:
    def __init__(self, db, actor):
        self.db, self.actor = db, actor
        Documents(db).require_admin(actor)

    def listing(self, kind, search='', status='', page=1, page_size=20):
        model = PROFILE_MODELS[kind]
        query = select(User).join(model, model.user_id == User.id)
        if search:
            term = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            query = query.where(or_(User.name.ilike(term, escape='\\'), User.email.ilike(term, escape='\\')))
        if status == 'verified':
            query = query.where(User.registration_status == 'approved', User.role_id == select(Role.id).where(Role.name == ROLE_FOR_TYPE[kind]).scalar_subquery())
        elif status == 'pending':
            query = query.where(or_(User.registration_status.is_(None), User.registration_status != 'approved', User.role_id == 0))
        total = self.db.scalar(select(func.count()).select_from(query.subquery()))
        users = self.db.scalars(query.order_by(User.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
        return {'items': [self.summary(user, kind) for user in users], 'total': total, 'page': page, 'page_size': page_size}

    def summary(self, user, kind):
        role = self.db.scalar(select(Role.name).where(Role.id == user.role_id))
        return {'id': user.id, 'name': user.name, 'email': user.email, 'user_type': kind, 'role': role,
                'registration_status': user.registration_status, 'verified': user.registration_status == 'approved' and role == ROLE_FOR_TYPE[kind],
                'is_active': user.is_active, 'email_confirmed': bool(user.email_verified_at), 'created_at': user.created_at}

    def load(self, user_id, lock=False):
        query = select(User).where(User.id == user_id).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        user = self.db.scalar(query)
        if not user:
            raise AuthFlowError('ACCOUNT_NOT_FOUND', 'Registration not found.', 404)
        profiles = [(kind, self.db.get(model, user.id)) for kind, model in PROFILE_MODELS.items()]
        profiles = [(kind, profile) for kind, profile in profiles if profile is not None]
        if len(profiles) != 1:
            raise AuthFlowError('INVALID_REGISTRATION', 'This account does not have a single registration profile.', 409)
        kind, profile = profiles[0]
        docs = self.db.scalars(select(Document).where(Document.user_id == user.id).order_by(Document.id)).all()
        latest = {doc.document_slot: doc for doc in docs}
        return user, kind, profile, list(latest.values())

    def blockers(self, user, kind, profile, docs):
        reasons = []
        role = self.db.scalar(select(Role.name).where(Role.id == user.role_id))
        if user.id == self.actor.id or role not in ('UNASSIGNED', ROLE_FOR_TYPE[kind]):
            reasons.append('This account has a different assigned role and cannot be verified here.')
        if not user.is_active:
            reasons.append('This account is disabled. Contact the administrator responsible for account access.')
        types = {doc.document_type for doc in docs if doc.user_type == kind}
        if profile.identity_type not in types:
            reasons.append('The selected identity document is missing.')
        if kind == 'ngo' and not types.intersection({'ngo_registration', 'ngo_certificate'}):
            reasons.append('The NGO registration certificate is missing.')
        return reasons

    def detail(self, user_id):
        user, kind, profile, docs = self.load(user_id)
        email = self.db.scalar(select(EmailOutbox).where(EmailOutbox.recipient == user.email, EmailOutbox.subject == SUBJECT).order_by(EmailOutbox.id.desc()).limit(1))
        return {**self.summary(user, kind), 'profile': {key: getattr(profile, key) for key in PROFILE_FIELDS if hasattr(profile, key)},
                'documents': [{'id': doc.id, 'document_type': doc.document_type, 'document_slot': doc.document_slot, 'status': doc.status, 'verified_at': doc.verified_at, 'verified_by': doc.verified_by} for doc in docs],
                'verification_blockers': self.blockers(user, kind, profile, docs), 'email_delivery': email.status if email else None}

    def verify(self, user_id):
        try:
            user, kind, profile, docs = self.load(user_id, lock=True)
            if self.summary(user, kind)['verified']:
                result = self.detail(user_id)
                self.db.commit()
                return result
            reasons = self.blockers(user, kind, profile, docs)
            if reasons:
                raise AuthFlowError('VERIFICATION_BLOCKED', ' '.join(reasons), 409)
            role_id = self.db.scalar(select(Role.id).where(Role.name == ROLE_FOR_TYPE[kind]))
            if role_id is None:
                raise AuthFlowError('ROLE_MISSING', 'The portal role has not been configured.', 409)
            for doc in docs:
                doc.status, doc.approval_type = 'Approved', 'manual'
                doc.verified_by, doc.verified_at = self.actor.id, now_utc()
            user.registration_status = 'approved'
            activate(self.db, user, kind)
            if user.role_id != role_id:
                raise AuthFlowError('ROLE_CONFLICT', 'The account role could not be assigned.', 409)
            detail = ('Your registration has been verified by PDMA. You can now sign in to your ' + kind + ' portal.'
                      if user.email_verified_at else 'Your registration has been verified by PDMA. Confirm your email address, then sign in to your ' + kind + ' portal.')
            enqueue(self.db, user.email, SUBJECT, f'Hello {user.name},\n\n{detail}\n\nAab o Hisaab',
                    f'<html lang="en"><body><h1>Account verified</h1><p>Hello {escape(user.name)},</p><p>{escape(detail)}</p><p>Aab o Hisaab</p></body></html>')
            self.db.flush()
            result = self.detail(user_id)
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

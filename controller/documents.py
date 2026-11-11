"""Ownership and admin checks are made against the database, never client role claims."""

from sqlalchemy import select

from config.document_storage import DocumentStorage
from controller.authentication import AuthFlowError, now_utc
from model.document_model import Document
from model.registration_model import PROFILE_MODELS
from model.registration_schemas import (
    AIVerificationResult,
    DOCUMENT_TYPES,
    DocumentResponse,
    document_slots,
)
from model.role_model import Role


class Documents:
    def __init__(self, db, storage=None):
        self.db = db
        self.storage = storage or DocumentStorage()

    def is_admin(self, user):
        return (
            self.db.scalar(select(Role.name).where(Role.id == user.role_id)) == "ADMIN"
        )

    def require_admin(self, user):
        if not self.is_admin(user):
            raise AuthFlowError("FORBIDDEN", "Administrator access is required.", 403)

    def owned_or_admin(self, document_id, user):
        document = self.db.get(Document, document_id)
        if document is None or (
            document.user_id != user.id and not self.is_admin(user)
        ):
            raise AuthFlowError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        return document

    def upload(self, user, slot, upload):
        reference = None
        try:
            profiles = [
                (kind, self.db.get(model, user.id))
                for kind, model in PROFILE_MODELS.items()
            ]
            profiles = [
                (kind, profile) for kind, profile in profiles if profile is not None
            ]
            if len(profiles) != 1:
                raise AuthFlowError(
                    "PROFILE_REQUIRED",
                    "A registered profile is required to upload documents.",
                    409,
                )
            kind, profile = profiles[0]
            if slot not in document_slots(kind, profile.identity_type):
                raise AuthFlowError(
                    "INVALID_DOCUMENT_TYPE",
                    "This document does not match your registration type.",
                    422,
                )
            reference, size = self.storage.save(upload)
            document = Document(
                user_id=user.id,
                user_type=kind,
                document_type=DOCUMENT_TYPES[slot],
                document_slot=slot,
                document_url=reference,
                content_type=upload.content_type,
                size_bytes=size,
            )
            self.db.add(document)
            if user.registration_status is not None:
                user.registration_status = "under_review"
            self.db.flush()
            response = DocumentResponse.model_validate(document)
            self.db.commit()
            return response
        except Exception:
            self.db.rollback()
            if reference:
                self.storage.remove(reference)
            raise

    def verify(self, document_id, user, data, verifier=None):
        self.require_admin(user)
        try:
            document = self.db.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            if document is None:
                raise AuthFlowError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
            if document.user_id == user.id:
                raise AuthFlowError(
                    "SELF_REVIEW_FORBIDDEN",
                    "You cannot verify your own documents.",
                    403,
                )
            # Reviewed versions are immutable; replacement uploads start Pending.
            if document.status != "Pending":
                raise AuthFlowError(
                    "ALREADY_REVIEWED", "This document has already been reviewed.", 409
                )
            if data.approval_type == "ai":
                if verifier is None:
                    raise AuthFlowError(
                        "AI_NOT_CONFIGURED",
                        "AI document verification is not configured.",
                        503,
                    )
                try:
                    result = AIVerificationResult.model_validate(
                        verifier.verify(
                            self.storage.path(document.document_url),
                            document.document_type,
                        )
                    )
                except Exception as exc:
                    raise AuthFlowError(
                        "AI_VERIFICATION_FAILED",
                        "The verification service did not return a valid result.",
                        502,
                    ) from exc
                document.status = result.status
                document.verification_result = result.model_dump()
            else:
                document.status = data.status
                document.verification_result = {"reason": data.reason}
            document.approval_type = data.approval_type
            document.verified_by = user.id
            document.verified_at = now_utc()
            self.db.flush()
            from model.user_model import User

            owner = self.db.get(User, document.user_id)
            if owner.registration_status is not None:
                # Keep old versions for review history, and decide registration
                # state from the newest version of each document slot.
                latest = dict(
                    self.db.execute(
                        select(Document.document_slot, Document.status)
                        .where(Document.user_id == owner.id)
                        .order_by(Document.id)
                    ).all()
                )
                states = list(latest.values())
                owner.registration_status = (
                    "approved"
                    if all(state == "Approved" for state in states)
                    else (
                        "corrections_required"
                        if any(state == "Rejected" for state in states)
                        else "under_review"
                    )
                )
            self.db.flush()
            response = DocumentResponse.model_validate(document)
            self.db.commit()
            return response
        except Exception:
            self.db.rollback()
            raise


def notify_document_review(bind, document_id):
    """Review records serve as a small persisted outbox; failures remain retryable."""
    import logging
    from sqlalchemy.orm import Session
    from config.auth_mail import AuthMailer, MailDeliveryError
    from model.user_model import User

    with Session(bind) as db:
        document = db.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        if (
            document is None
            or document.status == "Pending"
            or document.review_notification_sent_at
        ):
            return
        user = db.get(User, document.user_id)
        try:
            AuthMailer().send(
                user.email,
                user.name,
                "document_approved"
                if document.status == "Approved"
                else "document_rejected",
            )
            document.review_notification_sent_at = now_utc()
            db.commit()
        except MailDeliveryError:
            db.rollback()
            logging.getLogger(__name__).error(
                "Document review notification could not be delivered; administrator may retry."
            )

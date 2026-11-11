"""Consume a verified or manually queued document with the registration transaction."""

from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from agents.document_verification_agent import identity_matches
from controller.authentication import AuthFlowError, now_utc
from controller.verification import Verification, ACTIVE_STAGES, MANUAL_MESSAGE
from model.document_model import Document
from model.registration_model import PROFILE_MODELS
from model.registration_schemas import (
    DocumentResponse,
    RegistrationResponse,
    ROLE_FIELDS,
)
from model.user_model import User

COMMON_FIELDS = [
    "phone",
    "identity_type",
    "identity_number",
    "country",
    "state",
    "city",
    "postal_code",
    "address1",
    "address2",
    "address_confirmed",
]


class Registration:
    def __init__(self, db, storage=None):
        self.db = db

    def create(self, data, uploads=None):
        try:
            if (
                self.db.scalar(
                    select(User.id).where(func.lower(User.email) == data.email)
                )
                is not None
            ):
                raise AuthFlowError(
                    "USER_EXISTS",
                    "An account with this email already exists. Sign in or reset your password.",
                    409,
                )
            process = Verification(self.db).authorize(
                data.verification_id, data.verification_token, lock=True
            )
            if process.user_type != data.user_type:
                raise AuthFlowError(
                    "USER_TYPE_MISMATCH",
                    "Your registration type differs from the verified submission.",
                    422,
                )
            if process.stage in ACTIVE_STAGES or not (
                process.outcome == "checks_passed" or process.review_requested_at
            ):
                raise AuthFlowError(
                    "VERIFICATION_REQUIRED",
                    "Complete automated checks or request manual review after three attempts.",
                    409,
                )
            if (
                process.document_type in ("cnic", "passport")
                and process.document_type != data.identity_type
            ):
                raise AuthFlowError(
                    "IDENTITY_TYPE_MISMATCH",
                    "The identity document type must match your account details.",
                    422,
                )
            if process.outcome == "checks_passed" and not identity_matches(
                process.extracted_data, data.identity_number
            ):
                raise AuthFlowError(
                    "IDENTITY_MISMATCH",
                    "The identity number in your account details does not match the readable document.",
                    422,
                )
            approved = process.document_status == "Approved"
            user = User(
                name=data.full_name,
                email=data.email,
                password=PasswordHash.recommended().hash(data.password),
                role_id=0,
                registration_status="approved" if approved else "under_review",
            )
            self.db.add(user)
            self.db.flush()
            self.db.add(
                PROFILE_MODELS[data.user_type](
                    user_id=user.id,
                    **{
                        key: getattr(data, key)
                        for key in COMMON_FIELDS + ROLE_FIELDS[data.user_type]
                    },
                )
            )
            if not approved and process.review_requested_at is None:
                process.review_requested_at = now_utc()
            document = Document(
                user_id=user.id,
                user_type=data.user_type,
                document_type=process.document_type,
                document_slot={
                    "cnic": "cnicFront",
                    "passport": "passportCopy",
                    "home_address": "addressProof",
                    "ngo_registration": "ngoCertificate",
                }[process.document_type],
                document_url=process.document_url,
                content_type=process.content_type,
                size_bytes=process.size_bytes,
                verification_attempts=process.verification_attempts,
                verification_result=process.verification_result,
                extracted_data=process.extracted_data,
                review_requested_at=process.review_requested_at,
                status=process.document_status,
                approval_type="ai" if approved else None,
                verified_at=now_utc() if approved else None,
            )
            self.db.add(document)
            process.claimed_user_id, process.consumed_at = user.id, now_utc()
            self.db.flush()
            response = RegistrationResponse(
                message="Automated checks passed. Confirm your email."
                if approved
                else MANUAL_MESSAGE,
                user_id=user.id,
                user_type=data.user_type,
                registration_status=user.registration_status,
                documents=[DocumentResponse.model_validate(document)],
            )
            self.db.commit()
            return response
        except Exception as exc:
            self.db.rollback()
            if isinstance(exc, IntegrityError):
                existing = self.db.scalar(
                    select(User.id).where(func.lower(User.email) == data.email)
                )
                self.db.rollback()
                if existing is not None:
                    raise AuthFlowError(
                        "USER_EXISTS", "An account with this email already exists.", 409
                    ) from exc
            raise

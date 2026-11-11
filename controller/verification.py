"""Persisted verification orchestration. All CV/OCR and validation live in agents/."""

from datetime import timedelta
import hashlib
import hmac
import os
import secrets
from threading import BoundedSemaphore
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.document_storage import DocumentStorage
from controller.authentication import AuthFlowError, now_utc
from model.verification_model import VerificationProcess

ACTIVE_STAGES = {
    "queued",
    "scanning",
    "enhancing",
    "extracting",
    "validating",
    "completing",
}
WORKERS = BoundedSemaphore(
    max(1, int(os.environ.get("DOCUMENT_VERIFICATION_WORKERS", "2")))
)
MANUAL_MESSAGE = "Your registration is under process. Your document has been submitted for manual review. You will be notified once verification is complete."


def process_status(process):
    details = {}
    if process.stage == "completed" and process.extracted_data:
        import json
        from agents.document_verification_agent import cipher
        allowed = {"identity_number", "surname", "given_names", "nationality", "date_of_birth", "expiry_date", "organization_name", "registration_number", "address"}
        decoded = json.loads(cipher().decrypt(process.extracted_data.encode()))
        details = {key: value for key, value in decoded.items() if key in allowed and isinstance(value, str)}
    return {
        "extracted_details": details,
        "id": process.id,
        "document_type": process.document_type,
        "user_type": process.user_type,
        "stage": process.stage,
        "completed_stages": process.completed_stages,
        "outcome": process.outcome,
        "verification_attempts": process.verification_attempts,
        "attempts_remaining": 3 - process.verification_attempts,
        "verification_result": process.verification_result,
        "document_status": process.document_status,
        "review_requested_at": process.review_requested_at,
        "can_register": bool(
            process.outcome == "checks_passed" or process.review_requested_at
        )
        and process.stage not in ACTIVE_STAGES,
        "message": MANUAL_MESSAGE
        if process.review_requested_at
        else (process.verification_result or {}).get("message"),
        "registration_submitted": process.consumed_at is not None,
    }


class Verification:
    def __init__(self, db):
        self.db = db
        self.storage = DocumentStorage()

    def authorize(self, process_id, token, lock=False, allow_consumed=False):
        query = (
            select(VerificationProcess)
            .where(VerificationProcess.id == process_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update()
        process = self.db.scalar(query)
        if (
            not token
            or not process
            or not hmac.compare_digest(
                process.token_hash, hashlib.sha256(token.encode()).hexdigest()
            )
        ):
            raise AuthFlowError(
                "VERIFICATION_NOT_FOUND", "Verification process not found.", 404
            )
        if process.expires_at <= now_utc():
            raise AuthFlowError(
                "VERIFICATION_EXPIRED",
                "This verification session expired. Start a new registration.",
                410,
            )
        if process.consumed_at and not allow_consumed:
            raise AuthFlowError(
                "VERIFICATION_CONSUMED",
                "This document has already been submitted with a registration.",
                409,
            )
        return process

    def upload(
        self, upload, user_type, document_type, owner_key, process_id=None, token=None
    ):
        reference = None
        previous = None
        try:
            if document_type == "ngo_registration" and user_type != "ngo":
                raise AuthFlowError(
                    "INVALID_DOCUMENT_TYPE",
                    "An NGO registration certificate requires the NGO user type.",
                    422,
                )
            process = (
                self.authorize(process_id, token, lock=True) if process_id else None
            )
            if process:
                if process.user_type != user_type:
                    raise AuthFlowError(
                        "USER_TYPE_MISMATCH",
                        "The verification session belongs to a different user type.",
                        422,
                    )
                if process.document_type != document_type:
                    raise AuthFlowError("DOCUMENT_TYPE_LOCKED", "The document type is fixed for this verification session. Return to account details to change your identity type.", 422)
                if process.stage in ACTIVE_STAGES:
                    raise AuthFlowError(
                        "VERIFICATION_RUNNING",
                        "Wait for the current verification to finish.",
                        409,
                    )
                if process.verification_attempts >= 3 or process.review_requested_at:
                    raise AuthFlowError(
                        "MANUAL_REVIEW_REQUIRED",
                        "Automatic attempts are exhausted. Request manual review for this document.",
                        409,
                    )
                previous = process.document_url
            else:
                recent = self.db.scalar(
                    select(func.count())
                    .select_from(VerificationProcess)
                    .where(
                        VerificationProcess.owner_key == owner_key,
                        VerificationProcess.created_at > now_utc() - timedelta(hours=1),
                    )
                )
                if recent >= int(os.environ.get("DOCUMENT_SESSIONS_PER_HOUR", "100")):
                    raise AuthFlowError(
                        "SESSION_LIMIT",
                        "Too many verification sessions. Please try again later.",
                        429,
                    )
            reference, size = self.storage.save(upload)
            if process is None:
                token = secrets.token_urlsafe(32)
                process = VerificationProcess(
                    id=str(uuid4()),
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    owner_key=owner_key,
                    user_type=user_type,
                    document_type=document_type,
                    document_url=reference,
                    content_type=upload.content_type,
                    size_bytes=size,
                    expires_at=now_utc() + timedelta(hours=24),
                    completed_stages=[],
                )
                self.db.add(process)
            else:
                process.document_url, process.content_type, process.size_bytes = (
                    reference,
                    upload.content_type,
                    size,
                )
                process.document_type, process.stage = document_type, "uploaded"
                process.outcome, process.verification_result, process.extracted_data = (
                    None,
                    None,
                    None,
                )
                process.completed_stages = []
                process.document_status = "Pending"
            self.db.flush()
            response = {**process_status(process), "verification_token": token}
            self.db.commit()
        except Exception:
            self.db.rollback()
            if reference:
                self.storage.remove(reference)
            raise
        if previous:
            self.storage.remove(previous)
        return response

    def start(self, process_id, token, tasks):
        acquired = False
        try:
            process = self.authorize(process_id, token, lock=True)
            if process.stage in ACTIVE_STAGES:
                raise AuthFlowError(
                    "VERIFICATION_RUNNING", "Verification is already running.", 409
                )
            if process.verification_attempts >= 3 or process.review_requested_at:
                raise AuthFlowError(
                    "MANUAL_REVIEW_REQUIRED",
                    "The three automatic attempts are exhausted. Request manual review.",
                    409,
                )
            if process.outcome == "checks_passed":
                raise AuthFlowError(
                    "CHECKS_ALREADY_PASSED",
                    "Automated checks already passed. Continue registration.",
                    409,
                )
            acquired = WORKERS.acquire(blocking=False)
            if not acquired:
                raise AuthFlowError(
                    "VERIFICATION_BUSY",
                    "Verification workers are busy. Please try again shortly.",
                    429,
                )
            process.verification_attempts += 1
            process.stage, process.outcome = "queued", None
            (
                process.completed_stages,
                process.verification_result,
                process.extracted_data,
            ) = [], None, None
            process.document_status = "Pending"
            self.db.flush()
            response = process_status(process)
            attempt = process.verification_attempts
            bind = self.db.get_bind()
            self.db.commit()
            tasks.add_task(run_verification, bind, process_id, attempt)
            return response
        except Exception:
            self.db.rollback()
            if acquired:
                WORKERS.release()
            raise

    def manual_review(self, process_id, token):
        try:
            process = self.authorize(process_id, token, lock=True)
            if process.stage in ACTIVE_STAGES:
                raise AuthFlowError(
                    "VERIFICATION_RUNNING", "Wait for verification to finish.", 409
                )
            if process.verification_attempts < 3 and process.outcome != "checks_passed":
                raise AuthFlowError(
                    "ATTEMPTS_REMAIN",
                    "Use the remaining automatic attempts before requesting manual review.",
                    409,
                )
            if not process.review_requested_at:
                process.review_requested_at = now_utc()
            process.document_status = "Pending"
            self.db.flush()
            response = process_status(process)
            self.db.commit()
            return response
        except Exception:
            self.db.rollback()
            raise


def run_verification(bind, process_id, attempt):
    """BackgroundTasks uses a worker thread; each progress transition is committed."""
    try:
        from agents.document_verification_agent import DocumentVerificationAgent

        with Session(bind) as db:
            process = db.get(VerificationProcess, process_id)
            path = DocumentStorage().path(process.document_url)
            content_type, document_type = process.content_type, process.document_type
            db.rollback()

            def progress(stage, done):
                process = db.scalar(
                    select(VerificationProcess)
                    .where(VerificationProcess.id == process_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if process.verification_attempts != attempt or process.consumed_at:
                    raise RuntimeError("Verification version changed.")
                process.stage = stage
                if done and stage not in process.completed_stages:
                    process.completed_stages = [*process.completed_stages, stage]
                db.commit()

            try:
                result = DocumentVerificationAgent().analyze(
                    path, content_type, document_type, progress
                )
                process = db.get(VerificationProcess, process_id)
                db.refresh(process)
                process.outcome = result["outcome"]
                process.verification_result = result["result"]
                process.extracted_data = result["extracted_data"]
                process.document_status = result["document_status"]
                process.stage = "completed"
                process.completed_stages = [*process.completed_stages, "completing"]
                db.commit()
            except Exception as exc:
                db.rollback()
                import logging

                logging.getLogger(__name__).error(
                    "Document processing failed (%s).", type(exc).__name__
                )
                process = db.get(VerificationProcess, process_id)
                process.stage, process.outcome = "failed", "error"
                process.document_status = "Pending"
                process.verification_result = {
                    "automated_checks_passed": False,
                    "authenticity_verified": False,
                    "manual_review_required": True,
                    "message": str(exc) if isinstance(exc, ValueError) and str(exc).startswith(("Grok ", "Local OCR ")) else "Processing could not complete. Try again or request manual review after three attempts.",
                    "reasons": [
                        "The processing service could not safely complete this attempt."
                    ],
                }
                db.commit()
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error(
            "Document worker could not complete (%s).", type(exc).__name__
        )
        try:
            with Session(bind) as recovery:
                process = recovery.get(VerificationProcess, process_id)
                if (
                    process
                    and not process.consumed_at
                    and process.verification_attempts == attempt
                ):
                    process.stage, process.outcome = "failed", "error"
                    process.document_status = "Pending"
                    process.verification_result = {
                        "automated_checks_passed": False,
                        "message": "The processing worker was interrupted. Retry or request manual review after three attempts.",
                    }
                    recovery.commit()
        except Exception:
            logging.getLogger(__name__).error(
                "Document worker recovery requires a database connection."
            )
    finally:
        WORKERS.release()


def recover_interrupted_jobs():
    """Do not silently lose in-process jobs after a server restart; retain attempt counts."""
    from config.database import SessionLocal
    from sqlalchemy import inspect

    with SessionLocal() as db:
        if not inspect(db.get_bind()).has_table("verification_processes"):
            return
        for process in db.scalars(
            select(VerificationProcess).where(
                VerificationProcess.stage.in_(ACTIVE_STAGES)
            )
        ):
            process.stage, process.outcome = "failed", "error"
            process.verification_result = {
                "message": "Processing was interrupted by a server restart. Retry this attempt or request manual review when attempts are exhausted.",
                "automated_checks_passed": False,
            }
        db.commit()

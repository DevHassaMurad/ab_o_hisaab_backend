import hashlib
import hmac
import os
from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    Request,
    UploadFile,
)
from fastapi.responses import Response
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from config.database import get_db
from config.document_storage import MAX_FILE_BYTES
from controller.authentication import AuthFlowError
from controller.documents import Documents, notify_document_review
from controller.verification import Verification, process_status
from middlewares.auth_middleware import auth_middleware
from model.registration_schemas import UserType, VerificationRequest
from routes.auth_routes import auth_rate_limit

router = APIRouter(
    tags=["Document verification"], dependencies=[Depends(auth_rate_limit)]
)
DocumentType = Literal["cnic", "passport", "home_address", "ngo_registration"]


class StartVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=36, max_length=36)


@router.get("/documents/config")
def verification_config():
    return {
        "max_file_bytes": MAX_FILE_BYTES,
        "max_attempts": 3,
        "document_types": ["cnic", "passport", "home_address", "ngo_registration"],
    }


@router.post("/documents/upload", status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    user_type: UserType | None = Form(None),
    document_type: DocumentType | None = Form(None),
    process_id: str | None = Form(None),
    document_slot: str | None = Form(None),
    verification_token: str | None = Header(None, alias="X-Verification-Token"),
    credentials=Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db),
):
    if document_slot is not None:
        # Compatibility for signed-in users replacing documents after registration.
        user = await auth_middleware(credentials=credentials, db=db)
        return await run_in_threadpool(Documents(db).upload, user, document_slot, file)
    if user_type is None or document_type is None:
        raise AuthFlowError(
            "INVALID_REQUEST", "Select a user type and document type.", 422
        )
    secret = os.environ.get("AUTH_CODE_SECRET") or os.environ.get("JWT_SECRET_KEY")
    if not secret:
        raise AuthFlowError(
            "VERIFICATION_UNAVAILABLE", "Verification security is not configured.", 503
        )
    ip = request.client.host if request.client else "unknown"
    owner_key = hmac.new(
        secret.encode(), ("document-session:" + ip).encode(), hashlib.sha256
    ).hexdigest()
    return await run_in_threadpool(
        Verification(db).upload,
        file,
        user_type,
        document_type,
        owner_key,
        process_id,
        verification_token,
    )


@router.post("/documents/verify", status_code=202)
def start_verification(
    data: StartVerification,
    tasks: BackgroundTasks,
    verification_token: str | None = Header(None, alias="X-Verification-Token"),
    db: Session = Depends(get_db),
):
    return Verification(db).start(data.document_id, verification_token, tasks)


@router.get("/documents/{document_id}/verification-status")
def verification_status(
    document_id: str,
    verification_token: str | None = Header(None, alias="X-Verification-Token"),
    db: Session = Depends(get_db),
):
    return process_status(
        Verification(db).authorize(document_id, verification_token, allow_consumed=True)
    )


@router.get("/documents/{document_id}/preview")
def document_preview(
    document_id: str,
    verification_token: str | None = Header(None, alias="X-Verification-Token"),
    db: Session = Depends(get_db),
):
    from agents.document_preprocessor import preview_png

    service = Verification(db)
    process = service.authorize(document_id, verification_token)
    return Response(
        preview_png(service.storage.path(process.document_url), process.content_type),
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )


@router.post("/documents/{document_id}/request-manual-review")
def request_manual_review(
    document_id: str,
    verification_token: str | None = Header(None, alias="X-Verification-Token"),
    db: Session = Depends(get_db),
):
    return Verification(db).manual_review(document_id, verification_token)


@router.patch("/admin/documents/{document_id}/review")
def administrator_review(
    document_id: int,
    data: VerificationRequest,
    tasks: BackgroundTasks,
    user=Depends(auth_middleware),
    db: Session = Depends(get_db),
):
    if data.approval_type != "manual":
        raise AuthFlowError(
            "INVALID_REVIEW_METHOD", "This endpoint is for administrator review.", 422
        )
    response = Documents(db).verify(document_id, user, data)
    tasks.add_task(notify_document_review, db.get_bind(), document_id)
    return response


@router.post("/admin/documents/{document_id}/notify", status_code=202)
def retry_review_notification(
    document_id: int,
    tasks: BackgroundTasks,
    user=Depends(auth_middleware),
    db: Session = Depends(get_db),
):
    Documents(db).require_admin(user)
    document = Documents(db).owned_or_admin(document_id, user)
    if document.status == "Pending":
        raise AuthFlowError(
            "REVIEW_PENDING",
            "Complete document review before sending a notification.",
            409,
        )
    tasks.add_task(notify_document_review, db.get_bind(), document_id)
    return {"message": "Review notification scheduled."}

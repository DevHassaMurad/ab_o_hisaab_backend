from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.database import get_db
from controller.authentication import AuthFlowError
from controller.documents import Documents, notify_document_review
from middlewares.auth_middleware import auth_middleware
from model.document_model import Document
from model.registration_schemas import (
    DocumentResponse,
    VerificationRequest,
)
from routes.auth_routes import auth_rate_limit

router = APIRouter(
    prefix="/documents", tags=["Documents"], dependencies=[Depends(auth_rate_limit)]
)


@router.get("/me", response_model=list[DocumentResponse])
def my_documents(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user=Depends(auth_middleware),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(Document)
        .where(Document.user_id == user.id)
        .order_by(Document.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("", response_model=list[DocumentResponse])
def review_queue(
    status: str = Query("Pending", pattern="^(Pending|Approved|Rejected)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user=Depends(auth_middleware),
    db: Session = Depends(get_db),
):
    Documents(db).require_admin(user)
    return db.scalars(
        select(Document)
        .where(Document.status == status)
        .order_by(Document.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/{document_id}/download")
def download_document(
    document_id: int, user=Depends(auth_middleware), db: Session = Depends(get_db)
):
    service = Documents(db)
    document = service.owned_or_admin(document_id, user)
    path = service.storage.path(document.document_url)
    if not path.is_file():
        raise AuthFlowError("DOCUMENT_NOT_FOUND", "Document file not found.", 404)
    return FileResponse(
        path,
        media_type=document.content_type,
        filename=f"document-{document.id}{path.suffix}",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.patch("/{document_id}/verify", response_model=DocumentResponse)
def verify_document(
    document_id: int,
    data: VerificationRequest,
    request: Request,
    tasks: BackgroundTasks,
    user=Depends(auth_middleware),
    db: Session = Depends(get_db),
):
    response = Documents(db).verify(
        document_id, user, data, getattr(request.app.state, "document_verifier", None)
    )
    tasks.add_task(notify_document_review, db.get_bind(), document_id)
    return response

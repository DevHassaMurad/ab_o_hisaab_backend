from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from config.database import get_db
from config.outbox import deliver_due
from controller.campaigns import MAX_UPLOAD, Campaigns
from controller.donation import push_notification
from middlewares.role_middleware import PDMA_STAFF, require_roles, role_name

router = APIRouter(tags=["Donation campaigns"])
staff_only = require_roles(*PDMA_STAFF)
staff_or_ngo = require_roles(*PDMA_STAFF, "NGO")
anyone = require_roles(*PDMA_STAFF, "NGO", "DONOR")
donor_only = require_roles("DONOR")
Amount = Field(gt=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)
Status = Literal["draft", "pending_review", "under_review", "changes_requested", "approved", "scheduled", "active",
                 "goal_reached", "completed", "rejected", "paused", "cancelled"]


def _clean(v):
    if v is None:
        return v
    v = " ".join(v.split())
    if not v:
        raise ValueError("This field is required.")
    return v


class CampaignIn(BaseModel):
    name: str = Field(min_length=3, max_length=150)
    short_description: str = Field(min_length=10, max_length=300)
    description: str = Field(min_length=20, max_length=10000)
    objective: str = Field(min_length=5, max_length=500)
    reason: str | None = Field(default=None, max_length=1000)
    target_amount: Decimal = Amount
    allow_overfunding: bool = False
    overfunding_limit: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    start_date: date
    end_date: date | None = None
    district: str | None = Field(default=None, max_length=100)
    contact_info: str | None = Field(default=None, max_length=300)
    _n = field_validator("name", "short_description", "objective")(classmethod(lambda cls, v: _clean(v)))


class CampaignPatch(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=150)
    short_description: str | None = Field(default=None, min_length=10, max_length=300)
    description: str | None = Field(default=None, min_length=20, max_length=10000)
    objective: str | None = Field(default=None, min_length=5, max_length=500)
    reason: str | None = Field(default=None, max_length=1000)
    target_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    allow_overfunding: bool | None = None
    overfunding_limit: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    start_date: date | None = None
    end_date: date | None = None
    district: str | None = Field(default=None, max_length=100)
    contact_info: str | None = Field(default=None, max_length=300)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    _r = field_validator("reason")(classmethod(lambda cls, v: _clean(v)))


class NoteIn(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)


class ContributionIn(BaseModel):
    amount: Decimal = Amount
    payment_method: Literal["BANK_TRANSFER", "CHEQUE", "CASH", "ONLINE", "OTHER"] | None = None
    payment_reference: str | None = Field(default=None, max_length=100)
    remarks: str | None = Field(default=None, max_length=500)


class VerifyIn(BaseModel):
    action: Literal["verify", "reject"]
    reason: str | None = Field(default=None, max_length=500)


class VisibilityIn(BaseModel):
    visibility: Literal["internal", "donor_public"]


def svc(db, user):
    return Campaigns(db, user, role_name(db, user))


def _after(background_tasks, db, s):
    for user_id, payload in s.notes:
        background_tasks.add_task(push_notification, user_id, payload)
    background_tasks.add_task(deliver_due, db.get_bind())


def _do(background_tasks, db, user, fn, *args):
    s = svc(db, user)
    out = getattr(s, fn)(*args)
    _after(background_tasks, db, s)
    return out


# Shared --------------------------------------------------------------------------------------------
@router.get("/campaigns")
def list_campaigns(status: Status | None = None, search: str | None = Query(default=None, max_length=100),
                   source: Literal["PDMA", "NGO"] | None = None,
                   scope: Literal["review", "open", "closed"] | None = None,
                   page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                   user=Depends(anyone), db: Session = Depends(get_db)):
    return svc(db, user).list(status, search, source, page, page_size, scope)


@router.get("/campaigns/dashboard")
def campaign_dashboard(user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    return svc(db, user).dashboard()


@router.get("/campaigns/my-summary")
def donor_campaign_summary(user=Depends(donor_only), db: Session = Depends(get_db)):
    return svc(db, user).donor_summary()


@router.get("/campaign-contributions")
def list_contributions(campaign_id: int | None = Query(default=None, gt=0),
                       status: Literal["pending", "verified", "rejected"] | None = None,
                       page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                       user=Depends(anyone), db: Session = Depends(get_db)):
    return svc(db, user).contributions(campaign_id, status, page, page_size)


@router.post("/campaign-contributions/{contribution_id}/verify")
def verify_contribution(contribution_id: int, body: VerifyIn, background_tasks: BackgroundTasks,
                        user=Depends(staff_only), db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "verify_contribution", contribution_id, body.action, body.reason)


@router.get("/campaigns/{campaign_id}")
def campaign_detail(campaign_id: int, user=Depends(anyone), db: Session = Depends(get_db)):
    return svc(db, user).detail(campaign_id)


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignIn, user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    return svc(db, user).create(body.model_dump())


@router.patch("/campaigns/{campaign_id}")
def update_campaign(campaign_id: int, body: CampaignPatch, user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    return svc(db, user).update(campaign_id, body.model_dump(exclude_unset=True))


# NGO ---------------------------------------------------------------------------------------------
@router.post("/campaigns/{campaign_id}/submit")
def submit_campaign(campaign_id: int, background_tasks: BackgroundTasks,
                    user=Depends(require_roles("NGO")), db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "submit", campaign_id)


# PDMA review and lifecycle -----------------------------------------------------------------------
@router.post("/campaigns/{campaign_id}/start-review")
def start_review(campaign_id: int, background_tasks: BackgroundTasks, user=Depends(staff_only),
                 db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "start_review", campaign_id)


@router.post("/campaigns/{campaign_id}/approve")
def approve_campaign(campaign_id: int, body: NoteIn, background_tasks: BackgroundTasks, user=Depends(staff_only),
                     db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "approve", campaign_id, body.notes)


@router.post("/campaigns/{campaign_id}/reject")
def reject_campaign(campaign_id: int, body: ReasonIn, background_tasks: BackgroundTasks, user=Depends(staff_only),
                    db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "reject", campaign_id, body.reason)


@router.post("/campaigns/{campaign_id}/request-changes")
def request_changes(campaign_id: int, body: ReasonIn, background_tasks: BackgroundTasks, user=Depends(staff_only),
                    db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "request_changes", campaign_id, body.reason)


@router.post("/campaigns/{campaign_id}/publish")
def publish_campaign(campaign_id: int, background_tasks: BackgroundTasks, user=Depends(staff_only),
                     db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "publish", campaign_id)


@router.post("/campaigns/{campaign_id}/pause")
def pause_campaign(campaign_id: int, body: NoteIn, background_tasks: BackgroundTasks, user=Depends(staff_only),
                   db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "pause", campaign_id, body.notes)


@router.post("/campaigns/{campaign_id}/resume")
def resume_campaign(campaign_id: int, background_tasks: BackgroundTasks, user=Depends(staff_only),
                    db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "resume", campaign_id)


@router.post("/campaigns/{campaign_id}/complete")
def complete_campaign(campaign_id: int, body: NoteIn, background_tasks: BackgroundTasks, user=Depends(staff_only),
                      db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "complete", campaign_id, body.notes)


@router.post("/campaigns/{campaign_id}/cancel")
def cancel_campaign(campaign_id: int, body: ReasonIn, background_tasks: BackgroundTasks, user=Depends(staff_only),
                    db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "cancel", campaign_id, body.reason)


# Donor -------------------------------------------------------------------------------------------
@router.post("/campaigns/{campaign_id}/contributions", status_code=201)
def contribute(campaign_id: int, body: ContributionIn, background_tasks: BackgroundTasks,
               user=Depends(donor_only), db: Session = Depends(get_db)):
    return _do(background_tasks, db, user, "contribute", campaign_id, body.model_dump())


# Documents ---------------------------------------------------------------------------------------
@router.post("/campaigns/{campaign_id}/documents", status_code=201)
async def upload_document(campaign_id: int, file: UploadFile = File(...),
                          document_type: Literal["cover", "supporting"] = Form("supporting"),
                          visibility: Literal["internal", "donor_public"] = Form("internal"),
                          user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    data = await file.read(MAX_UPLOAD + 1)
    return svc(db, user).upload(campaign_id, file.filename, data, document_type, visibility)


@router.patch("/campaigns/{campaign_id}/documents/{document_id}")
def document_visibility(campaign_id: int, document_id: int, body: VisibilityIn, user=Depends(staff_only),
                        db: Session = Depends(get_db)):
    return svc(db, user).set_visibility(campaign_id, document_id, body.visibility)


def _file(d, data):
    disposition = "inline" if d.content_type.startswith("image/") else "attachment"
    safe = "".join(ch for ch in d.document_name if ch.isalnum() or ch in "._- ") or "document"
    return Response(data, media_type=d.content_type, headers={
        "Content-Disposition": f'{disposition}; filename="{safe}"', "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=300"})


@router.get("/campaigns/{campaign_id}/documents/{document_id}")
def download_document(campaign_id: int, document_id: int, user=Depends(anyone), db: Session = Depends(get_db)):
    return _file(*svc(db, user).document(campaign_id, document_id))


@router.get("/campaigns/{campaign_id}/cover")
def campaign_cover(campaign_id: int, user=Depends(anyone), db: Session = Depends(get_db)):
    return _file(*svc(db, user).cover(campaign_id))

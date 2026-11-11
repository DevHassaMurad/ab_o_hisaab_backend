from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from config.database import get_db
from config.outbox import deliver_due
from controller.donation import push_notification
from controller.funds import FundController
from middlewares.auth_middleware import auth_middleware
from middlewares.role_middleware import PDMA_STAFF, require_roles, role_name

router = APIRouter(tags=["PDMA funds"])

Amount = Field(gt=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)


class DecisionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class DonorDonationIn(BaseModel):
    amount: Decimal = Amount
    remarks: str | None = Field(default=None, max_length=500)
    payment_reference: str | None = Field(default=None, max_length=100)
    request_id: int | None = Field(default=None, gt=0)


class VerifyIn(BaseModel):
    action: Literal["verify", "reject"]
    received_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def check(self):
        if self.received_at:
            self.received_at = self.received_at.replace(tzinfo=None)
            if (self.received_at - datetime.utcnow()).total_seconds() > 300:
                raise ValueError("The received date cannot be in the future.")
        return self


class DisbursementIn(BaseModel):
    source_batch_id: int = Field(gt=0)
    recipient_type: Literal["ngo", "shopkeeper", "householder"]
    recipient_id: int = Field(gt=0)
    amount: Decimal = Amount
    remarks: str | None = Field(default=None, max_length=500)


class StatusIn(BaseModel):
    status: Literal["pending", "sent", "received", "rejected"]
    reason: str | None = Field(default=None, max_length=500)


def _push_all(background_tasks, notes):
    for user_id, payload in notes:
        background_tasks.add_task(push_notification, user_id, payload)


def _send_mail(background_tasks, db):
    background_tasks.add_task(deliver_due, db.get_bind())  # outbox also retries every 30 s


def _role(db, user, allowed):
    role = role_name(db, user)
    if role not in allowed:
        raise HTTPException(403, "You do not have access to these records.")
    return role


# Donor's decision on a PDMA request (commitment, not payment) -----------------------------------
@router.post("/donation-requests/{request_id}/accept")
def accept_request(request_id: int, background_tasks: BackgroundTasks,
                   donor=Depends(require_roles("DONOR")), db: Session = Depends(get_db)):
    out, notes = FundController(db).decide_request(donor, request_id, accept=True)
    _push_all(background_tasks, notes)
    _send_mail(background_tasks, db)
    return out


@router.post("/donation-requests/{request_id}/reject")
def reject_request(request_id: int, body: DecisionIn, background_tasks: BackgroundTasks,
                   donor=Depends(require_roles("DONOR")), db: Session = Depends(get_db)):
    out, notes = FundController(db).decide_request(donor, request_id, accept=False, reason=body.reason)
    _push_all(background_tasks, notes)
    _send_mail(background_tasks, db)
    return out


# Incoming: donor -> PDMA (DD-IN) ----------------------------------------------------------------------
@router.post("/donor-donations", status_code=201)
def create_donor_donation(body: DonorDonationIn, background_tasks: BackgroundTasks,
                          donor=Depends(require_roles("DONOR")), db: Session = Depends(get_db)):
    """A donor records a payment to PDMA. It counts only after PDMA verifies it."""
    row, notes = FundController(db).create_donor_donation(donor, body.model_dump())
    _push_all(background_tasks, notes)
    return row


@router.get("/donor-donations")
def list_donor_donations(
    status: Literal["pending", "verified", "rejected"] | None = None,
    search: str | None = Query(default=None, max_length=100),
    donor_id: int | None = Query(default=None, gt=0),
    available: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user=Depends(auth_middleware), db: Session = Depends(get_db),
):
    """PDMA staff see every contribution; a donor sees only their own. `available=true`: verified with balance left."""
    role = _role(db, user, (*PDMA_STAFF, "DONOR"))
    return FundController(db).list_donor_donations(user, role, status, search, donor_id, available, page, page_size)


@router.get("/donor-donations/{donation_id}")
def get_donor_donation(donation_id: int, user=Depends(auth_middleware), db: Session = Depends(get_db)):
    role = _role(db, user, (*PDMA_STAFF, "DONOR"))
    return FundController(db).get_donor_donation(donation_id, user, role)


@router.post("/donor-donations/{donation_id}/verify")
def verify_donor_donation(donation_id: int, body: VerifyIn, background_tasks: BackgroundTasks,
                          staff=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)):
    row, notes = FundController(db).verify_donor_donation(staff, donation_id, body.action, body.received_at, body.reason)
    _push_all(background_tasks, notes)
    _send_mail(background_tasks, db)
    return row


# Outgoing: PDMA -> NGO / shopkeeper / householder (DD-OUT) -----------------------------------------
@router.get("/recipients")
def list_recipients(type: Literal["ngo", "shopkeeper", "householder"],
                    search: str | None = Query(default=None, max_length=100),
                    _=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)):
    """Recipients for the Send Donations form. `available: false` when that register does not exist yet."""
    return FundController(db).list_recipients(type, search)


@router.post("/disbursements", status_code=201)
def create_disbursement(body: DisbursementIn, staff=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)):
    return FundController(db).create_disbursement(staff, body.model_dump())


@router.get("/disbursements")
def list_disbursements(
    status: Literal["draft", "pending", "sent", "received", "rejected"] | None = None,
    recipient_type: Literal["ngo", "shopkeeper", "householder"] | None = None,
    search: str | None = Query(default=None, max_length=100),
    date_from: date | None = None, date_to: date | None = None,
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
    _=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db),
):
    return FundController(db).list_disbursements(status, recipient_type, search, date_from, date_to, page, page_size)


@router.post("/disbursements/{disbursement_id}/status")
def set_disbursement_status(disbursement_id: int, body: StatusIn,
                            staff=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)):
    return FundController(db).set_disbursement_status(staff, disbursement_id, body.status, body.reason)


# Analytics and export --------------------------------------------------------------------------------
@router.get("/funds/summary")
def funds_summary(date_from: date | None = None, date_to: date | None = None,
                  _=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)):
    return FundController(db).summary(date_from, date_to)


@router.get("/funds/export")
def funds_export(date_from: date | None = None, date_to: date | None = None,
                 _=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)):
    csv_text = FundController(db).export_csv(date_from, date_to)
    name = f"pdma-funds-{date_from or 'all'}-to-{date_to or 'today'}.csv"
    return Response(csv_text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})

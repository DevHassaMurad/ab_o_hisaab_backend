from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from config.database import get_db
from config.outbox import deliver_due
from config.pdf_reports import ngo_monthly_pdf
from controller.donation import DonationController, push_notification
from controller.ngo import EXPENSE_CATEGORIES, NgoAdmin, NgoPortal
from middlewares.role_middleware import PDMA_STAFF, require_roles

router = APIRouter(tags=["NGO portal"])
only_ngo = require_roles("NGO")
staff_only = require_roles(*PDMA_STAFF)
Amount = Field(gt=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)
Category = Literal[EXPENSE_CATEGORIES]


def _clean(v):
    v = " ".join((v or "").split())
    if not v:
        raise ValueError("This field is required.")
    return v


class NgoRequestIn(BaseModel):
    subject: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=10, max_length=5000)
    amount: Decimal = Amount
    supporting_reference: str | None = Field(default=None, max_length=200)
    _s = field_validator("subject")(classmethod(lambda cls, v: _clean(v)))


class ExpenditureIn(BaseModel):
    allocation_id: int = Field(gt=0)
    category: Category
    description: str = Field(min_length=3, max_length=500)
    amount: Decimal = Amount
    expense_date: date
    supporting_document: str | None = Field(default=None, max_length=200)

    @field_validator("expense_date")
    @classmethod
    def not_future(cls, v):
        if v > (datetime.utcnow() + timedelta(hours=5)).date():
            raise ValueError("The expense date cannot be in the future.")
        return v


class FundingSourceIn(BaseModel):
    donor_donation_id: int = Field(gt=0)
    amount: Decimal = Amount


class ApproveIn(BaseModel):
    amount: Decimal = Amount
    # Donor contributions that fund this approval; must add up to `amount` (checked on the server).
    sources: list[FundingSourceIn] = Field(default_factory=list, max_length=100)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class MessageIn(BaseModel):
    message: str = Field(min_length=3, max_length=500)


class VerifyIn(BaseModel):
    action: Literal["verify", "reject"]
    reason: str | None = Field(default=None, max_length=500)


def _after(background_tasks, db, notes):
    for user_id, payload in notes:
        background_tasks.add_task(push_notification, user_id, payload)
    background_tasks.add_task(deliver_due, db.get_bind())


# NGO portal (own organisation only) -------------------------------------------------------------
@router.get("/ngo/profile")
def ngo_profile(user=Depends(only_ngo), db: Session = Depends(get_db)):
    return NgoPortal(db, user).profile()


@router.get("/ngo/dashboard")
def ngo_dashboard(user=Depends(only_ngo), db: Session = Depends(get_db)):
    return NgoPortal(db, user).dashboard()


@router.post("/ngo/requests", status_code=201)
def ngo_create_request(body: NgoRequestIn, background_tasks: BackgroundTasks,
                       user=Depends(only_ngo), db: Session = Depends(get_db)):
    row, notes = NgoPortal(db, user).create_request(body.model_dump())
    _after(background_tasks, db, notes)
    return row


@router.get("/ngo/requests")
def ngo_list_requests(
    status: Literal["pending", "under_review", "approved", "partially_approved", "rejected"] | None = None,
    search: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
    user=Depends(only_ngo), db: Session = Depends(get_db),
):
    return NgoPortal(db, user).list_requests(status, search, page, page_size)


@router.get("/ngo/requests/{request_id}")
def ngo_get_request(request_id: int, user=Depends(only_ngo), db: Session = Depends(get_db)):
    return NgoPortal(db, user).get_request(request_id)


@router.get("/ngo/allocations")
def ngo_allocations(status: Literal["awaiting_receipt", "received"] | None = None,
                    user=Depends(only_ngo), db: Session = Depends(get_db)):
    return NgoPortal(db, user).allocations(status)


@router.post("/ngo/allocations/{allocation_id}/acknowledge")
def ngo_acknowledge(allocation_id: int, background_tasks: BackgroundTasks,
                    user=Depends(only_ngo), db: Session = Depends(get_db)):
    row, notes = NgoPortal(db, user).acknowledge(allocation_id)
    _after(background_tasks, db, notes)
    return row


@router.post("/ngo/expenditures", status_code=201)
def ngo_create_expenditure(body: ExpenditureIn, background_tasks: BackgroundTasks,
                           user=Depends(only_ngo), db: Session = Depends(get_db)):
    row, notes = NgoPortal(db, user).create_expenditure(body.model_dump())
    _after(background_tasks, db, notes)
    return row


@router.get("/ngo/expenditures")
def ngo_list_expenditures(status: Literal["pending", "verified", "rejected"] | None = None,
                          page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                          user=Depends(only_ngo), db: Session = Depends(get_db)):
    return NgoPortal(db, user).list_expenditures(status, page, page_size)


@router.get("/ngo/reports/monthly")
def ngo_monthly(year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12),
                user=Depends(only_ngo), db: Session = Depends(get_db)):
    return NgoPortal(db, user).monthly_report(year, month)


@router.get("/ngo/reports/monthly.pdf")
def ngo_monthly_pdf_route(year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12),
                          user=Depends(only_ngo), db: Session = Depends(get_db)):
    portal = NgoPortal(db, user)
    report = portal.monthly_report(year, month)
    ref = DonationController(db).next_document_no_for("RPT")
    db.commit()
    pdf = ngo_monthly_pdf(report, ref, datetime.utcnow().isoformat())
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="ngo-monthly-{year}-{month:02d}.pdf"', "Cache-Control": "no-store"})


# PDMA: NGO requests and expenditure verification ------------------------------------------------
@router.get("/ngos/registered")
def registered_ngos(search: str | None = Query(default=None, max_length=100),
                    staff=Depends(staff_only), db: Session = Depends(get_db)):
    return NgoAdmin(db, staff).registered_ngos(search)


@router.get("/ngo-requests")
def list_ngo_requests(
    status: Literal["pending", "under_review", "approved", "partially_approved", "rejected"] | None = None,
    ngo_id: int | None = Query(default=None, gt=0), search: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
    staff=Depends(staff_only), db: Session = Depends(get_db),
):
    return NgoAdmin(db, staff).list_requests(status, ngo_id, search, page, page_size)


@router.get("/ngo-requests/eligible")
def eligible_ngo_requests(ngo_id: int = Query(gt=0), staff=Depends(staff_only), db: Session = Depends(get_db)):
    """Approved requests of an NGO with approved money still to transfer (for the allocation form)."""
    return NgoAdmin(db, staff).eligible_requests(ngo_id)


@router.get("/funding-sources/available")
def available_funding_sources(search: str | None = Query(default=None, max_length=100),
                              page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=100),
                              staff=Depends(staff_only), db: Session = Depends(get_db)):
    """Verified donor contributions with an available balance, for the 'Select funding sources' step."""
    from controller.funding import FundingService

    return FundingService(db).available_sources(search, page, page_size)


@router.post("/ngo-requests/{request_id}/allocation/cancel")
def cancel_ngo_allocation(request_id: int, body: ReasonIn, staff=Depends(staff_only), db: Session = Depends(get_db)):
    """Release the allocation's undisbursed reservations back to the donor contributions."""
    return NgoAdmin(db, staff).cancel_allocation(request_id, body.reason)


@router.get("/ngo-requests/{request_id}")
def get_ngo_request(request_id: int, staff=Depends(staff_only), db: Session = Depends(get_db)):
    return NgoAdmin(db, staff).get_request(request_id)


@router.post("/ngo-requests/{request_id}/review")
def review_ngo_request(request_id: int, background_tasks: BackgroundTasks,
                       staff=Depends(staff_only), db: Session = Depends(get_db)):
    row, notes = NgoAdmin(db, staff).start_review(request_id)
    _after(background_tasks, db, notes)
    return row


@router.post("/ngo-requests/{request_id}/request-info")
def request_info(request_id: int, body: MessageIn, background_tasks: BackgroundTasks,
                 staff=Depends(staff_only), db: Session = Depends(get_db)):
    row, notes = NgoAdmin(db, staff).request_info(request_id, body.message)
    _after(background_tasks, db, notes)
    return row


@router.post("/ngo-requests/{request_id}/approve")
def approve_ngo_request(request_id: int, body: ApproveIn, background_tasks: BackgroundTasks,
                        staff=Depends(staff_only), db: Session = Depends(get_db)):
    row, notes = NgoAdmin(db, staff).approve(request_id, body.amount, [s.model_dump() for s in body.sources])
    _after(background_tasks, db, notes)
    return row


@router.post("/ngo-requests/{request_id}/reject")
def reject_ngo_request(request_id: int, body: ReasonIn, background_tasks: BackgroundTasks,
                       staff=Depends(staff_only), db: Session = Depends(get_db)):
    row, notes = NgoAdmin(db, staff).reject(request_id, body.reason)
    _after(background_tasks, db, notes)
    return row


@router.get("/ngo-expenditures")
def list_ngo_expenditures(status: Literal["pending", "verified", "rejected"] | None = None,
                          ngo_id: int | None = Query(default=None, gt=0),
                          page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                          staff=Depends(staff_only), db: Session = Depends(get_db)):
    return NgoAdmin(db, staff).list_expenditures(status, ngo_id, page, page_size)


@router.post("/ngo-expenditures/{expenditure_id}/verify")
def verify_ngo_expenditure(expenditure_id: int, body: VerifyIn, background_tasks: BackgroundTasks,
                           staff=Depends(staff_only), db: Session = Depends(get_db)):
    row, notes = NgoAdmin(db, staff).verify_expenditure(expenditure_id, body.action, body.reason)
    _after(background_tasks, db, notes)
    return row

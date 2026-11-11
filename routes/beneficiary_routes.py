"""Householder / shopkeeper portal, NGO beneficiary desk, PDMA oversight and the PDMA / NGO funds ledgers."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.database import get_db
from config.outbox import deliver_due
from config.pdf_reports import ledger_pdf
from controller.authentication import AuthFlowError
from controller.beneficiaries import (
    BeneficiaryOversight,
    BeneficiaryPortal,
    NgoBeneficiaryDesk,
    ngo_ledger,
    pdma_ledger,
)
from controller.donation import push_notification
from middlewares.role_middleware import PDMA_STAFF, require_roles, role_name
from model.beneficiary_model import BFR_STATUSES, PURPOSES
from model.donation_model import Ngo

router = APIRouter(tags=["Beneficiaries and ledgers"])
beneficiary = require_roles("HOUSEHOLDER", "SHOPKEEPER")
only_ngo = require_roles("NGO")
staff_only = require_roles(*PDMA_STAFF)
Amount = Field(gt=0, le=Decimal("999999999.99"), max_digits=14, decimal_places=2)
Range = Field(default=None, ge=0, le=Decimal("999999999.99"), max_digits=14, decimal_places=2)
Status = Literal[BFR_STATUSES]
Kind = Literal["householder", "shopkeeper"]


def _clean(v):
    if v is None:
        return v
    v = " ".join(v.split())
    if not v:
        raise ValueError("This field is required.")
    return v


class ProfileIn(BaseModel):
    phone: str | None = Field(default=None, pattern=r"^\+?[\d\s()-]{7,20}$")
    state: str | None = Field(default=None, min_length=1, max_length=100)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    postal_code: str | None = Field(default=None, min_length=1, max_length=20)
    address1: str | None = Field(default=None, min_length=1, max_length=255)
    address2: str | None = Field(default=None, max_length=255)
    adults: int | None = Field(default=None, ge=1, le=100)
    children: int | None = Field(default=None, ge=0, le=100)
    males: int | None = Field(default=None, ge=0, le=200)
    females: int | None = Field(default=None, ge=0, le=200)
    income_min: Decimal | None = Range
    income_max: Decimal | None = Range
    shop_name: str | None = Field(default=None, min_length=1, max_length=200)
    business_category: str | None = Field(default=None, min_length=1, max_length=100)
    revenue_min: Decimal | None = Range
    revenue_max: Decimal | None = Range


class RequestIn(BaseModel):
    ngo_id: int = Field(gt=0)
    purpose: Literal[PURPOSES]
    description: str = Field(min_length=10, max_length=2000)
    amount: Decimal = Amount
    _d = field_validator("description")(classmethod(lambda cls, v: _clean(v)))


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    _r = field_validator("reason")(classmethod(lambda cls, v: _clean(v)))


class PayIn(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, le=Decimal("999999999.99"), max_digits=14, decimal_places=2)
    payment_method: Literal["BANK_TRANSFER", "CHEQUE", "CASH", "MOBILE_WALLET", "OTHER"]
    payment_reference: str | None = Field(default=None, max_length=100)


def _after(background_tasks, db, svc):
    for user_id, payload in svc.notes:
        background_tasks.add_task(push_notification, user_id, payload)
    background_tasks.add_task(deliver_due, db.get_bind())


def portal(db, user):
    return BeneficiaryPortal(db, user, role_name(db, user))


# Householder / shopkeeper ---------------------------------------------------------------------------
@router.get("/beneficiary/profile")
def my_profile(user=Depends(beneficiary), db: Session = Depends(get_db)):
    return portal(db, user).profile()


@router.patch("/beneficiary/profile")
def update_profile(body: ProfileIn, user=Depends(beneficiary), db: Session = Depends(get_db)):
    return portal(db, user).update_profile(body.model_dump(exclude_unset=True))


@router.get("/beneficiary/dashboard")
def my_dashboard(user=Depends(beneficiary), db: Session = Depends(get_db)):
    return portal(db, user).dashboard()


@router.get("/beneficiary/ngos")
def ngo_options(user=Depends(beneficiary), db: Session = Depends(get_db)):
    return portal(db, user).ngos()


@router.post("/beneficiary/requests", status_code=201)
def create_request(body: RequestIn, background_tasks: BackgroundTasks, user=Depends(beneficiary),
                   db: Session = Depends(get_db)):
    svc = portal(db, user)
    out = svc.create_request(body.model_dump())
    _after(background_tasks, db, svc)
    return out


@router.get("/beneficiary/requests")
def my_requests(status: Status | None = None, page: int = Query(default=1, ge=1),
                page_size: int = Query(default=20, ge=1, le=100), user=Depends(beneficiary),
                db: Session = Depends(get_db)):
    return portal(db, user).list_requests(status, page, page_size)


@router.get("/beneficiary/requests/{request_id}")
def my_request(request_id: int, user=Depends(beneficiary), db: Session = Depends(get_db)):
    return portal(db, user).detail(request_id)


@router.post("/beneficiary/requests/{request_id}/cancel")
def cancel_request(request_id: int, user=Depends(beneficiary), db: Session = Depends(get_db)):
    return portal(db, user).cancel(request_id)


@router.post("/beneficiary/requests/{request_id}/confirm-receipt")
def confirm_receipt(request_id: int, background_tasks: BackgroundTasks, user=Depends(beneficiary),
                    db: Session = Depends(get_db)):
    svc = portal(db, user)
    out = svc.confirm_receipt(request_id)
    _after(background_tasks, db, svc)
    return out


# NGO desk -------------------------------------------------------------------------------------------
@router.get("/ngo/beneficiary-requests")
def ngo_requests(status: Status | None = None, type: Kind | None = None,
                 search: str | None = Query(default=None, max_length=100), page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=20, ge=1, le=100), user=Depends(only_ngo), db: Session = Depends(get_db)):
    return NgoBeneficiaryDesk(db, user).list(status, type, search, page, page_size)


@router.get("/ngo/beneficiary-requests/shortfall")
def ngo_shortfall(user=Depends(only_ngo), db: Session = Depends(get_db)):
    return NgoBeneficiaryDesk(db, user).shortfall()


@router.get("/ngo/beneficiary-requests/{request_id}")
def ngo_request(request_id: int, user=Depends(only_ngo), db: Session = Depends(get_db)):
    return NgoBeneficiaryDesk(db, user).detail(request_id)


@router.post("/ngo/beneficiary-requests/{request_id}/hold")
def ngo_hold(request_id: int, body: ReasonIn, background_tasks: BackgroundTasks, user=Depends(only_ngo),
             db: Session = Depends(get_db)):
    svc = NgoBeneficiaryDesk(db, user)
    out = svc.hold(request_id, body.reason)
    _after(background_tasks, db, svc)
    return out


@router.post("/ngo/beneficiary-requests/{request_id}/reject")
def ngo_reject(request_id: int, body: ReasonIn, background_tasks: BackgroundTasks, user=Depends(only_ngo),
               db: Session = Depends(get_db)):
    svc = NgoBeneficiaryDesk(db, user)
    out = svc.reject(request_id, body.reason)
    _after(background_tasks, db, svc)
    return out


@router.post("/ngo/beneficiary-requests/{request_id}/pay")
def ngo_pay(request_id: int, body: PayIn, background_tasks: BackgroundTasks, user=Depends(only_ngo),
            db: Session = Depends(get_db)):
    svc = NgoBeneficiaryDesk(db, user)
    out = svc.pay(request_id, body.amount, body.payment_method, body.payment_reference)
    _after(background_tasks, db, svc)
    return out


# PDMA oversight -------------------------------------------------------------------------------------
@router.get("/beneficiary-requests")
def all_requests(status: Status | None = None, type: Kind | None = None, ngo_id: int | None = Query(default=None, gt=0),
                 search: str | None = Query(default=None, max_length=100), page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=20, ge=1, le=100), user=Depends(staff_only), db: Session = Depends(get_db)):
    return BeneficiaryOversight(db, user).list(status, type, ngo_id, search, page, page_size)


@router.get("/beneficiary-requests/{request_id}")
def any_request(request_id: int, user=Depends(staff_only), db: Session = Depends(get_db)):
    return BeneficiaryOversight(db, user).detail(request_id)


@router.get("/ngo-balances")
def ngo_balances(user=Depends(staff_only), db: Session = Depends(get_db)):
    return BeneficiaryOversight(db, user).ngo_balances()


# Ledgers ----------------------------------------------------------------------------------------------
def _strip(ledger):
    ledger.pop("all", None)
    return ledger


def _pdf(ledger, owner, name):
    data = ledger_pdf(ledger, owner, datetime.utcnow().isoformat())
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"', "Cache-Control": "no-store"})


def _check(date_from, date_to):
    if date_from and date_to and date_to < date_from:
        raise AuthFlowError("INVALID_DATES", "The end date must be on or after the start date.", 422)


@router.get("/ledger/pdma")
def pdma_ledger_view(date_from: date | None = None, date_to: date | None = None, page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=50, ge=1, le=200), user=Depends(staff_only),
                     db: Session = Depends(get_db)):
    _check(date_from, date_to)
    return _strip(pdma_ledger(db, date_from, date_to, page, page_size))


@router.get("/ledger/pdma.pdf")
def pdma_ledger_pdf(date_from: date | None = None, date_to: date | None = None, user=Depends(staff_only),
                    db: Session = Depends(get_db)):
    _check(date_from, date_to)
    return _pdf(pdma_ledger(db, date_from, date_to, None, None), "PDMA Punjab", "pdma-ledger")


def _own_ngo(db, user):
    ngo = db.scalar(select(Ngo).where(Ngo.user_id == user.id))
    if ngo is None:
        raise AuthFlowError("NGO_NOT_LINKED", "Your account is not linked to an NGO.", 403)
    return ngo


@router.get("/ngo/ledger")
def my_ngo_ledger(date_from: date | None = None, date_to: date | None = None, page: int = Query(default=1, ge=1),
                  page_size: int = Query(default=50, ge=1, le=200), user=Depends(only_ngo), db: Session = Depends(get_db)):
    _check(date_from, date_to)
    return _strip(ngo_ledger(db, _own_ngo(db, user), date_from, date_to, page, page_size))


@router.get("/ngo/ledger.pdf")
def my_ngo_ledger_pdf(date_from: date | None = None, date_to: date | None = None, user=Depends(only_ngo),
                      db: Session = Depends(get_db)):
    _check(date_from, date_to)
    ngo = _own_ngo(db, user)
    return _pdf(ngo_ledger(db, ngo, date_from, date_to, None, None), ngo.name, "ngo-ledger")


@router.get("/ngos/{ngo_id}/ledger")
def ngo_ledger_for_staff(ngo_id: int, date_from: date | None = None, date_to: date | None = None,
                         page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
                         user=Depends(staff_only), db: Session = Depends(get_db)):
    _check(date_from, date_to)
    ngo = db.get(Ngo, ngo_id)
    if ngo is None:
        raise AuthFlowError("NGO_NOT_FOUND", "This NGO does not exist.", 404)
    return _strip(ngo_ledger(db, ngo, date_from, date_to, page, page_size))

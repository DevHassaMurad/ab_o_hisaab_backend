from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from config.database import get_db
from controller.donation import DonationController, push_notification
from middlewares.auth_middleware import auth_middleware
from middlewares.role_middleware import PDMA_STAFF, require_roles, role_name

router = APIRouter(tags=["Donations"])

# Money: positive, at most 999,999,999,999.99 (Numeric(14, 2)).
Amount = Field(gt=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)


def _clean(v: str) -> str:
    v = " ".join((v or "").split())
    if not v:
        raise ValueError("This field is required.")
    return v


class DonationRequestIn(BaseModel):
    donor_id: int = Field(gt=0)
    type: Literal["donor", "ngo"] = "donor"
    reason: str = Field(min_length=3, max_length=500)
    amount: Decimal = Amount

    _reason = field_validator("reason")(classmethod(lambda cls, v: _clean(v)))


class DonationIn(BaseModel):
    amount: Decimal = Amount
    payment_reference: str = Field(min_length=2, max_length=100)
    payment_method: Literal["BANK_TRANSFER", "CHEQUE", "ONLINE", "CASH", "OTHER"]
    received_at: datetime
    remarks: str | None = Field(default=None, max_length=500)

    _ref = field_validator("payment_reference")(classmethod(lambda cls, v: _clean(v)))

    @field_validator("received_at")
    @classmethod
    def not_future(cls, v: datetime):
        v = v.replace(tzinfo=None)  # stored as UTC
        if (v - datetime.utcnow()).total_seconds() > 300:
            raise ValueError("The received date cannot be in the future.")
        return v


class RespondIn(BaseModel):
    action: Literal["accept", "reject"]
    amount: Decimal | None = Field(default=None, gt=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)
    payment_method: Literal["BANK_TRANSFER", "CHEQUE", "ONLINE", "CASH", "OTHER"] | None = None
    payment_reference: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def amount_when_accepting(self):
        if self.action == "accept" and self.amount is None:
            raise ValueError("Enter the amount to send.")
        return self


class VerifyIn(BaseModel):
    action: Literal["verify", "reject"]
    remarks: str | None = Field(default=None, max_length=500)


class AllocationIn(BaseModel):
    ngo_id: int = Field(gt=0)
    amount: Decimal = Amount
    allocation_date: date
    remarks: str | None = Field(default=None, max_length=500)


class NgoIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    registration_no: str | None = Field(default=None, max_length=50)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=30)

    _name = field_validator("name")(classmethod(lambda cls, v: _clean(v)))


def _role_or_403(db, user, allowed):
    role = role_name(db, user)
    if role not in allowed:
        raise HTTPException(403, "You do not have access to donation records.")
    return role


# Donors ----------------------------------------------------------------------------------------
@router.get("/donors")
def list_donors(
    search: str | None = Query(default=None, max_length=100),
    status: Literal["active", "inactive", "unverified"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _=Depends(require_roles(*PDMA_STAFF)),
    db: Session = Depends(get_db),
):
    """Registered donors with totals (PDMA staff only). Searchable, filterable and paginated."""
    return DonationController(db).list_donors(search, status, page, page_size)


@router.get("/donors/{donor_id}")
def get_donor(donor_id: int, _=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)):
    """One donor with their donation requests and donation history."""
    return DonationController(db).get_donor(donor_id)


# Donation requests -----------------------------------------------------------------------------
@router.post("/donation-requests", status_code=201)
def create_donation_request(
    body: DonationRequestIn,
    background_tasks: BackgroundTasks,
    staff=Depends(require_roles(*PDMA_STAFF)),
    db: Session = Depends(get_db),
):
    """Create a batch, email the donor and save their notification (all or nothing), then push it live."""
    ctrl = DonationController(db)
    request, donor, notification = ctrl.create_request(staff, body.model_dump())
    background_tasks.add_task(push_notification, donor.id, notification.to_dict())
    return ctrl.serialize_request(request, donor, ctrl.financials([request.id])[request.id])


@router.get("/donation-requests")
def list_donation_requests(
    donor_id: int | None = Query(default=None, gt=0),
    status: Literal["SENT", "PARTIALLY_FUNDED", "FUNDED", "CANCELLED"] | None = None,
    response: Literal["PENDING", "ACCEPTED", "REJECTED"] | None = None,
    search: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user=Depends(auth_middleware),
    db: Session = Depends(get_db),
):
    """PDMA staff see every batch; a donor sees only their own."""
    role = _role_or_403(db, user, (*PDMA_STAFF, "DONOR"))
    return DonationController(db).list_requests(user, role, donor_id, status, search, page, page_size, response)


@router.get("/donation-requests/summary")
def donation_summary(user=Depends(auth_middleware), db: Session = Depends(get_db)):
    """Totals requested / received / allocated / remaining across the batches the user may see."""
    role = _role_or_403(db, user, (*PDMA_STAFF, "DONOR"))
    return DonationController(db).summary(user, role)


@router.get("/donation-requests/{request_id}")
def get_donation_request(request_id: int, user=Depends(auth_middleware), db: Session = Depends(get_db)):
    """One batch with its donations, NGO allocations and running balance."""
    role = _role_or_403(db, user, (*PDMA_STAFF, "DONOR"))
    return DonationController(db).get_request(request_id, user, role)


@router.post("/donation-requests/{request_id}/respond")
def respond_to_request(
    request_id: int,
    body: RespondIn,
    background_tasks: BackgroundTasks,
    donor=Depends(require_roles("DONOR")),
    db: Session = Depends(get_db),
):
    """Donor accepts (sending an amount, never more than is still uncovered) or rejects a request."""
    out, staff_id, notification = DonationController(db).respond(donor, request_id, body.model_dump())
    background_tasks.add_task(push_notification, staff_id, notification)
    return out


# Donations received ----------------------------------------------------------------------------
@router.post("/donation-requests/{request_id}/donations", status_code=201)
def record_donation(
    request_id: int, body: DonationIn, staff=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)
):
    """Record money received against a batch. It counts only after it is verified."""
    return DonationController(db).record_donation(staff, request_id, body.model_dump())


@router.post("/donations/{donation_id}/verify")
def verify_donation(
    donation_id: int,
    body: VerifyIn,
    background_tasks: BackgroundTasks,
    staff=Depends(require_roles(*PDMA_STAFF)),
    db: Session = Depends(get_db),
):
    """Verify or reject a recorded donation. Verified money becomes available to allocate."""
    row, donor_id, notification = DonationController(db).verify_donation(staff, donation_id, body.action, body.remarks)
    if notification and donor_id:
        background_tasks.add_task(push_notification, donor_id, notification)
    return row


# Fund allocations ------------------------------------------------------------------------------
@router.post("/donation-requests/{request_id}/allocations", status_code=201)
def allocate_funds(
    request_id: int, body: AllocationIn, staff=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)
):
    """Allocate verified batch money to an NGO. Refused if it exceeds the remaining balance."""
    return DonationController(db).allocate(staff, request_id, body.model_dump())


@router.post("/fund-allocations/{allocation_id}/cancel")
def cancel_allocation(allocation_id: int, staff=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)):
    """Cancel an allocation (kept in history); its amount returns to the batch balance."""
    return DonationController(db).cancel_allocation(staff, allocation_id)


# NGOs --------------------------------------------------------------------------------------------
@router.get("/ngos")
def list_ngos(_=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)):
    items = DonationController(db).list_ngos()
    return {"items": items, "total": len(items)}


@router.post("/ngos", status_code=201)
def create_ngo(body: NgoIn, _=Depends(require_roles(*PDMA_STAFF)), db: Session = Depends(get_db)):
    return DonationController(db).create_ngo(body.model_dump())

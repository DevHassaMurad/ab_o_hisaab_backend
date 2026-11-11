"""Individual donor ledger and receipt vouchers (donor only; ownership checked on the server)."""
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from config.database import get_db
from config.pdf_reports import receipt_voucher_pdf
from controller.donor_ledger import DonorLedger
from middlewares.role_middleware import require_roles

router = APIRouter(prefix="/donor/ledger", tags=["Donor ledger"])
only_donor = require_roles("DONOR")


@router.get("")
def ledger(
    batch_no: str | None = Query(default=None, max_length=40),
    date_from: date | None = None, date_to: date | None = None,
    status: Literal["pending", "verified", "rejected"] | None = None,
    allocation_status: Literal["not_allocated", "reserved", "partially_disbursed", "disbursed"] | None = None,
    ngo: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=10, ge=1, le=50),
    donor=Depends(only_donor), db: Session = Depends(get_db),
):
    """The donor's own donations grouped by batch, with reserved / disbursed / available per entry and batch."""
    return DonorLedger(db, donor).ledger(batch_no, date_from, date_to, status, allocation_status, ngo, page, page_size)


@router.get("/summary")
def ledger_summary(donor=Depends(only_donor), db: Session = Depends(get_db)):
    return DonorLedger(db, donor).summary()


@router.get("/{donation_id}")
def ledger_entry(donation_id: int, donor=Depends(only_donor), db: Session = Depends(get_db)):
    """One donation with payment details and its NGO utilisation (this donor's share only)."""
    return DonorLedger(db, donor).detail(donation_id)


@router.get("/{donation_id}/voucher.pdf")
def receipt_voucher(donation_id: int, donor=Depends(only_donor), db: Session = Depends(get_db)):
    """DONATION RECEIPT VOUCHER (A4). Pending / rejected payments are clearly labelled, never as confirmed."""
    v = DonorLedger(db, donor).voucher_data(donation_id)
    pdf = receipt_voucher_pdf(v, datetime.utcnow().isoformat())
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="receipt-{v["receipt_no"]}.pdf"', "Cache-Control": "no-store"})

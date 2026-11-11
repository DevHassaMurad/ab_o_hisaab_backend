"""Individual donor reports (donor only). Every endpoint is scoped to the signed-in donor on the server."""
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from config.database import get_db
from config.pdf_reports import batch_pdf, daily_pdf, monthly_pdf
from controller.donor_reports import DonorReports
from middlewares.role_middleware import require_roles

router = APIRouter(prefix="/donor/reports", tags=["Donor reports"])
only_donor = require_roles("DONOR")


def _pdf(data: bytes, name: str):
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-store",  # personal financial data
    })


@router.get("/summary")
def summary(donor=Depends(only_donor), db: Session = Depends(get_db)):
    return DonorReports(db, donor).summary()


@router.get("/utilization")
def utilization(donor=Depends(only_donor), db: Session = Depends(get_db)):
    """'Where your money went': each allocation / transfer funded by this donor's contributions (own share only)."""
    return DonorReports(db, donor).utilization()


@router.get("/daily")
def daily(day: date = Query(alias="date"), donor=Depends(only_donor), db: Session = Depends(get_db)):
    return DonorReports(db, donor).daily(day)


@router.get("/monthly")
def monthly(year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12),
            donor=Depends(only_donor), db: Session = Depends(get_db)):
    return DonorReports(db, donor).monthly(year, month)


@router.get("/batches")
def batches(donor=Depends(only_donor), db: Session = Depends(get_db)):
    return DonorReports(db, donor).batches()


@router.get("/batches/{batch_no}")
def batch(batch_no: str, donor=Depends(only_donor), db: Session = Depends(get_db)):
    return DonorReports(db, donor).batch(batch_no)


@router.get("/daily.pdf")
def daily_report_pdf(day: date = Query(alias="date"), donor=Depends(only_donor), db: Session = Depends(get_db)):
    r = DonorReports(db, donor)
    report = r.daily(day)
    return _pdf(daily_pdf(report, r.report_reference(), r.generated_at()), f"donor-daily-{day.isoformat()}.pdf")


@router.get("/monthly.pdf")
def monthly_report_pdf(year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12),
                       donor=Depends(only_donor), db: Session = Depends(get_db)):
    r = DonorReports(db, donor)
    report = r.monthly(year, month)
    return _pdf(monthly_pdf(report, r.report_reference(), r.generated_at()), f"donor-monthly-{year}-{month:02d}.pdf")


@router.get("/batches/{batch_no}/report.pdf")
def batch_report_pdf(batch_no: str, donor=Depends(only_donor), db: Session = Depends(get_db)):
    r = DonorReports(db, donor)
    report = r.batch(batch_no)
    return _pdf(batch_pdf(report, r.report_reference(), r.generated_at()), f"donor-batch-{batch_no}.pdf")

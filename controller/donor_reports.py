"""Individual donor reports. Every query is scoped to the signed-in donor's own contributions.

Terms (built on existing records; nothing is duplicated):
- Contribution: a donor_donations row (DD-IN-..). "Donated" excludes rejected payments; "verified" is verified only.
- Batch: the donation request (DT-DONOR-..) a contribution answers, or the contribution itself when it
  answers no request. A batch can hold several contributions and several allocations.
- Allocation: a PDMA disbursement (DD-OUT-..) funded by the donor's verified contribution
  (status pending / sent / received). NGO names are shown; other recipients stay anonymous.
- Spent: verified ngo_expenditures against those allocations. When no expenditure has been reported,
  spent is None ("Not available yet") — never assumed to be zero.
Dates are grouped in Pakistan time (UTC+5).
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.authentication import AuthFlowError
from controller.donation import DonationController, iso, money
from model.donation_model import DonationRequest, Ngo
from model.fund_model import COUNTED_DISBURSEMENT, DonationDisbursement, DonorDonation, NgoExpenditure
from model.user_model import User

PKT = timedelta(hours=5)
ZERO = Decimal("0.00")
RECIPIENT_LABEL = {"ngo": "NGO", "shopkeeper": "Shopkeeper", "householder": "Householder"}


def local_date(dt: datetime | None) -> date | None:
    return (dt + PKT).date() if dt else None


def opt_money(v):
    return None if v is None else money(v)


class DonorReports:
    def __init__(self, db: Session, donor: User):
        self.db = db
        self.donor = donor

    # Data ---------------------------------------------------------------------------------------
    def _contributions(self, start_utc=None, end_utc=None):
        q = (
            select(DonorDonation, DonationRequest.document_no, DonationRequest.reason)
            .outerjoin(DonationRequest, DonationRequest.id == DonorDonation.request_id)
            .where(DonorDonation.donor_id == self.donor.id)
        )
        if start_utc:
            q = q.where(DonorDonation.created_at >= start_utc)
        if end_utc:
            q = q.where(DonorDonation.created_at < end_utc)
        return self.db.execute(q.order_by(DonorDonation.created_at, DonorDonation.id)).all()

    @staticmethod
    def batch_no(d: DonorDonation, request_no):
        return request_no or d.document_no

    def _allocations(self, donation_ids):
        """{donation_id: [DonationDisbursement]} for counted allocations of these contributions."""
        out = defaultdict(list)
        if donation_ids:
            for x in self.db.scalars(
                select(DonationDisbursement)
                .where(DonationDisbursement.source_batch_id.in_(donation_ids),
                       DonationDisbursement.status.in_(COUNTED_DISBURSEMENT))
                .order_by(DonationDisbursement.created_at, DonationDisbursement.id)
            ):
                out[x.source_batch_id].append(x)
        return out

    def _expenditures(self, disbursement_ids):
        """{disbursement_id: [NgoExpenditure]} (all statuses; only verified ones count as spent)."""
        out = defaultdict(list)
        if disbursement_ids:
            for e in self.db.scalars(
                select(NgoExpenditure).where(NgoExpenditure.disbursement_id.in_(disbursement_ids))
                .order_by(NgoExpenditure.expense_date, NgoExpenditure.id)
            ):
                out[e.disbursement_id].append(e)
        return out

    def _metrics(self, rows):
        """Totals for a set of (contribution, request_no, reason) rows."""
        donations = [d for d, _, _ in rows]
        allocs = self._allocations([d.id for d in donations if d.payment_status == "verified"])
        all_allocs = [x for xs in allocs.values() for x in xs]
        exps = self._expenditures([x.id for x in all_allocs])
        verified_exps = [e for es in exps.values() for e in es if e.verification_status == "verified"]
        donated = sum((Decimal(d.amount) for d in donations if d.payment_status != "rejected"), ZERO)
        verified = sum((Decimal(d.amount) for d in donations if d.payment_status == "verified"), ZERO)
        allocated = sum((Decimal(x.amount) for x in all_allocs), ZERO)
        spent = sum((Decimal(e.amount) for e in verified_exps), ZERO) if verified_exps else None
        return {
            "total_donated": money(donated),
            "total_verified": money(verified),
            "total_allocated": money(allocated),
            "total_spent": opt_money(spent),
            "spent_available": spent is not None,
            "remaining_balance": money(verified - allocated),  # verified money not yet allocated
            "unspent_allocated": opt_money(None if spent is None else allocated - spent),
            "contributions_count": len(donations),
            "batches_count": len({self.batch_no(d, rn) for d, rn, _ in rows}),
        }, allocs, exps

    def _contribution_row(self, d, request_no, allocs, exps):
        mine = allocs.get(d.id, [])
        allocated = sum((Decimal(x.amount) for x in mine), ZERO)
        verified_exps = [e for x in mine for e in exps.get(x.id, []) if e.verification_status == "verified"]
        spent = sum((Decimal(e.amount) for e in verified_exps), ZERO) if verified_exps else None
        verified = d.payment_status == "verified"
        return {
            "date": local_date(d.created_at).isoformat(),
            "batch_no": self.batch_no(d, request_no),
            "document_no": d.document_no,
            "amount": money(d.amount),
            "payment_status": d.payment_status,
            "allocated_amount": money(allocated),
            "spent_amount": opt_money(spent),
            "remaining_balance": money((Decimal(d.amount) - allocated) if verified else ZERO),
        }

    # Reports --------------------------------------------------------------------------------------
    def summary(self):
        metrics, _, _ = self._metrics(self._contributions())
        return {"donor": self.donor.name, **metrics}

    @staticmethod
    def _utc_range(first: date, last: date):
        start = datetime.combine(first, datetime.min.time()) - PKT
        end = datetime.combine(last + timedelta(days=1), datetime.min.time()) - PKT
        return start, end

    def daily(self, day: date):
        rows = self._contributions(*self._utc_range(day, day))
        metrics, allocs, exps = self._metrics(rows)
        return {
            "donor": self.donor.name,
            "period": {"type": "daily", "date": day.isoformat(), "label": day.strftime("%d %B %Y")},
            "summary": metrics,
            "items": [self._contribution_row(d, rn, allocs, exps) for d, rn, _ in rows],
        }

    def monthly(self, year: int, month: int):
        first = date(year, month, 1)
        last = date(year, month, monthrange(year, month)[1])
        rows = self._contributions(*self._utc_range(first, last))
        metrics, allocs, exps = self._metrics(rows)
        days = defaultdict(lambda: {"count": 0, "amount": ZERO, "verified": ZERO})
        batches = defaultdict(lambda: {"contributions": 0, "amount": ZERO, "verified": ZERO, "allocated": ZERO,
                                       "spent": None, "first_date": None})
        for d, rn, _ in rows:
            day = local_date(d.created_at).isoformat()
            if d.payment_status != "rejected":
                days[day]["count"] += 1
                days[day]["amount"] += Decimal(d.amount)
            if d.payment_status == "verified":
                days[day]["verified"] += Decimal(d.amount)
            b = batches[self.batch_no(d, rn)]
            b["contributions"] += 1
            b["first_date"] = b["first_date"] or day
            if d.payment_status != "rejected":
                b["amount"] += Decimal(d.amount)
            if d.payment_status == "verified":
                b["verified"] += Decimal(d.amount)
                for x in allocs.get(d.id, []):
                    b["allocated"] += Decimal(x.amount)
                    ver = [e for e in exps.get(x.id, []) if e.verification_status == "verified"]
                    if ver:
                        b["spent"] = (b["spent"] or ZERO) + sum((Decimal(e.amount) for e in ver), ZERO)
        return {
            "donor": self.donor.name,
            "period": {"type": "monthly", "year": year, "month": month, "label": first.strftime("%B %Y")},
            "summary": {**metrics, "transactions_count": metrics["contributions_count"]},
            "daily": [{"date": k, "count": v["count"], "amount": money(v["amount"]), "verified": money(v["verified"])}
                      for k, v in sorted(days.items())],
            "batches": [
                {"batch_no": k, "first_date": v["first_date"], "contributions": v["contributions"],
                 "amount": money(v["amount"]), "verified": money(v["verified"]), "allocated": money(v["allocated"]),
                 "spent": opt_money(v["spent"]), "remaining": money(v["verified"] - v["allocated"])}
                for k, v in sorted(batches.items(), key=lambda kv: kv[1]["first_date"] or "")
            ],
        }

    def batches(self):
        rows = self._contributions()
        groups = defaultdict(list)
        for r in rows:
            groups[self.batch_no(r[0], r[1])].append(r)
        out = []
        for batch_no, items in groups.items():
            metrics, _, _ = self._metrics(items)
            out.append({
                "batch_no": batch_no,
                "reason": items[0][2],
                "first_date": local_date(items[0][0].created_at).isoformat(),
                "contributions": len(items),
                "total_donated": metrics["total_donated"],
                "total_verified": metrics["total_verified"],
                "total_allocated": metrics["total_allocated"],
                "total_spent": metrics["total_spent"],
                "remaining_balance": metrics["remaining_balance"],
            })
        out.sort(key=lambda b: b["first_date"], reverse=True)
        return {"items": out, "total": len(out)}

    def batch(self, batch_no: str):
        """One batch of the signed-in donor. Unknown or someone else's batch -> 404 (no existence leak)."""
        rows = [r for r in self._contributions() if self.batch_no(r[0], r[1]) == batch_no]
        request = None
        if batch_no.startswith("DT-"):
            request = self.db.scalar(
                select(DonationRequest).where(DonationRequest.document_no == batch_no,
                                              DonationRequest.donor_id == self.donor.id)
            )
        if not rows and request is None:
            raise AuthFlowError("BATCH_NOT_FOUND", "This batch does not exist.", 404)
        metrics, allocs, exps = self._metrics(rows)
        ngo_ids = {x.recipient_id for xs in allocs.values() for x in xs if x.recipient_type == "ngo"}
        ngo_names = dict(self.db.execute(select(Ngo.id, Ngo.name).where(Ngo.id.in_(ngo_ids))).all()) if ngo_ids else {}
        allocations, expenditures = [], []
        for d, _, _ in rows:
            for x in allocs.get(d.id, []):
                allocations.append({
                    "reference": x.document_no,
                    "date": local_date(x.sent_at or x.created_at).isoformat(),
                    "amount": money(x.amount),
                    "purpose": x.remarks,
                    "recipient_type": RECIPIENT_LABEL[x.recipient_type],
                    "ngo_name": ngo_names.get(x.recipient_id) if x.recipient_type == "ngo" else None,
                    "status": x.status,
                    "source_document_no": d.document_no,
                })
                for e in exps.get(x.id, []):
                    expenditures.append({
                        "reference": e.reference,
                        "date": e.expense_date.isoformat(),
                        "purpose": e.purpose,
                        "amount": money(e.amount),
                        "evidence_reference": e.evidence_reference,
                        "verification_status": e.verification_status,
                        "allocation_reference": x.document_no,
                    })
        return {
            "donor": self.donor.name,
            "batch_no": batch_no,
            "request": {"document_no": request.document_no, "reason": request.reason,
                        "requested_amount": money(request.requested_amount),
                        "donor_response": request.donor_response} if request else None,
            "summary": metrics,
            "donations": [{"batch_no": batch_no, "date": local_date(d.created_at).isoformat(),
                           "document_no": d.document_no, "amount": money(d.amount),
                           "verification_status": d.payment_status} for d, _, _ in rows],
            "allocations": allocations,
            "expenditures": expenditures,
            "expenditure_available": bool(expenditures),
        }

    def report_reference(self):
        """Unique reference printed on each generated PDF (RPT-26-00001)."""
        ref = DonationController(self.db).next_document_no_for("RPT")
        self.db.commit()
        return ref

    def generated_at(self):
        return iso(datetime.utcnow())

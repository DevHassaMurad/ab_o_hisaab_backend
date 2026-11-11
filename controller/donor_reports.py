"""Individual donor reports. Every query is scoped to the signed-in donor's own contributions.

Terms (built on existing records; nothing is duplicated):
- Contribution: a donor_donations row (DD-IN-..). "Donated" excludes rejected payments; "verified" is verified only.
- Batch: the donation request (DT-DONOR-..) a contribution answers, or the contribution itself when it answers none.
- Money leaves a contribution in two ways:
  * a direct PDMA transfer from it (DD-OUT with source_batch_id) — allocated and disbursed at once;
  * an NGO allocation it funds (allocation_funding_sources): reserved at approval, disbursed as transfers happen
    (funding_disbursements), released if cancelled.
- Allocated = direct transfers + (reserved + disbursed) from allocations. Disbursed = money actually transferred.
  Remaining (unallocated) = verified - allocated.
- Spent: verified NGO expenditure, attributed to this donor in proportion to their share of each transfer.
  When nothing has been reported, spent is None ("Not available yet") — never assumed to be zero.
- When several donors fund one allocation, a donor only ever sees their own share — never other donors.
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
from model.fund_model import (
    COUNTED_DISBURSEMENT,
    AllocationFundingSource,
    DonationDisbursement,
    DonorDonation,
    FundingDisbursement,
    NgoAllocation,
    NgoDonationRequest,
    NgoExpenditure,
)
from model.user_model import User

PKT = timedelta(hours=5)
ZERO = Decimal("0.00")
RECIPIENT_LABEL = {"ngo": "NGO", "shopkeeper": "Shopkeeper", "householder": "Householder"}


def local_date(dt: datetime | None) -> date | None:
    return (dt + PKT).date() if dt else None


def opt_money(v):
    return None if v is None else money(v)


class Trace:
    """Everything that happened to a set of contributions, with per-contribution figures."""

    def __init__(self, db, donation_ids):
        self.db = db
        ids = list(donation_ids)
        self.direct = defaultdict(list)       # donation_id -> [DonationDisbursement]
        self.sources = defaultdict(list)      # donation_id -> [AllocationFundingSource]
        self.splits = defaultdict(list)       # funding_source_id -> [(FundingDisbursement, DonationDisbursement)]
        self.expenditures = defaultdict(list)  # disbursement_id -> [NgoExpenditure]
        if not ids:
            self.allocations, self.requests, self.ngo_names = {}, {}, {}
            return
        for x in db.scalars(select(DonationDisbursement).where(
                DonationDisbursement.source_batch_id.in_(ids), DonationDisbursement.status.in_(COUNTED_DISBURSEMENT))
                .order_by(DonationDisbursement.created_at, DonationDisbursement.id)):
            self.direct[x.source_batch_id].append(x)
        for s in db.scalars(select(AllocationFundingSource).where(AllocationFundingSource.donor_donation_id.in_(ids))
                            .order_by(AllocationFundingSource.id)):
            self.sources[s.donor_donation_id].append(s)
        src_ids = [s.id for ss in self.sources.values() for s in ss]
        if src_ids:
            for split, x in db.execute(
                select(FundingDisbursement, DonationDisbursement)
                .join(DonationDisbursement, DonationDisbursement.id == FundingDisbursement.disbursement_id)
                .where(FundingDisbursement.funding_source_id.in_(src_ids), FundingDisbursement.status == "active")
                .order_by(DonationDisbursement.sent_at, FundingDisbursement.id)
            ):
                self.splits[split.funding_source_id].append((split, x))
        disb_ids = {x.id for xs in self.direct.values() for x in xs} | \
                   {x.id for ps in self.splits.values() for _, x in ps}
        if disb_ids:
            for e in db.scalars(select(NgoExpenditure).where(NgoExpenditure.disbursement_id.in_(disb_ids))
                                .order_by(NgoExpenditure.expense_date, NgoExpenditure.id)):
                self.expenditures[e.disbursement_id].append(e)
        alloc_ids = {s.allocation_id for ss in self.sources.values() for s in ss}
        self.allocations = {a.id: a for a in db.scalars(select(NgoAllocation).where(NgoAllocation.id.in_(alloc_ids)))} \
            if alloc_ids else {}
        req_ids = {a.ngo_request_id for a in self.allocations.values()} | \
                  {x.ngo_request_id for xs in self.direct.values() for x in xs if x.ngo_request_id}
        self.requests = dict(db.execute(select(NgoDonationRequest.id, NgoDonationRequest.document_no)
                                        .where(NgoDonationRequest.id.in_(req_ids))).all()) if req_ids else {}
        ngo_ids = {a.ngo_id for a in self.allocations.values()} | \
                  {x.recipient_id for xs in self.direct.values() for x in xs if x.recipient_type == "ngo"}
        self.ngo_names = dict(db.execute(select(Ngo.id, Ngo.name).where(Ngo.id.in_(ngo_ids))).all()) if ngo_ids else {}

    @staticmethod
    def _verified_spend(exps):
        ver = [e for e in exps if e.verification_status == "verified"]
        return sum((Decimal(e.amount) for e in ver), ZERO) if ver else None

    def figures(self, d: DonorDonation):
        """{allocated, disbursed, reserved, spent|None} for one contribution (only verified money moves)."""
        allocated = disbursed = reserved = ZERO
        spent = None
        if d.payment_status == "verified":
            for x in self.direct[d.id]:
                allocated += Decimal(x.amount)
                disbursed += Decimal(x.amount)
                s = self._verified_spend(self.expenditures[x.id])
                if s is not None:
                    spent = (spent or ZERO) + s
            for src in self.sources[d.id]:
                active = Decimal(src.allocated_amount) - Decimal(src.released_amount)
                allocated += active
                disbursed += Decimal(src.disbursed_amount)
                reserved += active - Decimal(src.disbursed_amount)
                for split, x in self.splits[src.id]:
                    s = self._verified_spend(self.expenditures[x.id])
                    if s is not None:  # this donor's share of what the NGO spent from that transfer
                        spent = (spent or ZERO) + (s * Decimal(split.amount) / Decimal(x.amount)).quantize(Decimal("0.01"))
        return {"allocated": allocated, "disbursed": disbursed, "reserved": reserved, "spent": spent}

    def utilization(self, d: DonorDonation, batch_no: str):
        """'Where your money went' rows for one contribution: one per direct transfer and one per allocation."""
        rows = []
        for x in self.direct[d.id]:
            rows.append({
                "ngo_name": self.ngo_names.get(x.recipient_id) if x.recipient_type == "ngo" else None,
                "recipient_type": RECIPIENT_LABEL[x.recipient_type],
                "ngo_request_no": self.requests.get(x.ngo_request_id),
                "allocation_document_no": x.document_no,
                "batch_no": batch_no,
                "contribution_document_no": d.document_no,
                "allocated_amount": money(x.amount),
                "disbursed_amount": money(x.amount),
                "disbursement_date": iso(x.sent_at),
                "transfer_status": "disbursed",
                "receipt_status": "confirmed" if x.receipt_confirmed_at else ("not_applicable" if x.recipient_type != "ngo" else "pending"),
            })
        for src in self.sources[d.id]:
            a = self.allocations[src.allocation_id]
            paid = self.splits[src.id]
            confirmed = [x for _, x in paid if x.receipt_confirmed_at]
            if not paid:
                receipt = "not_transferred"
            elif len(confirmed) == len(paid):
                receipt = "confirmed"
            else:
                receipt = "partially_confirmed" if confirmed else "pending"
            rows.append({
                "ngo_name": self.ngo_names.get(a.ngo_id),
                "recipient_type": "NGO",
                "ngo_request_no": self.requests.get(a.ngo_request_id),
                "allocation_document_no": a.document_no,
                "batch_no": batch_no,
                "contribution_document_no": d.document_no,
                "allocated_amount": money(Decimal(src.allocated_amount) - Decimal(src.released_amount)),
                "disbursed_amount": money(src.disbursed_amount),
                "disbursement_date": iso(paid[-1][1].sent_at) if paid else None,
                "transfer_status": src.funding_status,
                "receipt_status": receipt,
                "transfers": [{"reference": x.document_no, "amount": money(split.amount), "date": iso(x.sent_at),
                               "receipt_confirmed": bool(x.receipt_confirmed_at)} for split, x in paid],
            })
        return rows


class DonorReports:
    def __init__(self, db: Session, donor: User):
        self.db = db
        self.donor = donor

    # Data ---------------------------------------------------------------------------------------------
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

    def _metrics(self, rows):
        trace = Trace(self.db, [d.id for d, _, _ in rows])
        donated = verified = allocated = disbursed = reserved = ZERO
        spent = None
        for d, _, _ in rows:
            if d.payment_status != "rejected":
                donated += Decimal(d.amount)
            if d.payment_status == "verified":
                verified += Decimal(d.amount)
            f = trace.figures(d)
            allocated += f["allocated"]
            disbursed += f["disbursed"]
            reserved += f["reserved"]
            if f["spent"] is not None:
                spent = (spent or ZERO) + f["spent"]
        return {
            "total_donated": money(donated),
            "total_verified": money(verified),
            "total_allocated": money(allocated),
            "total_reserved": money(reserved),
            "total_disbursed": money(disbursed),
            "total_spent": opt_money(spent),
            "spent_available": spent is not None,
            "remaining_balance": money(verified - allocated),  # verified money not yet allocated
            "unspent_allocated": opt_money(None if spent is None else disbursed - spent),
            "contributions_count": len(rows),
            "batches_count": len({self.batch_no(d, rn) for d, rn, _ in rows}),
        }, trace

    def _contribution_row(self, d, request_no, trace):
        f = trace.figures(d)
        verified = d.payment_status == "verified"
        return {
            "date": local_date(d.created_at).isoformat(),
            "batch_no": self.batch_no(d, request_no),
            "document_no": d.document_no,
            "amount": money(d.amount),
            "payment_status": d.payment_status,
            "allocated_amount": money(f["allocated"]),
            "reserved_amount": money(f["reserved"]),
            "disbursed_amount": money(f["disbursed"]),
            "spent_amount": opt_money(f["spent"]),
            "remaining_balance": money((Decimal(d.amount) - f["allocated"]) if verified else ZERO),
        }

    def _utilization(self, rows, trace):
        out = []
        for d, rn, _ in rows:
            out += trace.utilization(d, self.batch_no(d, rn))
        return out

    # Reports ------------------------------------------------------------------------------------------
    def summary(self):
        metrics, _ = self._metrics(self._contributions())
        # Campaign contributions are reported separately; they are never part of general donation totals.
        from controller.campaigns import Campaigns
        campaigns = Campaigns(self.db, self.donor, "DONOR").donor_summary()
        return {"donor": self.donor.name, **metrics, "campaigns": campaigns}

    def utilization(self):
        """'Where your money went' across all of this donor's contributions."""
        rows = self._contributions()
        _, trace = self._metrics(rows)
        items = self._utilization(rows, trace)
        items.sort(key=lambda r: r["disbursement_date"] or "", reverse=True)
        return {"items": items, "total": len(items)}

    @staticmethod
    def _utc_range(first: date, last: date):
        start = datetime.combine(first, datetime.min.time()) - PKT
        end = datetime.combine(last + timedelta(days=1), datetime.min.time()) - PKT
        return start, end

    def daily(self, day: date):
        rows = self._contributions(*self._utc_range(day, day))
        metrics, trace = self._metrics(rows)
        return {
            "donor": self.donor.name,
            "period": {"type": "daily", "date": day.isoformat(), "label": day.strftime("%d %B %Y")},
            "summary": metrics,
            "items": [self._contribution_row(d, rn, trace) for d, rn, _ in rows],
            "utilization": self._utilization(rows, trace),
        }

    def monthly(self, year: int, month: int):
        first = date(year, month, 1)
        last = date(year, month, monthrange(year, month)[1])
        rows = self._contributions(*self._utc_range(first, last))
        metrics, trace = self._metrics(rows)
        days = defaultdict(lambda: {"count": 0, "amount": ZERO, "verified": ZERO})
        batches = defaultdict(lambda: {"contributions": 0, "amount": ZERO, "verified": ZERO, "allocated": ZERO,
                                       "disbursed": ZERO, "spent": None, "first_date": None})
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
                f = trace.figures(d)
                b["allocated"] += f["allocated"]
                b["disbursed"] += f["disbursed"]
                if f["spent"] is not None:
                    b["spent"] = (b["spent"] or ZERO) + f["spent"]
        return {
            "donor": self.donor.name,
            "period": {"type": "monthly", "year": year, "month": month, "label": first.strftime("%B %Y")},
            "summary": {**metrics, "transactions_count": metrics["contributions_count"]},
            "daily": [{"date": k, "count": v["count"], "amount": money(v["amount"]), "verified": money(v["verified"])}
                      for k, v in sorted(days.items())],
            "batches": [
                {"batch_no": k, "first_date": v["first_date"], "contributions": v["contributions"],
                 "amount": money(v["amount"]), "verified": money(v["verified"]), "allocated": money(v["allocated"]),
                 "disbursed": money(v["disbursed"]), "spent": opt_money(v["spent"]),
                 "remaining": money(v["verified"] - v["allocated"])}
                for k, v in sorted(batches.items(), key=lambda kv: kv[1]["first_date"] or "")
            ],
            "utilization": self._utilization(rows, trace),
        }

    def batches(self):
        rows = self._contributions()
        groups = defaultdict(list)
        for r in rows:
            groups[self.batch_no(r[0], r[1])].append(r)
        out = []
        for batch_no, items in groups.items():
            metrics, _ = self._metrics(items)
            out.append({
                "batch_no": batch_no,
                "reason": items[0][2],
                "first_date": local_date(items[0][0].created_at).isoformat(),
                "contributions": len(items),
                "total_donated": metrics["total_donated"],
                "total_verified": metrics["total_verified"],
                "total_allocated": metrics["total_allocated"],
                "total_disbursed": metrics["total_disbursed"],
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
        metrics, trace = self._metrics(rows)
        allocations, expenditures = [], []
        for d, _, _ in rows:
            for x in trace.direct[d.id]:
                allocations.append({
                    "reference": x.document_no, "date": local_date(x.sent_at or x.created_at).isoformat(),
                    "amount": money(x.amount), "disbursed_amount": money(x.amount), "purpose": x.remarks,
                    "recipient_type": RECIPIENT_LABEL[x.recipient_type],
                    "ngo_name": trace.ngo_names.get(x.recipient_id) if x.recipient_type == "ngo" else None,
                    "ngo_request_no": trace.requests.get(x.ngo_request_id),
                    "status": x.status, "source_document_no": d.document_no,
                })
                for e in trace.expenditures[x.id]:
                    expenditures.append(self._expenditure_row(e, x.document_no, None))
            for src in trace.sources[d.id]:
                a = trace.allocations[src.allocation_id]
                allocations.append({
                    "reference": a.document_no, "date": local_date(a.approved_at).isoformat(),
                    "amount": money(Decimal(src.allocated_amount) - Decimal(src.released_amount)),
                    "disbursed_amount": money(src.disbursed_amount), "purpose": None, "recipient_type": "NGO",
                    "ngo_name": trace.ngo_names.get(a.ngo_id), "ngo_request_no": trace.requests.get(a.ngo_request_id),
                    "status": src.funding_status, "source_document_no": d.document_no,
                })
                for split, x in trace.splits[src.id]:
                    share = Decimal(split.amount) / Decimal(x.amount)
                    for e in trace.expenditures[x.id]:
                        expenditures.append(self._expenditure_row(e, x.document_no, share))
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
            "utilization": self._utilization(rows, trace),
        }

    @staticmethod
    def _expenditure_row(e, allocation_reference, share):
        """An NGO expense; when the transfer was shared between donors, `amount` is this donor's share only."""
        amount = Decimal(e.amount) if share is None else (Decimal(e.amount) * share).quantize(Decimal("0.01"))
        return {
            "reference": e.reference, "date": e.expense_date.isoformat(), "purpose": e.purpose,
            "amount": money(amount), "evidence_reference": e.evidence_reference or e.supporting_document,
            "verification_status": e.verification_status, "allocation_reference": allocation_reference,
        }

    def report_reference(self):
        """Unique reference printed on each generated PDF (RPT-26-00001)."""
        ref = DonationController(self.db).next_document_no_for("RPT")
        self.db.commit()
        return ref

    def generated_at(self):
        return iso(datetime.utcnow())

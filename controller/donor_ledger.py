"""Individual donor ledger and receipt vouchers. Strictly the signed-in donor's own contributions.

Built on existing records (no duplicated money):
- entries  = donor_donations (DD-IN-..), grouped by batch (the DT-DONOR request they answer, else themselves);
- reserved / disbursed per entry come from allocation_funding_sources / funding_disbursements and direct
  transfers (controller.donor_reports.Trace) — exact recorded amounts, never proportional estimates;
- available = verified - (direct transfers + active reservations), the same rule used when PDMA allocates.
"""
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from controller.authentication import AuthFlowError
from controller.donation import DonationController, iso, money
from controller.donor_reports import Trace, local_date
from model.donation_model import DonationRequest
from model.fund_model import DonorDonation
from model.user_model import User

ZERO = Decimal("0.00")
METHOD_LABEL = {"BANK_TRANSFER": "Bank transfer", "CHEQUE": "Cheque", "ONLINE": "Online payment",
                "CASH": "Cash", "OTHER": "Other"}


class DonorLedger:
    def __init__(self, db, donor: User):
        self.db = db
        self.donor = donor

    def _rows(self):
        return self.db.execute(
            select(DonorDonation, DonationRequest.document_no)
            .outerjoin(DonationRequest, DonationRequest.id == DonorDonation.request_id)
            .where(DonorDonation.donor_id == self.donor.id)  # ownership comes from the token, never the client
            .order_by(DonorDonation.created_at, DonorDonation.id)
        ).all()

    @staticmethod
    def _allocation_status(f):
        if f["allocated"] == 0:
            return "not_allocated"
        if f["disbursed"] == 0:
            return "reserved"
        return "disbursed" if f["reserved"] == 0 else "partially_disbursed"

    def _entry(self, d, batch_no, trace):
        f = trace.figures(d)
        util = [u for u in trace.utilization(d, batch_no) if Decimal(u["allocated_amount"]) > 0
                or Decimal(u["disbursed_amount"]) > 0]
        verified = d.payment_status == "verified"
        return {
            "id": d.id,
            "batch_no": batch_no,
            "document_no": d.document_no,
            "receipt_no": d.receipt_no,
            "date": local_date(d.created_at).isoformat(),
            "donated_amount": money(d.amount),
            "verified_amount": money(d.amount if verified else ZERO),
            "payment_status": d.payment_status,
            "allocated_amount": money(f["allocated"]),
            "reserved_amount": money(f["reserved"]),
            "disbursed_amount": money(f["disbursed"]),
            "available_amount": money((Decimal(d.amount) - f["allocated"]) if verified else ZERO),
            "allocation_status": self._allocation_status(f),
            "ngos_funded": len({u["ngo_name"] for u in util if u["ngo_name"]}),
            "ngo_names": sorted({u["ngo_name"] for u in util if u["ngo_name"]}),
        }

    # Ledger --------------------------------------------------------------------------------------
    def ledger(self, batch_no=None, date_from: date | None = None, date_to: date | None = None, status=None,
               allocation_status=None, ngo=None, page=1, page_size=10):
        rows = self._rows()
        trace = Trace(self.db, [d.id for d, _ in rows])
        entries = [self._entry(d, rn or d.document_no, trace) for d, rn in rows]
        if batch_no:
            entries = [e for e in entries if batch_no.lower() in e["batch_no"].lower()]
        if date_from:
            entries = [e for e in entries if e["date"] >= date_from.isoformat()]
        if date_to:
            entries = [e for e in entries if e["date"] <= date_to.isoformat()]
        if status:
            entries = [e for e in entries if e["payment_status"] == status]
        if allocation_status:
            entries = [e for e in entries if e["allocation_status"] == allocation_status]
        if ngo:
            entries = [e for e in entries if any(ngo.lower() in n.lower() for n in e["ngo_names"])]
        groups = OrderedDict()
        for e in sorted(entries, key=lambda e: (e["date"], e["id"]), reverse=True):
            groups.setdefault(e["batch_no"], []).append(e)
        batches = []
        for bno, items in groups.items():
            dec = lambda k: sum((Decimal(i[k]) for i in items), ZERO)  # noqa: E731
            batches.append({
                "batch_no": bno, "entries": items, "entries_count": len(items),
                "first_date": min(i["date"] for i in items), "last_date": max(i["date"] for i in items),
                "total_donated": money(sum((Decimal(i["donated_amount"]) for i in items if i["payment_status"] != "rejected"), ZERO)),
                "total_verified": money(dec("verified_amount")), "total_reserved": money(dec("reserved_amount")),
                "total_disbursed": money(dec("disbursed_amount")), "available_balance": money(dec("available_amount")),
                "ngos_funded": len({n for i in items for n in i["ngo_names"]}),
            })
        total = len(batches)
        start = (page - 1) * page_size
        return {"items": batches[start:start + page_size], "total": total, "page": page, "page_size": page_size,
                "entries_total": len(entries)}

    def _own(self, donation_id):
        d = self.db.get(DonorDonation, donation_id)
        if d is None or d.donor_id != self.donor.id:
            # Same answer for "missing" and "someone else's", so record ids reveal nothing.
            raise AuthFlowError("DONATION_NOT_FOUND", "This donation does not exist.", 404)
        return d

    def _batch_no(self, d):
        if d.request_id:
            no = self.db.scalar(select(DonationRequest.document_no).where(DonationRequest.id == d.request_id))
            if no:
                return no
        return d.document_no

    def detail(self, donation_id):
        d = self._own(donation_id)
        batch_no = self._batch_no(d)
        trace = Trace(self.db, [d.id])
        entry = self._entry(d, batch_no, trace)
        utilization = [u for u in trace.utilization(d, batch_no)
                       if Decimal(u["allocated_amount"]) > 0 or Decimal(u["disbursed_amount"]) > 0]
        return {
            **entry,
            "payment_method": METHOD_LABEL.get(d.payment_method, d.payment_method) if d.payment_method else None,
            "payment_reference": d.payment_reference,
            "verification_date": iso(d.verified_at),
            # Organisation identity only — never the individual PDMA officer.
            "verified_by": "PDMA Finance" if d.verified_by else None,
            "rejection_reason": d.rejection_reason if d.payment_status == "rejected" else None,
            "utilization": utilization,
        }

    def voucher_data(self, donation_id):
        """Assign the receipt number once (row lock prevents duplicates) and return voucher values from the database."""
        d = self.db.scalar(select(DonorDonation).where(DonorDonation.id == donation_id).with_for_update()
                           .execution_options(populate_existing=True))
        if d is None or d.donor_id != self.donor.id:
            self.db.rollback()
            raise AuthFlowError("DONATION_NOT_FOUND", "This donation does not exist.", 404)
        if not d.receipt_no:
            d.receipt_no = DonationController(self.db).next_document_no_for("RCPT")
        self.db.commit()
        return {
            "receipt_no": d.receipt_no,
            "document_no": d.document_no,
            "batch_no": self._batch_no(d),
            "donor": self.donor.name,
            "date": local_date(d.created_at).isoformat(),
            "amount": money(d.amount),
            "payment_method": METHOD_LABEL.get(d.payment_method, d.payment_method) if d.payment_method else None,
            "payment_reference": d.payment_reference,
            "payment_status": d.payment_status,
            "verification_date": iso(d.verified_at),
            "rejection_reason": d.rejection_reason if d.payment_status == "rejected" else None,
        }

    def summary(self):
        rows = self._rows()
        trace = Trace(self.db, [d.id for d, _ in rows])
        entries = [self._entry(d, rn or d.document_no, trace) for d, rn in rows]
        dec = lambda k: sum((Decimal(e[k]) for e in entries), ZERO)  # noqa: E731
        return {
            "total_donated": money(sum((Decimal(e["donated_amount"]) for e in entries if e["payment_status"] != "rejected"), ZERO)),
            "total_verified": money(dec("verified_amount")),
            "total_reserved": money(dec("reserved_amount")),
            "total_disbursed": money(dec("disbursed_amount")),
            "available_balance": money(dec("available_amount")),
            "ngos_funded": len({n for e in entries for n in e["ngo_names"]}),
            "entries": len(entries),
            "batches": len({e["batch_no"] for e in entries}),
        }

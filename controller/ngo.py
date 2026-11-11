"""NGO portal and PDMA NGO-request management.

Money stages are kept separate and never inferred from each other:
  requested  -> ngo_donation_requests.requested_amount
  approved   -> ngo_donation_requests.approved_amount (approval is a decision, not money)
  transferred-> donation_disbursements to this NGO (status pending/sent/received)
  received   -> transfers the NGO has acknowledged (receipt_confirmed_at)
  invested   -> verified ngo_expenditures
  net balance = received - invested
An NGO user only ever sees its own organisation (ngos.user_id = the signed-in user).
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select

from config.fund_mail import render_notice
from config.outbox import enqueue
from controller.authentication import AuthFlowError
from controller.donation import DonationController, iso, money
from model.donation_model import Ngo
from model.fund_model import COUNTED_DISBURSEMENT, DonationDisbursement, NgoDonationRequest, NgoExpenditure
from model.notification_model import Notification
from model.role_model import Role
from model.user_model import User

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
PKT = timedelta(hours=5)
OPEN_STATUSES = ("pending", "under_review")
APPROVED_STATUSES = ("approved", "partially_approved")
EXPENSE_CATEGORIES = ("relief_goods", "food", "shelter", "health", "water_sanitation", "cash_assistance",
                      "logistics", "administration", "other")


def now_utc():
    return datetime.utcnow()


def pkr(v):
    return f"PKR {Decimal(v):,.2f}"


class NgoBase:
    def __init__(self, db):
        self.db = db

    def _doc_no(self, prefix):
        return DonationController(self.db).next_document_no_for(prefix)

    def _notify(self, user_ids, type_, title, message, related_id, related_type):
        out = []
        for uid in dict.fromkeys(u for u in user_ids if u):
            n = Notification(user_id=uid, type=type_, title=title, message=message,
                             related_id=related_id, related_type=related_type)
            self.db.add(n)
            out.append(n)
        return out

    def _staff(self):
        roles = select(Role.id).where(Role.name.in_(("ADMIN", "PDMA_OFFICER")))
        return list(self.db.scalars(select(User).where(User.role_id.in_(roles), User.is_active.is_(True))))

    def _email(self, user, heading, label, intro, rows, path, link_text, greet=None):
        if user and user.email:
            enqueue(self.db, user.email, *render_notice(heading, label, greet or user.name, intro, rows, path, link_text))

    # Shared figures ---------------------------------------------------------------------------------
    def _transfers(self, ngo_id=None, request_ids=None):
        q = select(DonationDisbursement).where(
            DonationDisbursement.recipient_type == "ngo", DonationDisbursement.status.in_(COUNTED_DISBURSEMENT))
        if ngo_id is not None:
            q = q.where(DonationDisbursement.recipient_id == ngo_id)
        if request_ids is not None:
            if not request_ids:
                return []
            q = q.where(DonationDisbursement.ngo_request_id.in_(request_ids))
        return list(self.db.scalars(q.order_by(DonationDisbursement.created_at, DonationDisbursement.id)))

    def request_row(self, r: NgoDonationRequest, transfers, ngo: Ngo | None = None):
        mine = [x for x in transfers if x.ngo_request_id == r.id]
        transferred = sum((Decimal(x.amount) for x in mine), ZERO)
        received = sum((Decimal(x.amount) for x in mine if x.receipt_confirmed_at), ZERO)
        approved = Decimal(r.approved_amount) if r.approved_amount is not None else None
        if r.status in APPROVED_STATUSES:
            if received >= approved:
                progress = "completed"
            elif received > 0:
                progress = "partially_received"
            elif transferred > 0:
                progress = "transferred"  # sent, waiting for the NGO to acknowledge receipt
            else:
                progress = "awaiting_transfer"
        else:
            progress = r.status
        return {
            "id": r.id,
            "document_no": r.document_no,
            "ngo": {"id": ngo.id, "name": ngo.name} if ngo else None,
            "subject": r.subject,
            "description": r.description,
            "requested_amount": money(r.requested_amount),
            "approved_amount": money(approved) if approved is not None else None,
            "transferred_amount": money(transferred),
            "received_amount": money(received),
            "awaiting_transfer": money(max(approved - transferred, ZERO)) if approved is not None else None,
            "status": r.status,
            "progress_status": progress,
            "supporting_reference": r.supporting_reference,
            "rejection_reason": r.rejection_reason,
            "info_request": r.info_request,
            "allocation_document_no": self._alloc_no(r.id),
            "submitted_at": iso(r.submitted_at),
            "reviewed_at": iso(r.reviewed_at),
            "approved_at": iso(r.approved_at),
            "rejected_at": iso(r.rejected_at),
        }

    def _alloc_no(self, request_id):
        from model.fund_model import NgoAllocation

        return self.db.scalar(select(NgoAllocation.document_no).where(NgoAllocation.ngo_request_id == request_id))

    def transfer_row(self, x: DonationDisbursement, request_no=None, approved=None):
        # NGOs see the PDMA allocation and transfer references only — never donor names or donation details.
        return {
            "id": x.id,
            "allocation_reference": x.document_no,
            "allocation_document_no": self._alloc_no(x.ngo_request_id) if x.ngo_request_id else None,
            "request_id": x.ngo_request_id,
            "request_document_no": request_no,
            "approved_amount": money(approved) if approved is not None else None,
            "transferred_amount": money(x.amount),
            "purpose": x.remarks,
            "payment_reference": x.payment_reference,
            "transfer_date": iso(x.sent_at or x.created_at),
            "status": x.status,
            "receipt_confirmed_at": iso(x.receipt_confirmed_at),
        }

    def expenditure_row(self, e: NgoExpenditure, allocation_ref=None, ngo_name=None):
        return {
            "id": e.id,
            "reference": e.reference,
            "allocation_id": e.disbursement_id,
            "allocation_reference": allocation_ref,
            "ngo_name": ngo_name,
            "category": e.expense_category,
            "description": e.purpose,
            "amount": money(e.amount),
            "expense_date": e.expense_date.isoformat(),
            "supporting_document": e.supporting_document or e.evidence_reference,
            "verification_status": e.verification_status,
            "verified_at": iso(e.verified_at),
        }


class NgoPortal(NgoBase):
    """Everything the signed-in NGO user may see or do — only for their own organisation."""

    def __init__(self, db, user: User):
        super().__init__(db)
        self.user = user
        self.ngo = db.scalar(select(Ngo).where(Ngo.user_id == user.id))
        if self.ngo is None or not self.ngo.is_active:
            raise AuthFlowError("NGO_PROFILE_MISSING", "No active NGO organisation is linked to this account.", 403)

    # Profile --------------------------------------------------------------------------------------
    def profile(self):
        n = self.ngo
        return {"id": n.id, "name": n.name, "registration_no": n.registration_no, "contact_email": n.contact_email,
                "contact_phone": n.contact_phone, "district_id": n.district_id, "user_email": self.user.email,
                "registered_at": iso(n.created_at)}

    # Requests ---------------------------------------------------------------------------------------
    def create_request(self, data):
        r = NgoDonationRequest(
            document_no=self._doc_no("NGO-REQ"),
            ngo_id=self.ngo.id,
            subject=" ".join(data["subject"].split()),
            description=data["description"].strip(),
            requested_amount=Decimal(str(data["amount"])).quantize(CENT),
            status="pending",
            supporting_reference=(data.get("supporting_reference") or "").strip() or None,
            submitted_by=self.user.id,
            submitted_at=now_utc(),
        )
        self.db.add(r)
        self.db.flush()
        staff = self._staff()
        notes = self._notify([s.id for s in staff], "ngo_request", f"New NGO request {r.document_no}",
                             f"{self.ngo.name} requested {pkr(r.requested_amount)}: {r.subject}", r.id, "ngo_request")
        notes += self._notify([self.user.id], "ngo_request_submitted", f"Request {r.document_no} submitted",
                              f"Your request for {pkr(r.requested_amount)} was sent to PDMA.", r.id, "ngo_request")
        for s in staff:
            self._email(s, "New NGO funding request", "NGO REQUEST", f"{self.ngo.name} has submitted a funding request.",
                        [("Reference", r.document_no), ("NGO", self.ngo.name), ("Subject", r.subject),
                         ("Requested", pkr(r.requested_amount))], f"/ngo-requests/{r.id}", "Review the request")
        self.db.commit()
        return self.request_row(r, []), [(n.user_id, n.to_dict()) for n in notes]

    def list_requests(self, status=None, search=None, page=1, page_size=20):
        q = select(NgoDonationRequest).where(NgoDonationRequest.ngo_id == self.ngo.id)
        if status:
            q = q.where(NgoDonationRequest.status == status)
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(NgoDonationRequest.document_no.ilike(like), NgoDonationRequest.subject.ilike(like)))
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = list(self.db.scalars(q.order_by(NgoDonationRequest.submitted_at.desc(), NgoDonationRequest.id.desc())
                                    .offset((page - 1) * page_size).limit(page_size)))
        transfers = self._transfers(self.ngo.id, [r.id for r in rows])
        return {"items": [self.request_row(r, transfers) for r in rows], "total": int(total or 0),
                "page": page, "page_size": page_size}

    def _own_request(self, request_id):
        r = self.db.get(NgoDonationRequest, request_id)
        if r is None or r.ngo_id != self.ngo.id:
            raise AuthFlowError("REQUEST_NOT_FOUND", "This request does not exist.", 404)
        return r

    def get_request(self, request_id):
        r = self._own_request(request_id)
        transfers = self._transfers(self.ngo.id, [r.id])
        out = self.request_row(r, transfers)
        out["transfers"] = [self.transfer_row(x, r.document_no, r.approved_amount) for x in transfers]
        return out

    # Received funds -------------------------------------------------------------------------------
    def allocations(self, status=None):
        transfers = self._transfers(self.ngo.id)
        req = {r.id: r for r in self.db.scalars(select(NgoDonationRequest).where(NgoDonationRequest.ngo_id == self.ngo.id))}
        rows = []
        for x in reversed(transfers):
            r = req.get(x.ngo_request_id)
            row = self.transfer_row(x, r.document_no if r else None, r.approved_amount if r else None)
            if status == "awaiting_receipt" and x.receipt_confirmed_at:
                continue
            if status == "received" and not x.receipt_confirmed_at:
                continue
            rows.append(row)
        return {"items": rows, "total": len(rows)}

    def acknowledge(self, allocation_id):
        x = self.db.scalar(select(DonationDisbursement).where(DonationDisbursement.id == allocation_id).with_for_update()
                           .execution_options(populate_existing=True))
        if x is None or x.recipient_type != "ngo" or x.recipient_id != self.ngo.id:
            self.db.rollback()
            raise AuthFlowError("ALLOCATION_NOT_FOUND", "This transfer does not exist.", 404)
        if x.receipt_confirmed_at:
            self.db.rollback()
            raise AuthFlowError("ALREADY_ACKNOWLEDGED", "You have already confirmed receipt of this transfer.", 409)
        if x.status not in ("sent", "pending"):
            self.db.rollback()
            raise AuthFlowError("NOT_TRANSFERRED", "This transfer cannot be acknowledged.", 409)
        now = now_utc()
        x.receipt_confirmed_at, x.receipt_confirmed_by = now, self.user.id
        x.status, x.received_at = "received", now
        staff = self._staff()
        notes = self._notify([s.id for s in staff], "ngo_receipt", f"{self.ngo.name} confirmed {x.document_no}",
                             f"Receipt of {pkr(x.amount)} confirmed.", x.id, "ngo_allocation")
        from controller.funding import FundingService

        donor_notes = FundingService(self.db).notify_ngo_receipt(x)  # each funding donor, own share only
        self.db.commit()
        notes = [(n.user_id, n.to_dict()) for n in notes]
        r = self.db.get(NgoDonationRequest, x.ngo_request_id) if x.ngo_request_id else None
        return self.transfer_row(x, r.document_no if r else None, r.approved_amount if r else None), notes + donor_notes

    # Expenditure ----------------------------------------------------------------------------------
    def create_expenditure(self, data):
        x = self.db.scalar(select(DonationDisbursement).where(DonationDisbursement.id == data["allocation_id"])
                           .with_for_update().execution_options(populate_existing=True))
        if x is None or x.recipient_type != "ngo" or x.recipient_id != self.ngo.id:
            self.db.rollback()
            raise AuthFlowError("ALLOCATION_NOT_FOUND", "Choose one of your organisation's received transfers.", 404)
        if not x.receipt_confirmed_at:
            self.db.rollback()
            raise AuthFlowError("RECEIPT_NOT_CONFIRMED", "Confirm receipt of this transfer before recording spending.", 409)
        used = self.db.scalar(select(func.coalesce(func.sum(NgoExpenditure.amount), 0)).where(
            NgoExpenditure.disbursement_id == x.id, NgoExpenditure.verification_status != "rejected")) or 0
        amount = Decimal(str(data["amount"])).quantize(CENT)
        left = Decimal(x.amount) - Decimal(used)
        if amount > left:
            self.db.rollback()
            raise AuthFlowError("EXCEEDS_ALLOCATION", f"Only {pkr(left)} of {x.document_no} is left to record.", 409)
        e = NgoExpenditure(
            disbursement_id=x.id, ngo_id=self.ngo.id, reference=self._doc_no("NGO-EXP"),
            expense_category=data["category"], purpose=data["description"].strip(), amount=amount,
            expense_date=data["expense_date"],
            supporting_document=(data.get("supporting_document") or "").strip() or None,
            verification_status="pending", recorded_by=self.user.id,
        )
        self.db.add(e)
        self.db.flush()
        notes = self._notify([s.id for s in self._staff()], "ngo_expenditure",
                             f"{self.ngo.name} recorded expenditure {e.reference}",
                             f"{pkr(amount)} against {x.document_no} awaits verification.", e.id, "ngo_expenditure")
        self.db.commit()
        return self.expenditure_row(e, x.document_no), [(n.user_id, n.to_dict()) for n in notes]

    def list_expenditures(self, status=None, page=1, page_size=20):
        q = (select(NgoExpenditure, DonationDisbursement.document_no)
             .join(DonationDisbursement, DonationDisbursement.id == NgoExpenditure.disbursement_id)
             .where(NgoExpenditure.ngo_id == self.ngo.id))
        if status:
            q = q.where(NgoExpenditure.verification_status == status)
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = self.db.execute(q.order_by(NgoExpenditure.expense_date.desc(), NgoExpenditure.id.desc())
                               .offset((page - 1) * page_size).limit(page_size)).all()
        return {"items": [self.expenditure_row(e, ref) for e, ref in rows], "total": int(total or 0),
                "page": page, "page_size": page_size}

    # Dashboard ---------------------------------------------------------------------------------------
    def dashboard(self):
        reqs = list(self.db.scalars(select(NgoDonationRequest).where(NgoDonationRequest.ngo_id == self.ngo.id)))
        transfers = self._transfers(self.ngo.id)
        exps = list(self.db.scalars(select(NgoExpenditure).where(NgoExpenditure.ngo_id == self.ngo.id)))
        pending = sum((Decimal(r.requested_amount) for r in reqs if r.status in OPEN_STATUSES), ZERO)
        approved_reqs = [r for r in reqs if r.status in APPROVED_STATUSES]
        awaiting_transfer = ZERO
        for r in approved_reqs:
            t = sum((Decimal(x.amount) for x in transfers if x.ngo_request_id == r.id), ZERO)
            awaiting_transfer += max(Decimal(r.approved_amount) - t, ZERO)
        received = sum((Decimal(x.amount) for x in transfers if x.receipt_confirmed_at), ZERO)
        awaiting_receipt = sum((Decimal(x.amount) for x in transfers if not x.receipt_confirmed_at), ZERO)
        verified = [e for e in exps if e.verification_status == "verified"]
        invested = sum((Decimal(e.amount) for e in verified), ZERO)

        monthly = defaultdict(lambda: {"received": ZERO, "invested": ZERO})
        for x in transfers:
            if x.receipt_confirmed_at:
                monthly[(x.receipt_confirmed_at + PKT).strftime("%Y-%m")]["received"] += Decimal(x.amount)
        for e in verified:
            monthly[e.expense_date.strftime("%Y-%m")]["invested"] += Decimal(e.amount)
        counts = defaultdict(int)
        for r in reqs:
            counts["approved" if r.status in APPROVED_STATUSES else r.status] += 1
        utilization = []
        for x in transfers:
            if x.receipt_confirmed_at:
                spent = sum((Decimal(e.amount) for e in verified if e.disbursement_id == x.id), ZERO)
                utilization.append({"allocation_reference": x.document_no, "received": money(x.amount),
                                    "invested": money(spent), "unspent": money(Decimal(x.amount) - spent)})
        activity = (
            [{"kind": "request", "reference": r.document_no, "amount": money(r.requested_amount), "status": r.status,
              "date": iso(r.submitted_at)} for r in reqs]
            + [{"kind": "transfer", "reference": x.document_no, "amount": money(x.amount),
                "status": "received" if x.receipt_confirmed_at else "awaiting_receipt", "date": iso(x.sent_at or x.created_at)}
               for x in transfers]
            + [{"kind": "expenditure", "reference": e.reference, "amount": money(e.amount),
                "status": e.verification_status, "date": iso(e.created_at)} for e in exps]
        )
        activity.sort(key=lambda a: a["date"] or "", reverse=True)
        return {
            "ngo": {"id": self.ngo.id, "name": self.ngo.name},
            "pending_amount": money(pending),
            "approved_awaiting_transfer": money(awaiting_transfer),
            "transferred_awaiting_receipt": money(awaiting_receipt),
            "received_amount": money(received),
            "invested_amount": money(invested),
            "net_balance": money(received - invested),
            "request_counts": {k: counts.get(k, 0) for k in ("pending", "under_review", "approved", "rejected")},
            "monthly": [{"month": k, "received": money(v["received"]), "invested": money(v["invested"])}
                        for k, v in sorted(monthly.items())],
            "utilization": utilization,
            "recent_activity": activity[:10],
        }

    # Reports ------------------------------------------------------------------------------------------
    def monthly_report(self, year, month):
        first, last = date(year, month, 1), date(year, month, monthrange(year, month)[1])
        start = datetime.combine(first, datetime.min.time()) - PKT
        end = datetime.combine(last + timedelta(days=1), datetime.min.time()) - PKT
        reqs = list(self.db.scalars(select(NgoDonationRequest).where(
            NgoDonationRequest.ngo_id == self.ngo.id, NgoDonationRequest.submitted_at >= start,
            NgoDonationRequest.submitted_at < end).order_by(NgoDonationRequest.submitted_at)))
        transfers = [x for x in self._transfers(self.ngo.id) if start <= (x.sent_at or x.created_at) < end]
        exps = list(self.db.scalars(select(NgoExpenditure).where(
            NgoExpenditure.ngo_id == self.ngo.id, NgoExpenditure.expense_date >= first,
            NgoExpenditure.expense_date <= last).order_by(NgoExpenditure.expense_date)))
        refs = dict(self.db.execute(select(DonationDisbursement.id, DonationDisbursement.document_no).where(
            DonationDisbursement.id.in_({e.disbursement_id for e in exps}))).all()) if exps else {}
        received = sum((Decimal(x.amount) for x in transfers if x.receipt_confirmed_at), ZERO)
        invested = sum((Decimal(e.amount) for e in exps if e.verification_status == "verified"), ZERO)
        return {
            "ngo": self.ngo.name,
            "period": {"year": year, "month": month, "label": first.strftime("%B %Y")},
            "summary": {
                "requested": money(sum((Decimal(r.requested_amount) for r in reqs), ZERO)),
                "approved": money(sum((Decimal(r.approved_amount or 0) for r in reqs if r.status in APPROVED_STATUSES), ZERO)),
                "transferred": money(sum((Decimal(x.amount) for x in transfers), ZERO)),
                "received": money(received), "invested": money(invested), "net": money(received - invested),
            },
            "requests": [self.request_row(r, self._transfers(self.ngo.id, [r.id])) for r in reqs],
            "transfers": [self.transfer_row(x) for x in transfers],
            "expenditures": [self.expenditure_row(e, refs.get(e.disbursement_id)) for e in exps],
        }


class NgoAdmin(NgoBase):
    """PDMA staff: review NGO requests, decide, and verify NGO expenditure."""

    def __init__(self, db, staff: User):
        super().__init__(db)
        self.staff = staff

    def registered_ngos(self, search=None):
        q = select(Ngo)
        if search:
            q = q.where(or_(Ngo.name.ilike(f"%{search.strip()}%"), Ngo.registration_no.ilike(f"%{search.strip()}%")))
        ngos = list(self.db.scalars(q.order_by(Ngo.name)))
        counts = defaultdict(lambda: defaultdict(int))
        for nid, st, n in self.db.execute(select(NgoDonationRequest.ngo_id, NgoDonationRequest.status, func.count())
                                          .group_by(NgoDonationRequest.ngo_id, NgoDonationRequest.status)):
            counts[nid][st] = n
        return {"items": [{
            "id": n.id, "name": n.name, "registration_no": n.registration_no, "contact_email": n.contact_email,
            "contact_phone": n.contact_phone, "has_portal_account": bool(n.user_id), "is_active": n.is_active,
            "registered_at": iso(n.created_at),
            "requests_open": sum(counts[n.id][s] for s in OPEN_STATUSES),
            "requests_total": sum(counts[n.id].values()),
        } for n in ngos], "total": len(ngos)}

    def list_requests(self, status=None, ngo_id=None, search=None, page=1, page_size=20):
        q = select(NgoDonationRequest, Ngo).join(Ngo, Ngo.id == NgoDonationRequest.ngo_id)
        if status:
            q = q.where(NgoDonationRequest.status == status)
        if ngo_id:
            q = q.where(NgoDonationRequest.ngo_id == ngo_id)
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(NgoDonationRequest.document_no.ilike(like), NgoDonationRequest.subject.ilike(like),
                            Ngo.name.ilike(like)))
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = self.db.execute(q.order_by(NgoDonationRequest.submitted_at.desc(), NgoDonationRequest.id.desc())
                               .offset((page - 1) * page_size).limit(page_size)).all()
        transfers = self._transfers(request_ids=[r.id for r, _ in rows])
        counts = dict(self.db.execute(select(NgoDonationRequest.status, func.count()).group_by(NgoDonationRequest.status)).all())
        return {"items": [self.request_row(r, transfers, n) for r, n in rows], "total": int(total or 0),
                "page": page, "page_size": page_size, "status_counts": counts}

    def get_request(self, request_id):
        r = self.db.get(NgoDonationRequest, request_id)
        if r is None:
            raise AuthFlowError("REQUEST_NOT_FOUND", "This request does not exist.", 404)
        ngo = self.db.get(Ngo, r.ngo_id)
        transfers = self._transfers(request_ids=[r.id])
        out = self.request_row(r, transfers, ngo)
        out["ngo_profile"] = {"id": ngo.id, "name": ngo.name, "registration_no": ngo.registration_no,
                              "contact_email": ngo.contact_email, "contact_phone": ngo.contact_phone,
                              "district_id": ngo.district_id, "registered_at": iso(ngo.created_at),
                              "has_portal_account": bool(ngo.user_id)}
        out["transfers"] = [self.transfer_row(x, r.document_no, r.approved_amount) for x in transfers]
        from controller.funding import FundingService

        svc = FundingService(self.db)
        alloc = svc.allocation_for_request(r.id)
        out["allocation"] = svc.allocation_row(alloc) if alloc else None
        return out

    def cancel_allocation(self, request_id, reason=None):
        from controller.funding import FundingService

        svc = FundingService(self.db)
        alloc = svc.allocation_for_request(request_id)
        if alloc is None:
            raise AuthFlowError("ALLOCATION_NOT_FOUND", "This request has no allocation.", 404)
        return svc.cancel_allocation(self.staff, alloc.id, reason)

    def eligible_requests(self, ngo_id):
        """Approved requests of this NGO that still have an approved amount left to transfer."""
        reqs = list(self.db.scalars(select(NgoDonationRequest).where(
            NgoDonationRequest.ngo_id == ngo_id, NgoDonationRequest.status.in_(APPROVED_STATUSES))))
        transfers = self._transfers(request_ids=[r.id for r in reqs])
        from model.fund_model import NgoAllocation

        cancelled = set(self.db.scalars(select(NgoAllocation.ngo_request_id).where(
            NgoAllocation.ngo_request_id.in_([r.id for r in reqs]), NgoAllocation.cancelled_at.is_not(None)))) if reqs else set()
        rows = [self.request_row(r, transfers) for r in reqs if r.id not in cancelled]
        return {"items": [r for r in rows if Decimal(r["awaiting_transfer"] or 0) > 0]}

    def _lock(self, request_id):
        r = self.db.scalar(select(NgoDonationRequest).where(NgoDonationRequest.id == request_id).with_for_update()
                           .execution_options(populate_existing=True))
        if r is None:
            self.db.rollback()
            raise AuthFlowError("REQUEST_NOT_FOUND", "This request does not exist.", 404)
        return r

    def _ngo_user(self, r):
        ngo = self.db.get(Ngo, r.ngo_id)
        return ngo, (self.db.get(User, ngo.user_id) if ngo and ngo.user_id else None)

    def _finish(self, r, type_, title, message, email_heading, email_intro, rows):
        ngo, user = self._ngo_user(r)
        notes = self._notify([user.id] if user else [], type_, title, message, r.id, "ngo_request")
        self._email(user, email_heading, "NGO REQUEST", email_intro, rows, f"/ngo/requests/{r.id}",
                    "View your request", greet=ngo.name if ngo else None)
        self.db.commit()
        transfers = self._transfers(request_ids=[r.id])
        return self.request_row(r, transfers, ngo), [(n.user_id, n.to_dict()) for n in notes]

    def start_review(self, request_id):
        r = self._lock(request_id)
        if r.status != "pending":
            self.db.rollback()
            raise AuthFlowError("INVALID_TRANSITION", f"A {r.status.replace('_', ' ')} request cannot be put under review.", 409)
        r.status, r.reviewed_by, r.reviewed_at = "under_review", self.staff.id, now_utc()
        return self._finish(r, "ngo_request_review", f"{r.document_no} is under review",
                            "PDMA has started reviewing your request.", "Your request is under review",
                            "PDMA has started reviewing your funding request.", [("Reference", r.document_no)])

    def request_info(self, request_id, message):
        r = self._lock(request_id)
        if r.status not in OPEN_STATUSES:
            self.db.rollback()
            raise AuthFlowError("INVALID_TRANSITION", "Information can only be requested before a decision.", 409)
        r.info_request, r.status = message.strip(), "under_review"
        r.reviewed_by, r.reviewed_at = self.staff.id, now_utc()
        return self._finish(r, "ngo_request_info", f"PDMA needs more information on {r.document_no}", r.info_request,
                            "More information needed", "PDMA needs more information before deciding on your request.",
                            [("Reference", r.document_no), ("PDMA's question", r.info_request)])

    def approve(self, request_id, amount, sources=None):
        """Approve (fully or partly) AND fund it: the selected donor contributions must add up to the approved
        amount; they are reserved and an allocation (ALLOC-..) is created in the same transaction."""
        from controller.funding import FundingService

        r = self._lock(request_id)
        if r.status not in OPEN_STATUSES:
            self.db.rollback()
            raise AuthFlowError("ALREADY_DECIDED", f"This request is already {r.status.replace('_', ' ')}.", 409)
        amount = Decimal(str(amount)).quantize(CENT)
        if amount > Decimal(r.requested_amount):
            self.db.rollback()
            raise AuthFlowError("EXCEEDS_REQUESTED", f"You can approve at most {pkr(r.requested_amount)}.", 422)
        alloc = FundingService(self.db).approve_with_funding(self.staff, r, amount, sources or [])
        now = now_utc()
        r.approved_amount, r.approved_by, r.approved_at = amount, self.staff.id, now
        r.reviewed_by, r.reviewed_at = r.reviewed_by or self.staff.id, r.reviewed_at or now
        partial = amount < Decimal(r.requested_amount)
        r.status = "partially_approved" if partial else "approved"
        word = "partially approved" if partial else "approved"
        return self._finish(r, "ngo_request_approved", f"{r.document_no} {word}",
                            f"Approved {pkr(amount)} of {pkr(r.requested_amount)} (allocation {alloc.document_no}). "
                            "Funds will be transferred separately.",
                            f"Request {word}", f"PDMA has {word} your funding request. The money will be transferred separately.",
                            [("Reference", r.document_no), ("Requested", pkr(r.requested_amount)), ("Approved", pkr(amount))])

    def reject(self, request_id, reason):
        r = self._lock(request_id)
        if r.status not in OPEN_STATUSES:
            self.db.rollback()
            raise AuthFlowError("ALREADY_DECIDED", f"This request is already {r.status.replace('_', ' ')}.", 409)
        now = now_utc()
        r.status, r.rejection_reason, r.rejected_by, r.rejected_at = "rejected", reason.strip(), self.staff.id, now
        r.reviewed_by, r.reviewed_at = r.reviewed_by or self.staff.id, r.reviewed_at or now
        return self._finish(r, "ngo_request_rejected", f"{r.document_no} rejected", r.rejection_reason,
                            "Request rejected", "PDMA could not approve your funding request.",
                            [("Reference", r.document_no), ("Reason", r.rejection_reason)])

    # Expenditure verification ------------------------------------------------------------------------------
    def list_expenditures(self, status=None, ngo_id=None, page=1, page_size=20):
        q = (select(NgoExpenditure, DonationDisbursement.document_no, Ngo.name)
             .join(DonationDisbursement, DonationDisbursement.id == NgoExpenditure.disbursement_id)
             .outerjoin(Ngo, Ngo.id == NgoExpenditure.ngo_id))
        if status:
            q = q.where(NgoExpenditure.verification_status == status)
        if ngo_id:
            q = q.where(NgoExpenditure.ngo_id == ngo_id)
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = self.db.execute(q.order_by(NgoExpenditure.created_at.desc(), NgoExpenditure.id.desc())
                               .offset((page - 1) * page_size).limit(page_size)).all()
        return {"items": [self.expenditure_row(e, ref, name) for e, ref, name in rows], "total": int(total or 0),
                "page": page, "page_size": page_size}

    def verify_expenditure(self, expenditure_id, action, reason=None):
        e = self.db.scalar(select(NgoExpenditure).where(NgoExpenditure.id == expenditure_id).with_for_update()
                           .execution_options(populate_existing=True))
        if e is None:
            raise AuthFlowError("EXPENDITURE_NOT_FOUND", "This expenditure does not exist.", 404)
        if e.verification_status != "pending":
            self.db.rollback()
            raise AuthFlowError("ALREADY_VERIFIED", f"This expenditure is already {e.verification_status}.", 409)
        e.verification_status = "verified" if action == "verify" else "rejected"
        e.verified_by, e.verified_at = self.staff.id, now_utc()
        ngo = self.db.get(Ngo, e.ngo_id) if e.ngo_id else None
        notes = self._notify([ngo.user_id] if ngo and ngo.user_id else [], "ngo_expenditure_verified",
                             f"Expenditure {e.reference} {e.verification_status}",
                             reason or f"{pkr(e.amount)} was {e.verification_status} by PDMA.", e.id, "ngo_expenditure")
        self.db.commit()
        ref = self.db.scalar(select(DonationDisbursement.document_no).where(DonationDisbursement.id == e.disbursement_id))
        return self.expenditure_row(e, ref, ngo.name if ngo else None), [(n.user_id, n.to_dict()) for n in notes]

"""PDMA fund flow: Donor -> PDMA (DD-IN) -> NGO / shopkeeper / householder (DD-OUT).

Rules (enforced here, in transactions with row locks):
- Donors give only to PDMA. A donor donation is `pending` until PDMA verifies it; only `verified` money counts.
- Total received   = sum of verified donor donations.
- Total distributed = sum of counted disbursements (pending, sent, received); draft and rejected do not count.
- Available        = received - distributed, per source donation and overall. Never negative.
- A disbursement draws from one verified donor donation; that donation's row is locked while the amount
  is checked and saved, so concurrent distributions cannot overspend it.
"""
import csv
import io
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, inspect, or_, select, text
from sqlalchemy.orm import Session

from config.fund_mail import render_notice
from config.outbox import enqueue
from controller.authentication import AuthFlowError
from controller.donation import DonationController, iso, money
from model.donation_model import DonationRequest, Ngo
from model.fund_model import COUNTED_DISBURSEMENT, DonationDisbursement, DonorDonation
from model.notification_model import Notification
from model.role_model import Role
from model.user_model import User

log = logging.getLogger(__name__)
CENT = Decimal("0.01")
ZERO = Decimal("0.00")

# Registers owned by the beneficiary-register work. Distributions to these types are validated against
# these tables once they exist; until then they are refused with RECIPIENT_REGISTER_UNAVAILABLE.
REGISTER_TABLES = {"shopkeeper": "shopkeepers", "householder": "households"}


def now_utc():
    return datetime.utcnow()


class FundController:
    def __init__(self, db: Session):
        self.db = db

    # Shared -----------------------------------------------------------------------------------
    def _doc_no(self, prefix):
        return DonationController(self.db).next_document_no_for(prefix)

    def _staff_ids(self):
        roles = select(Role.id).where(Role.name.in_(("ADMIN", "PDMA_OFFICER")))
        return list(self.db.scalars(select(User.id).where(User.role_id.in_(roles), User.is_active.is_(True))))

    def _notify(self, user_ids, type_, title, message, related_id, related_type):
        rows = []
        for uid in dict.fromkeys(user_ids):
            n = Notification(
                user_id=uid, type=type_, title=title, message=message,
                related_id=related_id, related_type=related_type,
            )
            self.db.add(n)
            rows.append(n)
        return rows

    # Request commitment (accept / reject) ------------------------------------------------------
    def decide_request(self, donor: User, request_id: int, accept: bool, reason=None):
        """Accepting is a commitment to contribute, not a payment. Only the addressed donor may decide,
        only once, and only while the request is open."""
        ctrl = DonationController(self.db)
        batch = ctrl._lock_batch(request_id)
        if batch.donor_id != donor.id:
            self.db.rollback()
            raise AuthFlowError("REQUEST_NOT_FOUND", "This donation request does not exist.", 404)
        if batch.status == "CANCELLED":
            self.db.rollback()
            raise AuthFlowError("REQUEST_CANCELLED", "This donation request was cancelled by PDMA.", 409)
        if batch.donor_response != "PENDING":
            self.db.rollback()
            word = "accepted" if batch.donor_response == "ACCEPTED" else "rejected"
            raise AuthFlowError(f"ALREADY_{batch.donor_response}", f"You have already {word} this request.", 409)
        now = now_utc()
        reason = (reason or "").strip() or None
        if accept:
            batch.donor_response, batch.responded_at, batch.accepted_at, batch.accepted_by = "ACCEPTED", now, now, donor.id
            title, verb = f"{donor.name} accepted {batch.document_no}", "accepted"
            message = f"Commitment to contribute PKR {Decimal(batch.requested_amount):,.2f}."
        else:
            batch.donor_response, batch.responded_at, batch.rejected_at, batch.response_note = "REJECTED", now, now, reason
            title, verb = f"{donor.name} declined {batch.document_no}", "declined"
            message = reason or "The donor declined this donation request."
        notes = self._notify([batch.created_by], "donation_response", title, message, batch.id, "donation_request")
        staff = self.db.get(User, batch.created_by)
        if staff and staff.email:
            rows = [("Reference", batch.document_no), ("Donor", donor.name), ("Reason", batch.reason),
                    ("Requested", f"PKR {Decimal(batch.requested_amount):,.2f}")]
            if reason:
                rows.append(("Donor's reason", reason))
            enqueue(self.db, staff.email, *render_notice(
                f"Donation request {verb}", "DONATION REQUEST", staff.name,
                f"{donor.name} has {verb} donation request {batch.document_no}."
                + (" This is a commitment to contribute; the payment is recorded and verified separately." if accept else ""),
                rows, f"/donation-requests/{batch.id}", "Open the request",
            ))
        self.db.commit()
        self.db.refresh(batch)
        out = ctrl.serialize_request(batch, donor, ctrl.financials([batch.id])[batch.id])
        out["accepted_at"], out["rejected_at"] = iso(batch.accepted_at), iso(batch.rejected_at)
        return out, [(n.user_id, n.to_dict()) for n in notes]

    # Incoming donor donations (DD-IN) ---------------------------------------------------------------
    def _used(self, donation_ids):
        if not donation_ids:
            return {}
        return {
            k: Decimal(v)
            for k, v in self.db.execute(
                select(DonationDisbursement.source_batch_id, func.coalesce(func.sum(DonationDisbursement.amount), 0))
                .where(
                    DonationDisbursement.source_batch_id.in_(donation_ids),
                    DonationDisbursement.status.in_(COUNTED_DISBURSEMENT),
                )
                .group_by(DonationDisbursement.source_batch_id)
            )
        }

    def _donation_row(self, d: DonorDonation, donor: User | None, used=ZERO, request_no=None):
        verified = d.payment_status == "verified"
        return {
            "id": d.id,
            "document_no": d.document_no,
            "donor": {"id": donor.id, "name": donor.name, "email": donor.email} if donor else None,
            "request_id": d.request_id,
            "request_document_no": request_no,
            "amount": money(d.amount),
            "remarks": d.remarks,
            "event_id": d.event_id,
            "payment_status": d.payment_status,
            "payment_reference": d.payment_reference,
            "received_at": iso(d.received_at),
            "verified_at": iso(d.verified_at),
            "rejection_reason": d.rejection_reason,
            "distributed_amount": money(used if verified else ZERO),
            "available_amount": money((Decimal(d.amount) - used) if verified else ZERO),
            "created_at": iso(d.created_at),
        }

    def create_donor_donation(self, donor: User, data: dict):
        request_no = None
        if data.get("request_id"):
            req = self.db.get(DonationRequest, data["request_id"])
            if req is None or req.donor_id != donor.id:
                raise AuthFlowError("REQUEST_NOT_FOUND", "This donation request does not exist.", 404)
            if req.donor_response == "REJECTED" or req.status == "CANCELLED":
                raise AuthFlowError("REQUEST_CLOSED", "You cannot contribute against this request.", 409)
            request_no = req.document_no
        reference = (data.get("payment_reference") or "").strip() or None
        if reference and self.db.scalar(
            select(DonorDonation.id).where(
                DonorDonation.donor_id == donor.id,
                DonorDonation.payment_reference == reference,
                DonorDonation.payment_status != "rejected",
            )
        ):
            raise AuthFlowError("DUPLICATE_PAYMENT", "You have already submitted a payment with this reference.", 409)
        d = DonorDonation(
            document_no=self._doc_no("DD-IN"),
            donor_id=donor.id,
            request_id=data.get("request_id"),
            amount=Decimal(str(data["amount"])).quantize(CENT),
            remarks=(data.get("remarks") or "").strip() or None,
            payment_status="pending",
            payment_reference=reference,
        )
        self.db.add(d)
        self.db.flush()
        notes = self._notify(
            self._staff_ids(), "donor_donation",
            f"Donation {d.document_no} received for verification",
            f"{donor.name} sent PKR {Decimal(d.amount):,.2f}" + (f" for {request_no}" if request_no else "") + ".",
            d.id, "donor_donation",
        )
        self.db.commit()
        return self._donation_row(d, donor, ZERO, request_no), [(n.user_id, n.to_dict()) for n in notes]

    def list_donor_donations(self, user, role, status=None, search=None, donor_id=None, available_only=False,
                             page=1, page_size=20):
        q = (
            select(DonorDonation, User, DonationRequest.document_no)
            .join(User, User.id == DonorDonation.donor_id)
            .outerjoin(DonationRequest, DonationRequest.id == DonorDonation.request_id)
        )
        if role == "DONOR":
            q = q.where(DonorDonation.donor_id == user.id)
        elif donor_id:
            q = q.where(DonorDonation.donor_id == donor_id)
        # Money totals for the same donor scope (before status/search filters), computed by the database.
        scope = q.with_only_columns(DonorDonation.payment_status, DonorDonation.amount).subquery()
        totals = {k: {"count": 0, "amount": ZERO} for k in ("pending", "verified", "rejected")}
        for st, n, amt in self.db.execute(
            select(scope.c.payment_status, func.count(), func.coalesce(func.sum(scope.c.amount), 0))
            .group_by(scope.c.payment_status)
        ):
            totals[st] = {"count": int(n), "amount": Decimal(amt)}
        if status:
            q = q.where(DonorDonation.payment_status == status)
        if available_only:
            used = (
                select(func.coalesce(func.sum(DonationDisbursement.amount), 0))
                .where(
                    DonationDisbursement.source_batch_id == DonorDonation.id,
                    DonationDisbursement.status.in_(COUNTED_DISBURSEMENT),
                )
                .scalar_subquery()
            )
            q = q.where(DonorDonation.payment_status == "verified", DonorDonation.amount > used)
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(DonorDonation.document_no.ilike(like), DonorDonation.remarks.ilike(like),
                            DonorDonation.payment_reference.ilike(like), User.name.ilike(like)))
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = self.db.execute(
            q.order_by(DonorDonation.created_at.desc(), DonorDonation.id.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        used = self._used([d.id for d, _, _ in rows])
        return {
            "items": [self._donation_row(d, u, used.get(d.id, ZERO), rn) for d, u, rn in rows],
            "total": int(total or 0), "page": page, "page_size": page_size,
            "totals": {k: {"count": v["count"], "amount": money(v["amount"])} for k, v in totals.items()},
        }

    def get_donor_donation(self, donation_id, user, role):
        d = self.db.get(DonorDonation, donation_id)
        if d is None or (role == "DONOR" and d.donor_id != user.id):
            raise AuthFlowError("DONATION_NOT_FOUND", "This donation does not exist.", 404)
        donor = self.db.get(User, d.donor_id)
        request_no = self.db.scalar(select(DonationRequest.document_no).where(DonationRequest.id == d.request_id)) if d.request_id else None
        out = self._donation_row(d, donor, self._used([d.id]).get(d.id, ZERO), request_no)
        # Traceability: where this money went (donors see recipient type and amount; staff also see names).
        outs = self.db.scalars(
            select(DonationDisbursement).where(DonationDisbursement.source_batch_id == d.id).order_by(DonationDisbursement.id)
        ).all()
        out["disbursements"] = [self._disbursement_row(x, include_recipient=(role != "DONOR")) for x in outs]
        return out

    def verify_donor_donation(self, staff: User, donation_id: int, action: str, received_at=None, reason=None):
        d = self.db.scalar(
            select(DonorDonation).where(DonorDonation.id == donation_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        if d is None:
            raise AuthFlowError("DONATION_NOT_FOUND", "This donation does not exist.", 404)
        if d.payment_status != "pending":
            self.db.rollback()
            raise AuthFlowError("ALREADY_VERIFIED", f"This donation is already {d.payment_status}.", 409)
        now = now_utc()
        d.verified_by, d.verified_at = staff.id, now
        donor = self.db.get(User, d.donor_id)
        if action == "verify":
            d.payment_status, d.received_at = "verified", received_at or now
            title, message = f"Donation {d.document_no} verified", f"PKR {Decimal(d.amount):,.2f} has been received by PDMA. Thank you."
        else:
            d.payment_status, d.rejection_reason = "rejected", (reason or "").strip() or None
            title = f"Donation {d.document_no} could not be verified"
            message = d.rejection_reason or "PDMA could not confirm this payment. Contact PDMA for details."
        notes = self._notify([d.donor_id], "donor_donation_verified", title, message, d.id, "donor_donation")
        if donor and donor.email:
            enqueue(self.db, donor.email, *render_notice(
                title, "DONATION", donor.name, message,
                [("Reference", d.document_no), ("Amount", f"PKR {Decimal(d.amount):,.2f}"), ("Status", d.payment_status.title())],
                "/donor/contributions", "View your contributions",
            ))
        self.db.commit()
        return self._donation_row(d, donor), [(n.user_id, n.to_dict()) for n in notes]

    # Recipients -------------------------------------------------------------------------------------
    def _register_exists(self, table):
        return inspect(self.db.get_bind()).has_table(table)

    def recipient_name(self, rtype, rid):
        if rtype == "ngo":
            return self.db.scalar(select(Ngo.name).where(Ngo.id == rid))
        table = REGISTER_TABLES[rtype]
        if not self._register_exists(table):
            return None
        cols = {c["name"] for c in inspect(self.db.get_bind()).get_columns(table)}
        col = next((c for c in ("name", "head_name", "full_name") if c in cols), None)
        return self.db.execute(text(f"SELECT {col} FROM {table} WHERE id = :id"), {"id": rid}).scalar() if col else None

    def validate_recipient(self, rtype, rid):
        if rtype == "ngo":
            ngo = self.db.get(Ngo, rid)
            if ngo is None or not ngo.is_active:
                raise AuthFlowError("RECIPIENT_NOT_FOUND", "Choose an active NGO.", 404)
            return ngo.name
        table = REGISTER_TABLES[rtype]
        if not self._register_exists(table):
            raise AuthFlowError(
                "RECIPIENT_REGISTER_UNAVAILABLE",
                f"The {rtype} register is not available yet, so {rtype}s cannot receive funds yet.",
                422,
            )
        cols = {c["name"] for c in inspect(self.db.get_bind()).get_columns(table)}
        where = " AND is_active = 1" if "is_active" in cols else ""
        if self.db.execute(text(f"SELECT id FROM {table} WHERE id = :id{where}"), {"id": rid}).scalar() is None:
            raise AuthFlowError("RECIPIENT_NOT_FOUND", f"This {rtype} does not exist or is inactive.", 404)
        return self.recipient_name(rtype, rid)

    def list_recipients(self, rtype, search=None):
        if rtype == "ngo":
            q = select(Ngo).where(Ngo.is_active.is_(True))
            if search:
                q = q.where(Ngo.name.ilike(f"%{search.strip()}%"))
            return {"available": True, "items": [{"id": n.id, "name": n.name} for n in self.db.scalars(q.order_by(Ngo.name).limit(50))]}
        table = REGISTER_TABLES[rtype]
        if not self._register_exists(table):
            return {"available": False, "items": []}
        cols = {c["name"] for c in inspect(self.db.get_bind()).get_columns(table)}
        col = next((c for c in ("name", "head_name", "full_name") if c in cols), "id")
        sql = f"SELECT id, {col} AS name FROM {table}" + (" WHERE is_active = 1" if "is_active" in cols else "")
        rows = self.db.execute(text(sql + " ORDER BY id LIMIT 200")).all()
        items = [{"id": r.id, "name": str(r.name)} for r in rows]
        if search:
            items = [i for i in items if search.lower() in i["name"].lower()]
        return {"available": True, "items": items[:50]}

    # Outgoing disbursements (DD-OUT) ----------------------------------------------------------------
    def _disbursement_row(self, x: DonationDisbursement, include_recipient=True, source_no=None):
        return {
            "id": x.id,
            "document_no": x.document_no,
            "recipient_type": x.recipient_type,
            "recipient_id": x.recipient_id if include_recipient else None,
            "recipient_name": self.recipient_name(x.recipient_type, x.recipient_id) if include_recipient else None,
            "source_batch_id": x.source_batch_id,
            "source_document_no": source_no,
            "amount": money(x.amount),
            "remarks": x.remarks,
            "event_id": x.event_id,
            "status": x.status,
            "status_reason": x.status_reason,
            "sent_at": iso(x.sent_at),
            "received_at": iso(x.received_at),
            "created_at": iso(x.created_at),
        }

    def create_disbursement(self, staff: User, data: dict):
        amount = Decimal(str(data["amount"])).quantize(CENT)
        # Lock the source donation so concurrent disbursements are checked one after another.
        source = self.db.scalar(
            select(DonorDonation).where(DonorDonation.id == data["source_batch_id"]).with_for_update()
            .execution_options(populate_existing=True)
        )
        if source is None:
            self.db.rollback()
            raise AuthFlowError("SOURCE_NOT_FOUND", "Choose a verified incoming donation to send from.", 404)
        if source.payment_status != "verified":
            self.db.rollback()
            raise AuthFlowError("SOURCE_NOT_VERIFIED", f"{source.document_no} is not verified, so it cannot be spent.", 409)
        try:
            self.validate_recipient(data["recipient_type"], data["recipient_id"])
        except AuthFlowError:
            self.db.rollback()
            raise
        available = Decimal(source.amount) - self._used([source.id]).get(source.id, ZERO)
        if amount > available:
            self.db.rollback()
            raise AuthFlowError(
                "INSUFFICIENT_FUNDS", f"Only PKR {available:,.2f} is available in {source.document_no}.", 409
            )
        now = now_utc()
        x = DonationDisbursement(
            document_no=self._doc_no("DD-OUT"),
            sent_by=staff.id,
            recipient_type=data["recipient_type"],
            recipient_id=data["recipient_id"],
            source_batch_id=source.id,
            amount=amount,
            remarks=(data.get("remarks") or "").strip() or None,
            status="sent",
            sent_at=now,
        )
        self.db.add(x)
        self.db.commit()  # funds count as distributed only once this row is saved
        return self._disbursement_row(x, source_no=source.document_no)

    def list_disbursements(self, status=None, recipient_type=None, search=None, date_from=None, date_to=None,
                           page=1, page_size=20):
        q = select(DonationDisbursement, DonorDonation.document_no).join(
            DonorDonation, DonorDonation.id == DonationDisbursement.source_batch_id
        )
        if status:
            q = q.where(DonationDisbursement.status == status)
        if recipient_type:
            q = q.where(DonationDisbursement.recipient_type == recipient_type)
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(DonationDisbursement.document_no.ilike(like), DonationDisbursement.remarks.ilike(like),
                            DonorDonation.document_no.ilike(like)))
        if date_from:
            q = q.where(DonationDisbursement.created_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            q = q.where(DonationDisbursement.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = self.db.execute(
            q.order_by(DonationDisbursement.created_at.desc(), DonationDisbursement.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).all()
        return {"items": [self._disbursement_row(x, source_no=s) for x, s in rows],
                "total": int(total or 0), "page": page, "page_size": page_size}

    def set_disbursement_status(self, staff: User, disbursement_id: int, status: str, reason=None):
        x = self.db.scalar(
            select(DonationDisbursement).where(DonationDisbursement.id == disbursement_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        if x is None:
            raise AuthFlowError("DISBURSEMENT_NOT_FOUND", "This distribution does not exist.", 404)
        allowed = {"sent": ("received", "rejected"), "pending": ("sent", "rejected"), "draft": ("pending", "rejected")}
        if status not in allowed.get(x.status, ()):
            self.db.rollback()
            raise AuthFlowError("INVALID_TRANSITION", f"A {x.status} distribution cannot be marked {status}.", 409)
        x.status = status
        if status == "received":
            x.received_at = now_utc()
        if status == "sent":
            x.sent_at = now_utc()
        if reason:
            x.status_reason = reason.strip()
        self.db.commit()  # a rejected distribution stops counting, so its amount is available again
        source_no = self.db.scalar(select(DonorDonation.document_no).where(DonorDonation.id == x.source_batch_id))
        return self._disbursement_row(x, source_no=source_no)

    # Analytics ----------------------------------------------------------------------------------------
    def summary(self, date_from: date | None = None, date_to: date | None = None):
        def in_range(col):
            conds = []
            if date_from:
                conds.append(col >= datetime.combine(date_from, datetime.min.time()))
            if date_to:
                conds.append(col < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
            return conds

        in_q = select(DonorDonation).where(*in_range(DonorDonation.created_at)).subquery()
        received = Decimal(self.db.scalar(
            select(func.coalesce(func.sum(in_q.c.amount), 0)).where(in_q.c.payment_status == "verified")) or 0)
        verified_count = self.db.scalar(select(func.count()).where(in_q.c.payment_status == "verified")) or 0
        pending_payments = self.db.scalar(select(func.count()).where(in_q.c.payment_status == "pending")) or 0
        out_q = select(DonationDisbursement).where(
            DonationDisbursement.status.in_(COUNTED_DISBURSEMENT), *in_range(DonationDisbursement.created_at)
        ).subquery()
        distributed = Decimal(self.db.scalar(select(func.coalesce(func.sum(out_q.c.amount), 0))) or 0)
        pending_requests = self.db.scalar(
            select(func.count(DonationRequest.id)).where(
                DonationRequest.donor_response == "PENDING", DonationRequest.status != "CANCELLED")
        ) or 0

        by_type = {}
        for rtype in ("ngo", "shopkeeper", "householder"):
            rows = self.db.execute(
                select(out_q.c.recipient_id, func.sum(out_q.c.amount), func.count())
                .where(out_q.c.recipient_type == rtype).group_by(out_q.c.recipient_id)
                .order_by(func.sum(out_q.c.amount).desc())
            ).all()
            by_type[rtype] = {
                "total": money(sum((Decimal(r[1]) for r in rows), ZERO)),
                "recipients": [
                    {"id": rid, "name": self.recipient_name(rtype, rid) or f"#{rid}", "amount": money(amt), "count": n}
                    for rid, amt, n in rows[:10]
                ],
            }

        day_in = func.date(in_q.c.created_at)
        daily_in = {str(d): Decimal(a) for d, a in self.db.execute(
            select(day_in, func.sum(in_q.c.amount)).where(in_q.c.payment_status == "verified").group_by(day_in))}
        day_out = func.date(out_q.c.created_at)
        daily_out = {str(d): Decimal(a) for d, a in self.db.execute(
            select(day_out, func.sum(out_q.c.amount)).group_by(day_out))}
        days = sorted(set(daily_in) | set(daily_out))

        recent = []
        for d, u in self.db.execute(
            select(DonorDonation, User).join(User, User.id == DonorDonation.donor_id)
            .where(*in_range(DonorDonation.created_at)).order_by(DonorDonation.created_at.desc()).limit(10)
        ):
            recent.append({"direction": "in", "document_no": d.document_no, "party": u.name, "amount": money(d.amount),
                           "status": d.payment_status, "date": iso(d.created_at), "id": d.id})
        for x in self.db.scalars(
            select(DonationDisbursement).where(*in_range(DonationDisbursement.created_at))
            .order_by(DonationDisbursement.created_at.desc()).limit(10)
        ):
            recent.append({"direction": "out", "document_no": x.document_no,
                           "party": self.recipient_name(x.recipient_type, x.recipient_id) or f"{x.recipient_type} #{x.recipient_id}",
                           "amount": money(x.amount), "status": x.status, "date": iso(x.created_at), "id": x.id})
        recent.sort(key=lambda r: r["date"] or "", reverse=True)

        return {
            "total_received": money(received),
            "total_distributed": money(distributed),
            "available_balance": money(received - distributed),
            "pending_requests": int(pending_requests),
            "verified_donations": int(verified_count),
            "pending_payments": int(pending_payments),
            "distribution_by_type": by_type,
            "daily": [{"date": d, "incoming": money(daily_in.get(d, ZERO)), "outgoing": money(daily_out.get(d, ZERO))}
                      for d in days],
            "recent": recent[:15],
            "requests": self._request_stats(date_from, date_to),
            "top_donors": self._top_donors(in_q),
        }

    def _request_stats(self, date_from=None, date_to=None):
        """Donation requests PDMA sent, by donor response, with requested vs committed amounts."""
        q = select(DonationRequest).where(DonationRequest.status != "CANCELLED")
        if date_from:
            q = q.where(DonationRequest.created_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            q = q.where(DonationRequest.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
        sub = q.subquery()
        out = {"sent": 0, "accepted": 0, "rejected": 0, "awaiting": 0, "requested_amount": ZERO, "committed_amount": ZERO}
        for resp, n, amt in self.db.execute(
            select(sub.c.donor_response, func.count(), func.coalesce(func.sum(sub.c.requested_amount), 0))
            .group_by(sub.c.donor_response)
        ):
            out["sent"] += n
            out["requested_amount"] += Decimal(amt)
            if resp == "ACCEPTED":
                out["accepted"], out["committed_amount"] = n, Decimal(amt)
            elif resp == "REJECTED":
                out["rejected"] = n
            else:
                out["awaiting"] = n
        out["requested_amount"] = money(out["requested_amount"])
        out["committed_amount"] = money(out["committed_amount"])
        return out

    def _top_donors(self, in_q, limit=5):
        """Donors ranked by verified money received in the period."""
        rows = self.db.execute(
            select(User.id, User.name, func.sum(in_q.c.amount), func.count())
            .join(User, User.id == in_q.c.donor_id)
            .where(in_q.c.payment_status == "verified")
            .group_by(User.id, User.name)
            .order_by(func.sum(in_q.c.amount).desc())
            .limit(limit)
        ).all()
        return [{"id": i, "name": n, "amount": money(a), "count": c} for i, n, a, c in rows]

    def export_csv(self, date_from=None, date_to=None) -> str:
        """Every incoming and outgoing transaction in the period (an audit-ready ledger)."""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["direction", "document_no", "date", "party", "amount_pkr", "status", "source_document_no",
                    "recipient_type", "reference", "remarks"])
        conds_in, conds_out = [], []
        if date_from:
            start = datetime.combine(date_from, datetime.min.time())
            conds_in.append(DonorDonation.created_at >= start)
            conds_out.append(DonationDisbursement.created_at >= start)
        if date_to:
            end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
            conds_in.append(DonorDonation.created_at < end)
            conds_out.append(DonationDisbursement.created_at < end)
        for d, u in self.db.execute(
            select(DonorDonation, User).join(User, User.id == DonorDonation.donor_id).where(*conds_in)
            .order_by(DonorDonation.created_at)
        ):
            w.writerow(["in", d.document_no, iso(d.created_at), u.name, money(d.amount), d.payment_status, "", "",
                        d.payment_reference or "", d.remarks or ""])
        for x, src in self.db.execute(
            select(DonationDisbursement, DonorDonation.document_no)
            .join(DonorDonation, DonorDonation.id == DonationDisbursement.source_batch_id).where(*conds_out)
            .order_by(DonationDisbursement.created_at)
        ):
            w.writerow(["out", x.document_no, iso(x.created_at),
                        self.recipient_name(x.recipient_type, x.recipient_id) or f"#{x.recipient_id}",
                        money(x.amount), x.status, src, x.recipient_type, "", x.remarks or ""])
        return buf.getvalue()

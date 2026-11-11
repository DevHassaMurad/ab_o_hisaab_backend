"""Donor management, donation requests (batches), received donations and NGO fund allocations.

Money rules (all enforced here, inside database transactions with row locks on the batch):
- Total requested  = donation_requests.requested_amount
- Total received   = sum of VERIFIED donations (PENDING / REJECTED never count)
- Total allocated  = sum of ACTIVE fund allocations
- Remaining        = received - allocated; an allocation may never make it negative.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.auth_mail import AuthMailer, MailDeliveryError
from config.donation_mail import render_donation_request
from config.realtime import hub
from controller.authentication import AuthFlowError
from model.donation_model import (
    Donation,
    DonationRequest,
    DonorProfile,
    DocumentSequence,
    FundAllocation,
    Ngo,
)
from model.notification_model import Notification
from model.role_model import Role
from model.user_model import User

log = logging.getLogger(__name__)

DOC_PREFIX = {"donor": "DT-DONOR", "ngo": "DT-NGO"}
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
DUPLICATE_WINDOW = timedelta(minutes=2)


def money(value) -> str:
    return f"{Decimal(value or 0).quantize(CENT)}"


def now_utc():
    return datetime.utcnow()


def iso(value):
    return value.isoformat() if value else None


class DonationController:
    def __init__(self, db: Session, mailer=None):
        self.db = db
        self.mailer = mailer or AuthMailer()

    # Helpers ----------------------------------------------------------------------------------
    def _role_id(self, name):
        return self.db.scalar(select(Role.id).where(Role.name == name))

    def next_document_no(self, request_type: str) -> str:
        return self.next_document_no_for(DOC_PREFIX[request_type])

    def next_document_no_for(self, prefix: str) -> str:
        """<PREFIX>-26-00001: prefix, 2-digit year, 5-digit number restarting each year (DT-DONOR, DD-IN, DD-OUT).
        The sequence row is locked (SELECT ... FOR UPDATE) and every document_no column is unique,
        so two documents created at the same moment can never share a number."""
        year = now_utc().year
        for _ in range(3):
            seq = self.db.scalar(
                select(DocumentSequence)
                .where(DocumentSequence.prefix == prefix, DocumentSequence.year == year)
                .with_for_update()
            )
            if seq is None:
                try:
                    with self.db.begin_nested():
                        self.db.add(DocumentSequence(prefix=prefix, year=year, last_no=0))
                except IntegrityError:
                    pass  # another request created the row first
                continue  # lock the row on the next pass
            seq.last_no += 1
            self.db.flush()
            return f"{prefix}-{year % 100:02d}-{seq.last_no:05d}"
        raise AuthFlowError("DOCUMENT_NO_UNAVAILABLE", "Could not issue a document number. Try again.", 503)

    def _lock_batch(self, batch_id: int) -> DonationRequest:
        batch = self.db.scalar(
            select(DonationRequest)
            .where(DonationRequest.id == batch_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch is None:
            raise AuthFlowError("REQUEST_NOT_FOUND", "This donation request does not exist.", 404)
        return batch

    def financials(self, batch_ids):
        """{batch_id: {received, pending_received, allocated}} computed from the ledgers, never cached."""
        out = {i: {"received": ZERO, "pending_received": ZERO, "allocated": ZERO} for i in batch_ids}
        if not batch_ids:
            return out
        for bid, received, pending in self.db.execute(
            select(
                Donation.batch_id,
                func.coalesce(func.sum(case((Donation.verification_status == "VERIFIED", Donation.amount), else_=0)), 0),
                func.coalesce(func.sum(case((Donation.verification_status == "PENDING", Donation.amount), else_=0)), 0),
            )
            .where(Donation.batch_id.in_(batch_ids))
            .group_by(Donation.batch_id)
        ):
            out[bid]["received"] = Decimal(received)
            out[bid]["pending_received"] = Decimal(pending)
        for bid, allocated in self.db.execute(
            select(FundAllocation.batch_id, func.coalesce(func.sum(FundAllocation.amount), 0))
            .where(FundAllocation.batch_id.in_(batch_ids), FundAllocation.status == "ACTIVE")
            .group_by(FundAllocation.batch_id)
        ):
            out[bid]["allocated"] = Decimal(allocated)
        return out

    def _refresh_status(self, batch: DonationRequest):
        if batch.status == "CANCELLED":
            return
        received = self.financials([batch.id])[batch.id]["received"]
        batch.status = (
            "FUNDED" if received >= batch.requested_amount else "PARTIALLY_FUNDED" if received > 0 else "SENT"
        )

    def serialize_request(self, r: DonationRequest, donor: User | None, fin: dict):
        received, allocated = fin["received"], fin["allocated"]
        requested = Decimal(r.requested_amount)
        return {
            "id": r.id,
            "document_no": r.document_no,
            "type": r.type,
            "donor": {"id": donor.id, "name": donor.name, "email": donor.email} if donor else None,
            "ngo_id": r.ngo_id,
            "reason": r.reason,
            "currency": r.currency,
            "status": r.status,
            "requested_amount": money(requested),
            "total_received": money(received),
            "pending_verification": money(fin["pending_received"]),
            "outstanding_amount": money(max(requested - received, ZERO)),
            "total_allocated": money(allocated),
            "remaining_balance": money(received - allocated),
            # What the donor can still send: requested minus received and amounts awaiting verification.
            "available_to_send": money(max(requested - received - fin["pending_received"], ZERO)),
            "donor_response": r.donor_response,
            "responded_at": iso(r.responded_at),
            "response_note": r.response_note,
            "sent_at": iso(r.sent_at),
            "created_at": iso(r.created_at),
        }

    # Donors -----------------------------------------------------------------------------------
    def list_donors(self, search=None, status=None, page=1, page_size=20):
        donor_role = self._role_id("DONOR")
        req_stats = (
            select(DonationRequest.donor_id, func.count(DonationRequest.id).label("requests"))
            .group_by(DonationRequest.donor_id)
            .subquery()
        )
        donated = (
            select(Donation.donor_id, func.sum(Donation.amount).label("donated"))
            .where(Donation.verification_status == "VERIFIED")
            .group_by(Donation.donor_id)
            .subquery()
        )
        q = (
            select(User, DonorProfile.organization, req_stats.c.requests, donated.c.donated)
            .outerjoin(DonorProfile, DonorProfile.user_id == User.id)
            .outerjoin(req_stats, req_stats.c.donor_id == User.id)
            .outerjoin(donated, donated.c.donor_id == User.id)
            .where(User.role_id == donor_role)
        )
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(User.name.ilike(like), User.email.ilike(like), DonorProfile.organization.ilike(like)))
        if status == "active":
            q = q.where(User.is_active.is_(True), User.email_verified_at.is_not(None))
        elif status == "inactive":
            q = q.where(User.is_active.is_(False))
        elif status == "unverified":
            q = q.where(User.is_active.is_(True), User.email_verified_at.is_(None))
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = self.db.execute(q.order_by(User.name, User.id).offset((page - 1) * page_size).limit(page_size)).all()
        return {
            "items": [self._donor_row(u, org, n, d) for u, org, n, d in rows],
            "total": int(total or 0),
            "page": page,
            "page_size": page_size,
        }

    @staticmethod
    def _donor_status(u: User):
        if not u.is_active:
            return "INACTIVE"
        return "ACTIVE" if u.email_verified_at else "UNVERIFIED"

    def _donor_row(self, u, organization, requests, donated):
        return {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "organization": organization,
            "registered_at": iso(u.created_at),
            "total_donated": money(donated),
            "requests_count": int(requests or 0),
            "status": self._donor_status(u),
        }

    def _get_donor(self, donor_id) -> User:
        donor = self.db.get(User, donor_id)
        if donor is None or donor.role_id != self._role_id("DONOR"):
            raise AuthFlowError("DONOR_NOT_FOUND", "This donor does not exist.", 404)
        return donor

    def get_donor(self, donor_id: int):
        donor = self._get_donor(donor_id)
        profile = self.db.scalar(select(DonorProfile).where(DonorProfile.user_id == donor.id))
        requests = self.list_requests(None, "ADMIN", donor_id=donor.id, page_size=100)["items"]
        donations = self.db.execute(
            select(Donation, DonationRequest.document_no)
            .join(DonationRequest, DonationRequest.id == Donation.batch_id)
            .where(Donation.donor_id == donor.id)
            .order_by(Donation.received_at.desc(), Donation.id.desc())
        ).all()
        donated = sum((Decimal(d.amount) for d, _ in donations if d.verification_status == "VERIFIED"), ZERO)
        return {
            **self._donor_row(donor, profile.organization if profile else None, len(requests), donated),
            "phone": profile.phone if profile else None,
            "requests": requests,
            "donations": [self._donation_row(d, doc) for d, doc in donations],
        }

    # Donation requests ------------------------------------------------------------------------
    def create_request(self, staff: User, data: dict):
        """Save the batch, the donor's notification and send the email in ONE transaction.
        If the email cannot be sent the whole request is rolled back and an error is returned."""
        donor = self._get_donor(data["donor_id"])
        if not donor.is_active:
            raise AuthFlowError("DONOR_INACTIVE", "This donor account is disabled.", 409)
        amount = Decimal(str(data["amount"])).quantize(CENT)
        reason = " ".join(data["reason"].split())

        # Duplicate-submission guard: the same request to the same donor within two minutes.
        recent = self.db.scalar(
            select(DonationRequest.document_no).where(
                DonationRequest.donor_id == donor.id,
                DonationRequest.requested_amount == amount,
                DonationRequest.reason == reason,
                DonationRequest.created_at >= now_utc() - DUPLICATE_WINDOW,
            )
        )
        if recent:
            raise AuthFlowError(
                "DUPLICATE_REQUEST", f"This request was already sent as {recent}. Wait before sending it again.", 409
            )

        try:
            request = DonationRequest(
                document_no=self.next_document_no(data.get("type", "donor")),
                type=data.get("type", "donor"),
                donor_id=donor.id,
                reason=reason,
                requested_amount=amount,
                status="SENT",
                created_by=staff.id,
            )
            self.db.add(request)
            self.db.flush()
            notification = Notification(
                user_id=donor.id,
                type="donation_request",
                title=f"Donation request {request.document_no}",
                message=f"{reason} · PKR {amount:,.2f}",
                related_id=request.id,
                related_type="donation_request",
            )
            self.db.add(notification)
            self.db.flush()
            subject, text, html = render_donation_request(donor.name, request, staff.name)
            self.mailer.deliver(donor.email, subject, text, html)
            request.sent_at = now_utc()
            self.db.commit()
        except MailDeliveryError as exc:
            self.db.rollback()
            raise AuthFlowError(
                "EMAIL_FAILED", "The email to the donor could not be sent, so the request was not created. Try again.", 502
            ) from exc
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(request)
        self.db.refresh(notification)
        return request, donor, notification

    def list_requests(self, user, role, donor_id=None, status=None, search=None, page=1, page_size=20, response=None):
        q = select(DonationRequest, User).outerjoin(User, User.id == DonationRequest.donor_id)
        if role == "DONOR":
            q = q.where(DonationRequest.donor_id == user.id)  # donors only ever see their own batches
        elif donor_id:
            q = q.where(DonationRequest.donor_id == donor_id)
        if status:
            q = q.where(DonationRequest.status == status)
        if response:
            q = q.where(DonationRequest.donor_response == response)
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(DonationRequest.document_no.ilike(like), DonationRequest.reason.ilike(like)))
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = self.db.execute(
            q.order_by(DonationRequest.created_at.desc(), DonationRequest.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        fin = self.financials([r.id for r, _ in rows])
        return {
            "items": [self.serialize_request(r, d, fin[r.id]) for r, d in rows],
            "total": int(total or 0),
            "page": page,
            "page_size": page_size,
        }

    def summary(self, user, role):
        """Totals across every batch the user may see (all for PDMA staff, own for a donor)."""
        ids_q = select(DonationRequest.id)
        if role == "DONOR":
            ids_q = ids_q.where(DonationRequest.donor_id == user.id)
        ids = list(self.db.scalars(ids_q))
        requested = self.db.scalar(
            select(func.coalesce(func.sum(DonationRequest.requested_amount), 0)).where(DonationRequest.id.in_(ids))
        ) if ids else 0
        fin = self.financials(ids)
        received = sum((f["received"] for f in fin.values()), ZERO)
        allocated = sum((f["allocated"] for f in fin.values()), ZERO)
        pending = sum((f["pending_received"] for f in fin.values()), ZERO)
        return {
            "requests_count": len(ids),
            "total_requested": money(requested),
            "total_received": money(received),
            "pending_verification": money(pending),
            "total_allocated": money(allocated),
            "remaining_balance": money(received - allocated),
        }

    def _visible_batch(self, request_id, user, role) -> DonationRequest:
        r = self.db.get(DonationRequest, request_id)
        if r is None or (role == "DONOR" and r.donor_id != user.id):
            raise AuthFlowError("REQUEST_NOT_FOUND", "This donation request does not exist.", 404)
        return r

    def get_request(self, request_id: int, user, role):
        r = self._visible_batch(request_id, user, role)
        donor = self.db.get(User, r.donor_id) if r.donor_id else None
        out = self.serialize_request(r, donor, self.financials([r.id])[r.id])
        out["donations"] = [
            self._donation_row(d, r.document_no)
            for d in self.db.scalars(
                select(Donation).where(Donation.batch_id == r.id).order_by(Donation.received_at, Donation.id)
            )
        ]
        out["allocations"] = [
            self._allocation_row(a, name)
            for a, name in self.db.execute(
                select(FundAllocation, Ngo.name)
                .join(Ngo, Ngo.id == FundAllocation.ngo_id)
                .where(FundAllocation.batch_id == r.id)
                .order_by(FundAllocation.allocation_date, FundAllocation.id)
            )
        ]
        return out

    # Donations received -----------------------------------------------------------------------
    @staticmethod
    def _donation_row(d: Donation, document_no=None):
        return {
            "id": d.id,
            "batch_id": d.batch_id,
            "document_no": document_no,
            "amount": money(d.amount),
            "payment_reference": d.payment_reference,
            "payment_method": d.payment_method,
            "received_at": iso(d.received_at),
            "verification_status": d.verification_status,
            "verified_at": iso(d.verified_at),
            "remarks": d.remarks,
        }

    def record_donation(self, staff: User, batch_id: int, data: dict):
        batch = self._lock_batch(batch_id)
        if batch.status == "CANCELLED":
            raise AuthFlowError("REQUEST_CANCELLED", "This donation request is cancelled.", 409)
        reference = data["payment_reference"].strip()
        exists = self.db.scalar(
            select(Donation.id).where(
                Donation.batch_id == batch.id,
                Donation.payment_reference == reference,
                Donation.verification_status != "REJECTED",
            )
        )
        if exists:
            self.db.rollback()
            raise AuthFlowError(
                "DUPLICATE_PAYMENT", "A payment with this reference is already recorded for this request.", 409
            )
        donation = Donation(
            batch_id=batch.id,
            donor_id=batch.donor_id,
            amount=Decimal(str(data["amount"])).quantize(CENT),
            payment_reference=reference,
            payment_method=data["payment_method"],
            received_at=data["received_at"],
            verification_status="PENDING",
            recorded_by=staff.id,
            remarks=(data.get("remarks") or "").strip() or None,
        )
        self.db.add(donation)
        self.db.commit()
        return self._donation_row(donation, batch.document_no)

    def verify_donation(self, staff: User, donation_id: int, action: str, remarks=None):
        d = self.db.get(Donation, donation_id)
        if d is None:
            raise AuthFlowError("DONATION_NOT_FOUND", "This donation does not exist.", 404)
        batch = self._lock_batch(d.batch_id)  # lock the batch first, then the donation
        d = self.db.scalar(
            select(Donation).where(Donation.id == donation_id).with_for_update().execution_options(populate_existing=True)
        )
        if d.verification_status != "PENDING":
            self.db.rollback()
            raise AuthFlowError(
                "ALREADY_VERIFIED", f"This donation is already {d.verification_status.lower()}.", 409
            )
        d.verification_status = "VERIFIED" if action == "verify" else "REJECTED"
        d.verified_by, d.verified_at = staff.id, now_utc()
        if remarks:
            d.remarks = remarks.strip()
        self.db.flush()
        self._refresh_status(batch)
        notification = None
        if d.verification_status == "VERIFIED" and batch.donor_id:
            notification = Notification(
                user_id=batch.donor_id,
                type="donation_verified",
                title=f"Donation received for {batch.document_no}",
                message=f"PKR {Decimal(d.amount):,.2f} has been verified. Thank you.",
                related_id=batch.id,
                related_type="donation_request",
            )
            self.db.add(notification)
        self.db.commit()
        return self._donation_row(d, batch.document_no), batch.donor_id, (notification.to_dict() if notification else None)

    # Donor's answer ---------------------------------------------------------------------------
    def respond(self, donor: User, request_id: int, data: dict):
        """Donor accepts (sending an amount) or rejects a request addressed to them.
        Accepting records a PENDING donation that PDMA verifies; it is capped by what is still uncovered."""
        batch = self._lock_batch(request_id)
        if batch.donor_id != donor.id:
            self.db.rollback()
            raise AuthFlowError("REQUEST_NOT_FOUND", "This donation request does not exist.", 404)
        if batch.status == "CANCELLED":
            self.db.rollback()
            raise AuthFlowError("REQUEST_CANCELLED", "This donation request was cancelled by PDMA.", 409)
        if batch.donor_response == "REJECTED":
            self.db.rollback()
            raise AuthFlowError("ALREADY_REJECTED", "You have already rejected this request.", 409)
        now = now_utc()
        fin = self.financials([batch.id])[batch.id]
        note = (data.get("note") or "").strip() or None

        if data["action"] == "reject":
            if batch.donor_response == "ACCEPTED" or fin["received"] > 0 or fin["pending_received"] > 0:
                self.db.rollback()
                raise AuthFlowError(
                    "ALREADY_ACCEPTED", "You have already accepted this request, so it can no longer be rejected.", 409
                )
            batch.donor_response, batch.responded_at, batch.response_note = "REJECTED", now, note
            title = f"{donor.name} declined {batch.document_no}"
            message = note or "The donor declined this donation request."
            donation_row = None
        else:
            amount = Decimal(str(data["amount"])).quantize(CENT)
            available = Decimal(batch.requested_amount) - fin["received"] - fin["pending_received"]
            if available <= 0:
                self.db.rollback()
                raise AuthFlowError("REQUEST_COVERED", "This request is already fully covered.", 409)
            if amount > available:
                self.db.rollback()
                raise AuthFlowError(
                    "AMOUNT_EXCEEDS_REQUEST",
                    f"You can send at most PKR {available:,.2f} for {batch.document_no}.",
                    409,
                )
            count = self.db.scalar(select(func.count(Donation.id)).where(Donation.batch_id == batch.id)) or 0
            donation = Donation(
                batch_id=batch.id,
                donor_id=donor.id,
                amount=amount,
                payment_reference=(data.get("payment_reference") or "").strip() or f"{batch.document_no}-D{count + 1}",
                payment_method=data.get("payment_method") or "BANK_TRANSFER",
                received_at=now,
                verification_status="PENDING",
                recorded_by=donor.id,
                remarks=note,
            )
            self.db.add(donation)
            if batch.donor_response != "ACCEPTED":
                batch.donor_response, batch.responded_at = "ACCEPTED", now
            if note:
                batch.response_note = note
            self.db.flush()
            title = f"{donor.name} accepted {batch.document_no}"
            message = f"PKR {amount:,.2f} sent and awaiting verification."
            donation_row = self._donation_row(donation, batch.document_no)

        # Tell the PDMA user who sent the request (stored first, pushed live after commit).
        notification = Notification(
            user_id=batch.created_by,
            type="donation_response",
            title=title,
            message=message,
            related_id=batch.id,
            related_type="donation_request",
        )
        self.db.add(notification)
        self.db.commit()
        self.db.refresh(batch)
        out = self.serialize_request(batch, donor, self.financials([batch.id])[batch.id])
        out["donation"] = donation_row
        return out, batch.created_by, notification.to_dict()

    # Fund allocations -------------------------------------------------------------------------
    @staticmethod
    def _allocation_row(a: FundAllocation, ngo_name=None):
        return {
            "id": a.id,
            "batch_id": a.batch_id,
            "ngo_id": a.ngo_id,
            "ngo_name": ngo_name,
            "amount": money(a.amount),
            "allocation_date": a.allocation_date.isoformat(),
            "remarks": a.remarks,
            "status": a.status,
        }

    def allocate(self, staff: User, batch_id: int, data: dict):
        batch = self._lock_batch(batch_id)  # serialises allocations for this batch
        ngo = self.db.get(Ngo, data["ngo_id"])
        if ngo is None or not ngo.is_active:
            self.db.rollback()
            raise AuthFlowError("NGO_NOT_FOUND", "Choose an active NGO.", 404)
        amount = Decimal(str(data["amount"])).quantize(CENT)
        fin = self.financials([batch.id])[batch.id]
        remaining = fin["received"] - fin["allocated"]
        if amount > remaining:
            self.db.rollback()
            raise AuthFlowError(
                "OVER_ALLOCATION",
                f"Only PKR {remaining:,.2f} of verified funds is unallocated in {batch.document_no}.",
                409,
            )
        allocation = FundAllocation(
            batch_id=batch.id,
            ngo_id=ngo.id,
            amount=amount,
            allocation_date=data["allocation_date"],
            remarks=(data.get("remarks") or "").strip() or None,
            status="ACTIVE",
            allocated_by=staff.id,
        )
        self.db.add(allocation)
        self.db.commit()
        return self._allocation_row(allocation, ngo.name)

    def cancel_allocation(self, staff: User, allocation_id: int):
        a = self.db.get(FundAllocation, allocation_id)
        if a is None:
            raise AuthFlowError("ALLOCATION_NOT_FOUND", "This allocation does not exist.", 404)
        self._lock_batch(a.batch_id)
        if a.status != "ACTIVE":
            self.db.rollback()
            raise AuthFlowError("ALREADY_CANCELLED", "This allocation is already cancelled.", 409)
        a.status = "CANCELLED"
        self.db.commit()
        return self._allocation_row(a, self.db.scalar(select(Ngo.name).where(Ngo.id == a.ngo_id)))

    # NGOs --------------------------------------------------------------------------------------
    def list_ngos(self):
        return [
            {"id": n.id, "name": n.name, "registration_no": n.registration_no, "is_active": n.is_active}
            for n in self.db.scalars(select(Ngo).order_by(Ngo.name))
        ]

    def create_ngo(self, data: dict):
        ngo = Ngo(
            name=" ".join(data["name"].split()),
            registration_no=(data.get("registration_no") or "").strip() or None,
            contact_email=(data.get("contact_email") or None),
            contact_phone=(data.get("contact_phone") or None),
        )
        self.db.add(ngo)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise AuthFlowError("NGO_EXISTS", "An NGO with this registration number already exists.", 409) from exc
        return {"id": ngo.id, "name": ngo.name, "registration_no": ngo.registration_no, "is_active": ngo.is_active}


async def push_notification(user_id: int, notification: dict):
    """Live push after the notification is saved. Failures are logged by the hub; offline users read it later."""
    try:
        await hub.send_to(user_id, {"event": "notification", "notification": notification})
    except Exception:
        log.warning("Live notification push failed for user %s.", user_id)

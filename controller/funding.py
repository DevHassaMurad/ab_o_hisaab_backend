"""Donor fund traceability for NGO allocations.

Lifecycle (each stage stored separately):
  funding selected + reserved -> allocation approved (ALLOC-..)  [approve_with_funding]
  transfer made (DD-OUT)      -> funding sources disbursed       [transfer_for_allocation]
  transfer failed             -> disbursed amount returns to reserved [void_transfer]
  allocation cancelled        -> undisbursed reservation released   [cancel_allocation]
  NGO confirms receipt        -> handled on the transfer (receipt_confirmed_at)

Available balance of a donor contribution (backend only):
  verified amount - direct transfers from it (pending/sent/received) - active reservations (allocated - released)
Rows are locked (SELECT ... FOR UPDATE, always in id order) so concurrent approvals cannot reserve the same money.
"""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_, select

from config.fund_mail import render_notice
from config.outbox import enqueue
from controller.authentication import AuthFlowError
from controller.donation import DonationController, iso, money
from model.donation_model import DonationRequest, Ngo
from model.fund_model import (
    COUNTED_DISBURSEMENT,
    AllocationFundingSource,
    DonationDisbursement,
    DonorDonation,
    FundingDisbursement,
    FundLedgerEvent,
    NgoAllocation,
)
from model.notification_model import Notification
from model.user_model import User

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def now_utc():
    return datetime.utcnow()


def pkr(v):
    return f"PKR {Decimal(v):,.2f}"


def committed_amounts(db, donation_ids):
    """{donor_donation_id: money no longer available}: direct transfers + active reservations."""
    out = defaultdict(lambda: ZERO)
    if not donation_ids:
        return out
    for did, amt in db.execute(
        select(DonationDisbursement.source_batch_id, func.coalesce(func.sum(DonationDisbursement.amount), 0))
        .where(DonationDisbursement.source_batch_id.in_(donation_ids),
               DonationDisbursement.status.in_(COUNTED_DISBURSEMENT))
        .group_by(DonationDisbursement.source_batch_id)
    ):
        out[did] += Decimal(amt)
    for did, amt in db.execute(
        select(AllocationFundingSource.donor_donation_id,
               func.coalesce(func.sum(AllocationFundingSource.allocated_amount - AllocationFundingSource.released_amount), 0))
        .where(AllocationFundingSource.donor_donation_id.in_(donation_ids))
        .group_by(AllocationFundingSource.donor_donation_id)
    ):
        out[did] += Decimal(amt)
    return out


def committed_subquery():
    """Correlated SQL expression for the same 'committed' amount (used to filter selectable sources)."""
    direct = (
        select(func.coalesce(func.sum(DonationDisbursement.amount), 0))
        .where(DonationDisbursement.source_batch_id == DonorDonation.id,
               DonationDisbursement.status.in_(COUNTED_DISBURSEMENT))
        .scalar_subquery()
    )
    reserved = (
        select(func.coalesce(func.sum(AllocationFundingSource.allocated_amount - AllocationFundingSource.released_amount), 0))
        .where(AllocationFundingSource.donor_donation_id == DonorDonation.id)
        .scalar_subquery()
    )
    return direct + reserved


class FundingService:
    def __init__(self, db):
        self.db = db

    def _ledger(self, event, actor_id, amount, note=None, allocation_id=None, source=None, disbursement_id=None):
        self.db.add(FundLedgerEvent(
            event_type=event, actor_id=actor_id, amount=amount, note=note, allocation_id=allocation_id,
            funding_source_id=source.id if source else None, disbursement_id=disbursement_id,
            donor_donation_id=source.donor_donation_id if source else None,
        ))

    # Selection -----------------------------------------------------------------------------------
    def available_sources(self, search=None, page=1, page_size=50):
        """Verified contributions with money still available, for the PDMA approval step."""
        committed = committed_subquery()
        q = (
            select(DonorDonation, User, DonationRequest.document_no)
            .join(User, User.id == DonorDonation.donor_id)
            .outerjoin(DonationRequest, DonationRequest.id == DonorDonation.request_id)
            .where(DonorDonation.payment_status == "verified", DonorDonation.amount > committed)
        )
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(User.name.ilike(like), DonorDonation.document_no.ilike(like),
                            DonationRequest.document_no.ilike(like)))
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = self.db.execute(q.order_by(DonorDonation.received_at, DonorDonation.id)
                               .offset((page - 1) * page_size).limit(page_size)).all()
        used = committed_amounts(self.db, [d.id for d, _, _ in rows])
        return {"items": [{
            "donor_donation_id": d.id,
            "donor": {"id": u.id, "name": u.name},
            "batch_no": batch_no or d.document_no,
            "document_no": d.document_no,
            "original_amount": money(d.amount),
            "previously_allocated": money(used[d.id]),
            "available_balance": money(Decimal(d.amount) - used[d.id]),
            "verification_status": d.payment_status,
            "received_at": iso(d.received_at),
        } for d, u, batch_no in rows], "total": int(total or 0), "page": page, "page_size": page_size}

    # Approval + reservation ---------------------------------------------------------------------
    def approve_with_funding(self, staff: User, request, amount: Decimal, sources: list[dict]):
        """Reserve the selected donor money and create the allocation in one transaction.
        The caller has locked the NGO request. Raises (after rollback) on any validation failure."""
        if not sources:
            self.db.rollback()
            raise AuthFlowError("FUNDING_REQUIRED", "Select at least one donor contribution to fund this allocation.", 422)
        wanted = {}
        for s in sources:
            did = int(s["donor_donation_id"])
            if did in wanted:
                self.db.rollback()
                raise AuthFlowError("DUPLICATE_SOURCE", "Each contribution can be selected only once.", 422)
            wanted[did] = Decimal(str(s["amount"])).quantize(CENT)
        total = sum(wanted.values(), ZERO)
        if total != amount:
            self.db.rollback()
            short = amount - total
            raise AuthFlowError(
                "FUNDING_MISMATCH",
                (f"Selected funding is {pkr(total)}; {pkr(short)} more is needed to fund {pkr(amount)}."
                 if short > 0 else f"Selected funding {pkr(total)} is more than the approved {pkr(amount)}."),
                422,
            )
        # Lock contributions in id order (consistent order avoids deadlocks between parallel approvals).
        donations = {
            d.id: d for d in self.db.scalars(
                select(DonorDonation).where(DonorDonation.id.in_(sorted(wanted))).order_by(DonorDonation.id)
                .with_for_update().execution_options(populate_existing=True))
        }
        used = committed_amounts(self.db, list(wanted))
        for did, amt in wanted.items():
            d = donations.get(did)
            if d is None or d.payment_status != "verified":
                self.db.rollback()
                raise AuthFlowError("SOURCE_NOT_VERIFIED", "Only verified donor contributions can fund an allocation.", 409)
            available = Decimal(d.amount) - used[did]
            if amt > available:
                self.db.rollback()
                raise AuthFlowError("INSUFFICIENT_SOURCE_FUNDS",
                                    f"Only {pkr(available)} is available in {d.document_no}.", 409)
        now = now_utc()
        alloc = NgoAllocation(
            document_no=DonationController(self.db).next_document_no_for("ALLOC"),
            ngo_id=request.ngo_id, ngo_request_id=request.id, approved_amount=amount,
            status="approved", approved_by=staff.id, approved_at=now,
        )
        self.db.add(alloc)
        self.db.flush()
        self._ledger("approved", staff.id, amount, f"Allocation for {request.document_no}", allocation_id=alloc.id)
        for did, amt in wanted.items():
            d = donations[did]
            src = AllocationFundingSource(
                allocation_id=alloc.id, donor_id=d.donor_id, donor_donation_id=d.id, donation_batch_id=d.request_id,
                allocated_amount=amt, disbursed_amount=ZERO, released_amount=ZERO, funding_status="reserved",
            )
            self.db.add(src)
            self.db.flush()
            self._ledger("reserved", staff.id, amt, f"Reserved from {d.document_no}", allocation_id=alloc.id, source=src)
        return alloc

    # Transfers ----------------------------------------------------------------------------------------
    @staticmethod
    def _source_status(src):
        if Decimal(src.released_amount) > 0 and Decimal(src.disbursed_amount) + Decimal(src.released_amount) >= Decimal(src.allocated_amount):
            return "released" if Decimal(src.disbursed_amount) == 0 else "disbursed"
        if Decimal(src.disbursed_amount) >= Decimal(src.allocated_amount):
            return "disbursed"
        return "partially_disbursed" if Decimal(src.disbursed_amount) > 0 else "reserved"

    def _refresh_allocation(self, alloc):
        if alloc.status == "cancelled":
            return
        sources = self._sources(alloc.id)
        disbursed = sum((Decimal(s.disbursed_amount) for s in sources), ZERO)
        alloc.status = ("disbursed" if disbursed >= Decimal(alloc.approved_amount)
                        else "partially_disbursed" if disbursed > 0 else "approved")

    def _sources(self, allocation_id, lock=False):
        q = select(AllocationFundingSource).where(AllocationFundingSource.allocation_id == allocation_id) \
            .order_by(AllocationFundingSource.id)
        if lock:
            q = q.with_for_update().execution_options(populate_existing=True)
        return list(self.db.scalars(q))

    def lock_allocation(self, allocation_id):
        return self.db.scalar(select(NgoAllocation).where(NgoAllocation.id == allocation_id).with_for_update()
                              .execution_options(populate_existing=True))

    def transfer_for_allocation(self, staff: User, alloc: NgoAllocation, disbursement: DonationDisbursement):
        """Pay a (possibly partial) transfer from the allocation's reserved sources, oldest first.
        The caller has created and flushed `disbursement` and locked `alloc`. Returns donor notifications."""
        if alloc.status == "cancelled":
            self.db.rollback()
            raise AuthFlowError("ALLOCATION_CANCELLED", f"{alloc.document_no} was cancelled.", 409)
        sources = self._sources(alloc.id, lock=True)
        open_amount = {s.id: Decimal(s.allocated_amount) - Decimal(s.disbursed_amount) - Decimal(s.released_amount)
                       for s in sources}
        remaining = sum(open_amount.values(), ZERO)
        amount = Decimal(disbursement.amount)
        if amount > remaining:
            self.db.rollback()
            raise AuthFlowError("EXCEEDS_RESERVED_FUNDS",
                                f"Only {pkr(remaining)} of {alloc.document_no} is left to transfer.", 409)
        notes, left = [], amount
        ngo = self.db.get(Ngo, alloc.ngo_id)
        for src in sources:
            if left <= 0:
                break
            take = min(open_amount[src.id], left)
            if take <= 0:
                continue
            left -= take
            split = FundingDisbursement(disbursement_id=disbursement.id, funding_source_id=src.id, amount=take)
            self.db.add(split)
            src.disbursed_amount = Decimal(src.disbursed_amount) + take
            src.funding_status = self._source_status(src)
            self._ledger("disbursed", staff.id, take, f"Paid in {disbursement.document_no}",
                         allocation_id=alloc.id, source=src, disbursement_id=disbursement.id)
            self.db.flush()
            notes += self._notify_donor(split, src, alloc, disbursement, ngo)
        self._refresh_allocation(alloc)
        return notes

    def _notify_donor(self, split, src, alloc, disbursement, ngo):
        """'Your donation has been utilised' — only for money actually transferred; once per split."""
        if split.donor_notified_at:
            return []
        donor = self.db.get(User, src.donor_id)
        donation = self.db.get(DonorDonation, src.donor_donation_id)
        batch_no = (self.db.scalar(select(DonationRequest.document_no).where(DonationRequest.id == src.donation_batch_id))
                    if src.donation_batch_id else None) or donation.document_no
        date = (disbursement.sent_at or now_utc()).strftime("%d %B %Y")
        n = Notification(
            user_id=donor.id, type="donation_utilized", title="Your donation has been utilised",
            message=f"{pkr(split.amount)} from {donation.document_no} was disbursed to {ngo.name} ({alloc.document_no}).",
            related_id=donation.id, related_type="donor_donation",
        )
        self.db.add(n)
        enqueue(self.db, donor.email, *render_notice(
            "Your donation has been utilised", "DONATION UTILISATION", donor.name,
            "Your contribution has helped fund an NGO allocation. It is now linked to this allocation in your donation report.",
            [("NGO", ngo.name), ("Allocation reference", alloc.document_no), ("Original donation batch", batch_no),
             ("Your contribution", donation.document_no), ("Amount from your contribution", pkr(split.amount)),
             ("Disbursement date", date), ("Status", "Disbursed")],
            "/donor/reports", "View your report",
        ))
        split.donor_notified_at = now_utc()
        self.db.flush()
        return [(donor.id, n.to_dict())]

    def notify_ngo_receipt(self, disbursement: DonationDisbursement):
        """'Your donation has reached an NGO' — sent only after the NGO confirms receipt of an actual transfer,
        once per donor share, showing each donor only the amount attributed to their own contribution."""
        if not disbursement.receipt_confirmed_at or disbursement.recipient_type != "ngo":
            return []
        ngo = self.db.get(Ngo, disbursement.recipient_id)
        alloc = self.db.get(NgoAllocation, disbursement.ngo_allocation_id) if disbursement.ngo_allocation_id else None
        from model.fund_model import NgoDonationRequest

        request_no = (self.db.scalar(select(NgoDonationRequest.document_no)
                                     .where(NgoDonationRequest.id == disbursement.ngo_request_id))
                      if disbursement.ngo_request_id else None)
        shares = []  # (donation, amount, mark-as-notified callback)
        for split in self.db.scalars(select(FundingDisbursement).where(
                FundingDisbursement.disbursement_id == disbursement.id, FundingDisbursement.status == "active",
                FundingDisbursement.donor_receipt_notified_at.is_(None))):
            src = self.db.get(AllocationFundingSource, split.funding_source_id)
            shares.append((self.db.get(DonorDonation, src.donor_donation_id), Decimal(split.amount), split))
        if disbursement.source_batch_id and disbursement.donor_receipt_notified_at is None:
            shares.append((self.db.get(DonorDonation, disbursement.source_batch_id), Decimal(disbursement.amount),
                           disbursement))
        notes = []
        date = (disbursement.sent_at or now_utc()).strftime("%d %B %Y")
        for donation, amount, marker in shares:
            donor = self.db.get(User, donation.donor_id)
            batch_no = (self.db.scalar(select(DonationRequest.document_no).where(DonationRequest.id == donation.request_id))
                        if donation.request_id else None) or donation.document_no
            what = f" for {request_no}" if request_no else ""
            n = Notification(
                user_id=donor.id, type="donation_reached_ngo", title="Your donation has reached an NGO",
                message=f"An amount of {pkr(amount)} from your donation batch {batch_no} has been disbursed to "
                        f"{ngo.name}{what}. {ngo.name} has confirmed receipt.",
                related_id=donation.id, related_type="donor_donation",
            )
            self.db.add(n)
            enqueue(self.db, donor.email, *render_notice(
                "Your donation has reached an NGO", "DONATION UTILISATION", donor.name,
                f"An amount of {pkr(amount)} from your donation batch {batch_no} has been disbursed to {ngo.name}{what}, "
                "and the NGO has confirmed receiving it.",
                [("NGO", ngo.name), ("Donation batch", batch_no), ("Your donation", donation.document_no),
                 ("NGO funding request", request_no or "—"), ("PDMA allocation", alloc.document_no if alloc else disbursement.document_no),
                 ("Amount from your donation", pkr(amount)), ("Transfer reference", disbursement.document_no),
                 ("Disbursement date", date), ("Status", "Received by NGO")],
                f"/donor/ledger/{donation.id}", "View details",
            ))
            marker.donor_receipt_notified_at = now_utc()
            self.db.flush()
            notes.append((donor.id, n.to_dict()))
        return notes

    def void_transfer(self, staff: User, disbursement: DonationDisbursement):
        """A failed / cancelled transfer: its amounts go back to reserved on each funding source."""
        alloc = self.lock_allocation(disbursement.ngo_allocation_id)
        sources = {s.id: s for s in self._sources(alloc.id, lock=True)}
        for split in self.db.scalars(select(FundingDisbursement).where(
                FundingDisbursement.disbursement_id == disbursement.id, FundingDisbursement.status == "active")):
            src = sources[split.funding_source_id]
            src.disbursed_amount = Decimal(src.disbursed_amount) - Decimal(split.amount)
            src.funding_status = self._source_status(src)
            split.status = "voided"
            self._ledger("disbursement_voided", staff.id, split.amount, f"{disbursement.document_no} failed",
                         allocation_id=alloc.id, source=src, disbursement_id=disbursement.id)
        self._refresh_allocation(alloc)

    def cancel_allocation(self, staff: User, allocation_id: int, reason=None):
        """Release every undisbursed reservation back to its contribution. Transfers already made stay recorded."""
        alloc = self.lock_allocation(allocation_id)
        if alloc is None:
            raise AuthFlowError("ALLOCATION_NOT_FOUND", "This allocation does not exist.", 404)
        if alloc.status in ("cancelled", "disbursed"):
            self.db.rollback()
            raise AuthFlowError("INVALID_TRANSITION", f"A {alloc.status} allocation cannot be cancelled.", 409)
        released = ZERO
        for src in self._sources(alloc.id, lock=True):
            free = Decimal(src.allocated_amount) - Decimal(src.disbursed_amount) - Decimal(src.released_amount)
            if free > 0:
                src.released_amount = Decimal(src.released_amount) + free
                src.funding_status = self._source_status(src)
                released += free
                self._ledger("released", staff.id, free, reason or "Allocation cancelled", allocation_id=alloc.id, source=src)
        self._ledger("cancelled", staff.id, released, reason, allocation_id=alloc.id)
        disbursed = sum((Decimal(s.disbursed_amount) for s in self._sources(alloc.id)), ZERO)
        alloc.status = "cancelled" if disbursed == 0 else "disbursed"
        alloc.cancelled_by, alloc.cancelled_at = staff.id, now_utc()
        self.db.commit()
        return self.allocation_row(alloc)

    # Read models ----------------------------------------------------------------------------------------
    def allocation_row(self, alloc: NgoAllocation, include_donors=True):
        sources = self._sources(alloc.id)
        donors = {u.id: u.name for u in self.db.scalars(select(User).where(User.id.in_({s.donor_id for s in sources})))} \
            if sources and include_donors else {}
        docs = dict(self.db.execute(select(DonorDonation.id, DonorDonation.document_no).where(
            DonorDonation.id.in_({s.donor_donation_id for s in sources}))).all()) if sources else {}
        sum_ = lambda f: sum((Decimal(getattr(s, f)) for s in sources), ZERO)  # noqa: E731
        return {
            "id": alloc.id, "document_no": alloc.document_no, "ngo_request_id": alloc.ngo_request_id,
            "approved_amount": money(alloc.approved_amount), "status": alloc.status,
            "approved_at": iso(alloc.approved_at), "cancelled_at": iso(alloc.cancelled_at),
            "reserved_amount": money(sum_("allocated_amount") - sum_("released_amount") - sum_("disbursed_amount")),
            "disbursed_amount": money(sum_("disbursed_amount")),
            "released_amount": money(sum_("released_amount")),
            "funding_sources": [{
                "id": s.id, "donor": {"id": s.donor_id, "name": donors.get(s.donor_id)} if include_donors else None,
                "donor_donation_id": s.donor_donation_id, "document_no": docs.get(s.donor_donation_id),
                "allocated_amount": money(s.allocated_amount), "disbursed_amount": money(s.disbursed_amount),
                "released_amount": money(s.released_amount), "funding_status": s.funding_status,
            } for s in sources] if include_donors else [],
        }

    def allocation_for_request(self, request_id):
        return self.db.scalar(select(NgoAllocation).where(NgoAllocation.ngo_request_id == request_id))

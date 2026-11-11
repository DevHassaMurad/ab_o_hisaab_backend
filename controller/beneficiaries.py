"""Householder and shopkeeper portals, the NGO beneficiary desk, PDMA oversight and the PDMA / NGO ledgers.

Money rules
  NGO net balance = transfers the NGO confirmed receiving (credit)
                    - verified expenditures (debit) - payments to beneficiaries (debit)
  An NGO pays a beneficiary only from that balance; the NGO row is locked while it pays, so two payments can
  never spend the same money. When the balance is short, the NGO raises an ordinary NGO request to PDMA.
  Requests are a decision, not money: only "paid" / "received" requests reduce the balance.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select

from config.fund_mail import render_notice
from config.outbox import enqueue
from controller.authentication import AuthFlowError
from controller.donation import DonationController, iso, money
from model.beneficiary_model import BeneficiaryFundRequest
from model.campaign_model import CampaignContribution, DonationCampaign
from model.donation_model import Ngo
from model.fund_model import DonationDisbursement, DonorDonation, NgoExpenditure
from model.notification_model import Notification
from model.registration_model import HouseholderDetails, ShopkeeperDetails
from model.role_model import Role
from model.user_model import User

ZERO = Decimal("0.00")
PKT = timedelta(hours=5)
OPEN = ("pending", "on_hold")
SPENT = ("paid", "received")
TYPE_FOR_ROLE = {"HOUSEHOLDER": "householder", "SHOPKEEPER": "shopkeeper"}
DETAILS = {"householder": HouseholderDetails, "shopkeeper": ShopkeeperDetails}
EDITABLE = {
    "common": ("phone", "state", "city", "postal_code", "address1", "address2"),
    "householder": ("adults", "children", "males", "females", "income_min", "income_max"),
    "shopkeeper": ("shop_name", "business_category", "revenue_min", "revenue_max"),
}


def now_utc():
    return datetime.utcnow()


def pkr(v):
    return f"PKR {Decimal(v):,.2f}"


def dec(v):
    return Decimal(v or 0)


# Balances ------------------------------------------------------------------------------------------
def ngo_figures(db, ngo_id):
    received = dec(db.scalar(select(func.sum(DonationDisbursement.amount)).where(
        DonationDisbursement.recipient_type == "ngo", DonationDisbursement.recipient_id == ngo_id,
        DonationDisbursement.receipt_confirmed_at.is_not(None))))
    invested = dec(db.scalar(select(func.sum(NgoExpenditure.amount)).where(
        NgoExpenditure.ngo_id == ngo_id, NgoExpenditure.verification_status == "verified")))
    paid = dec(db.scalar(select(func.sum(BeneficiaryFundRequest.paid_amount)).where(
        BeneficiaryFundRequest.ngo_id == ngo_id, BeneficiaryFundRequest.status.in_(SPENT))))
    pending = dec(db.scalar(select(func.sum(BeneficiaryFundRequest.requested_amount)).where(
        BeneficiaryFundRequest.ngo_id == ngo_id, BeneficiaryFundRequest.status.in_(OPEN))))
    return {"received": received, "invested": invested, "beneficiary_paid": paid,
            "net_balance": received - invested - paid, "open_beneficiary_requests": pending}


def figures_out(f):
    return {k: money(v) for k, v in f.items()}


class Base:
    def __init__(self, db, user):
        self.db = db
        self.user = user
        self.notes = []

    def _notify(self, user_ids, type_, title, message, related_id, related_type):
        for uid in dict.fromkeys(u for u in user_ids if u):
            n = Notification(user_id=uid, type=type_, title=title, message=message[:500],
                             related_id=related_id, related_type=related_type)
            self.db.add(n)
            self.db.flush()
            self.notes.append((uid, n.to_dict()))

    def _email(self, user, heading, intro, rows, path, link_text="Open request"):
        if user and user.email:
            enqueue(self.db, user.email, *render_notice(heading, "Fund request", user.name, intro, rows, path, link_text))

    def row(self, r, ngo=None, with_beneficiary=False):
        ngo = ngo or self.db.get(Ngo, r.ngo_id)
        out = {
            "id": r.id, "document_no": r.document_no, "beneficiary_type": r.beneficiary_type,
            "ngo": {"id": ngo.id, "name": ngo.name} if ngo else None, "purpose": r.purpose,
            "description": r.description, "requested_amount": money(r.requested_amount), "status": r.status,
            "paid_amount": money(r.paid_amount) if r.paid_amount is not None else None, "payment_no": r.payment_no,
            "payment_method": r.payment_method, "payment_reference": r.payment_reference,
            "decision_reason": r.decision_reason, "decided_at": iso(r.decided_at), "paid_at": iso(r.paid_at),
            "received_at": iso(r.received_at), "created_at": iso(r.created_at), "updated_at": iso(r.updated_at),
        }
        if with_beneficiary:
            u = self.db.get(User, r.user_id)
            d = self.db.get(DETAILS[r.beneficiary_type], r.user_id)
            out["beneficiary"] = {
                "id": u.id, "name": u.name, "email": u.email, "phone": d.phone if d else None,
                "city": d.city if d else None, "state": d.state if d else None,
                **({"household_size": d.household_size, "adults": d.adults, "children": d.children,
                    "males": d.males, "females": d.females, "income_min": money(d.income_min) if d.income_min is not None else None,
                    "income_max": money(d.income_max) if d.income_max is not None else None}
                   if d is not None and r.beneficiary_type == "householder" else {}),
                **({"shop_name": d.shop_name, "business_category": d.business_category,
                    "revenue_min": money(d.revenue_min) if d.revenue_min is not None else None,
                    "revenue_max": money(d.revenue_max) if d.revenue_max is not None else None}
                   if d is not None and r.beneficiary_type == "shopkeeper" else {}),
            }
        return out

    def _page(self, q, page, page_size, with_beneficiary=False):
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = list(self.db.scalars(q.order_by(BeneficiaryFundRequest.created_at.desc(), BeneficiaryFundRequest.id.desc())
                                    .offset((page - 1) * page_size).limit(page_size)))
        return {"items": [self.row(r, with_beneficiary=with_beneficiary) for r in rows], "total": int(total or 0),
                "page": page, "page_size": page_size}


# Householder / shopkeeper portal ------------------------------------------------------------------------
class BeneficiaryPortal(Base):
    def __init__(self, db, user, role):
        super().__init__(db, user)
        self.kind = TYPE_FOR_ROLE[role]

    def _details(self):
        d = self.db.get(DETAILS[self.kind], self.user.id)
        if d is None:
            raise AuthFlowError("PROFILE_MISSING", "Your registration profile was not found. Contact PDMA.", 404)
        return d

    def profile(self):
        d = self._details()
        out = {"user_type": self.kind, "name": self.user.name, "email": self.user.email,
               "identity_type": d.identity_type, "identity_number_masked": "•" * max(len(d.identity_number) - 4, 0)
               + d.identity_number[-4:], "country": d.country,
               "registration_status": self.user.registration_status}
        for k in EDITABLE["common"] + EDITABLE[self.kind]:
            v = getattr(d, k)
            out[k] = money(v) if isinstance(v, Decimal) else v
        if self.kind == "householder":
            out["household_size"] = d.household_size
        else:
            out["business_number"] = d.business_number
        return out

    def update_profile(self, data):
        d = self._details()
        allowed = set(EDITABLE["common"] + EDITABLE[self.kind])
        for k, v in data.items():
            if k in allowed:
                setattr(d, k, v)
        if self.kind == "householder":
            if None in (d.adults, d.children, d.males, d.females):
                raise AuthFlowError("HOUSEHOLD_INCOMPLETE", "Enter adults, children, males and females.", 422)
            if d.males + d.females != d.adults + d.children:
                raise AuthFlowError("HOUSEHOLD_MISMATCH", "Males plus females must equal adults plus children.", 422)
            d.household_size, d.dependents = d.adults + d.children, d.children
            lo, hi = d.income_min, d.income_max
        else:
            lo, hi = d.revenue_min, d.revenue_max
        if lo is not None and hi is not None and Decimal(hi) < Decimal(lo):
            raise AuthFlowError("RANGE_INVALID", "The maximum cannot be below the minimum.", 422)
        self.db.commit()
        return self.profile()

    def ngos(self):
        rows = self.db.scalars(select(Ngo).where(Ngo.is_active.is_(True)).order_by(Ngo.name))
        return [{"id": n.id, "name": n.name, "contact_email": n.contact_email, "contact_phone": n.contact_phone}
                for n in rows]

    def create_request(self, data):
        ngo = self.db.get(Ngo, data["ngo_id"])
        if ngo is None or not ngo.is_active:
            raise AuthFlowError("NGO_NOT_FOUND", "Choose an active NGO from the list.", 404)
        open_same = self.db.scalar(select(func.count()).select_from(BeneficiaryFundRequest).where(
            BeneficiaryFundRequest.user_id == self.user.id, BeneficiaryFundRequest.ngo_id == ngo.id,
            BeneficiaryFundRequest.status.in_(OPEN)))
        if open_same:
            raise AuthFlowError("REQUEST_ALREADY_OPEN",
                                "You already have an open request with this NGO. Wait for its decision first.", 409)
        r = BeneficiaryFundRequest(
            document_no=DonationController(self.db).next_document_no_for("BEN-REQ"), user_id=self.user.id,
            beneficiary_type=self.kind, ngo_id=ngo.id, purpose=data["purpose"], description=data["description"],
            requested_amount=data["amount"], status="pending")
        self.db.add(r)
        self.db.flush()
        if ngo.user_id:
            label = "Householder" if self.kind == "householder" else "Shopkeeper"
            self._notify([ngo.user_id], "beneficiary_request", f"New {label.lower()} fund request",
                         f"{self.user.name} requested {pkr(r.requested_amount)} ({r.document_no}).", r.id,
                         "beneficiary_request_ngo")
            self._email(self.db.get(User, ngo.user_id), f"New {label.lower()} fund request",
                        f"{self.user.name} has requested funds from {ngo.name}.",
                        [("Reference", r.document_no), ("Amount", pkr(r.requested_amount)),
                         ("Purpose", r.purpose.replace("_", " ").title())], f"/ngo/beneficiary-requests/{r.id}")
        self.db.commit()
        return self.row(r, ngo)

    def _own(self, request_id, lock=False):
        q = select(BeneficiaryFundRequest).where(BeneficiaryFundRequest.id == request_id,
                                                 BeneficiaryFundRequest.user_id == self.user.id)
        r = self.db.scalar(q.with_for_update() if lock else q)
        if r is None:
            raise AuthFlowError("REQUEST_NOT_FOUND", "This request does not exist.", 404)
        return r

    def list_requests(self, status=None, page=1, page_size=20):
        q = select(BeneficiaryFundRequest).where(BeneficiaryFundRequest.user_id == self.user.id)
        if status:
            q = q.where(BeneficiaryFundRequest.status == status)
        return self._page(q, page, page_size)

    def detail(self, request_id):
        return self.row(self._own(request_id))

    def cancel(self, request_id):
        r = self._own(request_id, lock=True)
        if r.status not in OPEN:
            raise AuthFlowError("NOT_CANCELLABLE", "Only a pending or on-hold request can be withdrawn.", 409)
        r.status = "cancelled"
        self.db.commit()
        return self.row(r)

    def confirm_receipt(self, request_id):
        r = self._own(request_id, lock=True)
        if r.status != "paid":
            raise AuthFlowError("NOT_PAID", "You can confirm receipt only after the NGO has paid.", 409)
        r.status, r.received_at = "received", now_utc()
        ngo = self.db.get(Ngo, r.ngo_id)
        if ngo and ngo.user_id:
            self._notify([ngo.user_id], "beneficiary_received", "Beneficiary confirmed receipt",
                         f"{self.user.name} confirmed receiving {pkr(r.paid_amount)} ({r.payment_no}).", r.id,
                         "beneficiary_request_ngo")
        self.db.commit()
        return self.row(r)

    def dashboard(self):
        rows = list(self.db.scalars(select(BeneficiaryFundRequest).where(BeneficiaryFundRequest.user_id == self.user.id)))

        def total(statuses, field="requested_amount"):
            return sum((dec(getattr(r, field)) for r in rows if r.status in statuses), ZERO)

        def n(statuses):
            return sum(1 for r in rows if r.status in statuses)

        recent = sorted(rows, key=lambda r: (r.created_at or datetime.min, r.id), reverse=True)[:5]
        return {
            "user_type": self.kind, "name": self.user.name,
            "requested_amount": money(total(OPEN + ("rejected", "paid", "received"))),
            "received_amount": money(total(SPENT, "paid_amount")),
            "awaiting_confirmation": money(total(("paid",), "paid_amount")),
            "pending_amount": money(total(("pending",))), "on_hold_amount": money(total(("on_hold",))),
            "rejected_amount": money(total(("rejected",))),
            "counts": {"total": len(rows), "pending": n(("pending",)), "on_hold": n(("on_hold",)),
                       "rejected": n(("rejected",)), "paid": n(("paid",)), "received": n(("received",)),
                       "cancelled": n(("cancelled",))},
            "recent": [self.row(r) for r in recent],
        }


# NGO desk ------------------------------------------------------------------------------------------------
class NgoBeneficiaryDesk(Base):
    def __init__(self, db, user):
        super().__init__(db, user)
        self.ngo = db.scalar(select(Ngo).where(Ngo.user_id == user.id))
        if self.ngo is None or not self.ngo.is_active:
            raise AuthFlowError("NGO_NOT_LINKED", "Your account is not linked to an active NGO.", 403)

    def _get(self, request_id, lock=False):
        q = select(BeneficiaryFundRequest).where(BeneficiaryFundRequest.id == request_id,
                                                 BeneficiaryFundRequest.ngo_id == self.ngo.id)
        r = self.db.scalar(q.with_for_update() if lock else q)
        if r is None:
            raise AuthFlowError("REQUEST_NOT_FOUND", "This request does not exist.", 404)
        return r

    def balance(self):
        return figures_out(ngo_figures(self.db, self.ngo.id))

    def list(self, status=None, kind=None, search=None, page=1, page_size=20):
        q = select(BeneficiaryFundRequest).where(BeneficiaryFundRequest.ngo_id == self.ngo.id)
        if status:
            q = q.where(BeneficiaryFundRequest.status == status)
        if kind:
            q = q.where(BeneficiaryFundRequest.beneficiary_type == kind)
        if search:
            like = f"%{search.strip()}%"
            q = q.join(User, User.id == BeneficiaryFundRequest.user_id).where(
                or_(BeneficiaryFundRequest.document_no.like(like), User.name.like(like)))
        out = self._page(q, page, page_size, with_beneficiary=True)
        f = ngo_figures(self.db, self.ngo.id)
        for i in out["items"]:
            i["can_pay_in_full"] = Decimal(i["requested_amount"]) <= f["net_balance"]
        out["balance"] = figures_out(f)
        return out

    def detail(self, request_id):
        out = self.row(self._get(request_id), self.ngo, with_beneficiary=True)
        out["balance"] = self.balance()
        return out

    def _tell(self, r, type_, title, message, email_intro, rows):
        u = self.db.get(User, r.user_id)
        self._notify([r.user_id], type_, title, message, r.id, "beneficiary_request")
        self._email(u, title, email_intro, rows, f"/beneficiary/requests/{r.id}")

    def hold(self, request_id, reason):
        r = self._get(request_id, lock=True)
        if r.status != "pending":
            raise AuthFlowError("INVALID_STATUS", "Only a pending request can be put on hold.", 409)
        r.status, r.decision_reason, r.decided_by, r.decided_at = "on_hold", reason, self.user.id, now_utc()
        self._tell(r, "beneficiary_request_on_hold", "Your fund request is on hold",
                   f"{self.ngo.name} put {r.document_no} on hold: {reason}", f"{self.ngo.name} put your request on hold.",
                   [("Reference", r.document_no), ("Reason", reason)])
        self.db.commit()
        return self.row(r, self.ngo, True)

    def reject(self, request_id, reason):
        r = self._get(request_id, lock=True)
        if r.status not in OPEN:
            raise AuthFlowError("INVALID_STATUS", "Only a pending or on-hold request can be rejected.", 409)
        r.status, r.decision_reason, r.decided_by, r.decided_at = "rejected", reason, self.user.id, now_utc()
        self._tell(r, "beneficiary_request_rejected", "Your fund request was rejected",
                   f"{self.ngo.name} rejected {r.document_no}: {reason}", f"{self.ngo.name} rejected your request.",
                   [("Reference", r.document_no), ("Reason", reason)])
        self.db.commit()
        return self.row(r, self.ngo, True)

    def pay(self, request_id, amount, method, reference):
        # Lock the NGO first, then the request: concurrent payments queue here and see each other's spending.
        self.db.scalar(select(Ngo).where(Ngo.id == self.ngo.id).with_for_update())
        r = self._get(request_id, lock=True)
        if r.status not in OPEN:
            raise AuthFlowError("INVALID_STATUS", "Only a pending or on-hold request can be paid.", 409)
        amount = Decimal(amount if amount is not None else r.requested_amount)
        if amount > Decimal(r.requested_amount):
            raise AuthFlowError("AMOUNT_TOO_HIGH", "You cannot pay more than the requested amount.", 422)
        balance = ngo_figures(self.db, self.ngo.id)["net_balance"]
        if amount > balance:
            raise AuthFlowError("INSUFFICIENT_BALANCE",
                                f"Your net balance is {pkr(balance)}; {pkr(amount - balance)} more is needed. "
                                "Request funds from PDMA first.", 409)
        r.status, r.paid_amount, r.paid_at = "paid", amount, now_utc()
        r.payment_method, r.payment_reference = method, reference
        r.decided_by, r.decided_at = self.user.id, now_utc()
        r.payment_no = DonationController(self.db).next_document_no_for("BEN-PAY")
        self._tell(r, "beneficiary_request_paid", "Your fund request was paid",
                   f"{self.ngo.name} paid {pkr(amount)} against {r.document_no} ({r.payment_no}). Please confirm receipt.",
                   f"{self.ngo.name} has paid your request. Please confirm once you receive the money.",
                   [("Reference", r.document_no), ("Payment", r.payment_no), ("Amount", pkr(amount))])
        self.db.commit()
        return self.row(r, self.ngo, True)

    def shortfall(self):
        """What the NGO would need from PDMA to pay all open beneficiary requests."""
        f = ngo_figures(self.db, self.ngo.id)
        return {"open_requests": money(f["open_beneficiary_requests"]), "net_balance": money(f["net_balance"]),
                "shortfall": money(max(f["open_beneficiary_requests"] - f["net_balance"], ZERO))}


# PDMA oversight -----------------------------------------------------------------------------------------
class BeneficiaryOversight(Base):
    def list(self, status=None, kind=None, ngo_id=None, search=None, page=1, page_size=20):
        q = select(BeneficiaryFundRequest)
        if status:
            q = q.where(BeneficiaryFundRequest.status == status)
        if kind:
            q = q.where(BeneficiaryFundRequest.beneficiary_type == kind)
        if ngo_id:
            q = q.where(BeneficiaryFundRequest.ngo_id == ngo_id)
        if search:
            like = f"%{search.strip()}%"
            q = q.join(User, User.id == BeneficiaryFundRequest.user_id).where(
                or_(BeneficiaryFundRequest.document_no.like(like), User.name.like(like)))
        out = self._page(q, page, page_size, with_beneficiary=True)
        rows = self.db.execute(select(BeneficiaryFundRequest.status, func.count(), func.sum(BeneficiaryFundRequest.requested_amount))
                               .group_by(BeneficiaryFundRequest.status)).all()
        out["totals"] = {s: {"count": c, "amount": money(a)} for s, c, a in rows}
        return out

    def detail(self, request_id):
        r = self.db.get(BeneficiaryFundRequest, request_id)
        if r is None:
            raise AuthFlowError("REQUEST_NOT_FOUND", "This request does not exist.", 404)
        return self.row(r, with_beneficiary=True)

    def ngo_balances(self):
        out = []
        for n in self.db.scalars(select(Ngo).order_by(Ngo.name)):
            f = ngo_figures(self.db, n.id)
            out.append({"id": n.id, "name": n.name, "is_active": n.is_active, **figures_out(f),
                        "shortfall": money(max(f["open_beneficiary_requests"] - f["net_balance"], ZERO))})
        return {"items": out, "total": len(out)}


# Ledgers ------------------------------------------------------------------------------------------------
def _range(date_from, date_to):
    start = datetime.combine(date_from, datetime.min.time()) - PKT if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), datetime.min.time()) - PKT if date_to else None
    return start, end


def _finish(entries, date_from, date_to, page, page_size, title, extra=None):
    """Sort, compute the running balance (including everything before the period) and page."""
    entries.sort(key=lambda e: (e["at"], e["sort"]))
    start, end = _range(date_from, date_to)
    opening = sum((e["credit"] - e["debit"] for e in entries if start and e["at"] < start), ZERO)
    period = [e for e in entries if (not start or e["at"] >= start) and (not end or e["at"] < end)]
    bal = opening
    for e in period:
        bal += e["credit"] - e["debit"]
        e["balance"] = bal
    credits = sum((e["credit"] for e in period), ZERO)
    debits = sum((e["debit"] for e in period), ZERO)
    shown = list(reversed(period))  # newest first on screen
    items = [{"date": iso(e["at"]), "reference": e["reference"], "type": e["type"], "description": e["description"],
              "counterparty": e.get("counterparty"), "credit": money(e["credit"]) if e["credit"] else None,
              "debit": money(e["debit"]) if e["debit"] else None, "balance": money(e["balance"])}
             for e in shown[(page - 1) * page_size: page * page_size]] if page else None
    return {"title": title, "period": {"from": iso(date_from), "to": iso(date_to)},
            "opening_balance": money(opening), "total_credit": money(credits), "total_debit": money(debits),
            "closing_balance": money(opening + credits - debits), "items": items, "total": len(period),
            "page": page, "page_size": page_size, "all": shown, **(extra or {})}


def pdma_ledger(db, date_from=None, date_to=None, page=1, page_size=50, include_campaigns=True):
    entries = []
    for d, donor in db.execute(select(DonorDonation, User.name).join(User, User.id == DonorDonation.donor_id)
                               .where(DonorDonation.payment_status == "verified")).all():
        entries.append({"at": d.verified_at or d.received_at or d.created_at, "sort": f"a{d.id:09d}",
                        "reference": d.document_no, "type": "donor_donation", "counterparty": donor,
                        "description": "Donor contribution received", "credit": dec(d.amount), "debit": ZERO})
    if include_campaigns:
        for c, camp, donor in db.execute(
                select(CampaignContribution, DonationCampaign.reference_no, User.name)
                .join(DonationCampaign, DonationCampaign.id == CampaignContribution.campaign_id)
                .join(User, User.id == CampaignContribution.donor_id)
                .where(CampaignContribution.payment_status == "verified")).all():
            entries.append({"at": c.verified_at or c.created_at, "sort": f"b{c.id:09d}", "reference": c.reference_no,
                            "type": "campaign_contribution", "counterparty": donor,
                            "description": f"Campaign contribution ({camp})", "credit": dec(c.amount), "debit": ZERO})
    ngo_names = dict(db.execute(select(Ngo.id, Ngo.name)).all())
    for x in db.scalars(select(DonationDisbursement).where(DonationDisbursement.status.in_(("sent", "received")))):
        who = ngo_names.get(x.recipient_id) if x.recipient_type == "ngo" else x.recipient_type.title()
        entries.append({"at": x.sent_at or x.created_at, "sort": f"c{x.id:09d}", "reference": x.document_no,
                        "type": "disbursement", "counterparty": who,
                        "description": "Funds sent to NGO" + (" (receipt confirmed)" if x.receipt_confirmed_at else ""),
                        "credit": ZERO, "debit": dec(x.amount)})
    pending = dec(db.scalar(select(func.sum(DonationDisbursement.amount)).where(DonationDisbursement.status == "pending")))
    return _finish(entries, date_from, date_to, page, page_size, "PDMA Punjab — Funds Ledger",
                   {"pending_transfers": money(pending)})


def ngo_ledger(db, ngo, date_from=None, date_to=None, page=1, page_size=50):
    entries = []
    for x in db.scalars(select(DonationDisbursement).where(
            DonationDisbursement.recipient_type == "ngo", DonationDisbursement.recipient_id == ngo.id,
            DonationDisbursement.receipt_confirmed_at.is_not(None))):
        entries.append({"at": x.receipt_confirmed_at, "sort": f"a{x.id:09d}", "reference": x.document_no,
                        "type": "transfer_received", "counterparty": "PDMA Punjab",
                        "description": "Funds received from PDMA", "credit": dec(x.amount), "debit": ZERO})
    for e in db.scalars(select(NgoExpenditure).where(NgoExpenditure.ngo_id == ngo.id,
                                                     NgoExpenditure.verification_status == "verified")):
        at = datetime.combine(e.expense_date, datetime.min.time()) - PKT + timedelta(hours=12)
        entries.append({"at": at, "sort": f"b{e.id:09d}", "reference": e.reference, "type": "expenditure",
                        "counterparty": (e.expense_category or "").replace("_", " ").title() or None,
                        "description": e.purpose, "credit": ZERO, "debit": dec(e.amount)})
    for r, name in db.execute(select(BeneficiaryFundRequest, User.name).join(User, User.id == BeneficiaryFundRequest.user_id)
                              .where(BeneficiaryFundRequest.ngo_id == ngo.id,
                                     BeneficiaryFundRequest.status.in_(SPENT))).all():
        entries.append({"at": r.paid_at, "sort": f"c{r.id:09d}", "reference": r.payment_no, "type": "beneficiary_payment",
                        "counterparty": name,
                        "description": f"Paid {r.beneficiary_type} request {r.document_no}"
                        + (" (receipt confirmed)" if r.status == "received" else ""),
                        "credit": ZERO, "debit": dec(r.paid_amount)})
    return _finish(entries, date_from, date_to, page, page_size, f"{ngo.name} — Funds Ledger")


def staff_role_ids(db):
    return list(db.scalars(select(Role.id).where(Role.name.in_(("ADMIN", "PDMA_OFFICER")))))

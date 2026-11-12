"""Campaign finance: master batch -> NGO collaboration -> NGO sub-batch -> releases -> NGO distribution campaign ->
beneficiary distribution, with a double-entry ledger as the source of truth.

Accounts (balance = credits - debits; every posting is balanced):
  donor_receipts:{campaign}        clearing side of verified donor money (balance = -collected)
  campaign_funds:{campaign}        master batch: verified money not yet released to an NGO
  ngo_in_transit:{campaign}:{ngo}  released by PDMA, not yet acknowledged by the NGO
  ngo_batch_funds:{batch}          acknowledged by the NGO, not yet distributed
  beneficiary_payments:{batch}     distributed to beneficiaries (expenditure)
Postings
  contribution verified   Dr donor_receipts      Cr campaign_funds
  release (transfer)      Dr campaign_funds      Cr ngo_in_transit
  release cancelled       Dr ngo_in_transit      Cr campaign_funds      (reversal, original kept)
  release acknowledged    Dr ngo_in_transit      Cr ngo_batch_funds
  distribution            Dr ngo_batch_funds     Cr beneficiary_payments
  distribution reversed   Dr beneficiary_paym.   Cr ngo_batch_funds     (reversal, original kept)
A child (NGO) batch never holds extra money: releasing moves balance out of the master batch.

Donor attribution is exact, never proportional:
  a release takes money from specific verified contributions, oldest verification first
  (campaign_release_sources); a distribution takes money from the batch's acknowledged releases, oldest first,
  and inside each release from its recorded contribution splits (distribution_funding_sources).
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select

from config.fund_mail import render_notice
from config.outbox import enqueue
from controller.authentication import AuthFlowError
from controller.donation import DonationController, iso, money
from model.campaign_finance_model import (
    BeneficiaryDistribution,
    CampaignCollaboration,
    CollaborationCampaign,
    CampaignFundRelease,
    CampaignMasterBatch,
    CampaignReleaseSource,
    DistributionBeneficiary,
    DistributionFundingSource,
    FinanceAccount,
    FinanceLedgerEntry,
    NgoCampaignBatch,
    NgoDistributionCampaign,
)
from model.campaign_model import CampaignContribution, DonationCampaign
from model.donation_model import Ngo
from model.notification_model import Notification
from model.registration_model import HouseholderDetails, ShopkeeperDetails
from model.role_model import Role
from model.user_model import User

ZERO = Decimal("0.00")
PKT = timedelta(hours=5)
LIVE_COLLAB = ("draft", "invited", "accepted", "completed")
FUNDABLE_CAMPAIGN = ("scheduled", "active", "goal_reached", "completed", "paused")
BEN_ROLES = {"HOUSEHOLDER": "householder", "SHOPKEEPER": "shopkeeper"}
DETAILS = {"householder": HouseholderDetails, "shopkeeper": ShopkeeperDetails}


def now_utc():
    return datetime.utcnow()


def dec(v):
    return Decimal(v or 0)


def pkr(v):
    return f"PKR {Decimal(v):,.2f}"


def doc_no(db, prefix):
    return DonationController(db).next_document_no_for(prefix)


# Ledger --------------------------------------------------------------------------------------------------
def account(db, kind, campaign=None, ngo=None, batch=None):
    code = {"donor_receipts": lambda: f"DONOR-RECEIPTS:{campaign.id}",
            "campaign_funds": lambda: f"CAMPAIGN-FUNDS:{campaign.id}",
            "ngo_in_transit": lambda: f"NGO-IN-TRANSIT:{campaign.id}:{ngo.id}",
            "ngo_batch_funds": lambda: f"NGO-BATCH-FUNDS:{batch.id}",
            "beneficiary_payments": lambda: f"BENEFICIARY-PAYMENTS:{batch.id}"}[kind]()
    acc = db.scalar(select(FinanceAccount).where(FinanceAccount.code == code))
    if acc is None:
        names = {"donor_receipts": lambda: f"Donor receipts — {campaign.reference_no}",
                 "campaign_funds": lambda: f"Campaign funds (master batch) — {campaign.reference_no}",
                 "ngo_in_transit": lambda: f"Funds in transit — {ngo.name} — {campaign.reference_no}",
                 "ngo_batch_funds": lambda: f"NGO batch funds — {batch.reference_no}",
                 "beneficiary_payments": lambda: f"Beneficiary payments — {batch.reference_no}"}
        acc = FinanceAccount(code=code, kind=kind, name=names[kind]()[:200], campaign_id=campaign.id if campaign else None,
                             ngo_id=ngo.id if ngo else (batch.ngo_id if batch else None),
                             batch_id=batch.id if batch else None)
        db.add(acc)
        db.flush()
    return acc


def post(db, txn_ref, lines, event_type, source_type, source_id, campaign_id, memo=None, by=None, reversal_of=None):
    """Post one balanced transaction. Idempotent: a txn_ref that is already posted is not posted again."""
    if db.scalar(select(FinanceLedgerEntry.id).where(FinanceLedgerEntry.txn_ref == txn_ref).limit(1)):
        return False
    debit = sum((dec(d) for _, d, _ in lines), ZERO)
    credit = sum((dec(c) for _, _, c in lines), ZERO)
    if debit != credit or debit <= 0:
        raise AuthFlowError("LEDGER_UNBALANCED", "Internal error: the ledger posting does not balance.", 500)
    at = now_utc()
    for acc, d, c in lines:
        db.add(FinanceLedgerEntry(txn_ref=txn_ref, account_id=acc.id, debit=dec(d), credit=dec(c), event_type=event_type,
                                  source_type=source_type, source_id=source_id, campaign_id=campaign_id,
                                  memo=(memo or "")[:300] or None, reversal_of=reversal_of, posted_at=at, posted_by=by))
    db.flush()
    return True


def balance(db, acc):
    if acc is None:
        return ZERO
    c, d = db.execute(select(func.coalesce(func.sum(FinanceLedgerEntry.credit), 0),
                             func.coalesce(func.sum(FinanceLedgerEntry.debit), 0))
                      .where(FinanceLedgerEntry.account_id == acc.id)).one()
    return dec(c) - dec(d)


def find_account(db, code):
    return db.scalar(select(FinanceAccount).where(FinanceAccount.code == code))


# Master batch & contributions -------------------------------------------------------------------------
def master_batch(db, campaign):
    mb = db.scalar(select(CampaignMasterBatch).where(CampaignMasterBatch.campaign_id == campaign.id))
    if mb is None:
        mb = CampaignMasterBatch(reference_no=doc_no(db, "CMP-BATCH"), campaign_id=campaign.id)
        db.add(mb)
        db.flush()
    return mb


def post_contribution(db, contribution, campaign, by=None):
    """Called when PDMA verifies a campaign contribution (once; the txn_ref makes it idempotent)."""
    master_batch(db, campaign)
    post(db, f"CON-{contribution.id}",
         [(account(db, "donor_receipts", campaign), contribution.amount, 0),
          (account(db, "campaign_funds", campaign), 0, contribution.amount)],
         "contribution_verified", "campaign_contribution", contribution.id, campaign.id,
         f"Verified contribution {contribution.reference_no}", by)


def backfill(db, campaign):
    """Post any verified contribution that predates the ledger (idempotent)."""
    for x in db.scalars(select(CampaignContribution).where(CampaignContribution.campaign_id == campaign.id,
                                                           CampaignContribution.payment_status == "verified")):
        post_contribution(db, x, campaign)


def contribution_remaining(db, campaign_id):
    """{contribution: unreleased amount} for verified contributions, oldest verification first."""
    released = dict(db.execute(
        select(CampaignReleaseSource.contribution_id, func.sum(CampaignReleaseSource.amount))
        .join(CampaignFundRelease, CampaignFundRelease.id == CampaignReleaseSource.release_id)
        .where(CampaignFundRelease.status != "cancelled")
        .group_by(CampaignReleaseSource.contribution_id)).all())
    rows = db.scalars(select(CampaignContribution).where(
        CampaignContribution.campaign_id == campaign_id, CampaignContribution.payment_status == "verified")
        .order_by(CampaignContribution.verified_at, CampaignContribution.id))
    return [(x, dec(x.amount) - dec(released.get(x.id))) for x in rows]


# Notifications -------------------------------------------------------------------------------------------
class Notes:
    def __init__(self, db):
        self.db = db
        self.items = []

    def add(self, user_ids, type_, title, message, related_id, related_type):
        for uid in dict.fromkeys(u for u in user_ids if u):
            n = Notification(user_id=uid, type=type_, title=title, message=message[:500], related_id=related_id,
                             related_type=related_type)
            self.db.add(n)
            self.db.flush()
            self.items.append((uid, n.to_dict()))

    def email(self, user, heading, intro, rows, path, label="Campaign finance"):
        if user and user.email:
            enqueue(self.db, user.email, *render_notice(heading, label, user.name, intro, rows, path, "Open"))


def staff_ids(db):
    roles = select(Role.id).where(Role.name.in_(("ADMIN", "PDMA_OFFICER")))
    return list(db.scalars(select(User.id).where(User.role_id.in_(roles), User.is_active.is_(True))))


# Service --------------------------------------------------------------------------------------------------
class CampaignFinance:
    def __init__(self, db, user, role):
        self.db = db
        self.user = user
        self.role = role
        self.notes = Notes(db)
        self._ngo = None

    @property
    def staff(self):
        return self.role in ("ADMIN", "PDMA_OFFICER")

    def ngo(self):
        if self._ngo is None:
            self._ngo = self.db.scalar(select(Ngo).where(Ngo.user_id == self.user.id))
            if self._ngo is None or not self._ngo.is_active:
                raise AuthFlowError("NGO_NOT_LINKED", "Your account is not linked to an active NGO.", 403)
        return self._ngo

    def _campaign(self, campaign_id, lock=False):
        q = select(DonationCampaign).where(DonationCampaign.id == campaign_id)
        c = self.db.scalar(q.with_for_update() if lock else q)
        if c is None:
            raise AuthFlowError("CAMPAIGN_NOT_FOUND", "This campaign does not exist.", 404)
        return c

    def _collab(self, collab_id, lock=False):
        q = select(CampaignCollaboration).where(CampaignCollaboration.id == collab_id)
        if self.role == "NGO":
            q = q.where(CampaignCollaboration.ngo_id == self.ngo().id, CampaignCollaboration.status != "draft")
        elif not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        c = self.db.scalar(q.with_for_update() if lock else q)
        if c is None:
            raise AuthFlowError("COLLABORATION_NOT_FOUND", "This collaboration does not exist.", 404)
        return c

    def _batch_for(self, collab):
        return self.db.scalar(select(NgoCampaignBatch).where(NgoCampaignBatch.collaboration_id == collab.id))

    # Figures ------------------------------------------------------------------------------------------
    def collab_figures(self, collab):
        rel = dict(self.db.execute(select(CampaignFundRelease.status, func.sum(CampaignFundRelease.amount))
                                   .join(NgoCampaignBatch, NgoCampaignBatch.id == CampaignFundRelease.batch_id)
                                   .where(NgoCampaignBatch.collaboration_id == collab.id)
                                   .group_by(CampaignFundRelease.status)).all())
        in_transit, received = dec(rel.get("in_transit")), dec(rel.get("acknowledged"))
        batch = self._batch_for(collab)
        distributed = ZERO
        available = ZERO
        if batch:
            available = balance(self.db, find_account(self.db, f"NGO-BATCH-FUNDS:{batch.id}"))
            distributed = balance(self.db, find_account(self.db, f"BENEFICIARY-PAYMENTS:{batch.id}"))
        released = in_transit + received
        return {"assigned": dec(collab.assigned_amount), "released": released, "in_transit": in_transit,
                "received": received, "distributed": distributed, "available": available,
                "unreleased_assignment": max(dec(collab.assigned_amount) - released, ZERO)}

    def campaign_figures(self, campaign):
        backfill(self.db, campaign)
        collected = -balance(self.db, find_account(self.db, f"DONOR-RECEIPTS:{campaign.id}"))
        master_available = balance(self.db, find_account(self.db, f"CAMPAIGN-FUNDS:{campaign.id}"))
        collabs = list(self.db.scalars(select(CampaignCollaboration).where(CampaignCollaboration.campaign_id == campaign.id)))
        per = [(c, self.collab_figures(c)) for c in collabs]
        live = [(c, f) for c, f in per if c.status in LIVE_COLLAB]
        in_transit = sum((f["in_transit"] for _, f in per), ZERO)
        ngo_held = sum((f["available"] for _, f in per), ZERO)
        distributed = sum((f["distributed"] for _, f in per), ZERO)
        assigned = sum((f["assigned"] for c, f in live if c.status != "draft"), ZERO)
        reserved = sum((f["unreleased_assignment"] for c, f in live if c.status == "accepted"), ZERO)
        return {
            "collected": collected, "master_available": master_available, "assigned": assigned,
            "reserved_for_accepted": min(reserved, master_available), "released": in_transit + ngo_held + distributed,
            "in_transit": in_transit, "received": sum((f["received"] for _, f in per), ZERO), "ngo_held": ngo_held,
            "distributed": distributed, "unspent": master_available + in_transit + ngo_held,
            "reconciles": collected == master_available + in_transit + ngo_held + distributed,
            "per": per,
        }

    def _fig_out(self, f):
        return {k: (money(v) if isinstance(v, Decimal) else v) for k, v in f.items() if k != "per"}

    def collab_row(self, c, detail=False):
        ngo = self.db.get(Ngo, c.ngo_id)
        camp = self.db.get(DonationCampaign, c.campaign_id) if c.campaign_id else None
        parent = self.db.get(CollaborationCampaign, c.collaboration_campaign_id) if c.collaboration_campaign_id else None
        batch = self._batch_for(c)
        out = {"id": c.id, "reference_no": c.reference_no, "status": c.status,
               "collaboration_campaign": ({"id": parent.id, "reference_no": parent.reference_no, "title": parent.title,
                                           "objective": parent.objective, "start_date": iso(parent.start_date),
                                           "end_date": iso(parent.end_date), "status": parent.status}
                                          if parent else None),
               "campaign": {"id": camp.id, "reference_no": camp.reference_no, "name": camp.name} if camp else None,
               "funded": camp is not None, "scope_of_work": c.scope_of_work, "instructions": c.instructions,
               "distribution_campaigns": self.db.scalar(select(func.count()).select_from(NgoDistributionCampaign).where(
                   NgoDistributionCampaign.collaboration_id == c.id)),
               "ngo": {"id": ngo.id, "name": ngo.name}, "district": c.district, "operational_area": c.operational_area,
               "responsibility": c.responsibility, "start_date": iso(c.start_date),
               "expected_completion": iso(c.expected_completion), "remarks": c.remarks, "invited_at": iso(c.invited_at),
               "responded_at": iso(c.responded_at), "response_note": c.response_note,
               "batch_reference": batch.reference_no if batch else None, "batch_id": batch.id if batch else None,
               "created_at": iso(c.created_at), **self._fig_out(self.collab_figures(c))}
        if detail:
            out["releases"] = [self.release_row(r) for r in self.db.scalars(
                select(CampaignFundRelease).where(CampaignFundRelease.batch_id == (batch.id if batch else -1))
                .order_by(CampaignFundRelease.id))]
            mb = (self.db.scalar(select(CampaignMasterBatch).where(CampaignMasterBatch.campaign_id == camp.id))
                  if camp else None)
            out["master_batch_reference"] = mb.reference_no if mb else None
            out["distribution_campaign_list"] = [self.dist_row(d) for d in self.db.scalars(
                select(NgoDistributionCampaign).where(NgoDistributionCampaign.collaboration_id == c.id)
                .order_by(NgoDistributionCampaign.id))]
        return out

    def release_row(self, r):
        out = {"id": r.id, "reference_no": r.reference_no, "amount": money(r.amount), "status": r.status,
               "payment_method": r.payment_method, "transfer_reference": r.transfer_reference, "remarks": r.remarks,
               "released_at": iso(r.released_at), "acknowledged_at": iso(r.acknowledged_at),
               "cancelled_at": iso(r.cancelled_at), "cancel_reason": r.cancel_reason}
        if self.staff:
            out["sources"] = [{"contribution_reference": ref, "amount": money(a)} for ref, a in self.db.execute(
                select(CampaignContribution.reference_no, CampaignReleaseSource.amount)
                .join(CampaignContribution, CampaignContribution.id == CampaignReleaseSource.contribution_id)
                .where(CampaignReleaseSource.release_id == r.id).order_by(CampaignReleaseSource.id)).all()]
        return out

    # PDMA: campaign finance overview ---------------------------------------------------------------------
    def campaign_overview(self, campaign_id):
        if not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        camp = self._campaign(campaign_id)
        f = self.campaign_figures(camp)
        self.db.commit()
        mb = self.db.scalar(select(CampaignMasterBatch).where(CampaignMasterBatch.campaign_id == camp.id))
        contributions = [{"reference_no": x.reference_no, "amount": money(x.amount), "verified_at": iso(x.verified_at),
                          "unreleased": money(rem), "donor": self.db.get(User, x.donor_id).name}
                         for x, rem in contribution_remaining(self.db, camp.id)]
        return {"campaign": {"id": camp.id, "reference_no": camp.reference_no, "name": camp.name,
                             "district": camp.district, "status": camp.status, "target_amount": money(camp.target_amount)},
                "master_batch_reference": mb.reference_no if mb else None, **self._fig_out(f),
                "collaborations": [self.collab_row(c) for c, _ in f["per"]], "contributions": contributions}

    # PDMA: collaborations ---------------------------------------------------------------------------------
    def create_collaboration(self, campaign_id, data, send=True):
        if not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        camp = self._campaign(campaign_id, lock=True)
        if camp.status not in FUNDABLE_CAMPAIGN:
            raise AuthFlowError("CAMPAIGN_NOT_PUBLISHED", "NGOs can be added only to a published campaign.", 409)
        ngo = self.db.get(Ngo, data["ngo_id"])
        if ngo is None or not ngo.is_active:
            raise AuthFlowError("NGO_NOT_FOUND", "Choose an active registered NGO.", 404)
        if self.db.scalar(select(CampaignCollaboration.id).where(
                CampaignCollaboration.campaign_id == camp.id, CampaignCollaboration.ngo_id == ngo.id,
                CampaignCollaboration.status.in_(LIVE_COLLAB))):
            raise AuthFlowError("ALREADY_COLLABORATING", "This NGO is already a collaborator on this campaign.", 409)
        backfill(self.db, camp)
        assigned = dec(self.db.scalar(select(func.sum(CampaignCollaboration.assigned_amount)).where(
            CampaignCollaboration.campaign_id == camp.id, CampaignCollaboration.status.in_(LIVE_COLLAB))))
        collected = -balance(self.db, find_account(self.db, f"DONOR-RECEIPTS:{camp.id}"))
        ceiling = max(dec(camp.target_amount), collected)
        if assigned + dec(data["assigned_amount"]) > ceiling:
            raise AuthFlowError("ASSIGNMENT_EXCEEDS_CAMPAIGN",
                                f"Assignments would total {pkr(assigned + dec(data['assigned_amount']))}, above the campaign's "
                                f"{pkr(ceiling)}.", 409)
        parent = CollaborationCampaign(
            reference_no=doc_no(self.db, "COLC"), title=f"{camp.name} - NGO collaboration"[:200],
            description=camp.description[:4000], objective=camp.objective[:1000], districts=data["district"],
            start_date=camp.start_date, end_date=camp.end_date, funding_campaign_id=camp.id,
            status="launched" if send else "draft", launched_at=now_utc() if send else None, created_by=self.user.id)
        self.db.add(parent)
        self.db.flush()
        c = CampaignCollaboration(reference_no=doc_no(self.db, "COL"), campaign_id=camp.id, ngo_id=ngo.id,
                                  collaboration_campaign_id=parent.id, created_by=self.user.id, status="draft",
                                  **{k: data.get(k) for k in ("assigned_amount", "district", "operational_area",
                                                              "responsibility", "start_date", "expected_completion",
                                                              "remarks")})
        self.db.add(c)
        self.db.flush()
        if send:
            self._invite(c, camp, ngo)
        self.db.commit()
        return self.collab_row(c, True)

    def _invite(self, c, camp, ngo):
        """camp: the parent collaboration campaign or (legacy) the fundraising campaign — anything with a title."""
        c.status, c.invited_at = "invited", now_utc()
        title = getattr(camp, "title", None) or camp.name
        alloc = f" with a proposed allocation of {pkr(c.assigned_amount)}" if c.assigned_amount else ""
        msg = f"PDMA has invited {ngo.name} to participate in {title} ({camp.reference_no}) for {c.district}{alloc}."
        self.notes.add([ngo.user_id], "collaboration_invited", "Collaboration campaign invitation", msg, c.id,
                       "ngo_collaboration")
        rows = [("Invitation", c.reference_no), ("Campaign", f"{title} ({camp.reference_no})"), ("District", c.district),
                ("Responsibilities", c.responsibility)]
        if c.assigned_amount:
            rows.append(("Proposed allocation", pkr(c.assigned_amount)))
        self.notes.email(self.db.get(User, ngo.user_id) if ngo.user_id else None, "Collaboration campaign invitation",
                         msg, rows, f"/ngo/collaborations/{c.id}")

    def invite(self, collab_id):
        c = self._collab(collab_id, lock=True)
        if c.status != "draft":
            raise AuthFlowError("INVALID_STATUS", "Only a draft collaboration can be sent.", 409)
        parent = self.db.get(CollaborationCampaign, c.collaboration_campaign_id) if c.collaboration_campaign_id else None
        self._invite(c, parent or self.db.get(DonationCampaign, c.campaign_id), self.db.get(Ngo, c.ngo_id))
        self.db.commit()
        return self.collab_row(c, True)

    def respond(self, collab_id, accept, note=None):
        if self.role != "NGO":
            raise AuthFlowError("FORBIDDEN", "Only the invited NGO can respond.", 403)
        c = self._collab(collab_id, lock=True)
        if c.status != "invited":
            raise AuthFlowError("INVALID_STATUS", "This invitation is no longer awaiting a response.", 409)
        parent = self.db.get(CollaborationCampaign, c.collaboration_campaign_id) if c.collaboration_campaign_id else None
        camp = self.db.get(DonationCampaign, c.campaign_id) if c.campaign_id else None
        c.status = "accepted" if accept else "rejected"
        c.responded_at, c.responded_by, c.response_note = now_utc(), self.user.id, note
        if accept and camp is not None:
            self._ensure_batch(c, camp)
        word = "accepted" if accept else "rejected"
        title = parent.title if parent else camp.name
        self.notes.add(staff_ids(self.db), f"collaboration_{word}", f"NGO {word} the collaboration",
                       f"{self.ngo().name} {word} {c.reference_no} on {title}." + (f" Reason: {note}" if note else ""),
                       parent.id if parent else c.id, "collaboration_campaign" if parent else "campaign_collaboration")
        self.db.commit()
        return self.collab_row(c, True)

    def cancel_collaboration(self, collab_id, reason):
        c = self._collab(collab_id, lock=True)
        if c.status not in ("draft", "invited", "accepted"):
            raise AuthFlowError("INVALID_STATUS", "This collaboration cannot be cancelled.", 409)
        f = self.collab_figures(c)
        if f["released"] > 0:
            raise AuthFlowError("FUNDS_RELEASED", "Funds were already released to this NGO; cancel open releases first "
                                                  "or complete the collaboration.", 409)
        c.status, c.response_note = "cancelled", reason
        ngo = self.db.get(Ngo, c.ngo_id)
        self.notes.add([ngo.user_id], "collaboration_cancelled", "Collaboration cancelled",
                       f"PDMA cancelled collaboration {c.reference_no}. Reason: {reason}", c.id, "ngo_collaboration")
        self.db.commit()
        return self.collab_row(c, True)

    def list_collaborations(self, campaign_id=None, status=None, page=1, page_size=20):
        q = select(CampaignCollaboration)
        if self.role == "NGO":
            q = q.where(CampaignCollaboration.ngo_id == self.ngo().id, CampaignCollaboration.status != "draft")
        elif not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        if campaign_id:
            q = q.where(CampaignCollaboration.campaign_id == campaign_id)
        if status:
            q = q.where(CampaignCollaboration.status == status)
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = self.db.scalars(q.order_by(CampaignCollaboration.id.desc()).offset((page - 1) * page_size).limit(page_size))
        return {"items": [self.collab_row(c) for c in rows], "total": int(total or 0), "page": page, "page_size": page_size}

    def collaboration(self, collab_id):
        return self.collab_row(self._collab(collab_id), True)

    # PDMA: releases (exact attribution) ---------------------------------------------------------------
    def release(self, collab_id, amount, method, reference, remarks, idempotency_key=None):
        if not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        c = self._collab(collab_id)
        if not c.campaign_id or not c.assigned_amount:
            raise AuthFlowError("NO_FUNDING_LINK", "Link this participation to a fundraising campaign and allocation "
                                                   "before releasing funds.", 409)
        camp = self._campaign(c.campaign_id, lock=True)  # serialises every release of this campaign
        c = self._collab(collab_id, lock=True)
        if c.status != "accepted":
            raise AuthFlowError("NOT_ACCEPTED", "Funds can be released only after the NGO accepts the collaboration.", 409)
        if idempotency_key:
            prior = self.db.scalar(select(CampaignFundRelease).where(CampaignFundRelease.remarks == f"idem:{idempotency_key}"))
            if prior:
                return self.release_row(prior)
        backfill(self.db, camp)
        amount = dec(amount)
        f = self.collab_figures(c)
        if amount > f["unreleased_assignment"]:
            raise AuthFlowError("EXCEEDS_ASSIGNMENT",
                                f"Only {pkr(f['unreleased_assignment'])} of this NGO's assignment is still unreleased.", 409)
        available = balance(self.db, account(self.db, "campaign_funds", camp))
        if amount > available:
            raise AuthFlowError("INSUFFICIENT_CAMPAIGN_FUNDS",
                                f"The master batch has {pkr(available)} of verified, unreleased money.", 409)
        batch = self._batch_for(c)
        ngo = self.db.get(Ngo, c.ngo_id)
        r = CampaignFundRelease(reference_no=doc_no(self.db, "REL"), batch_id=batch.id, amount=amount, status="in_transit",
                                payment_method=method, transfer_reference=reference,
                                remarks=(f"idem:{idempotency_key}" if idempotency_key else remarks),
                                released_by=self.user.id, released_at=now_utc())
        self.db.add(r)
        self.db.flush()
        need = amount
        for x, rem in contribution_remaining(self.db, camp.id):
            if need <= 0:
                break
            take = min(rem, need)
            if take > 0:
                self.db.add(CampaignReleaseSource(release_id=r.id, contribution_id=x.id, amount=take))
                need -= take
        if need > 0:  # ledger and source records disagree: refuse rather than invent attribution
            raise AuthFlowError("ATTRIBUTION_SHORTFALL", "Verified contributions do not cover this release.", 409)
        post(self.db, f"REL-{r.id}", [(account(self.db, "campaign_funds", camp), amount, 0),
                                      (account(self.db, "ngo_in_transit", camp, ngo), 0, amount)],
             "release_sent", "campaign_release", r.id, camp.id, f"{r.reference_no} to {ngo.name}", self.user.id)
        self.notes.add([ngo.user_id], "campaign_funds_released", "Campaign funds released",
                       f"PDMA released {pkr(amount)} ({r.reference_no}) to {batch.reference_no} for {camp.name}. "
                       "Confirm receipt once the money arrives.", c.id, "ngo_collaboration")
        self.db.commit()
        return self.release_row(r)

    def cancel_release(self, release_id, reason):
        if not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        r = self.db.scalar(select(CampaignFundRelease).where(CampaignFundRelease.id == release_id).with_for_update())
        if r is None:
            raise AuthFlowError("RELEASE_NOT_FOUND", "This release does not exist.", 404)
        if r.status != "in_transit":
            raise AuthFlowError("INVALID_STATUS", "Only a release still in transit can be cancelled.", 409)
        batch = self.db.get(NgoCampaignBatch, r.batch_id)
        c = self.db.get(CampaignCollaboration, batch.collaboration_id)
        camp, ngo = self.db.get(DonationCampaign, c.campaign_id), self.db.get(Ngo, c.ngo_id)
        r.status, r.cancelled_at, r.cancel_reason = "cancelled", now_utc(), reason
        post(self.db, f"REL-{r.id}-X", [(account(self.db, "ngo_in_transit", camp, ngo), r.amount, 0),
                                        (account(self.db, "campaign_funds", camp), 0, r.amount)],
             "release_cancelled", "campaign_release", r.id, camp.id, f"Reversal of {r.reference_no}: {reason}",
             self.user.id, reversal_of=f"REL-{r.id}")
        self.db.commit()
        return self.release_row(r)

    def acknowledge(self, release_id):
        if self.role != "NGO":
            raise AuthFlowError("FORBIDDEN", "Only the receiving NGO can acknowledge.", 403)
        r = self.db.scalar(select(CampaignFundRelease).join(NgoCampaignBatch, NgoCampaignBatch.id == CampaignFundRelease.batch_id)
                           .where(CampaignFundRelease.id == release_id, NgoCampaignBatch.ngo_id == self.ngo().id)
                           .with_for_update())
        if r is None:
            raise AuthFlowError("RELEASE_NOT_FOUND", "This release does not exist.", 404)
        if r.status != "in_transit":
            raise AuthFlowError("INVALID_STATUS", "This release is not awaiting acknowledgement.", 409)
        batch = self.db.get(NgoCampaignBatch, r.batch_id)
        c = self.db.get(CampaignCollaboration, batch.collaboration_id)
        camp, ngo = self.db.get(DonationCampaign, c.campaign_id), self.ngo()
        r.status, r.acknowledged_at, r.acknowledged_by = "acknowledged", now_utc(), self.user.id
        post(self.db, f"ACK-{r.id}", [(account(self.db, "ngo_in_transit", camp, ngo), r.amount, 0),
                                      (account(self.db, "ngo_batch_funds", batch=batch), 0, r.amount)],
             "release_acknowledged", "campaign_release", r.id, camp.id, f"{ngo.name} confirmed {r.reference_no}",
             self.user.id)
        self.notes.add(staff_ids(self.db), "campaign_release_acknowledged", "NGO confirmed campaign funds",
                       f"{ngo.name} confirmed receiving {pkr(r.amount)} ({r.reference_no}) for {camp.name}.", c.id,
                       "campaign_collaboration")
        if r.donors_notified_at is None:  # "your contribution funded NGO X" — own attributed share only, once
            per = defaultdict(lambda: ZERO)
            for donor_id, amt in self.db.execute(
                    select(CampaignContribution.donor_id, CampaignReleaseSource.amount)
                    .join(CampaignContribution, CampaignContribution.id == CampaignReleaseSource.contribution_id)
                    .where(CampaignReleaseSource.release_id == r.id)).all():
                per[donor_id] += dec(amt)
            for donor_id, amt in per.items():
                self.notes.add([donor_id], "campaign_ngo_funded", "Your campaign contribution reached an NGO",
                               f"{pkr(amt)} from your contribution to {camp.name} was received by {ngo.name}.", camp.id,
                               "campaign_utilization")
            r.donors_notified_at = now_utc()
        self.db.commit()
        return self.release_row(r)

    # PDMA: collaboration campaigns (parent entity) --------------------------------------------------------
    PARENT_FIELDS = ("title", "description", "objective", "districts", "operational_areas", "start_date", "end_date",
                     "total_budget", "available_funding", "instructions", "required_capabilities")
    PARTICIPANT_FIELDS = ("district", "operational_area", "responsibility", "scope_of_work", "assigned_amount",
                          "instructions")

    def _parent(self, cc_id, lock=False):
        if not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        q = select(CollaborationCampaign).where(CollaborationCampaign.id == cc_id)
        cc = self.db.scalar(q.with_for_update() if lock else q)
        if cc is None:
            raise AuthFlowError("COLLABORATION_CAMPAIGN_NOT_FOUND", "This collaboration campaign does not exist.", 404)
        return cc

    def _set_participants(self, cc, participants):
        seen = set()
        for p in participants:
            if p["ngo_id"] in seen:
                raise AuthFlowError("DUPLICATE_NGO", "Each NGO can be selected only once.", 422)
            seen.add(p["ngo_id"])
            ngo = self.db.get(Ngo, p["ngo_id"])
            if ngo is None or not ngo.is_active:
                raise AuthFlowError("NGO_NOT_FOUND", f"NGO {p['ngo_id']} is not an active registered NGO.", 404)
        if cc.total_budget is not None:
            total = sum((dec(p.get("assigned_amount")) for p in participants), ZERO)
            if total > dec(cc.total_budget):
                raise AuthFlowError("ALLOCATION_EXCEEDS_BUDGET",
                                    f"Proposed allocations total {pkr(total)}, above the budget {pkr(cc.total_budget)}.", 422)
        existing = {c.ngo_id: c for c in self.db.scalars(select(CampaignCollaboration).where(
            CampaignCollaboration.collaboration_campaign_id == cc.id))}
        for ngo_id, c in existing.items():
            if ngo_id not in seen:
                self.db.delete(c)
        for p in participants:
            c = existing.get(p["ngo_id"])
            if c is None:
                c = CampaignCollaboration(reference_no=doc_no(self.db, "COL"), collaboration_campaign_id=cc.id,
                                          ngo_id=p["ngo_id"], campaign_id=cc.funding_campaign_id, created_by=self.user.id,
                                          status="draft", start_date=cc.start_date, expected_completion=cc.end_date)
                self.db.add(c)
            for k in self.PARTICIPANT_FIELDS:
                setattr(c, k, p.get(k))
            c.campaign_id = cc.funding_campaign_id if c.assigned_amount else None

    def create_collaboration_campaign(self, data, launch=False):
        if not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        participants = data.pop("participants")
        funding_id = data.pop("funding_campaign_id", None)
        if funding_id:
            self._campaign(funding_id)
        cc = CollaborationCampaign(reference_no=doc_no(self.db, "COLC"), created_by=self.user.id, status="draft",
                                   funding_campaign_id=funding_id, **{k: data.get(k) for k in self.PARENT_FIELDS})
        self.db.add(cc)
        self.db.flush()
        self._set_participants(cc, participants)
        self.db.flush()
        if launch:
            self._launch(cc)
        self.db.commit()
        return self.parent_row(cc, True)

    def update_collaboration_campaign(self, cc_id, data):
        cc = self._parent(cc_id, lock=True)
        if cc.status != "draft":
            raise AuthFlowError("NOT_EDITABLE", "Only a draft collaboration campaign can be edited.", 409)
        participants = data.pop("participants", None)
        if "funding_campaign_id" in data:
            if data["funding_campaign_id"]:
                self._campaign(data["funding_campaign_id"])
            cc.funding_campaign_id = data.pop("funding_campaign_id")
        for k in self.PARENT_FIELDS:
            if k in data:
                setattr(cc, k, data[k])
        if participants is not None:
            self._set_participants(cc, participants)
        self.db.commit()
        return self.parent_row(cc, True)

    def _launch(self, cc):
        participants = list(self.db.scalars(select(CampaignCollaboration).where(
            CampaignCollaboration.collaboration_campaign_id == cc.id, CampaignCollaboration.status == "draft")))
        if not participants:
            raise AuthFlowError("NO_NGOS_SELECTED", "Select at least one NGO before launching.", 422)
        cc.status, cc.launched_at = "launched", now_utc()
        for c in participants:  # one invitation (and one notification) per selected NGO
            self._invite(c, cc, self.db.get(Ngo, c.ngo_id))

    def launch_collaboration_campaign(self, cc_id):
        cc = self._parent(cc_id, lock=True)
        if cc.status != "draft":
            raise AuthFlowError("ALREADY_LAUNCHED", "This collaboration campaign has already been launched.", 409)
        self._launch(cc)
        self.db.commit()
        return self.parent_row(cc, True)

    def close_collaboration_campaign(self, cc_id, status, reason=None):
        cc = self._parent(cc_id, lock=True)
        if cc.status not in ("draft", "launched"):
            raise AuthFlowError("INVALID_STATUS", "This collaboration campaign is already closed.", 409)
        cc.status = status
        for c in self.db.scalars(select(CampaignCollaboration).where(
                CampaignCollaboration.collaboration_campaign_id == cc.id, CampaignCollaboration.status == "invited")):
            c.status, c.response_note = "cancelled", reason or f"Campaign {status}"
        self.db.commit()
        return self.parent_row(cc, True)

    def add_participant(self, cc_id, p):
        """Invite an additional NGO to a launched campaign (gets its own invitation and notification)."""
        cc = self._parent(cc_id, lock=True)
        if cc.status not in ("draft", "launched"):
            raise AuthFlowError("INVALID_STATUS", "This collaboration campaign is closed.", 409)
        if self.db.scalar(select(CampaignCollaboration.id).where(
                CampaignCollaboration.collaboration_campaign_id == cc.id, CampaignCollaboration.ngo_id == p["ngo_id"],
                CampaignCollaboration.status.in_(LIVE_COLLAB))):
            raise AuthFlowError("ALREADY_COLLABORATING", "This NGO is already part of this campaign.", 409)
        ngo = self.db.get(Ngo, p["ngo_id"])
        if ngo is None or not ngo.is_active:
            raise AuthFlowError("NGO_NOT_FOUND", "Choose an active registered NGO.", 404)
        c = CampaignCollaboration(reference_no=doc_no(self.db, "COL"), collaboration_campaign_id=cc.id, ngo_id=ngo.id,
                                  created_by=self.user.id, status="draft", start_date=cc.start_date,
                                  expected_completion=cc.end_date,
                                  campaign_id=cc.funding_campaign_id if p.get("assigned_amount") else None,
                                  **{k: p.get(k) for k in self.PARTICIPANT_FIELDS})
        self.db.add(c)
        self.db.flush()
        if cc.status == "launched":
            self._invite(c, cc, ngo)
        self.db.commit()
        return self.parent_row(cc, True)

    def link_funding(self, collab_id, campaign_id, amount):
        """PDMA attaches a fundraising campaign + allocation to an NGO participation (separate from acceptance)."""
        if not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        c = self._collab(collab_id, lock=True)
        if c.status not in ("draft", "invited", "accepted"):
            raise AuthFlowError("INVALID_STATUS", "Funding can be linked only to an open participation.", 409)
        if self._batch_for(c) and c.campaign_id and c.campaign_id != campaign_id:
            raise AuthFlowError("FUNDING_LOCKED", "This participation already has a funded batch from another campaign.", 409)
        camp = self._campaign(campaign_id, lock=True)
        if camp.status not in FUNDABLE_CAMPAIGN:
            raise AuthFlowError("CAMPAIGN_NOT_PUBLISHED", "Choose a published fundraising campaign.", 409)
        backfill(self.db, camp)
        others = dec(self.db.scalar(select(func.sum(CampaignCollaboration.assigned_amount)).where(
            CampaignCollaboration.campaign_id == camp.id, CampaignCollaboration.status.in_(LIVE_COLLAB),
            CampaignCollaboration.id != c.id)))
        ceiling = max(dec(camp.target_amount), -balance(self.db, find_account(self.db, f"DONOR-RECEIPTS:{camp.id}")))
        if others + dec(amount) > ceiling:
            raise AuthFlowError("ASSIGNMENT_EXCEEDS_CAMPAIGN", f"Allocations would exceed the campaign's {pkr(ceiling)}.", 409)
        if dec(amount) < self.collab_figures(c)["released"]:
            raise AuthFlowError("BELOW_RELEASED", "The allocation cannot be lower than the amount already released.", 409)
        c.campaign_id, c.assigned_amount = camp.id, dec(amount)
        if c.status == "accepted":
            self._ensure_batch(c, camp)
        ngo = self.db.get(Ngo, c.ngo_id)
        self.notes.add([ngo.user_id], "collaboration_funding_approved", "Funding allocated to your collaboration",
                       f"PDMA allocated {pkr(amount)} from {camp.name} to {c.reference_no}. Funds will be released in "
                       "transfers that you confirm on receipt.", c.id, "ngo_collaboration")
        self.db.commit()
        return self.collab_row(c, True)

    def _ensure_batch(self, c, camp):
        if self._batch_for(c) is None:
            mb = master_batch(self.db, camp)
            self.db.add(NgoCampaignBatch(reference_no=doc_no(self.db, "NGO-BATCH"), collaboration_id=c.id,
                                         master_batch_id=mb.id, ngo_id=c.ngo_id))
            self.db.flush()

    def parent_row(self, cc, detail=False):
        parts = list(self.db.scalars(select(CampaignCollaboration).where(
            CampaignCollaboration.collaboration_campaign_id == cc.id).order_by(CampaignCollaboration.id)))
        counts = defaultdict(int)
        for c in parts:
            counts[c.status] += 1
        fund = self.db.get(DonationCampaign, cc.funding_campaign_id) if cc.funding_campaign_id else None
        n_dist = self.db.scalar(select(func.count()).select_from(NgoDistributionCampaign).where(
            NgoDistributionCampaign.collaboration_campaign_id == cc.id))
        out = {"id": cc.id, "reference_no": cc.reference_no, "status": cc.status,
               **{k: (money(getattr(cc, k)) if k in ("total_budget", "available_funding") and getattr(cc, k) is not None
                      else (iso(getattr(cc, k)) if k in ("start_date", "end_date") else getattr(cc, k)))
                  for k in self.PARENT_FIELDS},
               "funding_campaign": {"id": fund.id, "name": fund.name, "reference_no": fund.reference_no} if fund else None,
               "launched_at": iso(cc.launched_at), "created_at": iso(cc.created_at),
               "selected_ngos": len([c for c in parts if c.status != "cancelled" or c.invited_at]),
               "accepted_ngos": counts["accepted"] + counts["completed"], "pending_ngos": counts["invited"],
               "rejected_ngos": counts["rejected"], "draft_ngos": counts["draft"], "distribution_campaigns": n_dist}
        if detail:
            out["participants"] = [self.collab_row(c) for c in parts]
            out["distribution_campaign_list"] = [self.dist_row(d) for d in self.db.scalars(
                select(NgoDistributionCampaign).where(NgoDistributionCampaign.collaboration_campaign_id == cc.id)
                .order_by(NgoDistributionCampaign.id))]
        return out

    def list_collaboration_campaigns(self, status=None, search=None, page=1, page_size=20):
        if not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        q = select(CollaborationCampaign)
        if status:
            q = q.where(CollaborationCampaign.status == status)
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(CollaborationCampaign.title.like(like), CollaborationCampaign.reference_no.like(like),
                            CollaborationCampaign.districts.like(like)))
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = self.db.scalars(q.order_by(CollaborationCampaign.id.desc()).offset((page - 1) * page_size).limit(page_size))
        return {"items": [self.parent_row(cc) for cc in rows], "total": int(total or 0), "page": page,
                "page_size": page_size}

    def collaboration_campaign(self, cc_id):
        return self.parent_row(self._parent(cc_id), True)

    # NGO: batches -------------------------------------------------------------------------------------
    def batch_row(self, b):
        c = self.db.get(CampaignCollaboration, b.collaboration_id)
        camp = self.db.get(DonationCampaign, c.campaign_id)
        mb = self.db.get(CampaignMasterBatch, b.master_batch_id)
        f = self.collab_figures(c)
        return {"id": b.id, "reference_no": b.reference_no, "collaboration_id": c.id,
                "collaboration_reference": c.reference_no, "campaign": {"id": camp.id, "name": camp.name,
                                                                        "reference_no": camp.reference_no},
                "master_batch_reference": mb.reference_no, "district": c.district, "assigned": money(f["assigned"]),
                "received": money(f["received"]), "in_transit": money(f["in_transit"]),
                "distributed": money(f["distributed"]), "available": money(f["available"]), "created_at": iso(b.created_at)}

    def batches(self):
        q = select(NgoCampaignBatch)
        if self.role == "NGO":
            q = q.where(NgoCampaignBatch.ngo_id == self.ngo().id)
        elif not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        return {"items": [self.batch_row(b) for b in self.db.scalars(q.order_by(NgoCampaignBatch.id.desc()))]}

    def _own_batch(self, batch_id, lock=False):
        q = select(NgoCampaignBatch).where(NgoCampaignBatch.id == batch_id)
        if self.role == "NGO":
            q = q.where(NgoCampaignBatch.ngo_id == self.ngo().id)
        elif not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        b = self.db.scalar(q.with_for_update() if lock else q)
        if b is None:
            raise AuthFlowError("BATCH_NOT_FOUND", "This batch does not exist.", 404)
        return b

    # NGO: distribution campaigns -------------------------------------------------------------------------
    def dist_row(self, d, detail=False):
        c = self.db.get(CampaignCollaboration, d.collaboration_id)
        b = self._batch_for(c)
        camp = self.db.get(DonationCampaign, c.campaign_id) if c.campaign_id else None
        parent = self.db.get(CollaborationCampaign, c.collaboration_campaign_id) if c.collaboration_campaign_id else None
        recorded = [x for x in self.db.scalars(select(BeneficiaryDistribution).where(
            BeneficiaryDistribution.distribution_campaign_id == d.id)) if x.status == "recorded"]
        out = {"id": d.id, "reference_no": d.reference_no, "name": d.name, "status": d.status, "district": d.district,
               "operational_area": d.operational_area, "objective": d.objective, "description": d.description,
               "planned_amount": money(d.planned_amount) if d.planned_amount is not None else None,
               "start_date": iso(d.start_date), "end_date": iso(d.end_date), "remarks": d.remarks,
               "target_beneficiaries": d.target_beneficiaries, "locations": d.locations,
               "expected_beneficiaries": d.expected_beneficiaries, "strategy": d.strategy,
               "ngo": {"id": d.ngo_id, "name": self.db.get(Ngo, d.ngo_id).name},
               "batch": {"id": b.id, "reference_no": b.reference_no} if b else None,
               "collaboration": {"id": c.id, "reference_no": c.reference_no, "district": c.district},
               "collaboration_campaign": ({"id": parent.id, "reference_no": parent.reference_no, "title": parent.title}
                                          if parent else None),
               "parent_campaign": ({"id": camp.id, "reference_no": camp.reference_no, "name": camp.name} if camp else None),
               "funded": b is not None,
               "distributed": money(sum((dec(x.amount) for x in recorded), ZERO)), "distribution_count": len(recorded),
               "batch_available": money(balance(self.db, find_account(self.db, f"NGO-BATCH-FUNDS:{b.id}")) if b else ZERO),
               "created_at": iso(d.created_at)}
        if detail:
            bens = list(self.db.scalars(select(DistributionBeneficiary).where(
                DistributionBeneficiary.distribution_campaign_id == d.id).order_by(DistributionBeneficiary.id)))
            out["beneficiaries"] = [self.ben_row(x, recorded) for x in bens]
            out["distributions"] = [self.distribution_row(x) for x in self.db.scalars(
                select(BeneficiaryDistribution).where(BeneficiaryDistribution.distribution_campaign_id == d.id)
                .order_by(BeneficiaryDistribution.id.desc()))]
        return out

    def _profile(self, user_id, kind):
        u = self.db.get(User, user_id)
        p = self.db.get(DETAILS[kind], user_id)
        return u, p

    def ben_row(self, x, recorded=None):
        u, p = self._profile(x.user_id, x.beneficiary_type)
        paid = sum((dec(r.amount) for r in (recorded or []) if r.beneficiary_id == x.id), ZERO)
        return {"id": x.id, "user_id": x.user_id, "beneficiary_type": x.beneficiary_type, "name": u.name,
                "shop_name": getattr(p, "shop_name", None), "city": p.city if p else None,
                "phone": p.phone if p else None, "planned_amount": money(x.planned_amount) if x.planned_amount else None,
                "assistance_type": x.assistance_type, "status": x.status, "paid": money(paid)}

    def distribution_row(self, x):
        ben = self.db.get(DistributionBeneficiary, x.beneficiary_id)
        u = self.db.get(User, ben.user_id)
        return {"id": x.id, "reference_no": x.reference_no, "beneficiary": {"id": ben.id, "name": u.name,
                                                                            "type": ben.beneficiary_type},
                "amount": money(x.amount), "distribution_date": iso(x.distribution_date),
                "payment_method": x.payment_method, "payment_reference": x.payment_reference,
                "proof_reference": x.proof_reference, "status": x.status, "reversal_reason": x.reversal_reason,
                "created_at": iso(x.created_at)}

    def _own_dist(self, dist_id, lock=False):
        q = select(NgoDistributionCampaign).where(NgoDistributionCampaign.id == dist_id)
        if self.role == "NGO":
            q = q.where(NgoDistributionCampaign.ngo_id == self.ngo().id)
        elif not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        d = self.db.scalar(q.with_for_update() if lock else q)
        if d is None:
            raise AuthFlowError("DISTRIBUTION_CAMPAIGN_NOT_FOUND", "This distribution campaign does not exist.", 404)
        return d

    def create_distribution_campaign(self, data, launch=False):
        """Created from the NGO's own ACCEPTED participation; inherits parent campaign, participation and district.
        Funds are not needed to plan; they are needed only to record a payment."""
        if self.role != "NGO":
            raise AuthFlowError("FORBIDDEN", "Only NGOs run distribution campaigns.", 403)
        if data.get("collaboration_id") is None and data.get("batch_id"):  # older clients pass the batch
            data["collaboration_id"] = self._own_batch(data["batch_id"]).collaboration_id
        c = self._collab(data["collaboration_id"])
        if c.status not in ("accepted", "completed"):
            raise AuthFlowError("COLLABORATION_NOT_ACCEPTED", "Distribution campaigns can be created only from an "
                                                              "accepted collaboration.", 409)
        parent = self.db.get(CollaborationCampaign, c.collaboration_campaign_id) if c.collaboration_campaign_id else None
        if parent and parent.status != "launched":
            raise AuthFlowError("COLLABORATION_CLOSED", "The parent collaboration campaign is not running.", 409)
        district = (data.get("district") or c.district).strip()
        if district.lower() != c.district.strip().lower():
            raise AuthFlowError("DISTRICT_OUT_OF_SCOPE", f"This collaboration covers {c.district} only.", 422)
        if parent and parent.end_date and data.get("end_date") and data["end_date"] > parent.end_date:
            raise AuthFlowError("DATES_OUT_OF_SCOPE", f"The collaboration ends on {parent.end_date}.", 422)
        b = self._batch_for(c)
        if data.get("planned_amount") is not None and b is not None:
            available = balance(self.db, find_account(self.db, f"NGO-BATCH-FUNDS:{b.id}"))
            if available > 0 and dec(data["planned_amount"]) > available + self._in_transit(c):
                raise AuthFlowError("PLANNED_EXCEEDS_BATCH", f"The batch has {pkr(available)} available.", 409)
        d = NgoDistributionCampaign(
            reference_no=doc_no(self.db, "DST"), ngo_id=self.ngo().id, collaboration_id=c.id,
            collaboration_campaign_id=c.collaboration_campaign_id, batch_id=b.id if b else None, district=c.district,
            created_by=self.user.id, status="active" if launch else "draft",
            **{k: data.get(k) for k in ("name", "operational_area", "objective", "description", "planned_amount",
                                        "start_date", "end_date", "remarks", "target_beneficiaries", "locations",
                                        "expected_beneficiaries", "strategy")})
        self.db.add(d)
        self.db.flush()
        if launch:
            self._dist_launched_notice(d, parent)
        self.db.commit()
        return self.dist_row(d, True)

    def _in_transit(self, c):
        return self.collab_figures(c)["in_transit"]

    def _dist_launched_notice(self, d, parent):
        self.notes.add(staff_ids(self.db), "distribution_campaign_launched", "NGO launched a distribution campaign",
                       f"{self.ngo().name} launched {d.name} ({d.reference_no})"
                       + (f" under {parent.title} ({parent.reference_no})." if parent else "."),
                       parent.id if parent else d.id, "collaboration_campaign" if parent else "distribution_campaign")

    def launch_distribution_campaign(self, dist_id):
        d = self._own_dist(dist_id, lock=True)
        if self.role != "NGO" or d.status != "draft":
            raise AuthFlowError("INVALID_STATUS", "Only a draft distribution campaign can be launched by its NGO.", 409)
        d.status = "active"
        c = self.db.get(CampaignCollaboration, d.collaboration_id)
        self._dist_launched_notice(d, self.db.get(CollaborationCampaign, c.collaboration_campaign_id)
                                   if c.collaboration_campaign_id else None)
        self.db.commit()
        return self.dist_row(d, True)

    def list_distribution_campaigns(self, status=None):
        q = select(NgoDistributionCampaign)
        if self.role == "NGO":
            q = q.where(NgoDistributionCampaign.ngo_id == self.ngo().id)
        elif not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        if status:
            q = q.where(NgoDistributionCampaign.status == status)
        return {"items": [self.dist_row(d) for d in self.db.scalars(q.order_by(NgoDistributionCampaign.id.desc()))]}

    def distribution_campaign(self, dist_id):
        return self.dist_row(self._own_dist(dist_id), True)

    def complete_distribution_campaign(self, dist_id):
        d = self._own_dist(dist_id, lock=True)
        if self.role != "NGO" or d.status != "active":
            raise AuthFlowError("INVALID_STATUS", "Only an active campaign can be completed by its NGO.", 409)
        d.status = "completed"
        self.notes.add(staff_ids(self.db), "distribution_campaign_completed", "Distribution campaign completed",
                       f"{self.ngo().name} completed {d.name} ({d.reference_no}).", d.collaboration_campaign_id or d.id,
                       "collaboration_campaign" if d.collaboration_campaign_id else "distribution_campaign")
        self.db.commit()
        return self.dist_row(d, True)

    def search_beneficiaries(self, dist_id, kind=None, search=None, page=1, page_size=20):
        d = self._own_dist(dist_id)
        roles = dict(self.db.execute(select(Role.name, Role.id).where(Role.name.in_(BEN_ROLES))).all())
        out = []
        for role_name, k in BEN_ROLES.items():
            if kind and kind != k:
                continue
            Det = DETAILS[k]
            q = (select(User, Det).join(Det, Det.user_id == User.id)
                 .where(User.role_id == roles.get(role_name), User.is_active.is_(True),
                        or_(User.registration_status.is_(None), User.registration_status == "approved"),
                        or_(func.lower(Det.city) == d.district.lower(), func.lower(Det.state) == d.district.lower())))
            if search:
                like = f"%{search.strip()}%"
                q = q.where(or_(User.name.like(like), *( [Det.shop_name.like(like)] if k == "shopkeeper" else [])))
            for u, p in self.db.execute(q).all():
                out.append({"user_id": u.id, "beneficiary_type": k, "name": u.name, "reference": f"{k[:2].upper()}-{u.id:05d}",
                            "shop_name": getattr(p, "shop_name", None), "city": p.city, "state": p.state,
                            "phone": p.phone, "registration_status": u.registration_status or "approved",
                            "household_size": getattr(p, "household_size", None),
                            "income_range": [money(p.income_min), money(p.income_max)] if k == "householder" and p.income_min is not None else None,
                            "revenue_range": [money(p.revenue_min), money(p.revenue_max)] if k == "shopkeeper" and p.revenue_min is not None else None,
                            "already_added": bool(self.db.scalar(select(DistributionBeneficiary.id).where(
                                DistributionBeneficiary.distribution_campaign_id == d.id,
                                DistributionBeneficiary.user_id == u.id)))})
        out.sort(key=lambda r: r["name"])
        return {"items": out[(page - 1) * page_size: page * page_size], "total": len(out), "page": page,
                "page_size": page_size, "district": d.district}

    def add_beneficiaries(self, dist_id, items):
        d = self._own_dist(dist_id, lock=True)
        if self.role != "NGO" or d.status not in ("draft", "active"):
            raise AuthFlowError("INVALID_STATUS", "Beneficiaries can be added only to a draft or active campaign by its "
                                                  "NGO.", 409)
        roles = {v: k for k, v in self.db.execute(select(Role.name, Role.id).where(Role.name.in_(BEN_ROLES))).all()}
        added = 0
        for it in items:
            u = self.db.get(User, it["user_id"])
            kind = BEN_ROLES.get(roles.get(u.role_id)) if u else None
            if not kind or not u.is_active or u.registration_status not in (None, "approved"):
                raise AuthFlowError("NOT_ELIGIBLE", f"User {it['user_id']} is not an approved shopkeeper or householder.", 422)
            p = self.db.get(DETAILS[kind], u.id)
            if p is None or d.district.lower() not in ((p.city or "").lower(), (p.state or "").lower()):
                raise AuthFlowError("DISTRICT_OUT_OF_SCOPE", f"{u.name} is not registered in {d.district}.", 422)
            if self.db.scalar(select(DistributionBeneficiary.id).where(
                    DistributionBeneficiary.distribution_campaign_id == d.id, DistributionBeneficiary.user_id == u.id)):
                continue
            self.db.add(DistributionBeneficiary(distribution_campaign_id=d.id, user_id=u.id, beneficiary_type=kind,
                                                planned_amount=it.get("planned_amount"),
                                                assistance_type=it.get("assistance_type") or "cash_assistance",
                                                added_by=self.user.id))
            added += 1
        self.db.commit()
        out = self.dist_row(d, True)
        out["added"] = added
        return out

    def record_distribution(self, dist_id, data):
        if self.role != "NGO":
            raise AuthFlowError("FORBIDDEN", "Only the NGO records distributions.", 403)
        key = data.get("idempotency_key")
        if key:
            prior = self.db.scalar(select(BeneficiaryDistribution).where(BeneficiaryDistribution.idempotency_key == key))
            if prior:
                return self.distribution_row(prior)
        d = self._own_dist(dist_id)
        if d.status != "active":
            raise AuthFlowError("INVALID_STATUS", "Launch this distribution campaign before recording payments.", 409)
        batch = self._batch_for(self.db.get(CampaignCollaboration, d.collaboration_id))
        if batch is None:
            raise AuthFlowError("NO_CONFIRMED_FUNDS", "No funds have been released to this collaboration yet.", 409)
        b = self._own_batch(batch.id, lock=True)  # serialises spending from this batch
        d = self._own_dist(dist_id, lock=True)
        if d.batch_id is None:
            d.batch_id = b.id
        ben = self.db.scalar(select(DistributionBeneficiary).where(
            DistributionBeneficiary.id == data["beneficiary_id"], DistributionBeneficiary.distribution_campaign_id == d.id))
        if ben is None or ben.status == "removed":
            raise AuthFlowError("BENEFICIARY_NOT_IN_CAMPAIGN", "Add this beneficiary to the campaign first.", 422)
        amount = dec(data["amount"])
        available = balance(self.db, account(self.db, "ngo_batch_funds", batch=b))
        if amount > available:
            raise AuthFlowError("INSUFFICIENT_BATCH_FUNDS", f"{b.reference_no} has {pkr(available)} available.", 409)
        x = BeneficiaryDistribution(reference_no=doc_no(self.db, "BDV"), distribution_campaign_id=d.id,
                                    beneficiary_id=ben.id, amount=amount, distribution_date=data["distribution_date"],
                                    payment_method=data["payment_method"], payment_reference=data.get("payment_reference"),
                                    proof_reference=data.get("proof_reference"), idempotency_key=key,
                                    status="recorded", recorded_by=self.user.id)
        self.db.add(x)
        self.db.flush()
        # exact attribution: acknowledged releases (oldest first) -> their recorded contribution splits
        used = defaultdict(lambda: ZERO)
        for rid, cid, amt in self.db.execute(
                select(DistributionFundingSource.release_id, DistributionFundingSource.contribution_id,
                       DistributionFundingSource.amount)
                .join(BeneficiaryDistribution, BeneficiaryDistribution.id == DistributionFundingSource.distribution_id)
                .where(BeneficiaryDistribution.status == "recorded")).all():
            used[(rid, cid)] += dec(amt)
        need = amount
        for r in self.db.scalars(select(CampaignFundRelease).where(CampaignFundRelease.batch_id == b.id,
                                                                   CampaignFundRelease.status == "acknowledged")
                                 .order_by(CampaignFundRelease.acknowledged_at, CampaignFundRelease.id)):
            for s in self.db.scalars(select(CampaignReleaseSource).where(CampaignReleaseSource.release_id == r.id)
                                     .order_by(CampaignReleaseSource.id)):
                if need <= 0:
                    break
                free = dec(s.amount) - used[(r.id, s.contribution_id)]
                take = min(free, need)
                if take > 0:
                    self.db.add(DistributionFundingSource(distribution_id=x.id, release_id=r.id,
                                                          contribution_id=s.contribution_id, amount=take))
                    used[(r.id, s.contribution_id)] += take
                    need -= take
        if need > 0:
            raise AuthFlowError("ATTRIBUTION_SHORTFALL", "Acknowledged releases do not cover this distribution.", 409)
        post(self.db, f"BDV-{x.id}", [(account(self.db, "ngo_batch_funds", batch=b), amount, 0),
                                      (account(self.db, "beneficiary_payments", batch=b), 0, amount)],
             "beneficiary_distribution", "beneficiary_distribution", x.id, self._campaign_id(d),
             f"{x.reference_no} to {ben.beneficiary_type}", self.user.id)
        self._update_ben_status(ben)
        self._donor_notices(d, x, ben)
        self.notes.add(staff_ids(self.db), "beneficiary_distribution_recorded", "Beneficiary distribution recorded",
                       f"{self.ngo().name} recorded {pkr(amount)} to a {ben.beneficiary_type} ({x.reference_no}) under "
                       f"{d.name}.", d.collaboration_campaign_id or d.id,
                       "collaboration_campaign" if d.collaboration_campaign_id else "distribution_campaign")
        self.db.commit()
        return self.distribution_row(x)

    def _campaign_id(self, d):
        return self.db.scalar(select(CampaignCollaboration.campaign_id).where(CampaignCollaboration.id == d.collaboration_id))

    def _update_ben_status(self, ben):
        self.db.flush()
        paid = dec(self.db.scalar(select(func.sum(BeneficiaryDistribution.amount)).where(
            BeneficiaryDistribution.beneficiary_id == ben.id, BeneficiaryDistribution.status == "recorded")))
        if paid <= 0:
            ben.status = "selected"
        elif ben.planned_amount and paid < dec(ben.planned_amount):
            ben.status = "partially_paid"
        else:
            ben.status = "paid"

    def _donor_notices(self, d, x, ben):
        camp = self.db.get(DonationCampaign, self._campaign_id(d))
        ngo = self.ngo()
        per = defaultdict(lambda: ZERO)
        for donor_id, amt in self.db.execute(
                select(CampaignContribution.donor_id, DistributionFundingSource.amount)
                .join(CampaignContribution, CampaignContribution.id == DistributionFundingSource.contribution_id)
                .where(DistributionFundingSource.distribution_id == x.id)).all():
            per[donor_id] += dec(amt)
        if d.donors_notified_at is None:
            self.notes.add(list(per), "campaign_distribution_started", "Distribution has started",
                           f"{ngo.name} has started distributing assistance using funds from {camp.name}.", camp.id,
                           "campaign_utilization")
            d.donors_notified_at = now_utc()
        for donor_id, amt in per.items():
            self.notes.add([donor_id], "campaign_assistance_distributed", "Your contribution helped a family"
                           if ben.beneficiary_type == "householder" else "Your contribution helped a business",
                           f"{pkr(amt)} from your contribution to {camp.name} was distributed to an eligible "
                           f"{ben.beneficiary_type} in {d.district}.", camp.id, "campaign_utilization")

    def reverse_distribution(self, distribution_id, reason):
        x = self.db.get(BeneficiaryDistribution, distribution_id)
        if x is None:
            raise AuthFlowError("DISTRIBUTION_NOT_FOUND", "This distribution does not exist.", 404)
        d = self._own_dist(x.distribution_campaign_id)
        if self.role != "NGO":
            raise AuthFlowError("FORBIDDEN", "Only the NGO can reverse its distribution.", 403)
        b = self._own_batch(d.batch_id, lock=True)  # set when the first payment was recorded
        x = self.db.scalar(select(BeneficiaryDistribution).where(BeneficiaryDistribution.id == distribution_id)
                           .with_for_update().execution_options(populate_existing=True))
        if x.status != "recorded":
            raise AuthFlowError("INVALID_STATUS", "This distribution is already reversed.", 409)
        x.status, x.reversal_reason, x.reversed_at = "reversed", reason, now_utc()
        post(self.db, f"BDV-{x.id}-X", [(account(self.db, "beneficiary_payments", batch=b), x.amount, 0),
                                        (account(self.db, "ngo_batch_funds", batch=b), 0, x.amount)],
             "distribution_reversed", "beneficiary_distribution", x.id, self._campaign_id(d),
             f"Reversal of {x.reference_no}: {reason}", self.user.id, reversal_of=f"BDV-{x.id}")
        self._update_ben_status(self.db.get(DistributionBeneficiary, x.beneficiary_id))
        self.db.commit()
        return self.distribution_row(x)

    # Ledgers -------------------------------------------------------------------------------------------
    def ledger(self, scope, scope_id, date_from=None, date_to=None, search=None, page=1, page_size=50):
        """scope: campaign | collaboration | batch | distribution."""
        if scope == "campaign":
            if not self.staff:
                raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
            camp = self._campaign(scope_id)
            backfill(self.db, camp)
            self.db.commit()
            accs = [find_account(self.db, f"CAMPAIGN-FUNDS:{camp.id}")]
            title, owner = f"Master batch ledger — {camp.name}", camp.reference_no
        elif scope == "collaboration":
            c = self._collab(scope_id)
            camp, ngo = self.db.get(DonationCampaign, c.campaign_id), self.db.get(Ngo, c.ngo_id)
            accs = [find_account(self.db, f"NGO-IN-TRANSIT:{camp.id}:{ngo.id}")]
            title, owner = f"Collaboration ledger — {c.reference_no} (funds in transit)", ngo.name
        else:
            if scope == "distribution" and self._own_dist(scope_id).batch_id is None:
                return {"title": "Distribution ledger", "owner": self._own_dist(scope_id).reference_no,
                        "period": {"from": iso(date_from), "to": iso(date_to)}, "opening_balance": "0.00",
                        "total_credit": "0.00", "total_debit": "0.00", "closing_balance": "0.00", "items": [],
                        "total": 0, "page": page, "page_size": page_size, "all": []}
            b = self._own_batch(scope_id if scope == "batch" else self._own_dist(scope_id).batch_id)
            accs = [find_account(self.db, f"NGO-BATCH-FUNDS:{b.id}")]
            title, owner = f"Sub-batch ledger — {b.reference_no}", self.db.get(Ngo, b.ngo_id).name
        accs = [a for a in accs if a]
        q = select(FinanceLedgerEntry).where(FinanceLedgerEntry.account_id.in_([a.id for a in accs] or [-1]))
        if scope == "distribution":
            ids = list(self.db.scalars(select(BeneficiaryDistribution.id).where(
                BeneficiaryDistribution.distribution_campaign_id == scope_id)))
            q = q.where(or_(FinanceLedgerEntry.source_type != "beneficiary_distribution",
                            FinanceLedgerEntry.source_id.in_(ids or [-1])))
        entries = list(self.db.scalars(q.order_by(FinanceLedgerEntry.posted_at, FinanceLedgerEntry.id)))
        bal, rows = ZERO, []
        for e in entries:
            bal += dec(e.credit) - dec(e.debit)
            rows.append({"at": e.posted_at, "reference": e.txn_ref, "type": e.event_type, "description": e.memo or "",
                         "counterparty": None, "credit": dec(e.credit), "debit": dec(e.debit), "balance": bal,
                         "reversal_of": e.reversal_of})
        start = datetime.combine(date_from, datetime.min.time()) - PKT if date_from else None
        end = datetime.combine(date_to + timedelta(days=1), datetime.min.time()) - PKT if date_to else None
        opening = sum((r["credit"] - r["debit"] for r in rows if start and r["at"] < start), ZERO)
        period = [r for r in rows if (not start or r["at"] >= start) and (not end or r["at"] < end)]
        if search:
            s = search.lower()
            period = [r for r in period if s in r["reference"].lower() or s in r["description"].lower()]
        shown = list(reversed(period))
        cr = sum((r["credit"] for r in period), ZERO)
        dr = sum((r["debit"] for r in period), ZERO)
        return {"title": title, "owner": owner, "period": {"from": iso(date_from), "to": iso(date_to)},
                "opening_balance": money(opening), "total_credit": money(cr), "total_debit": money(dr),
                "closing_balance": money(bal if not end else (period[-1]["balance"] if period else opening)),
                "items": [{"date": iso(r["at"]), "reference": r["reference"], "type": r["type"],
                           "description": r["description"], "credit": money(r["credit"]) if r["credit"] else None,
                           "debit": money(r["debit"]) if r["debit"] else None, "balance": money(r["balance"]),
                           "reversal_of": r["reversal_of"]}
                          for r in (shown[(page - 1) * page_size: page * page_size] if page else shown)],
                "total": len(period), "page": page, "page_size": page_size, "all": shown}

    # Donor traceability --------------------------------------------------------------------------------
    def donor_utilization(self):
        if self.role != "DONOR":
            raise AuthFlowError("FORBIDDEN", "Only donors have contribution utilization.", 403)
        out, totals = [], defaultdict(lambda: ZERO)
        ngos = set()
        for x in self.db.scalars(select(CampaignContribution).where(CampaignContribution.donor_id == self.user.id)
                                 .order_by(CampaignContribution.created_at.desc())):
            camp = self.db.get(DonationCampaign, x.campaign_id)
            mb = self.db.scalar(select(CampaignMasterBatch).where(CampaignMasterBatch.campaign_id == camp.id))
            by_ngo = defaultdict(lambda: {"in_transit": ZERO, "received": ZERO, "distributed": ZERO})
            for status, ngo_name, amt in self.db.execute(
                    select(CampaignFundRelease.status, Ngo.name, CampaignReleaseSource.amount)
                    .join(CampaignFundRelease, CampaignFundRelease.id == CampaignReleaseSource.release_id)
                    .join(NgoCampaignBatch, NgoCampaignBatch.id == CampaignFundRelease.batch_id)
                    .join(Ngo, Ngo.id == NgoCampaignBatch.ngo_id)
                    .where(CampaignReleaseSource.contribution_id == x.id, CampaignFundRelease.status != "cancelled")).all():
                by_ngo[ngo_name]["received" if status == "acknowledged" else "in_transit"] += dec(amt)
            for ngo_name, kind, amt in self.db.execute(
                    select(Ngo.name, DistributionBeneficiary.beneficiary_type, DistributionFundingSource.amount)
                    .join(BeneficiaryDistribution, BeneficiaryDistribution.id == DistributionFundingSource.distribution_id)
                    .join(DistributionBeneficiary, DistributionBeneficiary.id == BeneficiaryDistribution.beneficiary_id)
                    .join(NgoDistributionCampaign, NgoDistributionCampaign.id == BeneficiaryDistribution.distribution_campaign_id)
                    .join(Ngo, Ngo.id == NgoDistributionCampaign.ngo_id)
                    .where(DistributionFundingSource.contribution_id == x.id,
                           BeneficiaryDistribution.status == "recorded")).all():
                by_ngo[ngo_name]["distributed"] += dec(amt)
            verified = x.payment_status == "verified"
            released = sum((v["in_transit"] + v["received"] for v in by_ngo.values()), ZERO)
            received = sum((v["received"] for v in by_ngo.values()), ZERO)
            distributed = sum((v["distributed"] for v in by_ngo.values()), ZERO)
            ngos |= {n for n, v in by_ngo.items() if v["received"] > 0}
            if verified:
                totals["verified"] += dec(x.amount)
                totals["released"] += released
                totals["received"] += received
                totals["distributed"] += distributed
            totals["contributed"] += dec(x.amount)
            out.append({"reference_no": x.reference_no, "campaign": {"id": camp.id, "name": camp.name,
                                                                     "reference_no": camp.reference_no},
                        "master_batch_reference": mb.reference_no if mb else None, "amount": money(x.amount),
                        "payment_status": x.payment_status, "verified_at": iso(x.verified_at),
                        "unreleased": money(dec(x.amount) - released if verified else 0),
                        "released_to_ngos": money(released), "received_by_ngos": money(received),
                        "distributed": money(distributed), "remaining_with_ngos": money(received - distributed),
                        "ngos": [{"name": n, **{k: money(v) for k, v in vals.items()},
                                  "remaining": money(vals["received"] - vals["distributed"])}
                                 for n, vals in sorted(by_ngo.items())]})
        return {"items": out, "totals": {k: money(v) for k, v in totals.items()},
                "campaigns_supported": len({i["campaign"]["id"] for i in out if i["payment_status"] == "verified"}),
                "ngos_funded": len(ngos),
                "method": "Exact recorded attribution: releases draw from specific verified contributions (oldest "
                          "first) and distributions draw from specific releases. Nothing is estimated."}

    # Dashboards ----------------------------------------------------------------------------------------
    def pdma_dashboard(self):
        if not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        camps = list(self.db.scalars(select(DonationCampaign).where(DonationCampaign.status.in_(FUNDABLE_CAMPAIGN))))
        tot = defaultdict(lambda: ZERO)
        per_campaign, per_ngo = [], defaultdict(lambda: {"assigned": ZERO, "received": ZERO, "distributed": ZERO})
        for c in camps:
            f = self.campaign_figures(c)
            for k in ("collected", "assigned", "released", "in_transit", "received", "distributed", "unspent",
                      "master_available"):
                tot[k] += f[k]
            per_campaign.append({"id": c.id, "name": c.name, "district": c.district, "target": money(c.target_amount),
                                 "collected": money(f["collected"]), "distributed": money(f["distributed"])})
            for col, cf in f["per"]:
                if col.status in LIVE_COLLAB and col.status != "draft":
                    n = self.db.get(Ngo, col.ngo_id).name
                    for k in ("assigned", "received", "distributed"):
                        per_ngo[n][k] += cf[k]
        self.db.commit()
        district = defaultdict(lambda: ZERO)
        for dist, amt in self.db.execute(select(NgoDistributionCampaign.district, func.sum(BeneficiaryDistribution.amount))
                                         .join(BeneficiaryDistribution, BeneficiaryDistribution.distribution_campaign_id == NgoDistributionCampaign.id)
                                         .where(BeneficiaryDistribution.status == "recorded")
                                         .group_by(NgoDistributionCampaign.district)).all():
            district[dist] += dec(amt)
        monthly = defaultdict(lambda: {"inflow": ZERO, "outflow": ZERO})
        for e in self.db.scalars(select(FinanceLedgerEntry).join(FinanceAccount, FinanceAccount.id == FinanceLedgerEntry.account_id)
                                 .where(FinanceAccount.kind.in_(("campaign_funds", "beneficiary_payments")))):
            acc = self.db.get(FinanceAccount, e.account_id)
            m = (e.posted_at + PKT).strftime("%Y-%m")
            if acc.kind == "campaign_funds" and e.event_type == "contribution_verified":
                monthly[m]["inflow"] += dec(e.credit)
            if acc.kind == "beneficiary_payments":
                monthly[m]["outflow"] += dec(e.credit) - dec(e.debit)
        return {
            "fundraising_campaigns": len(camps),
            "master_batches": self.db.scalar(select(func.count()).select_from(CampaignMasterBatch)),
            "collaborating_ngos": self.db.scalar(select(func.count(func.distinct(CampaignCollaboration.ngo_id))).where(
                CampaignCollaboration.status.in_(("accepted", "completed")))),
            **{k: money(v) for k, v in tot.items()},
            "per_campaign": per_campaign,
            "per_ngo": [{"ngo": n, **{k: money(v) for k, v in vals.items()}} for n, vals in sorted(per_ngo.items())],
            "per_district": [{"district": k, "distributed": money(v)} for k, v in sorted(district.items())],
            "monthly": [{"month": k, "inflow": money(v["inflow"]), "outflow": money(v["outflow"])}
                        for k, v in sorted(monthly.items())],
        }

    def ngo_dashboard(self):
        ngo = self.ngo()
        collabs = list(self.db.scalars(select(CampaignCollaboration).where(CampaignCollaboration.ngo_id == ngo.id,
                                                                           CampaignCollaboration.status != "draft")))
        tot = defaultdict(lambda: ZERO)
        for c in collabs:
            if c.status in ("accepted", "completed"):
                f = self.collab_figures(c)
                for k in ("assigned", "received", "in_transit", "distributed", "available"):
                    tot[k] += f[k]
        kinds = dict(self.db.execute(
            select(DistributionBeneficiary.beneficiary_type, func.count(func.distinct(DistributionBeneficiary.user_id)))
            .join(NgoDistributionCampaign, NgoDistributionCampaign.id == DistributionBeneficiary.distribution_campaign_id)
            .join(BeneficiaryDistribution, BeneficiaryDistribution.beneficiary_id == DistributionBeneficiary.id)
            .where(NgoDistributionCampaign.ngo_id == ngo.id, BeneficiaryDistribution.status == "recorded")
            .group_by(DistributionBeneficiary.beneficiary_type)).all())
        dist_status = dict(self.db.execute(select(NgoDistributionCampaign.status, func.count())
                                           .where(NgoDistributionCampaign.ngo_id == ngo.id)
                                           .group_by(NgoDistributionCampaign.status)).all())
        return {
            "total_invitations": len(collabs),
            "rejected_invitations": sum(1 for c in collabs if c.status == "rejected"),
            "completed_distribution_campaigns": dist_status.get("completed", 0),
            "draft_distribution_campaigns": dist_status.get("draft", 0),
            "active_collaborations": sum(1 for c in collabs if c.status == "accepted"),
            "pending_invitations": sum(1 for c in collabs if c.status == "invited"),
            "assigned": money(tot["assigned"]), "received": money(tot["received"]),
            "pending_acknowledgement": money(tot["in_transit"]), "available": money(tot["available"]),
            "distributed": money(tot["distributed"]),
            "active_distribution_campaigns": self.db.scalar(select(func.count()).select_from(NgoDistributionCampaign).where(
                NgoDistributionCampaign.ngo_id == ngo.id, NgoDistributionCampaign.status == "active")),
            "shopkeepers_assisted": kinds.get("shopkeeper", 0), "householders_assisted": kinds.get("householder", 0),
        }

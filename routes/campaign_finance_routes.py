"""Campaign finance API: NGO collaborations, releases, NGO sub-batches, distribution campaigns, ledgers, donor
traceability, dashboards and PDFs. Every rule is enforced in controller/campaign_finance.py."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from config.database import get_db
from config.outbox import deliver_due
from config.pdf_reports import campaign_statement_pdf, distribution_voucher_pdf, ledger_pdf
from controller.authentication import AuthFlowError
from controller.campaign_finance import CampaignFinance
from controller.donation import push_notification
from middlewares.role_middleware import PDMA_STAFF, require_roles, role_name
from model.campaign_finance_model import BeneficiaryDistribution

router = APIRouter(tags=["Campaign finance"])
staff_only = require_roles(*PDMA_STAFF)
only_ngo = require_roles("NGO")
staff_or_ngo = require_roles(*PDMA_STAFF, "NGO")
only_donor = require_roles("DONOR")
Amount = Field(gt=0, le=Decimal("999999999999.99"), max_digits=14, decimal_places=2)
Method = Literal["BANK_TRANSFER", "CHEQUE", "CASH", "MOBILE_WALLET", "ONLINE", "OTHER"]


def _req(v):
    v = " ".join((v or "").split())
    if not v:
        raise ValueError("This field is required.")
    return v


class CollaborationIn(BaseModel):
    ngo_id: int = Field(gt=0)
    assigned_amount: Decimal = Amount
    district: str = Field(min_length=2, max_length=100)
    operational_area: str | None = Field(default=None, max_length=200)
    responsibility: str = Field(min_length=5, max_length=1000)
    start_date: date | None = None
    expected_completion: date | None = None
    remarks: str | None = Field(default=None, max_length=1000)
    send_invitation: bool = True
    _d = field_validator("district", "responsibility")(classmethod(lambda cls, v: _req(v)))


class RespondIn(BaseModel):
    accept: bool
    note: str | None = Field(default=None, max_length=1000)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ReleaseIn(BaseModel):
    amount: Decimal = Amount
    payment_method: Method
    transfer_reference: str | None = Field(default=None, max_length=100)
    remarks: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=64)


class DistributionCampaignIn(BaseModel):
    collaboration_id: int | None = Field(default=None, gt=0)  # the NGO's accepted participation
    batch_id: int | None = Field(default=None, gt=0)  # legacy clients
    name: str = Field(min_length=3, max_length=150)
    district: str | None = Field(default=None, max_length=100)
    operational_area: str | None = Field(default=None, max_length=200)
    objective: str = Field(min_length=5, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    target_beneficiaries: str | None = Field(default=None, max_length=500)
    locations: str | None = Field(default=None, max_length=1000)
    expected_beneficiaries: int | None = Field(default=None, ge=1, le=1000000)
    strategy: str | None = Field(default=None, max_length=2000)
    planned_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    start_date: date
    end_date: date | None = None
    remarks: str | None = Field(default=None, max_length=1000)
    launch: bool = False


class ParticipantIn(BaseModel):
    ngo_id: int = Field(gt=0)
    district: str = Field(min_length=2, max_length=100)
    operational_area: str | None = Field(default=None, max_length=200)
    responsibility: str = Field(min_length=3, max_length=1000)
    scope_of_work: str | None = Field(default=None, max_length=1000)
    assigned_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    instructions: str | None = Field(default=None, max_length=2000)


class CollaborationCampaignIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=4000)
    objective: str = Field(min_length=5, max_length=1000)
    districts: str = Field(min_length=2, max_length=500)
    operational_areas: str | None = Field(default=None, max_length=1000)
    start_date: date
    end_date: date | None = None
    total_budget: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    available_funding: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    instructions: str | None = Field(default=None, max_length=4000)
    required_capabilities: str | None = Field(default=None, max_length=1000)
    funding_campaign_id: int | None = Field(default=None, gt=0)
    participants: list[ParticipantIn] = Field(default_factory=list, max_length=100)
    launch: bool = False


class CollaborationCampaignPatch(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, min_length=10, max_length=4000)
    objective: str | None = Field(default=None, min_length=5, max_length=1000)
    districts: str | None = Field(default=None, min_length=2, max_length=500)
    operational_areas: str | None = Field(default=None, max_length=1000)
    start_date: date | None = None
    end_date: date | None = None
    total_budget: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    available_funding: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    instructions: str | None = Field(default=None, max_length=4000)
    required_capabilities: str | None = Field(default=None, max_length=1000)
    funding_campaign_id: int | None = Field(default=None, gt=0)
    participants: list[ParticipantIn] | None = None


class LinkFundingIn(BaseModel):
    campaign_id: int = Field(gt=0)
    assigned_amount: Decimal = Amount


class BeneficiaryPick(BaseModel):
    user_id: int = Field(gt=0)
    planned_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    assistance_type: str = Field(default="cash_assistance", max_length=40)


class AddBeneficiariesIn(BaseModel):
    items: list[BeneficiaryPick] = Field(min_length=1, max_length=200)


class DistributionIn(BaseModel):
    beneficiary_id: int = Field(gt=0)
    amount: Decimal = Amount
    distribution_date: date
    payment_method: Method
    payment_reference: str | None = Field(default=None, max_length=100)
    proof_reference: str | None = Field(default=None, max_length=200)
    idempotency_key: str | None = Field(default=None, max_length=64)

    @field_validator("distribution_date")
    @classmethod
    def not_future(cls, v):
        if v > datetime.utcnow().date():
            raise ValueError("The distribution date cannot be in the future.")
        return v


def svc(db, user):
    return CampaignFinance(db, user, role_name(db, user))


def _after(bg, db, s):
    for uid, payload in s.notes.items:
        bg.add_task(push_notification, uid, payload)
    bg.add_task(deliver_due, db.get_bind())


def _do(bg, db, user, fn, *args):
    s = svc(db, user)
    out = getattr(s, fn)(*args)
    _after(bg, db, s)
    return out


def _pdf(data, name):
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"', "Cache-Control": "no-store"})


# Collaborations ---------------------------------------------------------------------------------------
@router.get("/campaigns/{campaign_id}/finance")
def campaign_finance(campaign_id: int, user=Depends(staff_only), db: Session = Depends(get_db)):
    return svc(db, user).campaign_overview(campaign_id)


@router.post("/campaigns/{campaign_id}/collaborations", status_code=201)
def create_collaboration(campaign_id: int, body: CollaborationIn, bg: BackgroundTasks, user=Depends(staff_only),
                         db: Session = Depends(get_db)):
    data = body.model_dump()
    send = data.pop("send_invitation")
    return _do(bg, db, user, "create_collaboration", campaign_id, data, send)


@router.get("/collaborations")
def list_collaborations(campaign_id: int | None = Query(default=None, gt=0),
                        status: Literal["draft", "invited", "accepted", "rejected", "cancelled", "completed"] | None = None,
                        page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                        user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    return svc(db, user).list_collaborations(campaign_id, status, page, page_size)


@router.get("/collaborations/{collab_id}")
def get_collaboration(collab_id: int, user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    return svc(db, user).collaboration(collab_id)


@router.post("/collaborations/{collab_id}/invite")
def invite(collab_id: int, bg: BackgroundTasks, user=Depends(staff_only), db: Session = Depends(get_db)):
    return _do(bg, db, user, "invite", collab_id)


@router.post("/collaborations/{collab_id}/respond")
def respond(collab_id: int, body: RespondIn, bg: BackgroundTasks, user=Depends(only_ngo), db: Session = Depends(get_db)):
    return _do(bg, db, user, "respond", collab_id, body.accept, body.note)


@router.post("/collaborations/{collab_id}/cancel")
def cancel_collaboration(collab_id: int, body: ReasonIn, bg: BackgroundTasks, user=Depends(staff_only),
                         db: Session = Depends(get_db)):
    return _do(bg, db, user, "cancel_collaboration", collab_id, body.reason)


@router.post("/collaborations/{collab_id}/releases", status_code=201)
def release(collab_id: int, body: ReleaseIn, bg: BackgroundTasks, user=Depends(staff_only), db: Session = Depends(get_db)):
    return _do(bg, db, user, "release", collab_id, body.amount, body.payment_method, body.transfer_reference,
               body.remarks, body.idempotency_key)


@router.post("/campaign-releases/{release_id}/cancel")
def cancel_release(release_id: int, body: ReasonIn, bg: BackgroundTasks, user=Depends(staff_only),
                   db: Session = Depends(get_db)):
    return _do(bg, db, user, "cancel_release", release_id, body.reason)


@router.post("/campaign-releases/{release_id}/acknowledge")
def acknowledge(release_id: int, bg: BackgroundTasks, user=Depends(only_ngo), db: Session = Depends(get_db)):
    return _do(bg, db, user, "acknowledge", release_id)


# NGO batches and distribution campaigns -------------------------------------------------------------------
@router.get("/campaign-batches")
def batches(user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    return svc(db, user).batches()


@router.get("/distribution-campaigns")
def list_distribution_campaigns(status: Literal["active", "completed", "cancelled"] | None = None,
                                user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    return svc(db, user).list_distribution_campaigns(status)


@router.post("/distribution-campaigns", status_code=201)
def create_distribution_campaign(body: DistributionCampaignIn, bg: BackgroundTasks, user=Depends(only_ngo),
                                 db: Session = Depends(get_db)):
    if body.end_date and body.end_date < body.start_date:
        raise AuthFlowError("INVALID_DATES", "The end date must be on or after the start date.", 422)
    if not body.collaboration_id and not body.batch_id:
        raise AuthFlowError("COLLABORATION_REQUIRED", "Choose the accepted collaboration this campaign belongs to.", 422)
    data = body.model_dump()
    launch = data.pop("launch")
    return _do(bg, db, user, "create_distribution_campaign", data, launch)


@router.post("/distribution-campaigns/{dist_id}/launch")
def launch_distribution_campaign(dist_id: int, bg: BackgroundTasks, user=Depends(only_ngo), db: Session = Depends(get_db)):
    return _do(bg, db, user, "launch_distribution_campaign", dist_id)


# PDMA collaboration campaigns (parent) --------------------------------------------------------------------
@router.get("/collaboration-campaigns")
def list_collaboration_campaigns(status: Literal["draft", "launched", "completed", "cancelled"] | None = None,
                                 search: str | None = Query(default=None, max_length=100),
                                 page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                                 user=Depends(staff_only), db: Session = Depends(get_db)):
    return svc(db, user).list_collaboration_campaigns(status, search, page, page_size)


@router.post("/collaboration-campaigns", status_code=201)
def create_collaboration_campaign(body: CollaborationCampaignIn, bg: BackgroundTasks, user=Depends(staff_only),
                                  db: Session = Depends(get_db)):
    if body.end_date and body.end_date < body.start_date:
        raise AuthFlowError("INVALID_DATES", "The end date must be on or after the start date.", 422)
    data = body.model_dump()
    launch = data.pop("launch")
    return _do(bg, db, user, "create_collaboration_campaign", data, launch)


@router.get("/collaboration-campaigns/{cc_id}")
def get_collaboration_campaign(cc_id: int, user=Depends(staff_only), db: Session = Depends(get_db)):
    return svc(db, user).collaboration_campaign(cc_id)


@router.patch("/collaboration-campaigns/{cc_id}")
def update_collaboration_campaign(cc_id: int, body: CollaborationCampaignPatch, user=Depends(staff_only),
                                  db: Session = Depends(get_db)):
    return svc(db, user).update_collaboration_campaign(cc_id, body.model_dump(exclude_unset=True))


@router.post("/collaboration-campaigns/{cc_id}/launch")
def launch_collaboration_campaign(cc_id: int, bg: BackgroundTasks, user=Depends(staff_only),
                                  db: Session = Depends(get_db)):
    return _do(bg, db, user, "launch_collaboration_campaign", cc_id)


@router.post("/collaboration-campaigns/{cc_id}/participants", status_code=201)
def add_participant(cc_id: int, body: ParticipantIn, bg: BackgroundTasks, user=Depends(staff_only),
                    db: Session = Depends(get_db)):
    return _do(bg, db, user, "add_participant", cc_id, body.model_dump())


@router.post("/collaboration-campaigns/{cc_id}/complete")
def complete_collaboration_campaign(cc_id: int, user=Depends(staff_only), db: Session = Depends(get_db)):
    return svc(db, user).close_collaboration_campaign(cc_id, "completed")


@router.post("/collaboration-campaigns/{cc_id}/cancel")
def cancel_collaboration_campaign(cc_id: int, body: ReasonIn, user=Depends(staff_only), db: Session = Depends(get_db)):
    return svc(db, user).close_collaboration_campaign(cc_id, "cancelled", body.reason)


@router.post("/collaborations/{collab_id}/funding")
def link_funding(collab_id: int, body: LinkFundingIn, bg: BackgroundTasks, user=Depends(staff_only),
                 db: Session = Depends(get_db)):
    return _do(bg, db, user, "link_funding", collab_id, body.campaign_id, body.assigned_amount)


@router.get("/distribution-campaigns/{dist_id}")
def get_distribution_campaign(dist_id: int, user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    return svc(db, user).distribution_campaign(dist_id)


@router.post("/distribution-campaigns/{dist_id}/complete")
def complete_distribution_campaign(dist_id: int, user=Depends(only_ngo), db: Session = Depends(get_db)):
    return svc(db, user).complete_distribution_campaign(dist_id)


@router.get("/distribution-campaigns/{dist_id}/eligible-beneficiaries")
def eligible(dist_id: int, type: Literal["householder", "shopkeeper"] | None = None,
             search: str | None = Query(default=None, max_length=100), page: int = Query(default=1, ge=1),
             page_size: int = Query(default=20, ge=1, le=100), user=Depends(only_ngo), db: Session = Depends(get_db)):
    return svc(db, user).search_beneficiaries(dist_id, type, search, page, page_size)


@router.post("/distribution-campaigns/{dist_id}/beneficiaries")
def add_beneficiaries(dist_id: int, body: AddBeneficiariesIn, user=Depends(only_ngo), db: Session = Depends(get_db)):
    return svc(db, user).add_beneficiaries(dist_id, [i.model_dump() for i in body.items])


@router.post("/distribution-campaigns/{dist_id}/distributions", status_code=201)
def record_distribution(dist_id: int, body: DistributionIn, bg: BackgroundTasks, user=Depends(only_ngo),
                        db: Session = Depends(get_db)):
    return _do(bg, db, user, "record_distribution", dist_id, body.model_dump())


@router.post("/beneficiary-distributions/{distribution_id}/reverse")
def reverse_distribution(distribution_id: int, body: ReasonIn, user=Depends(only_ngo), db: Session = Depends(get_db)):
    return svc(db, user).reverse_distribution(distribution_id, body.reason)


# Ledgers, dashboards, traceability ---------------------------------------------------------------------
Scope = Literal["campaign", "collaboration", "batch", "distribution"]


@router.get("/finance-ledger/{scope}/{scope_id}")
def ledger(scope: Scope, scope_id: int, date_from: date | None = None, date_to: date | None = None,
           search: str | None = Query(default=None, max_length=100), page: int = Query(default=1, ge=1),
           page_size: int = Query(default=50, ge=1, le=200), user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    out = svc(db, user).ledger(scope, scope_id, date_from, date_to, search, page, page_size)
    out.pop("all", None)
    return out


@router.get("/finance-ledger/{scope}/{scope_id}/pdf")
def ledger_pdf_route(scope: Scope, scope_id: int, date_from: date | None = None, date_to: date | None = None,
                     user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    out = svc(db, user).ledger(scope, scope_id, date_from, date_to, None, None, None)
    return _pdf(ledger_pdf(out, out["owner"], datetime.utcnow().isoformat()), f"{scope}-ledger-{scope_id}")


@router.get("/campaigns/{campaign_id}/finance/statement.pdf")
def campaign_statement(campaign_id: int, user=Depends(staff_only), db: Session = Depends(get_db)):
    s = svc(db, user)
    return _pdf(campaign_statement_pdf(s.campaign_overview(campaign_id), datetime.utcnow().isoformat()),
                f"campaign-statement-{campaign_id}")


@router.get("/beneficiary-distributions/{distribution_id}/voucher.pdf")
def distribution_voucher(distribution_id: int, user=Depends(staff_or_ngo), db: Session = Depends(get_db)):
    s = svc(db, user)
    x = db.get(BeneficiaryDistribution, distribution_id)
    if x is None:
        raise AuthFlowError("DISTRIBUTION_NOT_FOUND", "This distribution does not exist.", 404)
    d = s.distribution_campaign(x.distribution_campaign_id)  # ownership check for NGOs
    row = next(r for r in d["distributions"] if r["id"] == x.id)
    return _pdf(distribution_voucher_pdf(row, d, datetime.utcnow().isoformat()), f"distribution-{x.reference_no}")


@router.get("/campaign-finance/dashboard")
def pdma_dashboard(user=Depends(staff_only), db: Session = Depends(get_db)):
    return svc(db, user).pdma_dashboard()


@router.get("/ngo/campaign-finance/dashboard")
def ngo_dashboard(user=Depends(only_ngo), db: Session = Depends(get_db)):
    return svc(db, user).ngo_dashboard()


@router.get("/donor/campaign-utilization")
def donor_utilization(user=Depends(only_donor), db: Session = Depends(get_db)):
    return svc(db, user).donor_utilization()

"""Donation campaigns (Phase 2).

Campaigns are a separate funding channel: their money lives only in campaign_contributions and never becomes a
general donor donation, an NGO request or an allocation. There is no campaign disbursement workflow here.

Lifecycle
  NGO proposal : draft -> pending_review -> under_review -> approved | rejected | changes_requested (-> pending_review)
  PDMA campaign: draft -> approved (PDMA verifies its own campaign)
  publication  : approved -> scheduled (start date in the future) | active
  running      : active <-> paused, active -> goal_reached (verified total reaches the target),
                 active | goal_reached | paused -> completed (end date passed or closed by PDMA)
  cancelled    : any state that is not final
Progress counts verified contributions only. The cap (target, or the overfunding limit when allowed) is checked
against verified + pending contributions under a row lock, so the target cannot be oversubscribed.
"""
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from config.fund_mail import render_notice
from config.outbox import enqueue
from controller.authentication import AuthFlowError
from controller.donation import DonationController, iso, money
from model.campaign_model import (
    DONOR_VISIBLE,
    CampaignContribution,
    CampaignDocument,
    CampaignEvent,
    CampaignNotice,
    DonationCampaign,
)
from model.donation_model import Ngo
from model.notification_model import Notification
from model.role_model import Role
from model.user_model import User

ZERO = Decimal("0.00")
PKT = timedelta(hours=5)
FINAL = ("completed", "rejected", "cancelled")
NGO_EDITABLE = ("draft", "changes_requested")
PDMA_EDITABLE = ("draft", "pending_review", "under_review", "changes_requested", "approved", "scheduled")
STORAGE = Path(__file__).resolve().parent.parent / "storage" / "campaigns"
MAX_UPLOAD = 5 * 1024 * 1024
# Content type is decided from the file's first bytes, never from the client.
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"%PDF-", "application/pdf", ".pdf"),
)
EDIT_FIELDS = ("name", "short_description", "description", "objective", "reason", "target_amount",
               "allow_overfunding", "overfunding_limit", "start_date", "end_date", "district", "contact_info")


def now_utc():
    return datetime.utcnow()


def today_pkt() -> date:
    return (datetime.utcnow() + PKT).date()


def pkr(v):
    return f"PKR {Decimal(v):,.2f}"


def sniff(data: bytes):
    for magic, ctype, ext in SIGNATURES:
        if data.startswith(magic):
            return ctype, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None, None


class Campaigns:
    """All campaign rules. `role` is the caller's role name; every query is scoped by it."""

    def __init__(self, db, user, role):
        self.db = db
        self.user = user
        self.role = role
        self.notes = []  # (user_id, payload) for live push after commit

    @property
    def staff(self):
        return self.role in ("ADMIN", "PDMA_OFFICER")

    # Helpers ----------------------------------------------------------------------------------------
    def _ngo(self):
        ngo = self.db.scalar(select(Ngo).where(Ngo.user_id == self.user.id))
        if not ngo or not ngo.is_active:
            raise AuthFlowError("NGO_NOT_LINKED", "Your account is not linked to an active NGO.", 403)
        return ngo

    def _get(self, campaign_id, lock=False):
        q = select(DonationCampaign).where(DonationCampaign.id == campaign_id)
        if lock:
            q = q.with_for_update()
        c = self.db.scalar(q)
        if c is None or not self._can_see(c):
            raise AuthFlowError("CAMPAIGN_NOT_FOUND", "This campaign does not exist.", 404)
        return c

    def _can_see(self, c):
        if self.staff:
            return True
        if self.role == "NGO":
            return c.ngo_id is not None and c.ngo_id == self._ngo().id
        if self.role == "DONOR":
            return c.status in DONOR_VISIBLE and c.published_at is not None
        return False

    def _event(self, c, event_type, from_status, note=None):
        self.db.add(CampaignEvent(campaign_id=c.id, event_type=event_type, from_status=from_status,
                                  to_status=c.status, actor_id=self.user.id if self.user else None, note=note))

    def _move(self, c, allowed, to, event_type, note=None):
        if c.status not in allowed:
            raise AuthFlowError("INVALID_STATUS", f"This action is not allowed while the campaign is {c.status.replace('_', ' ')}.", 409)
        prev = c.status
        c.status = to
        self._event(c, event_type, prev, note)

    def _notify(self, user_ids, type_, title, message, c, related_type):
        for uid in dict.fromkeys(u for u in user_ids if u):
            n = Notification(user_id=uid, type=type_, title=title, message=message,
                             related_id=c.id, related_type=related_type)
            self.db.add(n)
            self.db.flush()
            self.notes.append((uid, n.to_dict()))

    def _once(self, c, key):
        """True the first time `key` is claimed for this campaign (unique row); later calls return False."""
        try:
            with self.db.begin_nested():
                self.db.add(CampaignNotice(campaign_id=c.id, notice_key=key))
                self.db.flush()
            return True
        except IntegrityError:
            return False

    def _staff_users(self):
        roles = select(Role.id).where(Role.name.in_(("ADMIN", "PDMA_OFFICER")))
        return list(self.db.scalars(select(User).where(User.role_id.in_(roles), User.is_active.is_(True))))

    def _donor_users(self):
        role = select(Role.id).where(Role.name == "DONOR")
        return list(self.db.scalars(select(User).where(User.role_id.in_(role), User.is_active.is_(True))))

    def _ngo_user(self, c):
        if not c.ngo_id:
            return None
        ngo = self.db.get(Ngo, c.ngo_id)
        return self.db.get(User, ngo.user_id) if ngo and ngo.user_id else None

    def _email(self, user, heading, intro, rows, path, link_text="Open campaign"):
        if user and user.email:
            enqueue(self.db, user.email, *render_notice(heading, "Donation campaign", user.name, intro, rows, path, link_text))

    def _tell_ngo(self, c, type_, title, message, email_intro=None):
        u = self._ngo_user(c)
        if u:
            self._notify([u.id], type_, title, message, c, "ngo_campaign")
            self._email(u, title, email_intro or message, [("Campaign", f"{c.name} ({c.reference_no})"),
                                                          ("Status", c.status.replace("_", " ").title())],
                        f"/ngo/campaigns/{c.id}")

    def _tell_staff(self, c, type_, title, message, exclude_self=True):
        ids = [u.id for u in self._staff_users() if not (exclude_self and self.user and u.id == self.user.id)]
        self._notify(ids, type_, title, message, c, "campaign_admin")

    def _tell_donors(self, c, key, type_, title, message, donors=None):
        """Broadcast once per campaign and key (published, started, goal reached, completed, cancelled)."""
        if not self._once(c, key):
            return
        users = donors if donors is not None else self._donor_users()
        self._notify([u.id for u in users], type_, title, message, c, "campaign")

    def _contributors(self, c):
        ids = select(CampaignContribution.donor_id).where(CampaignContribution.campaign_id == c.id,
                                                          CampaignContribution.payment_status != "rejected")
        return list(self.db.scalars(select(User).where(User.id.in_(ids))))

    def _totals(self, campaign_ids):
        if not campaign_ids:
            return {}
        rows = self.db.execute(
            select(CampaignContribution.campaign_id, CampaignContribution.payment_status,
                   func.count(CampaignContribution.id), func.coalesce(func.sum(CampaignContribution.amount), 0))
            .where(CampaignContribution.campaign_id.in_(campaign_ids))
            .group_by(CampaignContribution.campaign_id, CampaignContribution.payment_status)).all()
        out = {}
        for cid, status, count, amount in rows:
            out.setdefault(cid, {})[status] = (count, Decimal(amount))
        return out

    @staticmethod
    def _cap(c):
        if not c.allow_overfunding:
            return Decimal(c.target_amount)
        return Decimal(c.overfunding_limit) if c.overfunding_limit is not None else None

    # Serialisation ----------------------------------------------------------------------------------
    def row(self, c, totals=None, detail=False):
        t = (totals if totals is not None else self._totals([c.id])).get(c.id, {})
        verified_count, verified = t.get("verified", (0, ZERO))
        pending_count, pending = t.get("pending", (0, ZERO))
        target = Decimal(c.target_amount)
        progress = float(min(verified / target * 100, Decimal(999))) if target else 0.0
        donors = self.db.scalar(select(func.count(func.distinct(CampaignContribution.donor_id)))
                                .where(CampaignContribution.campaign_id == c.id,
                                       CampaignContribution.payment_status == "verified")) if detail else None
        ngo = self.db.get(Ngo, c.ngo_id) if c.ngo_id else None
        cap = self._cap(c)
        out = {
            "id": c.id, "reference_no": c.reference_no, "source_type": c.source_type,
            "organizer": ngo.name if ngo else "PDMA Punjab",
            "ngo_id": c.ngo_id if not self.role == "DONOR" else None,
            "name": c.name, "short_description": c.short_description, "objective": c.objective,
            "target_amount": money(target), "allow_overfunding": c.allow_overfunding,
            "overfunding_limit": money(c.overfunding_limit) if c.overfunding_limit is not None else None,
            "start_date": iso(c.start_date), "end_date": iso(c.end_date), "district": c.district,
            "status": c.status, "has_cover": c.cover_document_id is not None,
            "collected_amount": money(verified), "verified_count": verified_count,
            "remaining_amount": money(max(target - verified, ZERO)), "progress_percent": round(progress, 1),
            "accepting_contributions": c.status == "active" and (cap is None or verified + pending < cap),
            "published_at": iso(c.published_at), "created_at": iso(c.created_at), "updated_at": iso(c.updated_at),
        }
        if self.role != "DONOR":
            out.update({"pending_amount": money(pending), "pending_count": pending_count,
                        "submitted_at": iso(c.submitted_at)})
        if detail:
            out.update({"description": c.description, "reason": c.reason, "contact_info": c.contact_info,
                        "donor_count": donors, "cancellation_reason": c.cancellation_reason})
            if self.role != "DONOR":
                out.update({"rejection_reason": c.rejection_reason, "changes_requested": c.changes_requested,
                            "approved_at": iso(c.approved_at), "verified_at": iso(c.verified_at),
                            "rejected_at": iso(c.rejected_at)})
            if self.staff:
                out["review_notes"] = c.review_notes
            out["documents"] = self._documents(c)
            out["history"] = self._history(c)
            out["actions"] = self._actions(c)
        return out

    def _documents(self, c):
        q = select(CampaignDocument).where(CampaignDocument.campaign_id == c.id)
        if self.role == "DONOR":
            q = q.where(CampaignDocument.visibility == "donor_public")
        return [{"id": d.id, "document_name": d.document_name, "content_type": d.content_type,
                 "size_bytes": d.size_bytes, "document_type": d.document_type, "visibility": d.visibility,
                 "created_at": iso(d.created_at)}
                for d in self.db.scalars(q.order_by(CampaignDocument.id))]

    def _history(self, c):
        q = select(CampaignEvent).where(CampaignEvent.campaign_id == c.id).order_by(CampaignEvent.id)
        out = []
        for e in self.db.scalars(q):
            if self.role == "DONOR" and e.to_status not in DONOR_VISIBLE:
                continue
            note = e.note if (self.staff or e.event_type not in ("reviewed", "note")) else None
            out.append({"event_type": e.event_type, "from_status": e.from_status, "to_status": e.to_status,
                        "note": note if self.role != "DONOR" else None, "created_at": iso(e.created_at)})
        return out

    def _actions(self, c):
        s = c.status
        if self.staff:
            acts = []
            if s in PDMA_EDITABLE and (c.source_type == "PDMA" or s in ("approved", "scheduled")):
                acts.append("edit")
            if s == "pending_review":
                acts.append("start_review")
            if s in ("pending_review", "under_review"):
                acts += ["approve", "reject", "request_changes"]
            if s == "draft" and c.source_type == "PDMA":
                acts.append("approve")
            if s == "approved":
                acts.append("publish")
            if s == "active":
                acts.append("pause")
            if s == "paused":
                acts.append("resume")
            if s in ("active", "goal_reached", "paused"):
                acts.append("complete")
            if s not in FINAL:
                acts.append("cancel")
            if s not in FINAL:
                acts.append("upload")
            return acts
        if self.role == "NGO":
            return ["edit", "submit", "upload"] if s in NGO_EDITABLE else []
        if self.role == "DONOR":
            return ["contribute"] if s == "active" else []
        return []

    # Create / edit ----------------------------------------------------------------------------------
    def _validate(self, data, existing=None):
        get = lambda k: data[k] if k in data else (getattr(existing, k) if existing else None)  # noqa: E731
        target = Decimal(get("target_amount"))
        start, end = get("start_date"), get("end_date")
        if end and end < start:
            raise AuthFlowError("INVALID_DATES", "The end date must be on or after the start date.", 422)
        if (existing is None or "end_date" in data) and end and end < today_pkt():
            raise AuthFlowError("INVALID_DATES", "The end date cannot be in the past.", 422)
        if get("allow_overfunding"):
            limit = get("overfunding_limit")
            if limit is not None and Decimal(limit) < target:
                raise AuthFlowError("INVALID_OVERFUNDING_LIMIT", "The overfunding limit cannot be below the target.", 422)
        else:
            data["overfunding_limit"] = None
        if existing is not None and "target_amount" in data:
            verified = self._totals([existing.id]).get(existing.id, {}).get("verified", (0, ZERO))[1]
            if existing.status in ("scheduled",) and target < verified:
                raise AuthFlowError("TARGET_BELOW_COLLECTED", "The target cannot be lower than the amount collected.", 409)

    def create(self, data):
        self._validate(data)
        if self.staff:
            source, ngo_id = "PDMA", None
        else:
            source, ngo_id = "NGO", self._ngo().id
        c = DonationCampaign(reference_no=DonationController(self.db).next_document_no_for("CMP"),
                             source_type=source, ngo_id=ngo_id, created_by=self.user.id, status="draft",
                             **{k: data.get(k) for k in EDIT_FIELDS})
        self.db.add(c)
        self.db.flush()
        self._event(c, "created", None)
        self.db.commit()
        return self.row(c, detail=True)

    def update(self, campaign_id, data):
        c = self._get(campaign_id, lock=True)
        if self.staff:
            if c.status not in PDMA_EDITABLE or (c.source_type == "NGO" and c.status not in ("approved", "scheduled")):
                raise AuthFlowError("NOT_EDITABLE", "This campaign can no longer be edited.", 409)
        elif c.status not in NGO_EDITABLE:
            raise AuthFlowError("NOT_EDITABLE", "You can only edit a draft or a proposal returned for changes.", 409)
        self._validate(data, c)
        for k in EDIT_FIELDS:
            if k in data:
                setattr(c, k, data[k])
        self._event(c, "edited", c.status)
        self.db.commit()
        return self.row(c, detail=True)

    # NGO proposal -----------------------------------------------------------------------------------
    def submit(self, campaign_id):
        c = self._get(campaign_id, lock=True)
        self._move(c, NGO_EDITABLE, "pending_review", "submitted")
        c.submitted_at = now_utc()
        ngo = self.db.get(Ngo, c.ngo_id)
        self._tell_staff(c, "campaign_proposal_submitted", "New campaign proposal",
                         f"{ngo.name} submitted the campaign proposal {c.reference_no} ({c.name}) for review.",
                         exclude_self=False)
        for u in self._staff_users():
            self._email(u, "New campaign proposal", f"{ngo.name} submitted a campaign proposal for review.",
                        [("Campaign", f"{c.name} ({c.reference_no})"), ("Target", pkr(c.target_amount))],
                        f"/campaigns/{c.id}", "Review proposal")
        self.db.commit()
        return self.row(c, detail=True)

    # PDMA review ------------------------------------------------------------------------------------
    def start_review(self, campaign_id):
        c = self._get(campaign_id, lock=True)
        self._move(c, ("pending_review",), "under_review", "review_started")
        self._tell_ngo(c, "campaign_under_review", "Campaign proposal under review",
                       f"PDMA has started reviewing your campaign proposal {c.reference_no}.")
        self.db.commit()
        return self.row(c, detail=True)

    def approve(self, campaign_id, notes=None):
        c = self._get(campaign_id, lock=True)
        allowed = ("pending_review", "under_review") + (("draft",) if c.source_type == "PDMA" else ())
        self._move(c, allowed, "approved", "approved", notes)
        c.verified_at = c.approved_at = now_utc()
        c.verified_by = c.approved_by = self.user.id
        c.review_notes = notes or c.review_notes
        c.changes_requested = None
        self._tell_ngo(c, "campaign_approved", "Campaign proposal approved",
                       f"Your campaign proposal {c.reference_no} was approved. PDMA will publish it to donors.")
        self.db.commit()
        return self.row(c, detail=True)

    def reject(self, campaign_id, reason):
        c = self._get(campaign_id, lock=True)
        self._move(c, ("pending_review", "under_review"), "rejected", "rejected", reason)
        c.rejected_at = now_utc()
        c.rejection_reason = reason
        self._tell_ngo(c, "campaign_rejected", "Campaign proposal rejected",
                       f"Your campaign proposal {c.reference_no} was rejected. Reason: {reason}"[:500])
        self.db.commit()
        return self.row(c, detail=True)

    def request_changes(self, campaign_id, comments):
        c = self._get(campaign_id, lock=True)
        self._move(c, ("pending_review", "under_review"), "changes_requested", "changes_requested", comments)
        c.changes_requested = comments
        self._tell_ngo(c, "campaign_changes_requested", "Changes requested on your campaign",
                       f"PDMA requested changes to {c.reference_no}: {comments}"[:500])
        self.db.commit()
        return self.row(c, detail=True)

    # Publication and running ------------------------------------------------------------------------
    def publish(self, campaign_id):
        c = self._get(campaign_id, lock=True)
        if c.end_date and c.end_date < today_pkt():
            raise AuthFlowError("INVALID_DATES", "The end date has passed. Update the dates before publishing.", 409)
        to = "scheduled" if c.start_date > today_pkt() else "active"
        self._move(c, ("approved",), to, "published")
        c.published_at = now_utc()
        c.published_by = self.user.id
        verb = "is now open for contributions" if to == "active" else f"opens on {c.start_date:%d %B %Y}"
        self._tell_donors(c, "published", "campaign_published", "New donation campaign",
                          f"{c.name} ({c.reference_no}) {verb}. Target {pkr(c.target_amount)}.")
        if to == "active":
            self._once(c, "started")
        self._tell_ngo(c, "campaign_published", "Campaign published",
                       f"Your campaign {c.reference_no} was published and {verb}.")
        self.db.commit()
        return self.row(c, detail=True)

    def pause(self, campaign_id, reason=None):
        c = self._get(campaign_id, lock=True)
        self._move(c, ("active",), "paused", "paused", reason)
        self._tell_ngo(c, "campaign_paused", "Campaign paused", f"Campaign {c.reference_no} was paused by PDMA.")
        self.db.commit()
        return self.row(c, detail=True)

    def resume(self, campaign_id):
        c = self._get(campaign_id, lock=True)
        verified = self._totals([c.id]).get(c.id, {}).get("verified", (0, ZERO))[1]
        to = "goal_reached" if verified >= Decimal(c.target_amount) and not c.allow_overfunding else "active"
        self._move(c, ("paused",), to, "resumed")
        self._tell_ngo(c, "campaign_resumed", "Campaign resumed", f"Campaign {c.reference_no} is running again.")
        self.db.commit()
        return self.row(c, detail=True)

    def complete(self, campaign_id, note=None):
        c = self._get(campaign_id, lock=True)
        self._close(c, note or "Closed by PDMA")
        self.db.commit()
        return self.row(c, detail=True)

    def _close(self, c, note):
        self._move(c, ("active", "goal_reached", "paused"), "completed", "completed", note)
        verified = self._totals([c.id]).get(c.id, {}).get("verified", (0, ZERO))[1]
        self._tell_donors(c, "completed", "campaign_completed", "Campaign completed",
                          f"{c.name} ({c.reference_no}) has closed with {pkr(verified)} collected. Thank you for your support.",
                          donors=self._contributors(c))
        self._tell_ngo(c, "campaign_completed", "Campaign completed",
                       f"Campaign {c.reference_no} has closed with {pkr(verified)} collected.")

    def cancel(self, campaign_id, reason):
        c = self._get(campaign_id, lock=True)
        was_public = c.published_at is not None
        self._move(c, ("draft", "pending_review", "under_review", "changes_requested", "approved", "scheduled",
                       "active", "goal_reached", "paused"), "cancelled", "cancelled", reason)
        c.cancellation_reason = reason
        if was_public:
            self._tell_donors(c, "cancelled", "campaign_cancelled", "Campaign cancelled",
                              f"{c.name} ({c.reference_no}) was cancelled. Reason: {reason}"[:500],
                              donors=self._contributors(c))
        self._tell_ngo(c, "campaign_cancelled", "Campaign cancelled",
                       f"Campaign {c.reference_no} was cancelled. Reason: {reason}"[:500])
        self.db.commit()
        return self.row(c, detail=True)

    # Lists and dashboards ---------------------------------------------------------------------------
    def list(self, status=None, search=None, source=None, page=1, page_size=20, scope=None):
        q = select(DonationCampaign)
        if self.role == "NGO":
            q = q.where(DonationCampaign.ngo_id == self._ngo().id)
        elif self.role == "DONOR":
            q = q.where(DonationCampaign.status.in_(DONOR_VISIBLE), DonationCampaign.published_at.is_not(None))
            if scope == "open":
                q = q.where(DonationCampaign.status.in_(("active", "goal_reached", "scheduled")))
            elif scope == "closed":
                q = q.where(DonationCampaign.status.in_(("completed", "cancelled", "paused")))
        elif not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        elif scope == "review":
            q = q.where(DonationCampaign.status.in_(("pending_review", "under_review")))
        if status:
            q = q.where(DonationCampaign.status == status)
        if source and self.staff:
            q = q.where(DonationCampaign.source_type == source)
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(DonationCampaign.name.like(like), DonationCampaign.reference_no.like(like),
                            DonationCampaign.district.like(like)))
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = list(self.db.scalars(q.order_by(DonationCampaign.created_at.desc(), DonationCampaign.id.desc())
                                    .offset((page - 1) * page_size).limit(page_size)))
        totals = self._totals([c.id for c in rows])
        return {"items": [self.row(c, totals) for c in rows], "total": total, "page": page, "page_size": page_size}

    def detail(self, campaign_id):
        return self.row(self._get(campaign_id), detail=True)

    def dashboard(self):
        q = select(DonationCampaign.status, func.count(DonationCampaign.id))
        if self.role == "NGO":
            q = q.where(DonationCampaign.ngo_id == self._ngo().id)
        counts = dict(self.db.execute(q.group_by(DonationCampaign.status)).all())
        cq = select(CampaignContribution.payment_status, func.count(CampaignContribution.id),
                    func.coalesce(func.sum(CampaignContribution.amount), 0))
        if self.role == "NGO":
            cq = cq.join(DonationCampaign, DonationCampaign.id == CampaignContribution.campaign_id) \
                   .where(DonationCampaign.ngo_id == self._ngo().id)
        money_rows = {s: (n, Decimal(a)) for s, n, a in self.db.execute(cq.group_by(CampaignContribution.payment_status)).all()}
        return {
            "counts": {s: counts.get(s, 0) for s in ("draft", "pending_review", "under_review", "changes_requested",
                                                     "approved", "scheduled", "active", "goal_reached", "completed",
                                                     "rejected", "paused", "cancelled")},
            "total": sum(counts.values()),
            "collected_amount": money(money_rows.get("verified", (0, ZERO))[1]),
            "pending_amount": money(money_rows.get("pending", (0, ZERO))[1]),
            "pending_contributions": money_rows.get("pending", (0, ZERO))[0],
            "verified_contributions": money_rows.get("verified", (0, ZERO))[0],
        }

    # Contributions ----------------------------------------------------------------------------------
    def contribute(self, campaign_id, data):
        c = self._get(campaign_id, lock=True)  # lock serialises the cap check
        if c.status != "active":
            raise AuthFlowError("CAMPAIGN_NOT_OPEN", "This campaign is not accepting contributions.", 409)
        if c.end_date and c.end_date < today_pkt():
            raise AuthFlowError("CAMPAIGN_NOT_OPEN", "This campaign has ended.", 409)
        amount = Decimal(data["amount"])
        ref = (data.get("payment_reference") or "").strip() or None
        if ref and self.db.scalar(select(CampaignContribution.id).where(
                CampaignContribution.donor_id == self.user.id, CampaignContribution.payment_reference == ref,
                CampaignContribution.payment_status != "rejected")):
            raise AuthFlowError("DUPLICATE_PAYMENT", "You have already submitted a payment with this reference.", 409)
        cap = self._cap(c)
        if cap is not None:
            t = self._totals([c.id]).get(c.id, {})
            committed = t.get("verified", (0, ZERO))[1] + t.get("pending", (0, ZERO))[1]
            room = max(cap - committed, ZERO)
            if amount > room:
                raise AuthFlowError("OVERFUNDING_NOT_ALLOWED",
                                    f"This campaign can accept at most {pkr(room)} more." if room > 0
                                    else "This campaign has reached its target.", 409)
        x = CampaignContribution(reference_no=DonationController(self.db).next_document_no_for("CMP-IN"),
                                 campaign_id=c.id, donor_id=self.user.id, amount=amount,
                                 payment_method=data.get("payment_method"), payment_reference=ref,
                                 remarks=(data.get("remarks") or "").strip() or None)
        self.db.add(x)
        self.db.flush()
        self._tell_staff(c, "campaign_contribution_received", "Campaign contribution awaiting verification",
                         f"{self.user.name} pledged {pkr(amount)} to {c.reference_no} ({x.reference_no}).",
                         exclude_self=False)
        self.db.commit()
        return self.contribution_row(x, c)

    def contribution_row(self, x, c=None):
        c = c or self.db.get(DonationCampaign, x.campaign_id)
        out = {"id": x.id, "reference_no": x.reference_no, "campaign_id": x.campaign_id,
               "campaign_reference_no": c.reference_no, "campaign_name": c.name, "amount": money(x.amount),
               "payment_method": x.payment_method, "payment_reference": x.payment_reference,
               "payment_status": x.payment_status, "verified_at": iso(x.verified_at),
               "rejection_reason": x.rejection_reason, "remarks": x.remarks, "created_at": iso(x.created_at)}
        if self.staff:
            donor = self.db.get(User, x.donor_id)
            out["donor"] = {"id": donor.id, "name": donor.name, "email": donor.email} if donor else None
        return out

    def contributions(self, campaign_id=None, status=None, page=1, page_size=20):
        q = select(CampaignContribution)
        if self.role == "DONOR":
            q = q.where(CampaignContribution.donor_id == self.user.id)
        elif self.role == "NGO":
            if campaign_id is None:
                raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
            self._get(campaign_id)
            q = q.where(CampaignContribution.payment_status == "verified")
        elif not self.staff:
            raise AuthFlowError("FORBIDDEN", "You do not have access to this action.", 403)
        if campaign_id is not None:
            if self.staff:
                self._get(campaign_id)
            q = q.where(CampaignContribution.campaign_id == campaign_id)
        if status:
            q = q.where(CampaignContribution.payment_status == status)
        total = self.db.scalar(select(func.count()).select_from(q.subquery()))
        rows = list(self.db.scalars(q.order_by(CampaignContribution.created_at.desc(), CampaignContribution.id.desc())
                                    .offset((page - 1) * page_size).limit(page_size)))
        items = [self.contribution_row(x) for x in rows]
        if self.role == "NGO":  # donor identities are not shared with NGOs
            for i in items:
                i.pop("payment_reference", None)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def verify_contribution(self, contribution_id, action, reason=None):
        x = self.db.scalar(select(CampaignContribution).where(CampaignContribution.id == contribution_id))
        if x is None:
            raise AuthFlowError("CONTRIBUTION_NOT_FOUND", "This contribution does not exist.", 404)
        c = self._get(x.campaign_id, lock=True)
        x = self.db.scalar(select(CampaignContribution).where(CampaignContribution.id == contribution_id)
                           .with_for_update().execution_options(populate_existing=True))
        if x.payment_status != "pending":
            raise AuthFlowError("ALREADY_PROCESSED", "This contribution has already been processed.", 409)
        donor = self.db.get(User, x.donor_id)
        if action == "reject":
            if not reason:
                raise AuthFlowError("REASON_REQUIRED", "Give a reason for rejecting this payment.", 422)
            x.payment_status, x.rejection_reason = "rejected", reason
            x.verified_by, x.verified_at = self.user.id, now_utc()
            self._notify([donor.id], "campaign_contribution_rejected", "Campaign payment not confirmed",
                         f"Your payment {x.reference_no} to {c.name} could not be confirmed. Reason: {reason}"[:500],
                         c, "campaign")
        else:
            x.payment_status = "verified"
            x.verified_by, x.verified_at = self.user.id, now_utc()
            self._notify([donor.id], "campaign_contribution_verified", "Campaign payment confirmed",
                         f"Your contribution of {pkr(x.amount)} to {c.name} ({x.reference_no}) was confirmed.",
                         c, "campaign")
            self._email(donor, "Campaign payment confirmed", f"Your contribution to {c.name} was confirmed.",
                        [("Reference", x.reference_no), ("Amount", pkr(x.amount)), ("Campaign", c.reference_no)],
                        f"/donor/campaigns/{c.id}")
            self.db.flush()
            verified = self._totals([c.id]).get(c.id, {}).get("verified", (0, ZERO))[1]
            if verified >= Decimal(c.target_amount) and c.status == "active":
                if not c.allow_overfunding:
                    self._move(c, ("active",), "goal_reached", "goal_reached")
                if self._once(c, "goal_reached"):
                    msg = f"{c.name} ({c.reference_no}) reached its target of {pkr(c.target_amount)}."
                    self._notify([u.id for u in self._contributors(c)], "campaign_goal_reached",
                                 "Campaign target reached", msg, c, "campaign")
                    self._tell_staff(c, "campaign_goal_reached", "Campaign target reached", msg, exclude_self=False)
                    self._tell_ngo(c, "campaign_goal_reached", "Campaign target reached", msg)
        self.db.commit()
        return self.contribution_row(x, c)

    def donor_summary(self):
        rows = self.db.execute(
            select(CampaignContribution.payment_status, func.count(CampaignContribution.id),
                   func.coalesce(func.sum(CampaignContribution.amount), 0))
            .where(CampaignContribution.donor_id == self.user.id)
            .group_by(CampaignContribution.payment_status)).all()
        t = {s: (n, Decimal(a)) for s, n, a in rows}
        campaigns = self.db.scalar(select(func.count(func.distinct(CampaignContribution.campaign_id)))
                                   .where(CampaignContribution.donor_id == self.user.id,
                                          CampaignContribution.payment_status == "verified"))
        open_count = self.db.scalar(select(func.count(DonationCampaign.id))
                                    .where(DonationCampaign.status == "active"))
        return {"verified_amount": money(t.get("verified", (0, ZERO))[1]),
                "pending_amount": money(t.get("pending", (0, ZERO))[1]),
                "rejected_amount": money(t.get("rejected", (0, ZERO))[1]),
                "verified_count": t.get("verified", (0, ZERO))[0], "pending_count": t.get("pending", (0, ZERO))[0],
                "campaigns_supported": campaigns, "open_campaigns": open_count}

    # Documents --------------------------------------------------------------------------------------
    def upload(self, campaign_id, filename, data: bytes, document_type, visibility):
        c = self._get(campaign_id, lock=True)
        if "upload" not in self._actions(c):
            raise AuthFlowError("NOT_EDITABLE", "Documents cannot be added to this campaign now.", 409)
        if not data:
            raise AuthFlowError("FILE_EMPTY", "The file is empty.", 422)
        if len(data) > MAX_UPLOAD:
            raise AuthFlowError("FILE_TOO_LARGE", "Files must be 5 MB or smaller.", 413)
        ctype, ext = sniff(data)
        if not ctype or (document_type == "cover" and ctype == "application/pdf"):
            raise AuthFlowError("FILE_TYPE_NOT_ALLOWED",
                                "Upload a PNG, JPG or WEBP image" + ("." if document_type == "cover" else " or a PDF."), 415)
        if not self.staff:
            visibility = "internal"  # PDMA decides what donors can see
        name = Path(filename or "document").name[:150] or "document"
        key = f"{secrets.token_hex(16)}{ext}"
        STORAGE.mkdir(parents=True, exist_ok=True)
        (STORAGE / key).write_bytes(data)
        d = CampaignDocument(campaign_id=c.id, uploaded_by=self.user.id, document_name=name, storage_key=key,
                             content_type=ctype, size_bytes=len(data), document_type=document_type,
                             visibility="donor_public" if document_type == "cover" and self.staff else visibility)
        self.db.add(d)
        self.db.flush()
        if document_type == "cover":
            c.cover_document_id = d.id
        self._event(c, "document_added", c.status, name)
        self.db.commit()
        return self._documents(c)

    def document(self, campaign_id, document_id):
        c = self._get(campaign_id)
        d = self.db.scalar(select(CampaignDocument).where(CampaignDocument.id == document_id,
                                                          CampaignDocument.campaign_id == c.id))
        if d is None or (self.role == "DONOR" and d.visibility != "donor_public"):
            raise AuthFlowError("DOCUMENT_NOT_FOUND", "This document does not exist.", 404)
        path = STORAGE / d.storage_key
        if not path.is_file():
            raise AuthFlowError("DOCUMENT_NOT_FOUND", "This document does not exist.", 404)
        return d, path.read_bytes()

    def cover(self, campaign_id):
        c = self._get(campaign_id)
        if not c.cover_document_id:
            raise AuthFlowError("DOCUMENT_NOT_FOUND", "This campaign has no cover image.", 404)
        return self.document(campaign_id, c.cover_document_id)

    def set_visibility(self, campaign_id, document_id, visibility):
        c = self._get(campaign_id)
        d = self.db.scalar(select(CampaignDocument).where(CampaignDocument.id == document_id,
                                                          CampaignDocument.campaign_id == c.id))
        if d is None:
            raise AuthFlowError("DOCUMENT_NOT_FOUND", "This document does not exist.", 404)
        d.visibility = visibility
        d.verified_at = now_utc() if visibility == "donor_public" else d.verified_at
        self.db.commit()
        return self._documents(c)


def run_schedule(db):
    """Activate scheduled campaigns whose start date has come and close campaigns whose end date has passed.
    Idempotent: notices are claimed once per campaign, so running it repeatedly never repeats a notification."""
    svc = Campaigns(db, None, "SYSTEM")
    today = today_pkt()
    for c in db.scalars(select(DonationCampaign).where(DonationCampaign.status == "scheduled",
                                                       DonationCampaign.start_date <= today).with_for_update()):
        prev = c.status
        c.status = "active"
        svc._event(c, "started", prev)
        svc._tell_donors(c, "started", "campaign_started", "Campaign now open",
                         f"{c.name} ({c.reference_no}) is now open for contributions.")
        svc._tell_ngo(c, "campaign_started", "Campaign now open", f"Campaign {c.reference_no} is now open.")
    for c in db.scalars(select(DonationCampaign).where(DonationCampaign.status.in_(("active", "goal_reached", "paused")),
                                                       DonationCampaign.end_date.is_not(None),
                                                       DonationCampaign.end_date < today).with_for_update()):
        svc._close(c, "End date reached")
    db.commit()
    return svc.notes

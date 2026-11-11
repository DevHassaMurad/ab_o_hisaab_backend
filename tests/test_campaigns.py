"""Donation campaigns: lifecycle, review, permissions, contributions, overfunding, notices and uploads."""
import unittest
from datetime import date, timedelta

from sqlalchemy import select, update

from controller.campaigns import run_schedule
from model.campaign_model import DonationCampaign
from model.notification_model import Notification
from tests.test_donations import NGO, DonationFixture

TODAY = date.today()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def body(**extra):
    return {"name": "Flood shelter kits", "short_description": "Shelter kits for displaced families in Muzaffargarh.",
            "description": "Tents, tarpaulins and bedding for 500 families displaced by flooding.",
            "objective": "Provide shelter to 500 families", "target_amount": "100000.00",
            "start_date": TODAY.isoformat(), "end_date": (TODAY + timedelta(days=30)).isoformat(),
            "district": "Muzaffargarh", **extra}


class CampaignTests(DonationFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ngo = cls.login(*NGO)

    def post(self, path, headers, json=None):
        return self.client.post(f"/api/{path}", json=json or {}, headers=headers)

    def notes(self, headers, type_):
        return [n for n in self.client.get("/api/notifications?page_size=100", headers=headers).json()["items"]
                if n["type"] == type_]

    def live_pdma_campaign(self, **extra):
        c = self.post("campaigns", self.admin, body(**extra)).json()
        self.assertEqual(c["status"], "draft")
        self.assertEqual(self.post(f"campaigns/{c['id']}/approve", self.admin).json()["status"], "approved")
        return self.post(f"campaigns/{c['id']}/publish", self.admin).json()

    def test_01_pdma_campaign_approval_and_publication_are_separate(self):
        c = self.post("campaigns", self.admin, body()).json()
        # donors cannot see an unpublished campaign
        self.assertEqual(self.client.get(f"/api/campaigns/{c['id']}", headers=self.donor).status_code, 404)
        self.post(f"campaigns/{c['id']}/approve", self.admin)
        self.assertEqual(self.client.get(f"/api/campaigns/{c['id']}", headers=self.donor).status_code, 404)
        p = self.post(f"campaigns/{c['id']}/publish", self.admin).json()
        self.assertEqual(p["status"], "active")
        self.assertEqual(self.client.get(f"/api/campaigns/{c['id']}", headers=self.donor).status_code, 200)
        self.assertTrue(any(n["related_id"] == c["id"] for n in self.notes(self.donor, "campaign_published")))
        # publishing twice is refused, so the published notice is not repeated
        self.assertEqual(self.post(f"campaigns/{c['id']}/publish", self.admin).status_code, 409)

    def test_02_ngo_proposal_review_flow(self):
        c = self.post("campaigns", self.ngo, body(name="NGO water filters")).json()
        self.assertEqual(c["source_type"], "NGO")
        # NGOs cannot approve or publish
        self.assertEqual(self.post(f"campaigns/{c['id']}/approve", self.ngo).status_code, 403)
        self.assertEqual(self.post(f"campaigns/{c['id']}/submit", self.ngo).json()["status"], "pending_review")
        self.assertEqual(self.post(f"campaigns/{c['id']}/start-review", self.admin).json()["status"], "under_review")
        r = self.post(f"campaigns/{c['id']}/request-changes", self.admin, {"reason": "Add a budget breakdown."}).json()
        self.assertEqual(r["status"], "changes_requested")
        self.assertTrue(self.notes(self.ngo, "campaign_changes_requested"))
        e = self.client.patch(f"/api/campaigns/{c['id']}", json={"objective": "Filters for 300 households"},
                              headers=self.ngo).json()
        self.assertEqual(e["objective"], "Filters for 300 households")
        self.post(f"campaigns/{c['id']}/submit", self.ngo)
        self.assertEqual(self.post(f"campaigns/{c['id']}/approve", self.admin).json()["status"], "approved")
        # an approved proposal cannot be edited by the NGO
        self.assertEqual(self.client.patch(f"/api/campaigns/{c['id']}", json={"name": "Changed"},
                                           headers=self.ngo).status_code, 409)
        self.assertNotIn("review_notes", self.client.get(f"/api/campaigns/{c['id']}", headers=self.ngo).json())

    def test_03_rejection_needs_reason_and_is_final(self):
        c = self.post("campaigns", self.ngo, body(name="Rejected idea")).json()
        self.post(f"campaigns/{c['id']}/submit", self.ngo)
        self.assertEqual(self.post(f"campaigns/{c['id']}/reject", self.admin, {"reason": ""}).status_code, 422)
        self.assertEqual(self.post(f"campaigns/{c['id']}/reject", self.admin, {"reason": "Duplicate"}).json()["status"],
                         "rejected")
        self.assertEqual(self.post(f"campaigns/{c['id']}/submit", self.ngo).status_code, 409)

    def test_04_contributions_count_only_when_verified_and_cap_is_enforced(self):
        c = self.live_pdma_campaign(name="Capped campaign")
        a = self.post(f"campaigns/{c['id']}/contributions", self.donor, {"amount": "70000.00"}).json()
        d = self.client.get(f"/api/campaigns/{c['id']}", headers=self.donor).json()
        self.assertEqual(d["collected_amount"], "0.00")  # pending does not count
        over = self.post(f"campaigns/{c['id']}/contributions", self.donor, {"amount": "40000.00"})
        self.assertEqual(over.status_code, 409)
        self.assertEqual(over.json()["code"], "OVERFUNDING_NOT_ALLOWED")
        b = self.post(f"campaigns/{c['id']}/contributions", self.donor, {"amount": "30000.00"}).json()
        self.post(f"campaign-contributions/{a['id']}/verify", self.admin, {"action": "verify"})
        d = self.client.get(f"/api/campaigns/{c['id']}", headers=self.donor).json()
        self.assertEqual((d["collected_amount"], d["status"]), ("70000.00", "active"))
        self.post(f"campaign-contributions/{b['id']}/verify", self.admin, {"action": "verify"})
        d = self.client.get(f"/api/campaigns/{c['id']}", headers=self.donor).json()
        self.assertEqual((d["collected_amount"], d["status"]), ("100000.00", "goal_reached"))
        self.assertEqual(len([n for n in self.notes(self.donor, "campaign_goal_reached") if n["related_id"] == c["id"]]), 1)
        self.assertEqual(self.post(f"campaign-contributions/{b['id']}/verify", self.admin,
                                   {"action": "verify"}).status_code, 409)
        self.assertEqual(self.post(f"campaigns/{c['id']}/contributions", self.donor,
                                   {"amount": "1.00"}).status_code, 409)

    def test_05_rejected_contribution_frees_room_and_needs_reason(self):
        c = self.live_pdma_campaign(name="Reject room")
        a = self.post(f"campaigns/{c['id']}/contributions", self.donor, {"amount": "100000.00"}).json()
        self.assertEqual(self.post(f"campaign-contributions/{a['id']}/verify", self.admin,
                                   {"action": "reject"}).status_code, 422)
        self.post(f"campaign-contributions/{a['id']}/verify", self.admin, {"action": "reject", "reason": "Not received"})
        self.assertEqual(self.post(f"campaigns/{c['id']}/contributions", self.donor,
                                   {"amount": "100000.00"}).status_code, 201)

    def test_06_overfunding_allowed_up_to_limit(self):
        c = self.live_pdma_campaign(name="Overfund", allow_overfunding=True, overfunding_limit="120000.00")
        self.assertEqual(self.post(f"campaigns/{c['id']}/contributions", self.donor,
                                   {"amount": "115000.00"}).status_code, 201)
        self.assertEqual(self.post(f"campaigns/{c['id']}/contributions", self.donor,
                                   {"amount": "10000.00"}).status_code, 409)

    def test_07_scheduled_campaign_activates_and_expires_once(self):
        c = self.post("campaigns", self.admin, body(name="Future campaign",
                                                    start_date=(TODAY + timedelta(days=3)).isoformat())).json()
        self.post(f"campaigns/{c['id']}/approve", self.admin)
        self.assertEqual(self.post(f"campaigns/{c['id']}/publish", self.admin).json()["status"], "scheduled")
        self.assertEqual(self.post(f"campaigns/{c['id']}/contributions", self.donor,
                                   {"amount": "10.00"}).status_code, 409)
        with self.Session() as db:
            db.execute(update(DonationCampaign).where(DonationCampaign.id == c["id"])
                       .values(start_date=TODAY - timedelta(days=2), end_date=TODAY - timedelta(days=1)))
            db.commit()
            db.execute(update(DonationCampaign).where(DonationCampaign.id == c["id"]).values(end_date=TODAY))
            db.commit()
            run_schedule(db)
            run_schedule(db)
            self.assertEqual(db.scalar(select(DonationCampaign.status).where(DonationCampaign.id == c["id"])), "active")
            started = db.scalars(select(Notification).where(Notification.type == "campaign_started",
                                                            Notification.related_id == c["id"])).all()
            self.assertEqual(len({n.user_id for n in started}), len(started))  # one per user
            db.execute(update(DonationCampaign).where(DonationCampaign.id == c["id"])
                       .values(end_date=TODAY - timedelta(days=1)))
            db.commit()
            run_schedule(db)
            self.assertEqual(db.scalar(select(DonationCampaign.status).where(DonationCampaign.id == c["id"])),
                             "completed")

    def test_08_permissions(self):
        c = self.live_pdma_campaign(name="Permission check")
        self.assertEqual(self.post("campaigns", self.donor, body()).status_code, 403)
        self.assertEqual(self.post(f"campaigns/{c['id']}/pause", self.donor).status_code, 403)
        self.assertEqual(self.post(f"campaigns/{c['id']}/contributions", self.admin, {"amount": "5.00"}).status_code, 403)
        # the NGO cannot see a PDMA campaign or another organisation's work
        self.assertEqual(self.client.get(f"/api/campaigns/{c['id']}", headers=self.ngo).status_code, 404)
        self.assertEqual(self.client.get("/api/campaign-contributions", headers=self.ngo).status_code, 403)
        d = self.client.get(f"/api/campaigns/{c['id']}", headers=self.donor).json()
        self.assertNotIn("review_notes", d)
        self.assertIsNone(d["ngo_id"])

    def test_09_pause_resume_cancel(self):
        c = self.live_pdma_campaign(name="Pause me")
        self.assertEqual(self.post(f"campaigns/{c['id']}/pause", self.admin).json()["status"], "paused")
        self.assertEqual(self.post(f"campaigns/{c['id']}/contributions", self.donor, {"amount": "5.00"}).status_code, 409)
        self.assertEqual(self.post(f"campaigns/{c['id']}/resume", self.admin).json()["status"], "active")
        self.assertEqual(self.post(f"campaigns/{c['id']}/cancel", self.admin, {"reason": "Merged into another"}).json()[
            "status"], "cancelled")
        self.assertEqual(self.post(f"campaigns/{c['id']}/resume", self.admin).status_code, 409)

    def test_10_uploads_are_checked_and_permissioned(self):
        c = self.post("campaigns", self.admin, body(name="With cover")).json()
        bad = self.client.post(f"/api/campaigns/{c['id']}/documents", headers=self.admin,
                               files={"file": ("x.png", b"<script>alert(1)</script>", "image/png")},
                               data={"document_type": "cover"})
        self.assertEqual(bad.status_code, 415)
        big = self.client.post(f"/api/campaigns/{c['id']}/documents", headers=self.admin,
                               files={"file": ("x.png", PNG + b"\x00" * (5 * 1024 * 1024), "image/png")})
        self.assertEqual(big.status_code, 413)
        ok = self.client.post(f"/api/campaigns/{c['id']}/documents", headers=self.admin,
                              files={"file": ("cover.png", PNG, "image/png")}, data={"document_type": "cover"})
        self.assertEqual(ok.status_code, 201)
        self.client.post(f"/api/campaigns/{c['id']}/documents", headers=self.admin,
                         files={"file": ("budget.pdf", b"%PDF-1.4 test", "application/pdf")})
        self.post(f"campaigns/{c['id']}/approve", self.admin)
        self.post(f"campaigns/{c['id']}/publish", self.admin)
        cover = self.client.get(f"/api/campaigns/{c['id']}/cover", headers=self.donor)
        self.assertEqual((cover.status_code, cover.headers["content-type"]), (200, "image/png"))
        docs = self.client.get(f"/api/campaigns/{c['id']}", headers=self.donor).json()["documents"]
        self.assertEqual([d["document_name"] for d in docs], ["cover.png"])  # internal PDF hidden
        internal = [d for d in self.client.get(f"/api/campaigns/{c['id']}", headers=self.admin).json()["documents"]
                    if d["document_name"] == "budget.pdf"][0]
        self.assertEqual(self.client.get(f"/api/campaigns/{c['id']}/documents/{internal['id']}",
                                         headers=self.donor).status_code, 404)

    def test_11_dashboards_and_donor_summary(self):
        dash = self.client.get("/api/campaigns/dashboard", headers=self.admin).json()
        self.assertGreater(dash["total"], 0)
        self.assertEqual(self.client.get("/api/campaigns/dashboard", headers=self.donor).status_code, 403)
        s = self.client.get("/api/campaigns/my-summary", headers=self.donor).json()
        self.assertIn("verified_amount", s)
        mine = self.client.get("/api/campaign-contributions", headers=self.donor).json()
        self.assertTrue(mine["items"])
        self.assertNotIn("donor", mine["items"][0])


if __name__ == "__main__":
    unittest.main()

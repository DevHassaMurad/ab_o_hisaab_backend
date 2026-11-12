"""Required end-to-end test: PDMA Collaboration Campaign (parent) -> NGO participations -> NGO Distribution Campaigns."""
import unittest
from datetime import date, timedelta

from pwdlib import PasswordHash
from sqlalchemy import func, select

from model.campaign_finance_model import CampaignCollaboration, NgoDistributionCampaign
from model.donation_model import Ngo
from model.notification_model import Notification
from model.registration_model import HouseholderDetails
from model.role_model import Role
from model.user_model import User
from tests.test_donations import DonationFixture

TODAY = date.today()
ADDR = dict(identity_type="cnic", country="PK", state="Sindh", postal_code="74000", address1="Block 5",
            address_confirmed=True)


class CollaborationCampaignFlow(DonationFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        hasher = PasswordHash.recommended()
        cls.ngo_ids, cls.ngo_user_ids = {}, {}
        with cls.Session() as db:
            role = dict(db.execute(select(Role.name, Role.id)).all())
            for name in ("Alpha", "Beta", "Gamma"):
                u = User(name=f"NGO {name} Admin", email=f"{name.lower()}@ngo.pk", role_id=role["NGO"],
                         password=hasher.hash(f"{name.lower()}-pass-1234"), email_verified_at=func.now())
                db.add(u)
                db.flush()
                n = Ngo(name=f"NGO {name}", registration_no=f"REG-{name}", user_id=u.id, is_active=True)
                db.add(n)
                db.flush()
                cls.ngo_ids[name], cls.ngo_user_ids[name] = n.id, u.id
            h = User(name="Central Household", email="central@x.pk", role_id=role["HOUSEHOLDER"],
                     password=hasher.hash("household-pass-1"), email_verified_at=func.now(), registration_status="approved")
            db.add(h)
            db.flush()
            db.add(HouseholderDetails(user_id=h.id, phone="+92 300 1111111", identity_number="4210112345671",
                                      household_size=5, dependents=3, adults=2, children=3, males=2, females=3,
                                      city="District Central", **ADDR))
            cls.household_id = h.id
            db.commit()
        cls.h = {n: cls.login(f"{n.lower()}@ngo.pk", f"{n.lower()}-pass-1234") for n in ("Alpha", "Beta", "Gamma")}

    def post(self, path, headers, json=None):
        return self.client.post(f"/api/{path}", json=json or {}, headers=headers)

    def ok(self, r):
        self.assertIn(r.status_code, (200, 201), r.text)
        return r.json()

    def test_karachi_flood_relief(self):
        c = self.client
        areas = {"Alpha": "District Central", "Beta": "District East", "Gamma": "District South"}
        # 1-2: PDMA creates ONE collaboration campaign with three NGOs (saved as draft first)
        cc = self.ok(self.post("collaboration-campaigns", self.admin, {
            "title": "Karachi Flood Relief Collaboration Campaign", "description": "Coordinated flood relief across Karachi.",
            "objective": "Household and shop recovery", "districts": "District Central, District East, District South",
            "start_date": TODAY.isoformat(), "end_date": (TODAY + timedelta(days=60)).isoformat(),
            "participants": [{"ngo_id": self.ngo_ids[n], "district": a, "responsibility": f"Relief in {a}"}
                             for n, a in areas.items()]}))
        self.assertEqual((cc["status"], cc["draft_ngos"]), ("draft", 3))
        self.assertTrue(cc["reference_no"].startswith("COLC"))
        # NGOs cannot see a draft campaign's invitation
        self.assertEqual(self.ok(c.get("/api/collaborations", headers=self.h["Alpha"]))["total"], 0)
        # 3-4: launch -> one invitation + one notification per NGO
        launched = self.ok(self.post(f"collaboration-campaigns/{cc['id']}/launch", self.admin))
        self.assertEqual((launched["status"], launched["pending_ngos"]), ("launched", 3))
        self.assertEqual(self.post(f"collaboration-campaigns/{cc['id']}/launch", self.admin).status_code, 409)
        with self.Session() as db:
            for n in areas:
                notes = db.scalars(select(Notification).where(Notification.user_id == self.ngo_user_ids[n],
                                                              Notification.type == "collaboration_invited")).all()
                self.assertEqual(len(notes), 1, n)
                self.assertIn("Karachi Flood Relief Collaboration Campaign", notes[0].message)
            refs = set(db.scalars(select(CampaignCollaboration.reference_no).where(
                CampaignCollaboration.collaboration_campaign_id == cc["id"])))
            self.assertEqual(len(refs), 3)  # unique invitation reference per NGO
        inv = {n: self.ok(c.get("/api/collaborations", headers=self.h[n]))["items"][0] for n in areas}
        self.assertEqual(inv["Alpha"]["collaboration_campaign"]["id"], cc["id"])
        self.assertEqual(inv["Alpha"]["district"], "District Central")
        # 5-7: Alpha and Beta accept, Gamma rejects (reason optional)
        self.ok(self.post(f"collaborations/{inv['Alpha']['id']}/respond", self.h["Alpha"], {"accept": True}))
        self.ok(self.post(f"collaborations/{inv['Beta']['id']}/respond", self.h["Beta"], {"accept": True}))
        self.ok(self.post(f"collaborations/{inv['Gamma']['id']}/respond", self.h["Gamma"], {"accept": False}))
        self.assertEqual(self.post(f"collaborations/{inv['Alpha']['id']}/respond", self.h["Alpha"],
                                   {"accept": False}).status_code, 409)
        # accepting is not funding: no batch, nothing to spend
        self.assertFalse(self.ok(c.get(f"/api/collaborations/{inv['Alpha']['id']}", headers=self.h["Alpha"]))["funded"])
        # 8-9: Alpha creates its distribution campaign from the accepted collaboration (inherits links)
        da = self.ok(self.post("distribution-campaigns", self.h["Alpha"], {
            "collaboration_id": inv["Alpha"]["id"], "name": "Central District Household Relief Distribution",
            "objective": "Household relief", "target_beneficiaries": "Flood-affected households",
            "expected_beneficiaries": 200, "start_date": TODAY.isoformat(), "launch": True}))
        self.assertEqual((da["collaboration_campaign"]["id"], da["collaboration"]["id"], da["district"]),
                         (cc["id"], inv["Alpha"]["id"], "District Central"))
        self.assertEqual(self.post("distribution-campaigns", self.h["Alpha"], {
            "collaboration_id": inv["Alpha"]["id"], "name": "Wrong district", "objective": "x" * 6,
            "district": "District East", "start_date": TODAY.isoformat()}).status_code, 422)
        # 10: Beta creates its own (draft first, then launches)
        db_ = self.ok(self.post("distribution-campaigns", self.h["Beta"], {
            "collaboration_id": inv["Beta"]["id"], "name": "East District Shop Recovery", "objective": "Shop recovery",
            "start_date": TODAY.isoformat()}))
        self.assertEqual(db_["status"], "draft")
        self.assertEqual(self.ok(self.post(f"distribution-campaigns/{db_['id']}/launch", self.h["Beta"]))["status"], "active")
        # 11: PDMA sees all three participations and both distribution campaigns under the parent
        view = self.ok(c.get(f"/api/collaboration-campaigns/{cc['id']}", headers=self.admin))
        self.assertEqual((view["selected_ngos"], view["accepted_ngos"], view["rejected_ngos"], view["pending_ngos"]),
                         (3, 2, 1, 0))
        by_ngo = {p["ngo"]["name"]: p for p in view["participants"]}
        self.assertEqual((by_ngo["NGO Alpha"]["status"], by_ngo["NGO Alpha"]["distribution_campaigns"]), ("accepted", 1))
        self.assertEqual((by_ngo["NGO Gamma"]["status"], by_ngo["NGO Gamma"]["distribution_campaigns"]), ("rejected", 0))
        self.assertEqual({d["reference_no"] for d in view["distribution_campaign_list"]},
                         {da["reference_no"], db_["reference_no"]})
        with self.Session() as db:  # real database relationship to the parent
            self.assertEqual(set(db.scalars(select(NgoDistributionCampaign.collaboration_campaign_id).where(
                NgoDistributionCampaign.id.in_((da["id"], db_["id"]))))), {cc["id"]})
        # 12: Gamma cannot create from the rejected invitation
        g = self.post("distribution-campaigns", self.h["Gamma"], {
            "collaboration_id": inv["Gamma"]["id"], "name": "Not allowed", "objective": "Not allowed",
            "start_date": TODAY.isoformat()})
        self.assertEqual((g.status_code, g.json()["code"]), (409, "COLLABORATION_NOT_ACCEPTED"))
        # ... nor from someone else's participation
        self.assertEqual(self.post("distribution-campaigns", self.h["Gamma"], {
            "collaboration_id": inv["Alpha"]["id"], "name": "Not mine", "objective": "Not mine",
            "start_date": TODAY.isoformat()}).status_code, 404)
        # 13: Alpha cannot access Beta's distribution campaign or participation
        self.assertEqual(c.get(f"/api/distribution-campaigns/{db_['id']}", headers=self.h["Alpha"]).status_code, 404)
        self.assertEqual(c.get(f"/api/collaborations/{inv['Beta']['id']}", headers=self.h["Alpha"]).status_code, 404)
        self.assertEqual(c.get(f"/api/collaboration-campaigns/{cc['id']}", headers=self.h["Alpha"]).status_code, 403)
        # 14: no distribution beyond confirmed funds (here: none released at all)
        self.ok(self.post(f"distribution-campaigns/{da['id']}/beneficiaries", self.h["Alpha"],
                          {"items": [{"user_id": self.household_id}]}))
        ben = self.ok(c.get(f"/api/distribution-campaigns/{da['id']}", headers=self.h["Alpha"]))["beneficiaries"][0]
        pay = self.post(f"distribution-campaigns/{da['id']}/distributions", self.h["Alpha"], {
            "beneficiary_id": ben["id"], "amount": "100.00", "distribution_date": TODAY.isoformat(),
            "payment_method": "CASH"})
        self.assertEqual((pay.status_code, pay.json()["code"]), (409, "NO_CONFIRMED_FUNDS"))
        # releasing needs a funding link first (funding is a separate PDMA step)
        self.assertEqual(self.post(f"collaborations/{inv['Alpha']['id']}/releases", self.admin,
                                   {"amount": "100.00", "payment_method": "CASH"}).json()["code"], "NO_FUNDING_LINK")
        # PDMA was told about every response
        with self.Session() as db:
            types = [t for (t,) in db.execute(select(Notification.type).where(Notification.type.in_(
                ("collaboration_accepted", "collaboration_rejected", "distribution_campaign_launched"))))]
        self.assertGreaterEqual(types.count("collaboration_accepted"), 2)
        self.assertGreaterEqual(types.count("collaboration_rejected"), 1)
        self.assertGreaterEqual(types.count("distribution_campaign_launched"), 2)
        dash = self.ok(c.get("/api/ngo/campaign-finance/dashboard", headers=self.h["Gamma"]))
        self.assertEqual((dash["total_invitations"], dash["rejected_invitations"]), (1, 1))


if __name__ == "__main__":
    unittest.main()

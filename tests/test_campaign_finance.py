"""Mandatory financial scenario (spec section 19): 3 donors -> master batch -> 2 NGOs -> sub-batches -> NGO A
distribution campaign -> 4 beneficiaries, with reconciliation, exact donor attribution, no double counting,
isolation, idempotency, reversals and ledger balance checks."""
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from pwdlib import PasswordHash
from sqlalchemy import func, select

from model.campaign_finance_model import FinanceLedgerEntry
from model.donation_model import Ngo
from model.notification_model import Notification
from model.registration_model import HouseholderDetails, ShopkeeperDetails
from model.role_model import Role
from model.user_model import User
from tests.test_donations import NGO, DonationFixture

D = Decimal
TODAY = date.today()
ADDR = dict(identity_type="cnic", country="PK", state="Sindh", postal_code="73000", address1="Main road",
            address_confirmed=True)


class CampaignFinanceScenario(DonationFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ngo_a = cls.login(*NGO)
        hasher = PasswordHash.recommended()
        with cls.Session() as db:
            roles = dict(db.execute(select(Role.name, Role.id)).all())

            def user(email, name, role, **extra):
                u = User(name=name, email=email, role_id=roles[role], password=hasher.hash(f"{role.lower()}-pass-123"),
                         email_verified_at=func.now(), **extra)
                db.add(u)
                db.flush()
                return u

            user("donor.b@x.pk", "Donor B", "DONOR")
            user("donor.c@x.pk", "Donor C", "DONOR")
            nb = user("ngo.b@x.pk", "NGO B Admin", "NGO")
            db.add(Ngo(name="NGO B", registration_no="NGO-B-1", user_id=nb.id, is_active=True))
            cls.bens = {}
            for i, (kind, city) in enumerate((("shopkeeper", "Thatta"), ("householder", "Thatta"),
                                              ("shopkeeper", "Thatta"), ("householder", "Thatta"),
                                              ("householder", "Karachi"))):
                u = user(f"ben{i}@x.pk", f"Beneficiary {i}", kind.upper(), registration_status="approved")
                if kind == "shopkeeper":
                    db.add(ShopkeeperDetails(user_id=u.id, phone="+92 300 0000000", identity_number=f"12345123456{i}0",
                                             shop_name=f"Shop {i}", business_number=f"B-{i}", business_category="Grocery",
                                             city=city, **ADDR))
                else:
                    db.add(HouseholderDetails(user_id=u.id, phone="+92 300 0000000", identity_number=f"12345123456{i}0",
                                              household_size=4, dependents=2, adults=2, children=2, males=2, females=2,
                                              city=city, **ADDR))
                cls.bens[i] = u.id
            db.commit()
            cls.ngo_a_id = db.scalar(select(Ngo.id).where(Ngo.name == "Demo Relief NGO"))
            cls.ngo_b_id = db.scalar(select(Ngo.id).where(Ngo.name == "NGO B"))
        cls.donor_b = cls.login("donor.b@x.pk", "donor-pass-123")
        cls.donor_c = cls.login("donor.c@x.pk", "donor-pass-123")
        cls.ngo_b = cls.login("ngo.b@x.pk", "ngo-pass-123")

    def post(self, path, h, json=None):
        r = self.client.post(f"/api/{path}", json=json or {}, headers=h)
        return r

    def ok(self, r, code=(200, 201)):
        self.assertIn(r.status_code, code, r.text)
        return r.json()

    def test_full_scenario(self):
        c, h = self.client, self.admin
        # Step 1 - campaign + three verified donor contributions (A 4,000 / B 3,000 / C 3,000)
        camp = self.ok(self.post("campaigns", h, {
            "name": "Flood Relief Thatta", "short_description": "Relief for flood affected families in Thatta.",
            "description": "Cash support for flood affected shopkeepers and households in Thatta district.",
            "objective": "Support recovery in Thatta", "target_amount": "10000.00", "start_date": TODAY.isoformat(),
            "end_date": (TODAY + timedelta(days=30)).isoformat(), "district": "Thatta"}))
        self.ok(self.post(f"campaigns/{camp['id']}/approve", h))
        self.ok(self.post(f"campaigns/{camp['id']}/publish", h))
        refs = {}
        for who, hd, amt in (("A", self.donor, "4000.00"), ("B", self.donor_b, "3000.00"), ("C", self.donor_c, "3000.00")):
            x = self.ok(self.post(f"campaigns/{camp['id']}/contributions", hd, {"amount": amt}))
            self.ok(self.post(f"campaign-contributions/{x['id']}/verify", h, {"action": "verify"}))
            refs[who] = x["reference_no"]
        fin = self.ok(c.get(f"/api/campaigns/{camp['id']}/finance", headers=h))
        self.assertEqual((fin["collected"], fin["master_available"]), ("10000.00", "10000.00"))
        self.assertTrue(fin["master_batch_reference"].startswith("CMP-BATCH"))

        # Step 2 - collaborations (NGO A 6,000 / NGO B 3,000); acceptance moves no money
        col_a = self.ok(self.post(f"campaigns/{camp['id']}/collaborations", h, {
            "ngo_id": self.ngo_a_id, "assigned_amount": "6000.00", "district": "Thatta",
            "responsibility": "Shopkeepers and households in Thatta city"}))
        col_b = self.ok(self.post(f"campaigns/{camp['id']}/collaborations", h, {
            "ngo_id": self.ngo_b_id, "assigned_amount": "3000.00", "district": "Thatta",
            "responsibility": "Households in rural Thatta"}))
        over = self.post(f"campaigns/{camp['id']}/collaborations", h, {
            "ngo_id": self.ngo_b_id, "assigned_amount": "5000.00", "district": "Thatta", "responsibility": "Duplicate"})
        self.assertEqual(over.status_code, 409)
        self.assertEqual(self.post(f"collaborations/{col_a['id']}/releases", h,
                                   {"amount": "100.00", "payment_method": "BANK_TRANSFER"}).status_code, 409)  # not accepted
        self.assertEqual(c.get(f"/api/collaborations/{col_a['id']}", headers=self.ngo_b).status_code, 404)  # isolation
        self.ok(self.post(f"collaborations/{col_a['id']}/respond", self.ngo_a, {"accept": True}))
        self.ok(self.post(f"collaborations/{col_b['id']}/respond", self.ngo_b, {"accept": True}))
        self.assertEqual(self.ok(c.get(f"/api/campaigns/{camp['id']}/finance", headers=h))["master_available"], "10000.00")

        # Step 3 - releases (NGO A in two partial releases) and acknowledgement
        r1 = self.ok(self.post(f"collaborations/{col_a['id']}/releases", h,
                               {"amount": "4000.00", "payment_method": "BANK_TRANSFER", "idempotency_key": "rel-a-1"}))
        again = self.ok(self.post(f"collaborations/{col_a['id']}/releases", h,
                                  {"amount": "4000.00", "payment_method": "BANK_TRANSFER", "idempotency_key": "rel-a-1"}))
        self.assertEqual(again["id"], r1["id"])  # idempotent: no duplicate release
        r2 = self.ok(self.post(f"collaborations/{col_a['id']}/releases", h, {"amount": "2000.00", "payment_method": "CHEQUE"}))
        self.assertEqual(self.post(f"collaborations/{col_a['id']}/releases", h,
                                   {"amount": "1.00", "payment_method": "CASH"}).json()["code"], "EXCEEDS_ASSIGNMENT")
        rb = self.ok(self.post(f"collaborations/{col_b['id']}/releases", h, {"amount": "3000.00", "payment_method": "BANK_TRANSFER"}))
        mid = self.ok(c.get(f"/api/campaigns/{camp['id']}/finance", headers=h))
        self.assertEqual((mid["master_available"], mid["in_transit"], mid["ngo_held"]), ("1000.00", "9000.00", "0.00"))
        # exact attribution of the releases: FIFO over verified contributions
        self.assertEqual([(s["contribution_reference"], s["amount"]) for s in r1["sources"]], [(refs["A"], "4000.00")])
        self.assertEqual([(s["contribution_reference"], s["amount"]) for s in r2["sources"]], [(refs["B"], "2000.00")])
        self.assertEqual([(s["contribution_reference"], s["amount"]) for s in rb["sources"]],
                         [(refs["B"], "1000.00"), (refs["C"], "2000.00")])
        # NGO cannot spend money still in transit
        batch_a = self.ok(c.get("/api/campaign-batches", headers=self.ngo_a))["items"][0]
        self.assertEqual(batch_a["available"], "0.00")
        # planning is allowed before funds arrive, but no payment can be recorded from money in transit
        early = self.ok(self.post("distribution-campaigns", self.ngo_a, {
            "collaboration_id": col_a["id"], "name": "Thatta Early Plan", "objective": "Help shops and households",
            "start_date": TODAY.isoformat(), "launch": True}))
        self.ok(self.post(f"distribution-campaigns/{early['id']}/beneficiaries", self.ngo_a,
                          {"items": [{"user_id": self.bens[0]}]}))
        early_ben = self.ok(c.get(f"/api/distribution-campaigns/{early['id']}", headers=self.ngo_a))["beneficiaries"][0]["id"]
        blocked = self.post(f"distribution-campaigns/{early['id']}/distributions", self.ngo_a, {
            "beneficiary_id": early_ben, "amount": "1.00", "distribution_date": TODAY.isoformat(), "payment_method": "CASH"})
        self.assertEqual(blocked.json()["code"], "INSUFFICIENT_BATCH_FUNDS")
        for rel, hd in ((r1, self.ngo_a), (r2, self.ngo_a), (rb, self.ngo_b)):
            self.ok(self.post(f"campaign-releases/{rel['id']}/acknowledge", hd))
        self.assertEqual(self.post(f"campaign-releases/{rb['id']}/acknowledge", self.ngo_a).status_code, 404)
        held = self.ok(c.get(f"/api/campaigns/{camp['id']}/finance", headers=h))
        self.assertEqual((held["in_transit"], held["ngo_held"], held["collected"]), ("0.00", "9000.00", "10000.00"))

        # Step 4 - NGO A distribution campaign: 1,000 + 500 + 1,500 + 1,000 = 4,000
        dist = self.ok(self.post("distribution-campaigns", self.ngo_a, {
            "collaboration_id": col_a["id"], "name": "Thatta Household and Shopkeeper Recovery",
            "objective": "Recovery support", "planned_amount": "6000.00", "start_date": TODAY.isoformat(),
            "launch": True}))
        self.assertEqual(dist["parent_campaign"]["id"], camp["id"])
        self.assertIsNotNone(dist["collaboration_campaign"])
        elig = self.ok(c.get(f"/api/distribution-campaigns/{dist['id']}/eligible-beneficiaries", headers=self.ngo_a))
        self.assertNotIn(self.bens[4], [e["user_id"] for e in elig["items"]])  # Karachi is out of scope
        self.assertEqual(self.post(f"distribution-campaigns/{dist['id']}/beneficiaries", self.ngo_a,
                                   {"items": [{"user_id": self.bens[4]}]}).status_code, 422)
        d = self.ok(self.post(f"distribution-campaigns/{dist['id']}/beneficiaries", self.ngo_a,
                              {"items": [{"user_id": self.bens[i]} for i in range(4)]}))
        ben_ids = {b["user_id"]: b["id"] for b in d["beneficiaries"]}
        vouchers = []
        for i, amt in ((0, "1000.00"), (1, "500.00"), (2, "1500.00"), (3, "1000.00")):
            vouchers.append(self.ok(self.post(f"distribution-campaigns/{dist['id']}/distributions", self.ngo_a, {
                "beneficiary_id": ben_ids[self.bens[i]], "amount": amt, "distribution_date": TODAY.isoformat(),
                "payment_method": "BANK_TRANSFER", "idempotency_key": f"pay-{i}"})))
        dup = self.ok(self.post(f"distribution-campaigns/{dist['id']}/distributions", self.ngo_a, {
            "beneficiary_id": ben_ids[self.bens[0]], "amount": "1000.00", "distribution_date": TODAY.isoformat(),
            "payment_method": "BANK_TRANSFER", "idempotency_key": "pay-0"}))
        self.assertEqual(dup["id"], vouchers[0]["id"])  # duplicate payment posting prevented
        too_much = self.post(f"distribution-campaigns/{dist['id']}/distributions", self.ngo_a, {
            "beneficiary_id": ben_ids[self.bens[0]], "amount": "2000.01", "distribution_date": TODAY.isoformat(),
            "payment_method": "CASH"})
        self.assertEqual(too_much.json()["code"], "INSUFFICIENT_BATCH_FUNDS")

        # Step 5 - reconciliation (no double counting of master and child batches)
        f = self.ok(c.get(f"/api/campaigns/{camp['id']}/finance", headers=h))
        self.assertEqual(f["collected"], "10000.00")
        self.assertEqual(f["assigned"], "9000.00")
        self.assertEqual(f["master_available"], "1000.00")
        self.assertEqual(f["distributed"], "4000.00")
        self.assertEqual(f["unspent"], "6000.00")
        self.assertTrue(f["reconciles"])
        by_ngo = {x["ngo"]["name"]: x for x in f["collaborations"]}
        self.assertEqual((by_ngo["Demo Relief NGO"]["received"], by_ngo["Demo Relief NGO"]["distributed"],
                          by_ngo["Demo Relief NGO"]["available"]), ("6000.00", "4000.00", "2000.00"))
        self.assertEqual((by_ngo["NGO B"]["received"], by_ngo["NGO B"]["available"]), ("3000.00", "3000.00"))
        with self.Session() as db:  # every posted transaction balances; the whole ledger balances
            dr, cr = db.execute(select(func.sum(FinanceLedgerEntry.debit), func.sum(FinanceLedgerEntry.credit))).one()
            self.assertEqual(D(dr), D(cr))

        # Step 6 - donor traceability is exact (A funded NGO A 4,000, all 4,000 distributed came from A)
        ua = self.ok(c.get("/api/donor/campaign-utilization", headers=self.donor))
        a = next(i for i in ua["items"] if i["reference_no"] == refs["A"])
        self.assertEqual((a["received_by_ngos"], a["distributed"], a["remaining_with_ngos"], a["unreleased"]),
                         ("4000.00", "4000.00", "0.00", "0.00"))
        ub = next(i for i in self.ok(c.get("/api/donor/campaign-utilization", headers=self.donor_b))["items"])
        self.assertEqual({n["name"]: n["received"] for n in ub["ngos"]}, {"Demo Relief NGO": "2000.00", "NGO B": "1000.00"})
        self.assertEqual(ub["distributed"], "0.00")
        uc = self.ok(c.get("/api/donor/campaign-utilization", headers=self.donor_c))["items"][0]
        self.assertEqual((uc["received_by_ngos"], uc["unreleased"]), ("2000.00", "1000.00"))
        self.assertNotIn(refs["A"], str(self.ok(c.get("/api/donor/campaign-utilization", headers=self.donor_b))))
        with self.Session() as db:
            donor_b_id = db.scalar(select(User.id).where(User.email == "donor.b@x.pk"))
            types = set(db.scalars(select(Notification.type).where(Notification.user_id == donor_b_id)))
        self.assertIn("campaign_ngo_funded", types)
        self.assertNotIn("campaign_assistance_distributed", types)  # none of B's money was distributed

        # reversal keeps history and restores the batch
        rev = self.ok(self.post(f"beneficiary-distributions/{vouchers[1]['id']}/reverse", self.ngo_a,
                                {"reason": "Duplicate household"}))
        self.assertEqual(rev["status"], "reversed")
        f2 = self.ok(c.get(f"/api/campaigns/{camp['id']}/finance", headers=h))
        self.assertEqual((f2["distributed"], f2["unspent"]), ("3500.00", "6500.00"))
        led = self.ok(c.get(f"/api/finance-ledger/batch/{batch_a['id']}", headers=self.ngo_a))
        self.assertEqual(led["closing_balance"], "2500.00")
        self.assertTrue(any(i["reversal_of"] for i in led["items"]))
        self.assertEqual(c.get(f"/api/finance-ledger/batch/{batch_a['id']}", headers=self.ngo_b).status_code, 404)
        self.assertEqual(c.get(f"/api/finance-ledger/campaign/{camp['id']}", headers=self.ngo_a).status_code, 403)
        for path, hd in ((f"finance-ledger/batch/{batch_a['id']}/pdf", self.ngo_a),
                         (f"campaigns/{camp['id']}/finance/statement.pdf", h),
                         (f"beneficiary-distributions/{vouchers[0]['id']}/voucher.pdf", self.ngo_a)):
            self.assertEqual(c.get(f"/api/{path}", headers=hd).content[:4], b"%PDF", path)
        self.assertEqual(c.get(f"/api/beneficiary-distributions/{vouchers[0]['id']}/voucher.pdf",
                               headers=self.ngo_b).status_code, 404)
        dash = self.ok(c.get("/api/campaign-finance/dashboard", headers=h))
        self.assertGreaterEqual(D(dash["distributed"]), D("3500.00"))
        nd = self.ok(c.get("/api/ngo/campaign-finance/dashboard", headers=self.ngo_a))
        self.assertEqual((nd["available"], nd["shopkeepers_assisted"], nd["householders_assisted"]), ("2500.00", 2, 1))


if __name__ == "__main__":
    unittest.main()

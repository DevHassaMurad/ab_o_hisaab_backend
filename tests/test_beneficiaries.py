"""Householder / shopkeeper portals, NGO beneficiary desk (balance-checked payments), PDMA oversight and ledgers."""
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from pwdlib import PasswordHash
from sqlalchemy import func, select

from model.donation_model import Ngo
from model.fund_model import DonationDisbursement
from model.notification_model import Notification
from model.registration_model import HouseholderDetails, ShopkeeperDetails
from model.role_model import Role
from model.user_model import User
from tests.test_donations import ADMIN, NGO, DonationFixture

ADDRESS = dict(identity_type="cnic", country="PK", state="Punjab", city="Multan", postal_code="60000",
               address1="House 1", address_confirmed=True)


class BeneficiaryTests(DonationFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ngo = cls.login(*NGO)
        hasher = PasswordHash.recommended()
        with cls.Session() as db:
            roles = dict(db.execute(select(Role.name, Role.id)).all())
            h = User(name="Test Householder", email="house@x.pk", role_id=roles["HOUSEHOLDER"],
                     password=hasher.hash("householder-pass"), email_verified_at=func.now(), registration_status="approved")
            s = User(name="Test Shopkeeper", email="shop@x.pk", role_id=roles["SHOPKEEPER"],
                     password=hasher.hash("shopkeeper-pass"), email_verified_at=func.now(), registration_status="approved")
            db.add_all([h, s])
            db.flush()
            db.add(HouseholderDetails(user_id=h.id, phone="+92 300 0000000", identity_number="1234512345671",
                                      household_size=5, dependents=3, adults=2, children=3, males=2, females=3,
                                      income_min=10000, income_max=20000, **ADDRESS))
            db.add(ShopkeeperDetails(user_id=s.id, phone="+92 300 1111111", identity_number="1234512345672",
                                     shop_name="Corner Store", business_number="B-1", business_category="Grocery",
                                     revenue_min=50000, revenue_max=90000, **ADDRESS))
            cls.ngo_row = db.scalar(select(Ngo).where(Ngo.name == "Demo Relief NGO"))
            cls.ngo_id = cls.ngo_row.id
            admin_id = db.scalar(select(User.id).where(User.email == ADMIN[0]))
            # 30,000 received by the NGO (receipt confirmed) -> its net balance
            db.add(DonationDisbursement(document_no="DD-OUT-TEST-1", sent_by=admin_id, recipient_type="ngo",
                                        recipient_id=cls.ngo_id, amount=Decimal("30000.00"), status="received",
                                        sent_at=datetime.utcnow() - timedelta(days=2),
                                        receipt_confirmed_at=datetime.utcnow() - timedelta(days=1)))
            db.commit()
        cls.house = cls.login("house@x.pk", "householder-pass")
        cls.shop = cls.login("shop@x.pk", "shopkeeper-pass")

    def post(self, path, headers, json=None):
        return self.client.post(f"/api/{path}", json=json or {}, headers=headers)

    def ask(self, headers, amount, purpose="food"):
        return self.post("beneficiary/requests", headers, {"ngo_id": self.ngo_id, "purpose": purpose, "amount": amount,
                                                           "description": "Flood damaged our home and food stock."})

    def test_01_profile_view_and_edit(self):
        p = self.client.get("/api/beneficiary/profile", headers=self.house).json()
        self.assertEqual((p["user_type"], p["adults"], p["household_size"]), ("householder", 2, 5))
        self.assertTrue(p["identity_number_masked"].endswith("5671"))
        self.assertNotIn("identity_number", p)
        bad = self.client.patch("/api/beneficiary/profile", json={"males": 4}, headers=self.house)
        self.assertEqual(bad.status_code, 422)  # 4 + 3 != 5
        ok = self.client.patch("/api/beneficiary/profile", json={"children": 4, "males": 3, "city": "Dera Ghazi Khan"},
                               headers=self.house).json()
        self.assertEqual((ok["household_size"], ok["city"]), (6, "Dera Ghazi Khan"))
        shop = self.client.patch("/api/beneficiary/profile", json={"revenue_min": "100000", "revenue_max": "90000"},
                                 headers=self.shop)
        self.assertEqual(shop.status_code, 422)
        # shopkeepers cannot set household fields; unknown keys for their type are ignored
        self.client.patch("/api/beneficiary/profile", json={"adults": 9}, headers=self.shop)
        self.assertNotIn("adults", self.client.get("/api/beneficiary/profile", headers=self.shop).json())

    def test_02_request_pay_from_balance_and_confirm(self):
        self.assertIn(self.ngo_id, [n["id"] for n in self.client.get("/api/beneficiary/ngos", headers=self.house).json()])
        r = self.ask(self.house, "20000.00").json()
        self.assertEqual((r["status"], r["document_no"][:7]), ("pending", "BEN-REQ"))
        self.assertEqual(self.ask(self.house, "100.00").status_code, 409)  # one open request per NGO
        with self.Session() as db:
            ngo_user = db.scalar(select(Ngo.user_id).where(Ngo.id == self.ngo_id))
            self.assertTrue(db.scalar(select(func.count()).select_from(Notification).where(
                Notification.user_id == ngo_user, Notification.type == "beneficiary_request")))
        desk = self.client.get("/api/ngo/beneficiary-requests", headers=self.ngo).json()
        self.assertEqual(desk["balance"]["net_balance"], "30000.00")
        row = next(i for i in desk["items"] if i["id"] == r["id"])
        self.assertTrue(row["can_pay_in_full"])
        self.assertEqual(row["beneficiary"]["adults"] is not None, True)
        paid = self.post(f"ngo/beneficiary-requests/{r['id']}/pay", self.ngo, {"payment_method": "BANK_TRANSFER"}).json()
        self.assertEqual((paid["status"], paid["paid_amount"]), ("paid", "20000.00"))
        self.assertEqual(self.client.get("/api/ngo/dashboard", headers=self.ngo).json()["net_balance"], "10000.00")
        # second request larger than the remaining balance is blocked
        r2 = self.ask(self.shop, "15000.00", "stock_replacement").json()
        short = self.post(f"ngo/beneficiary-requests/{r2['id']}/pay", self.ngo, {"payment_method": "CASH"})
        self.assertEqual((short.status_code, short.json()["code"]), (409, "INSUFFICIENT_BALANCE"))
        self.assertEqual(self.client.get("/api/ngo/beneficiary-requests/shortfall", headers=self.ngo).json()["shortfall"],
                         "5000.00")
        part = self.post(f"ngo/beneficiary-requests/{r2['id']}/pay", self.ngo, {"payment_method": "CASH", "amount": "10000"})
        self.assertEqual(part.json()["paid_amount"], "10000.00")
        self.assertEqual(self.post(f"ngo/beneficiary-requests/{r2['id']}/pay", self.ngo,
                                   {"payment_method": "CASH"}).status_code, 409)
        done = self.post(f"beneficiary/requests/{r['id']}/confirm-receipt", self.house).json()
        self.assertEqual(done["status"], "received")
        dash = self.client.get("/api/beneficiary/dashboard", headers=self.house).json()
        self.assertEqual(dash["received_amount"], "20000.00")

    def test_03_hold_reject_and_isolation(self):
        r = self.ask(self.house, "500.00", "health").json()
        self.assertEqual(self.post(f"ngo/beneficiary-requests/{r['id']}/hold", self.ngo, {"reason": "Need documents"})
                         .json()["status"], "on_hold")
        self.assertEqual(self.post(f"ngo/beneficiary-requests/{r['id']}/reject", self.ngo, {"reason": "Not eligible"})
                         .json()["status"], "rejected")
        dash = self.client.get("/api/beneficiary/dashboard", headers=self.house).json()
        self.assertEqual(dash["counts"]["rejected"], 1)
        # one beneficiary cannot see another's request; donors cannot use the portal
        self.assertEqual(self.client.get(f"/api/beneficiary/requests/{r['id']}", headers=self.shop).status_code, 404)
        self.assertEqual(self.client.get("/api/beneficiary/dashboard", headers=self.donor).status_code, 403)
        self.assertEqual(self.client.get("/api/beneficiary-requests", headers=self.ngo).status_code, 403)

    def test_04_pdma_oversight_and_ledgers(self):
        allr = self.client.get("/api/beneficiary-requests", headers=self.admin).json()
        self.assertGreaterEqual(allr["total"], 1)
        bal = {n["id"]: n for n in self.client.get("/api/ngo-balances", headers=self.admin).json()["items"]}
        self.assertIn(self.ngo_id, bal)
        led = self.client.get("/api/ngo/ledger", headers=self.ngo).json()
        credit, debit = Decimal(led["total_credit"]), Decimal(led["total_debit"])
        self.assertEqual(credit, Decimal("30000.00"))
        self.assertEqual(Decimal(led["closing_balance"]), credit - debit)
        self.assertEqual(led["closing_balance"],
                         self.client.get("/api/ngo/dashboard", headers=self.ngo).json()["net_balance"])
        self.assertNotIn("all", led)
        pdf = self.client.get("/api/ngo/ledger.pdf", headers=self.ngo)
        self.assertEqual((pdf.status_code, pdf.content[:4]), (200, b"%PDF"))
        p = self.client.get("/api/ledger/pdma", headers=self.admin).json()
        self.assertEqual(Decimal(p["closing_balance"]), Decimal(p["total_credit"]) - Decimal(p["total_debit"]))
        self.assertEqual(self.client.get("/api/ledger/pdma.pdf", headers=self.admin).content[:4], b"%PDF")
        self.assertEqual(self.client.get("/api/ledger/pdma", headers=self.ngo).status_code, 403)
        self.assertEqual(self.client.get("/api/ledger/pdma?date_from=2026-02-01&date_to=2026-01-01",
                                         headers=self.admin).status_code, 422)


if __name__ == "__main__":
    unittest.main()

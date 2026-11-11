"""Donor ledger, receipt vouchers and 'your donation has reached an NGO' notifications."""
import unittest
from decimal import Decimal

from pwdlib import PasswordHash
from sqlalchemy import func, select

from model.donation_model import Ngo
from model.fund_model import DonorDonation
from model.notification_model import Notification
from model.role_model import Role
from model.user_model import User
from tests.test_donations import NGO, DonationFixture


class DonorLedgerTests(DonationFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ngo = cls.login(*NGO)
        with cls.Session() as db:
            role = db.scalar(select(Role.id).where(Role.name == "DONOR"))
            db.add(User(name="Ledger Donor Two", email="ledger2@donor.pk", role_id=role, email_verified_at=func.now(),
                        password=PasswordHash.recommended().hash("ledger-two-pass")))
            db.commit()
            cls.ngo_id = db.scalar(select(Ngo.id).where(Ngo.name == "Demo Relief NGO"))
        cls.donor2 = cls.login("ledger2@donor.pk", "ledger-two-pass")
        c = cls.client
        # donor 1: a PDMA request (batch) answered by two contributions, plus a pending and a rejected one
        req = c.post("/api/donation-requests", json={"donor_id": cls.donor_id, "type": "donor", "reason": "Ledger batch",
                                                     "amount": "200000.00"}, headers=cls.admin).json()
        cls.batch_no = req["document_no"]
        c.post(f"/api/donation-requests/{req['id']}/accept", headers=cls.donor)

        def give(amount, headers, verify="verify", **extra):
            d = c.post("/api/donor-donations", json={"amount": amount, **extra}, headers=headers).json()
            if verify:
                c.post(f"/api/donor-donations/{d['id']}/verify", json={"action": verify, "reason": "No such payment"},
                       headers=cls.admin)
            return d["id"]

        cls.a1 = give("100000.00", cls.donor, request_id=req["id"], payment_method="BANK_TRANSFER", payment_reference="HBL-1")
        cls.a2 = give("50000.00", cls.donor, request_id=req["id"])
        cls.pending = give("7000.00", cls.donor, verify=None)
        cls.rejected = give("3000.00", cls.donor, verify="reject")
        cls.b1 = give("40000.00", cls.donor2)
        # NGO request funded by donor 1 (a1: 60,000) and donor 2 (b1: 40,000)
        r = c.post("/api/ngo/requests", json={"subject": "Ledger NGO", "amount": "100000.00",
                                              "description": "Relief kits for flood families."}, headers=cls.ngo).json()
        cls.ngo_request = r
        c.post(f"/api/ngo-requests/{r['id']}/approve", headers=cls.admin, json={
            "amount": "100000.00", "sources": [{"donor_donation_id": cls.a1, "amount": "60000.00"},
                                               {"donor_donation_id": cls.b1, "amount": "40000.00"}]})

    def reached(self, headers):
        return [n for n in self.client.get("/api/notifications", headers=headers).json()["items"]
                if n["type"] == "donation_reached_ngo"]

    def test_01_ledger_grouped_by_batch_with_exact_figures(self):
        led = self.client.get("/api/donor/ledger", headers=self.donor).json()
        batch = next(b for b in led["items"] if b["batch_no"] == self.batch_no)
        self.assertEqual(batch["entries_count"], 2)
        self.assertEqual(batch["total_verified"], "150000.00")
        self.assertEqual(batch["total_reserved"], "60000.00")   # reserved, not disbursed
        self.assertEqual(batch["total_disbursed"], "0.00")
        self.assertEqual(batch["available_balance"], "90000.00")
        a1 = next(e for e in batch["entries"] if e["id"] == self.a1)
        self.assertEqual(a1["allocation_status"], "reserved")
        self.assertEqual(a1["ngos_funded"], 1)
        # pending and rejected never count as verified or available
        pending = next(e for b in led["items"] for e in b["entries"] if e["id"] == self.pending)
        self.assertEqual(pending["verified_amount"], "0.00")
        self.assertEqual(pending["available_amount"], "0.00")
        s = self.client.get("/api/donor/ledger/summary", headers=self.donor).json()
        self.assertEqual(s["total_verified"], "150000.00")
        self.assertEqual(s["total_donated"], "157000.00")  # rejected excluded
        self.assertEqual(s["ngos_funded"], 1)
        # filters
        self.assertEqual(self.client.get("/api/donor/ledger", params={"status": "rejected"}, headers=self.donor)
                         .json()["entries_total"], 1)
        self.assertEqual(self.client.get("/api/donor/ledger", params={"ngo": "Demo Relief"}, headers=self.donor)
                         .json()["entries_total"], 1)
        self.assertEqual(self.client.get("/api/donor/ledger", params={"allocation_status": "reserved"},
                                         headers=self.donor).json()["entries_total"], 1)

    def test_02_detail_shows_only_own_share_and_no_other_donor(self):
        d = self.client.get(f"/api/donor/ledger/{self.a1}", headers=self.donor).json()
        self.assertEqual(d["batch_no"], self.batch_no)
        self.assertEqual(d["payment_method"], "Bank transfer")
        self.assertEqual(d["payment_reference"], "HBL-1")
        self.assertEqual(d["verified_by"], "PDMA Finance")
        u = d["utilization"][0]
        self.assertEqual(u["ngo_name"], "Demo Relief NGO")
        self.assertEqual(u["ngo_request_no"], self.ngo_request["document_no"])
        self.assertEqual(u["allocated_amount"], "60000.00")
        self.assertNotIn("Ledger Donor Two", str(d))
        with self.Session() as db:
            other_doc = db.get(DonorDonation, self.b1).document_no
        self.assertNotIn(other_doc, str(d))  # never the other donor's contribution
        # another donor cannot open it (same answer as a missing record)
        self.assertEqual(self.client.get(f"/api/donor/ledger/{self.a1}", headers=self.donor2).status_code, 404)
        self.assertEqual(self.client.get("/api/donor/ledger/999999", headers=self.donor).status_code, 404)
        self.assertEqual(self.client.get("/api/donor/ledger", headers=self.admin).status_code, 403)

    def test_03_vouchers_unique_stable_and_owned(self):
        r1 = self.client.get(f"/api/donor/ledger/{self.a1}/voucher.pdf", headers=self.donor)
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.content.startswith(b"%PDF"))
        self.assertEqual(r1.headers["cache-control"], "no-store")
        r2 = self.client.get(f"/api/donor/ledger/{self.a2}/voucher.pdf", headers=self.donor)
        with self.Session() as db:
            n1 = db.get(DonorDonation, self.a1).receipt_no
            n2 = db.get(DonorDonation, self.a2).receipt_no
        self.assertRegex(n1, r"^RCPT-\d{2}-\d{5}$")
        self.assertNotEqual(n1, n2)
        self.assertIn(n1, r1.headers["content-disposition"])
        self.assertIn(n2, r2.headers["content-disposition"])
        # reprinting keeps the same number
        self.client.get(f"/api/donor/ledger/{self.a1}/voucher.pdf", headers=self.donor)
        with self.Session() as db:
            self.assertEqual(db.get(DonorDonation, self.a1).receipt_no, n1)
        # pending / rejected vouchers are generated with their real status (not 'confirmed')
        self.assertEqual(self.client.get(f"/api/donor/ledger/{self.pending}/voucher.pdf", headers=self.donor).status_code, 200)
        self.assertEqual(self.client.get(f"/api/donor/ledger/{self.rejected}/voucher.pdf", headers=self.donor).status_code, 200)
        # another donor cannot download it by changing the id
        self.assertEqual(self.client.get(f"/api/donor/ledger/{self.a1}/voucher.pdf", headers=self.donor2).status_code, 404)
        from controller.donor_ledger import DonorLedger

        with self.Session() as db:
            donor = db.get(User, self.donor_id)
            v = DonorLedger(db, donor).voucher_data(self.pending)
            self.assertEqual(v["payment_status"], "pending")
            self.assertEqual(v["amount"], "7000.00")

    def test_04_reached_ngo_only_after_receipt_confirmation_per_donor_once(self):
        rid = self.ngo_request["id"]
        t = self.client.post("/api/disbursements", headers=self.admin, json={
            "recipient_type": "ngo", "recipient_id": self.ngo_id, "amount": "80000.00", "ngo_request_id": rid}).json()
        # transfer recorded, NGO has not confirmed: no 'reached' notification yet
        self.assertEqual(self.reached(self.donor), [])
        self.assertEqual(self.reached(self.donor2), [])
        ack = self.client.post(f"/api/ngo/allocations/{t['id']}/acknowledge", headers=self.ngo)
        self.assertEqual(ack.status_code, 200, ack.text)
        mine, theirs = self.reached(self.donor), self.reached(self.donor2)
        self.assertEqual(len(mine), 1)
        self.assertEqual(len(theirs), 1)
        # oldest source first: a1 pays 60,000, b1 pays 20,000 — each donor sees only their own amount
        self.assertIn("PKR 60,000.00", mine[0]["message"])
        self.assertIn(self.batch_no, mine[0]["message"])
        self.assertEqual(mine[0]["related_type"], "donor_donation")
        self.assertEqual(mine[0]["related_id"], self.a1)
        self.assertIn("PKR 20,000.00", theirs[0]["message"])
        self.assertNotIn("Ledger Donor Two", mine[0]["message"])
        # second transfer + confirmation -> one new notification for donor 2 only, no duplicates
        t2 = self.client.post("/api/disbursements", headers=self.admin, json={
            "recipient_type": "ngo", "recipient_id": self.ngo_id, "amount": "20000.00", "ngo_request_id": rid}).json()
        self.client.post(f"/api/ngo/allocations/{t2['id']}/acknowledge", headers=self.ngo)
        self.assertEqual(len(self.reached(self.donor)), 1)
        self.assertEqual(len(self.reached(self.donor2)), 2)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Notification).where(
                Notification.type == "donation_reached_ngo")), 3)
        # ledger now shows the disbursement and confirmed receipt
        d = self.client.get(f"/api/donor/ledger/{self.a1}", headers=self.donor).json()
        self.assertEqual(d["disbursed_amount"], "60000.00")
        self.assertEqual(d["allocation_status"], "disbursed")
        self.assertEqual(d["utilization"][0]["receipt_status"], "confirmed")
        self.assertEqual(Decimal(d["available_amount"]), Decimal("40000.00"))


if __name__ == "__main__":
    unittest.main()

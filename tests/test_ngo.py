"""NGO portal: requests, PDMA review / (partial) approval / rejection, linked transfers, receipt acknowledgement,
expenditure + verification, dashboard figures, reports and isolation between NGOs."""
import unittest
from datetime import date

from pwdlib import PasswordHash
from sqlalchemy import func, select

from model.donation_model import Ngo
from model.fund_model import EmailOutbox
from model.notification_model import Notification
from model.role_model import Role
from model.user_model import User
from tests.test_donations import NGO, DonationFixture


class NgoPortalTests(DonationFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ngo = cls.login(*NGO)
        with cls.Session() as db:
            role = db.scalar(select(Role.id).where(Role.name == "NGO"))
            other = User(name="Other NGO Admin", email="other@ngo.pk", role_id=role, email_verified_at=func.now(),
                         password=PasswordHash.recommended().hash("other-ngo-password"))
            db.add(other)
            db.flush()
            db.add(Ngo(name="Other NGO", user_id=other.id))
            cls.ngo_id = db.scalar(select(Ngo.id).where(Ngo.name == "Demo Relief NGO"))
            db.commit()
        cls.other_ngo = cls.login("other@ngo.pk", "other-ngo-password")
        # verified donor money PDMA can send from
        d = cls.client.post("/api/donor-donations", json={"amount": "500000.00"}, headers=cls.donor).json()
        cls.client.post(f"/api/donor-donations/{d['id']}/verify", json={"action": "verify"}, headers=cls.admin)
        cls.source_id = d["id"]

    def funded(self, amount):
        """Approval body funded entirely from the verified donor contribution created in setUpClass."""
        return {"amount": amount, "sources": [{"donor_donation_id": self.source_id, "amount": amount}]}

    def submit(self, amount="100000.00", subject="Shelter kits for Dadu", headers=None):
        r = self.client.post("/api/ngo/requests", json={
            "subject": subject, "description": "Tents and blankets for 200 displaced families.",
            "amount": amount, "supporting_reference": "Needs assessment NA-12"}, headers=headers or self.ngo)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def transfer(self, amount, request_id=None, ngo_id=None, ref="TT-1"):
        return self.client.post("/api/disbursements", json={
            "source_batch_id": self.source_id, "recipient_type": "ngo", "recipient_id": ngo_id or self.ngo_id,
            "amount": amount, "remarks": "Shelter", "ngo_request_id": request_id, "payment_reference": ref},
            headers=self.admin)

    def test_submit_notifies_pdma_and_numbers_requests(self):
        before = self.Session().query(EmailOutbox).count()
        a, b = self.submit(), self.submit("5000.00", "Second")
        self.assertRegex(a["document_no"], r"^NGO-REQ-\d{2}-\d{5}$")
        self.assertEqual(int(b["document_no"][-5:]), int(a["document_no"][-5:]) + 1)
        self.assertEqual(a["status"], "pending")
        with self.Session() as db:
            self.assertTrue(db.scalar(select(Notification).where(Notification.type == "ngo_request",
                                                                 Notification.related_id == a["id"])))
            self.assertGreater(db.query(EmailOutbox).count(), before)
        staff_items = self.client.get("/api/notifications", headers=self.admin).json()["items"]
        self.assertTrue(any(n["related_id"] == a["id"] and n["type"] == "ngo_request" for n in staff_items))

    def test_full_flow_partial_approval_transfer_receipt_expenditure(self):
        base = {k: float(v) for k, v in self.client.get("/api/ngo/dashboard", headers=self.ngo).json().items()
                if k in ("approved_awaiting_transfer", "received_amount", "invested_amount", "net_balance")}

        def delta(dash, key):  # other tests share this NGO, so compare changes from this test only
            return round(float(dash[key]) - base[key], 2)

        req = self.submit("100000.00")
        rid = req["id"]
        self.assertEqual(self.client.post(f"/api/ngo-requests/{rid}/review", headers=self.admin).json()["status"], "under_review")
        over = self.client.post(f"/api/ngo-requests/{rid}/approve", json=self.funded("100000.01"), headers=self.admin)
        self.assertEqual(over.json()["code"], "EXCEEDS_REQUESTED")
        appr = self.client.post(f"/api/ngo-requests/{rid}/approve", json=self.funded("60000.00"), headers=self.admin).json()
        self.assertEqual(appr["status"], "partially_approved")
        self.assertEqual(appr["progress_status"], "awaiting_transfer")
        self.assertEqual(self.client.post(f"/api/ngo-requests/{rid}/reject", json={"reason": "late"},
                                          headers=self.admin).json()["code"], "ALREADY_DECIDED")
        # approval is not money: nothing received yet
        dash = self.client.get("/api/ngo/dashboard", headers=self.ngo).json()
        self.assertEqual(delta(dash, "approved_awaiting_transfer"), 60000.0)
        self.assertEqual(delta(dash, "received_amount"), 0.0)
        eligible = self.client.get("/api/ngo-requests/eligible", params={"ngo_id": self.ngo_id}, headers=self.admin).json()
        self.assertIn(rid, [e["id"] for e in eligible["items"]])
        # transfers capped by the approved amount
        t1 = self.transfer("40000.00", rid)
        self.assertEqual(t1.status_code, 201, t1.text)
        self.assertEqual(t1.json()["ngo_request_document_no"], req["document_no"])
        self.assertEqual(self.transfer("20000.01", rid).json()["code"], "EXCEEDS_APPROVED_AMOUNT")
        detail = self.client.get(f"/api/ngo/requests/{rid}", headers=self.ngo).json()
        self.assertEqual(detail["progress_status"], "transferred")
        self.assertEqual(detail["transfers"][0]["payment_reference"], "TT-1")
        # NGO notified about the transfer
        notes = self.client.get("/api/notifications", headers=self.ngo).json()["items"]
        self.assertTrue(any(n["type"] == "ngo_transfer" for n in notes))
        # expenditure blocked until receipt is confirmed
        alloc = t1.json()["id"]
        blocked = self.client.post("/api/ngo/expenditures", json={"allocation_id": alloc, "category": "shelter",
                                   "description": "Tents", "amount": "1000.00", "expense_date": date.today().isoformat()},
                                   headers=self.ngo)
        self.assertEqual(blocked.json()["code"], "RECEIPT_NOT_CONFIRMED")
        ack = self.client.post(f"/api/ngo/allocations/{alloc}/acknowledge", headers=self.ngo)
        self.assertEqual(ack.status_code, 200, ack.text)
        self.assertIsNotNone(ack.json()["receipt_confirmed_at"])
        self.assertEqual(self.client.post(f"/api/ngo/allocations/{alloc}/acknowledge", headers=self.ngo).json()["code"],
                         "ALREADY_ACKNOWLEDGED")
        detail = self.client.get(f"/api/ngo/requests/{rid}", headers=self.ngo).json()
        self.assertEqual(detail["progress_status"], "partially_received")
        # expenditure within the allocation, then verified
        e = self.client.post("/api/ngo/expenditures", json={"allocation_id": alloc, "category": "shelter",
                             "description": "Tents purchased", "amount": "15000.00",
                             "expense_date": date.today().isoformat(), "supporting_document": "INV-9"}, headers=self.ngo)
        self.assertEqual(e.status_code, 201, e.text)
        self.assertRegex(e.json()["reference"], r"^NGO-EXP-\d{2}-\d{5}$")
        too_much = self.client.post("/api/ngo/expenditures", json={"allocation_id": alloc, "category": "shelter",
                                    "description": "More", "amount": "25000.01", "expense_date": date.today().isoformat()},
                                    headers=self.ngo)
        self.assertEqual(too_much.json()["code"], "EXCEEDS_ALLOCATION")
        v = self.client.post(f"/api/ngo-expenditures/{e.json()['id']}/verify", json={"action": "verify"}, headers=self.admin)
        self.assertEqual(v.json()["verification_status"], "verified")
        dash = self.client.get("/api/ngo/dashboard", headers=self.ngo).json()
        self.assertEqual(delta(dash, "received_amount"), 40000.0)
        self.assertEqual(delta(dash, "invested_amount"), 15000.0)
        self.assertEqual(delta(dash, "net_balance"), 25000.0)
        self.assertEqual(delta(dash, "approved_awaiting_transfer"), 20000.0)
        self.assertTrue(dash["monthly"])
        self.assertIn("25000.00", [u["unspent"] for u in dash["utilization"]])
        # verified NGO spending now appears in the donor's batch report (traceability)
        batch = next(b for b in self.client.get("/api/donor/reports/batches", headers=self.donor).json()["items"]
                     if b["total_allocated"] != "0.00")
        self.assertIsNotNone(batch["total_spent"])

    def test_rejection_requires_reason_and_is_not_pending(self):
        req = self.submit("9000.00", "Reject me")
        self.assertEqual(self.client.post(f"/api/ngo-requests/{req['id']}/reject", json={"reason": ""},
                                          headers=self.admin).status_code, 422)
        r = self.client.post(f"/api/ngo-requests/{req['id']}/reject", json={"reason": "Outside flood area"}, headers=self.admin)
        self.assertEqual(r.json()["status"], "rejected")
        self.assertEqual(r.json()["rejection_reason"], "Outside flood area")
        pending_before = self.client.get("/api/ngo/dashboard", headers=self.ngo).json()["pending_amount"]
        self.assertEqual(self.client.get(f"/api/ngo/requests/{req['id']}", headers=self.ngo).json()["progress_status"], "rejected")
        self.submit("1000.00", "Open one")
        pending_after = self.client.get("/api/ngo/dashboard", headers=self.ngo).json()["pending_amount"]
        self.assertAlmostEqual(float(pending_after) - float(pending_before), 1000.0)

    def test_request_info(self):
        req = self.submit("3000.00", "Info please")
        r = self.client.post(f"/api/ngo-requests/{req['id']}/request-info", json={"message": "Attach the beneficiary list"},
                             headers=self.admin).json()
        self.assertEqual(r["info_request"], "Attach the beneficiary list")
        self.assertEqual(r["status"], "under_review")

    def test_transfer_must_match_request_ngo_and_approval(self):
        req = self.submit("8000.00", "Mismatch")
        self.assertEqual(self.transfer("100.00", req["id"]).json()["code"], "NGO_REQUEST_NOT_APPROVED")
        self.client.post(f"/api/ngo-requests/{req['id']}/approve", json=self.funded("8000.00"), headers=self.admin)
        with self.Session() as db:
            other_id = db.scalar(select(Ngo.id).where(Ngo.name == "Other NGO"))
        self.assertEqual(self.transfer("100.00", req["id"], ngo_id=other_id).json()["code"], "NGO_REQUEST_MISMATCH")

    def test_authorization_and_isolation(self):
        req = self.submit("2000.00", "Mine only")
        # another NGO cannot see it, acknowledge transfers or use PDMA endpoints
        self.assertEqual(self.client.get(f"/api/ngo/requests/{req['id']}", headers=self.other_ngo).status_code, 404)
        self.assertNotIn(req["id"], [r["id"] for r in self.client.get("/api/ngo/requests", headers=self.other_ngo).json()["items"]])
        self.assertEqual(self.client.post(f"/api/ngo-requests/{req['id']}/approve", json={"amount": "1.00"},
                                          headers=self.ngo).status_code, 403)
        self.assertEqual(self.client.get("/api/ngo-requests", headers=self.ngo).status_code, 403)
        self.assertEqual(self.client.post("/api/disbursements", json={"source_batch_id": self.source_id, "recipient_type": "ngo",
                                          "recipient_id": self.ngo_id, "amount": "1.00"}, headers=self.ngo).status_code, 403)
        # donors and DCs have no NGO portal access
        self.assertEqual(self.client.get("/api/ngo/dashboard", headers=self.donor).status_code, 403)
        self.assertEqual(self.client.get("/api/ngo/dashboard", headers=self.dc).status_code, 403)
        # another NGO cannot acknowledge this NGO's transfer
        self.client.post(f"/api/ngo-requests/{req['id']}/approve", json=self.funded("2000.00"), headers=self.admin)
        t = self.transfer("500.00", req["id"]).json()
        self.assertEqual(self.client.post(f"/api/ngo/allocations/{t['id']}/acknowledge", headers=self.other_ngo).status_code, 404)

    def test_pdma_lists_and_reports(self):
        self.submit("4000.00", "Listed")
        listing = self.client.get("/api/ngo-requests", params={"ngo_id": self.ngo_id}, headers=self.admin).json()
        self.assertGreaterEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["ngo"]["name"], "Demo Relief NGO")
        ngos = self.client.get("/api/ngos/registered", headers=self.admin).json()
        demo = next(n for n in ngos["items"] if n["name"] == "Demo Relief NGO")
        self.assertTrue(demo["has_portal_account"])
        self.assertEqual(self.client.get("/api/recipients", params={"type": "ngo", "search": "Demo"},
                                         headers=self.admin).json()["items"][0]["name"], "Demo Relief NGO")
        today = date.today()
        m = self.client.get("/api/ngo/reports/monthly", params={"year": today.year, "month": today.month}, headers=self.ngo)
        self.assertEqual(m.status_code, 200)
        pdf = self.client.get("/api/ngo/reports/monthly.pdf", params={"year": today.year, "month": today.month},
                              headers=self.ngo)
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        profile = self.client.get("/api/ngo/profile", headers=self.ngo).json()
        self.assertEqual(profile["name"], "Demo Relief NGO")


if __name__ == "__main__":
    unittest.main()

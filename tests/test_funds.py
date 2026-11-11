"""PDMA fund flow: request commitment, donor -> PDMA donations (DD-IN), verification,
PDMA -> recipient disbursements (DD-OUT), balances, analytics, export and authorization."""
import unittest
from unittest.mock import patch

from sqlalchemy import select

from config.auth_mail import AuthMailer, MailDeliveryError
from config.outbox import deliver_due
from model.donation_model import DonationRequest, Ngo
from model.fund_model import EmailOutbox
from tests.test_donations import DonationFixture


class FundFlowTests(DonationFixture, unittest.TestCase):
    def new_request(self, amount="20000.00", reason=None):
        reason = reason or f"Fund flow {amount} {self.id()}"
        r = self.send_request(amount, reason)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def contribute(self, amount, **extra):
        return self.client.post("/api/donor-donations", json={"amount": amount, **extra}, headers=self.donor)

    def verified_donation(self, amount):
        d = self.contribute(amount).json()
        r = self.client.post(f"/api/donor-donations/{d['id']}/verify", json={"action": "verify"}, headers=self.admin)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def ngo(self, name):
        with self.Session() as db:
            n = Ngo(name=name)
            db.add(n)
            db.commit()
            return n.id

    # 2 acceptance and rejection (commitment) ------------------------------------------------------
    def test_accept_is_a_commitment_once_and_notifies_pdma_with_email(self):
        req = self.new_request("15000.00")
        with patch("routes.fund_routes.push_notification") as push, patch("routes.fund_routes.deliver_due"):
            r = self.client.post(f"/api/donation-requests/{req['id']}/accept", headers=self.donor)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["donor_response"], "ACCEPTED")
        self.assertIsNotNone(body["accepted_at"])
        self.assertEqual(body["total_received"], "0.00")  # accepting is not a payment
        push.assert_called()
        with self.Session() as db:
            batch = db.get(DonationRequest, req["id"])
            self.assertEqual(batch.accepted_by, self.donor_id)
            mail = db.scalars(select(EmailOutbox).where(EmailOutbox.subject == "Donation request accepted")).all()
            self.assertTrue(mail)
        again = self.client.post(f"/api/donation-requests/{req['id']}/accept", headers=self.donor)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["code"], "ALREADY_ACCEPTED")
        self.assertEqual(self.client.post(f"/api/donation-requests/{req['id']}/reject", json={}, headers=self.donor).status_code, 409)

    def test_reject_stores_time_and_optional_reason(self):
        req = self.new_request("9000.00")
        r = self.client.post(f"/api/donation-requests/{req['id']}/reject", json={"reason": "Not this quarter"},
                             headers=self.donor)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["donor_response"], "REJECTED")
        self.assertIsNotNone(r.json()["rejected_at"])
        self.assertEqual(r.json()["response_note"], "Not this quarter")

    def test_only_the_addressed_donor_can_decide(self):
        req = self.new_request("1200.00")
        self.assertEqual(self.client.post(f"/api/donation-requests/{req['id']}/accept", headers=self.admin).status_code, 403)
        self.assertEqual(self.client.post("/api/donation-requests/999999/accept", headers=self.donor).status_code, 404)

    # 3-4, 7 incoming contributions, verification and document numbers ----------------------------------
    def test_contribution_document_numbers_and_verification(self):
        a = self.contribute("1000.00", remarks="First").json()
        b = self.contribute("2000.00").json()
        self.assertRegex(a["document_no"], r"^DD-IN-\d{2}-\d{5}$")
        self.assertEqual(int(b["document_no"][-5:]), int(a["document_no"][-5:]) + 1)
        self.assertEqual(a["payment_status"], "pending")
        self.assertEqual(a["available_amount"], "0.00")  # pending money is not available
        r = self.client.post(f"/api/donor-donations/{a['id']}/verify", json={"action": "verify"}, headers=self.admin)
        self.assertEqual(r.json()["payment_status"], "verified")
        self.assertEqual(r.json()["available_amount"], "1000.00")
        self.assertEqual(self.client.post(f"/api/donor-donations/{a['id']}/verify", json={"action": "verify"},
                                          headers=self.admin).status_code, 409)
        rej = self.client.post(f"/api/donor-donations/{b['id']}/verify", json={"action": "reject", "reason": "No such transfer"},
                               headers=self.admin).json()
        self.assertEqual(rej["payment_status"], "rejected")

    def test_contribution_can_answer_an_accepted_request(self):
        req = self.new_request("5000.00")
        self.client.post(f"/api/donation-requests/{req['id']}/accept", headers=self.donor)
        r = self.contribute("2500.00", request_id=req["id"], payment_reference="BANK-77")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["request_document_no"], req["document_no"])
        dup = self.contribute("1.00", payment_reference="BANK-77")
        self.assertEqual(dup.json()["code"], "DUPLICATE_PAYMENT")

    # 5-6, 8-10 outgoing distributions, recipients, batch tracking, balances, over-distribution ------------
    def test_distribution_rules_and_traceability(self):
        src = self.verified_donation("10000.00")
        ngo_a, ngo_b = self.ngo("Relief A"), self.ngo("Relief B")
        out1 = self.client.post("/api/disbursements", json={"source_batch_id": src["id"], "recipient_type": "ngo",
                                                            "recipient_id": ngo_a, "amount": "6000.00"}, headers=self.admin)
        self.assertEqual(out1.status_code, 201, out1.text)
        self.assertRegex(out1.json()["document_no"], r"^DD-OUT-\d{2}-\d{5}$")
        self.assertEqual(out1.json()["recipient_name"], "Relief A")
        self.assertEqual(out1.json()["source_document_no"], src["document_no"])
        # second NGO from the same batch; then over-distribution refused
        self.assertEqual(self.client.post("/api/disbursements", json={"source_batch_id": src["id"], "recipient_type": "ngo",
                                                                      "recipient_id": ngo_b, "amount": "4000.00"},
                                          headers=self.admin).status_code, 201)
        over = self.client.post("/api/disbursements", json={"source_batch_id": src["id"], "recipient_type": "ngo",
                                                            "recipient_id": ngo_a, "amount": "0.01"}, headers=self.admin)
        self.assertEqual(over.status_code, 409)
        self.assertEqual(over.json()["code"], "INSUFFICIENT_FUNDS")
        detail = self.client.get(f"/api/donor-donations/{src['id']}", headers=self.admin).json()
        self.assertEqual(detail["distributed_amount"], "10000.00")
        self.assertEqual(detail["available_amount"], "0.00")
        self.assertEqual(len(detail["disbursements"]), 2)
        # a rejected distribution stops counting, so its amount is available again
        r = self.client.post(f"/api/disbursements/{out1.json()['id']}/status", json={"status": "rejected", "reason": "Wrong account"},
                             headers=self.admin)
        self.assertEqual(r.json()["status"], "rejected")
        detail = self.client.get(f"/api/donor-donations/{src['id']}", headers=self.admin).json()
        self.assertEqual(detail["available_amount"], "6000.00")
        # status rules
        self.assertEqual(self.client.post(f"/api/disbursements/{out1.json()['id']}/status", json={"status": "received"},
                                          headers=self.admin).json()["code"], "INVALID_TRANSITION")

    def test_unverified_source_and_invalid_recipients_are_refused(self):
        pending = self.contribute("500.00").json()
        r = self.client.post("/api/disbursements", json={"source_batch_id": pending["id"], "recipient_type": "ngo",
                                                         "recipient_id": self.ngo("X"), "amount": "1.00"}, headers=self.admin)
        self.assertEqual(r.json()["code"], "SOURCE_NOT_VERIFIED")
        src = self.verified_donation("500.00")
        missing = self.client.post("/api/disbursements", json={"source_batch_id": src["id"], "recipient_type": "ngo",
                                                               "recipient_id": 999999, "amount": "1.00"}, headers=self.admin)
        self.assertEqual(missing.json()["code"], "RECIPIENT_NOT_FOUND")
        shop = self.client.post("/api/disbursements", json={"source_batch_id": src["id"], "recipient_type": "shopkeeper",
                                                            "recipient_id": 1, "amount": "1.00"}, headers=self.admin)
        self.assertEqual(shop.json()["code"], "RECIPIENT_REGISTER_UNAVAILABLE")
        self.assertFalse(self.client.get("/api/recipients", params={"type": "householder"}, headers=self.admin).json()["available"])

    # 13 analytics and export ----------------------------------------------------------------------
    def test_summary_and_export(self):
        src = self.verified_donation("3000.00")
        self.client.post("/api/disbursements", json={"source_batch_id": src["id"], "recipient_type": "ngo",
                                                     "recipient_id": self.ngo("Summary NGO"), "amount": "1000.00"},
                         headers=self.admin)
        s = self.client.get("/api/funds/summary", headers=self.admin).json()
        received, distributed = float(s["total_received"]), float(s["total_distributed"])
        self.assertAlmostEqual(float(s["available_balance"]), received - distributed)
        self.assertGreaterEqual(received, 3000)
        self.assertTrue(any(r["name"] == "Summary NGO" for r in s["distribution_by_type"]["ngo"]["recipients"]))
        self.assertTrue(s["daily"])
        self.assertTrue(any(r["direction"] == "out" for r in s["recent"]))
        self.assertEqual(s["top_donors"][0]["name"], "Demo Donor")
        self.assertGreaterEqual(s["requests"]["sent"], 1)
        self.assertEqual(s["requests"]["sent"], s["requests"]["accepted"] + s["requests"]["rejected"] + s["requests"]["awaiting"])
        csv_resp = self.client.get("/api/funds/export", headers=self.admin)
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp.headers["content-type"])
        self.assertIn(src["document_no"], csv_resp.text)

    # Authorization ------------------------------------------------------------------------------------
    def test_authorization(self):
        d = self.contribute("100.00").json()
        self.assertEqual(self.client.get("/api/funds/summary", headers=self.donor).status_code, 403)
        self.assertEqual(self.client.post("/api/disbursements", json={"source_batch_id": d["id"], "recipient_type": "ngo",
                                                                      "recipient_id": 1, "amount": "1.00"},
                                          headers=self.donor).status_code, 403)
        self.assertEqual(self.client.post(f"/api/donor-donations/{d['id']}/verify", json={"action": "verify"},
                                          headers=self.donor).status_code, 403)
        self.assertEqual(self.client.post("/api/donor-donations", json={"amount": "1.00"}, headers=self.admin).status_code, 403)
        self.assertEqual(self.client.get("/api/donor-donations", headers=self.dc).status_code, 403)
        body = self.client.get("/api/donor-donations", headers=self.donor).json()
        self.assertTrue(all(i["donor"]["id"] == self.donor_id for i in body["items"]))
        # server-computed totals per status for this donor
        self.assertGreaterEqual(body["totals"]["pending"]["count"], 1)
        self.assertIn("amount", body["totals"]["verified"])

    # 12 email outbox: failures never lose records; retried later --------------------------------------
    def test_email_outbox_retries_and_keeps_records(self):
        req = self.new_request("700.00")
        with patch("routes.fund_routes.deliver_due"):
            self.client.post(f"/api/donation-requests/{req['id']}/accept", headers=self.donor)

        def fail(*_a, **_k):
            raise MailDeliveryError("smtp down")

        with patch.object(AuthMailer, "deliver", fail):
            self.assertEqual(deliver_due(self.engine), 0)
        with self.Session() as db:
            row = db.scalars(select(EmailOutbox).where(EmailOutbox.status == "pending").order_by(EmailOutbox.id.desc())).first()
            self.assertEqual(row.attempts, 1)
            self.assertIsNotNone(row.last_error)
            self.assertEqual(db.get(DonationRequest, req["id"]).donor_response, "ACCEPTED")  # record kept
            row.next_attempt_at = row.created_at  # make it due now
            db.commit()
        self.sent.clear()
        self.assertGreaterEqual(deliver_due(self.engine), 1)  # mailer is captured in the fixture
        self.assertTrue(self.sent)


if __name__ == "__main__":
    unittest.main()

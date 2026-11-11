"""Donor fund traceability through NGO allocations: funding selection, reservation, partial / failed transfers,
release, donor notifications, donor reports, privacy and reconciliation."""
import unittest
from decimal import Decimal

from pwdlib import PasswordHash
from sqlalchemy import func, select

from model.donation_model import Ngo
from model.fund_model import AllocationFundingSource, DonorDonation, FundingDisbursement, FundLedgerEvent
from model.notification_model import Notification
from model.role_model import Role
from model.user_model import User
from tests.test_donations import NGO, DonationFixture


class TraceabilityTests(DonationFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ngo = cls.login(*NGO)
        with cls.Session() as db:
            role = db.scalar(select(Role.id).where(Role.name == "DONOR"))
            db.add(User(name="Second Donor", email="second@donor.pk", role_id=role, email_verified_at=func.now(),
                        password=PasswordHash.recommended().hash("second-donor-pass")))
            db.commit()
            cls.ngo_id = db.scalar(select(Ngo.id).where(Ngo.name == "Demo Relief NGO"))
        cls.donor2 = cls.login("second@donor.pk", "second-donor-pass")

    # helpers ------------------------------------------------------------------------------------------
    def contribution(self, amount, headers=None, verify=True):
        d = self.client.post("/api/donor-donations", json={"amount": amount}, headers=headers or self.donor).json()
        if verify:
            self.client.post(f"/api/donor-donations/{d['id']}/verify", json={"action": "verify"}, headers=self.admin)
        return d["id"]

    def ngo_request(self, amount):
        r = self.client.post("/api/ngo/requests", json={"subject": "Funding test", "amount": amount,
                             "description": "Relief goods for affected families."}, headers=self.ngo)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def approve(self, rid, amount, sources):
        return self.client.post(f"/api/ngo-requests/{rid}/approve", headers=self.admin, json={
            "amount": amount, "sources": [{"donor_donation_id": d, "amount": a} for d, a in sources]})

    def transfer(self, rid, amount):
        return self.client.post("/api/disbursements", headers=self.admin, json={
            "recipient_type": "ngo", "recipient_id": self.ngo_id, "amount": amount, "ngo_request_id": rid,
            "payment_reference": f"PAY-{rid}-{amount}"})

    def available(self, donation_id):
        return Decimal(self.client.get(f"/api/donor-donations/{donation_id}", headers=self.admin).json()["available_amount"])

    # tests ----------------------------------------------------------------------------------------------
    def test_selection_validation(self):
        a = self.contribution("1000.00")
        pending = self.contribution("1000.00", verify=False)
        rid = self.ngo_request("5000.00")
        self.assertEqual(self.approve(rid, "1000.00", []).json()["code"], "FUNDING_REQUIRED")
        short = self.approve(rid, "1000.00", [(a, "600.00")]).json()
        self.assertEqual(short["code"], "FUNDING_MISMATCH")
        self.assertIn("400.00 more is needed", short["detail"])
        self.assertEqual(self.approve(rid, "1000.00", [(a, "1000.01")]).json()["code"], "FUNDING_MISMATCH")
        self.assertEqual(self.approve(rid, "1001.00", [(a, "1001.00")]).json()["code"], "INSUFFICIENT_SOURCE_FUNDS")
        self.assertEqual(self.approve(rid, "500.00", [(pending, "500.00")]).json()["code"], "SOURCE_NOT_VERIFIED")
        self.assertEqual(self.approve(rid, "500.00", [(a, "250.00"), (a, "250.00")]).json()["code"], "DUPLICATE_SOURCE")
        # nothing was reserved by the failed attempts
        self.assertEqual(self.available(a), Decimal("1000.00"))
        listed = self.client.get("/api/funding-sources/available", headers=self.admin).json()["items"]
        self.assertIn(a, [s["donor_donation_id"] for s in listed])
        self.assertNotIn(pending, [s["donor_donation_id"] for s in listed])

    def test_multi_donor_funding_partial_and_failed_transfers(self):
        a = self.contribution("200000.00")                       # donor 1
        b = self.contribution("150000.00", headers=self.donor2)  # donor 2
        c = self.contribution("300000.00")                       # donor 1, another batch
        rid = self.ngo_request("500000.00")
        r = self.approve(rid, "500000.00", [(a, "200000.00"), (b, "150000.00"), (c, "150000.00")])
        self.assertEqual(r.status_code, 200, r.text)
        detail = self.client.get(f"/api/ngo-requests/{rid}", headers=self.admin).json()
        alloc = detail["allocation"]
        self.assertRegex(alloc["document_no"], r"^ALLOC-\d{2}-\d{5}$")
        self.assertEqual(alloc["reserved_amount"], "500000.00")
        self.assertEqual(alloc["disbursed_amount"], "0.00")
        self.assertEqual(len(alloc["funding_sources"]), 3)
        # reservations reduce availability; approval is not disbursement
        self.assertEqual(self.available(a), Decimal("0.00"))
        self.assertEqual(self.available(c), Decimal("150000.00"))
        s1 = self.client.get("/api/donor/reports/summary", headers=self.donor).json()
        self.assertEqual(Decimal(s1["total_reserved"]), Decimal("350000.00"))
        self.assertEqual(Decimal(s1["total_disbursed"]), Decimal("0.00"))
        # no donor told anything yet (only reserved)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Notification).where(
                Notification.type == "donation_utilized")), 0)
        # partial transfer 250,000: paid oldest source first (a 200k, then b 50k)
        t1 = self.transfer(rid, "250000.00")
        self.assertEqual(t1.status_code, 201, t1.text)
        self.assertIsNone(t1.json()["source_batch_id"])
        self.assertEqual(t1.json()["ngo_allocation_document_no"], alloc["document_no"])
        alloc = self.client.get(f"/api/ngo-requests/{rid}", headers=self.admin).json()["allocation"]
        self.assertEqual(alloc["status"], "partially_disbursed")
        by_doc = {s["donor_donation_id"]: s for s in alloc["funding_sources"]}
        self.assertEqual(by_doc[a]["disbursed_amount"], "200000.00")
        self.assertEqual(by_doc[b]["disbursed_amount"], "50000.00")
        self.assertEqual(by_doc[c]["disbursed_amount"], "0.00")
        # donor 2 is told about exactly 50,000 and sees only their own share
        notes2 = self.client.get("/api/notifications", headers=self.donor2).json()["items"]
        util = [n for n in notes2 if n["type"] == "donation_utilized"]
        self.assertEqual(len(util), 1)
        self.assertIn("PKR 50,000.00", util[0]["message"])
        w2 = self.client.get("/api/donor/reports/utilization", headers=self.donor2).json()["items"]
        row = next(u for u in w2 if u["allocation_document_no"] == alloc["document_no"])
        self.assertEqual(row["allocated_amount"], "150000.00")
        self.assertEqual(row["disbursed_amount"], "50000.00")
        self.assertEqual(row["ngo_name"], "Demo Relief NGO")
        self.assertEqual(row["receipt_status"], "pending")
        self.assertNotIn("Demo Donor", str(w2))  # never another donor's identity
        # cannot transfer more than remains reserved (250,000) ...
        self.assertEqual(self.transfer(rid, "250000.01").json()["code"], "EXCEEDS_APPROVED_AMOUNT")
        # a failed transfer returns its money to reserved
        t2 = self.transfer(rid, "100000.00").json()
        failed = self.client.post(f"/api/disbursements/{t2['id']}/status", json={"status": "rejected", "reason": "Bank"},
                                  headers=self.admin)
        self.assertEqual(failed.json()["status"], "rejected")
        alloc = self.client.get(f"/api/ngo-requests/{rid}", headers=self.admin).json()["allocation"]
        self.assertEqual(alloc["disbursed_amount"], "250000.00")
        self.assertEqual(alloc["reserved_amount"], "250000.00")
        # NGO confirms the first transfer -> donor sees receipt confirmed
        self.client.post(f"/api/ngo/allocations/{t1.json()['id']}/acknowledge", headers=self.ngo)
        w1 = self.client.get("/api/donor/reports/utilization", headers=self.donor).json()["items"]
        row_a = next(u for u in w1 if u["contribution_document_no"] and u["allocation_document_no"] == alloc["document_no"]
                     and u["disbursed_amount"] == "200000.00")
        self.assertEqual(row_a["receipt_status"], "confirmed")
        # NGO never sees donor details
        ngo_view = self.client.get(f"/api/ngo/requests/{rid}", headers=self.ngo).json()
        self.assertNotIn("allocation", ngo_view)
        self.assertNotIn("Second Donor", str(ngo_view))
        self.assertEqual(ngo_view["transfers"][0]["allocation_document_no"], alloc["document_no"])
        # cancelling releases the undisbursed reservation (250,000) back to the contributions
        cancel = self.client.post(f"/api/ngo-requests/{rid}/allocation/cancel", json={"reason": "Scope reduced"},
                                  headers=self.admin)
        self.assertEqual(cancel.status_code, 200, cancel.text)
        self.assertEqual(cancel.json()["released_amount"], "250000.00")
        eligible = self.client.get("/api/ngo-requests/eligible", params={"ngo_id": self.ngo_id}, headers=self.admin).json()
        self.assertNotIn(rid, [e["id"] for e in eligible["items"]])  # nothing left to send once cancelled
        self.assertEqual(self.available(b), Decimal("100000.00"))
        self.assertEqual(self.available(c), Decimal("300000.00"))
        self.reconcile()

    def test_one_contribution_split_across_two_ngo_allocations(self):
        a = self.contribution("10000.00")
        r1, r2 = self.ngo_request("6000.00"), self.ngo_request("6000.00")
        self.assertEqual(self.approve(r1, "6000.00", [(a, "6000.00")]).status_code, 200)
        self.assertEqual(self.approve(r2, "6000.00", [(a, "6000.00")]).json()["code"], "INSUFFICIENT_SOURCE_FUNDS")
        self.assertEqual(self.approve(r2, "4000.00", [(a, "4000.00")]).status_code, 200)
        self.assertEqual(self.available(a), Decimal("0.00"))
        # direct (non-allocation) sends also respect reservations
        direct = self.client.post("/api/disbursements", headers=self.admin, json={
            "source_batch_id": a, "recipient_type": "ngo", "recipient_id": self.ngo_id, "amount": "1.00"})
        self.assertEqual(direct.json()["code"], "INSUFFICIENT_FUNDS")
        self.transfer(r1, "6000.00")
        self.transfer(r2, "4000.00")
        util = self.client.get("/api/donor/reports/utilization", headers=self.donor).json()["items"]
        mine = [u for u in util if u["contribution_document_no"] and u["disbursed_amount"] in ("6000.00", "4000.00")
                and u["allocated_amount"] in ("6000.00", "4000.00")]
        self.assertGreaterEqual(len(mine), 2)  # each allocation listed separately
        self.reconcile()

    def test_notifications_once_and_pdf_section(self):
        a = self.contribution("3000.00")
        rid = self.ngo_request("3000.00")
        self.approve(rid, "3000.00", [(a, "3000.00")])
        self.transfer(rid, "1000.00")
        self.transfer(rid, "2000.00")
        with self.Session() as db:
            splits = db.scalars(select(FundingDisbursement).join(AllocationFundingSource).where(
                AllocationFundingSource.donor_donation_id == a)).all()
            self.assertEqual(len(splits), 2)
            self.assertTrue(all(s.donor_notified_at for s in splits))
            self.assertEqual(db.scalar(select(func.count()).select_from(Notification).where(
                Notification.type == "donation_utilized", Notification.related_id == a)), 2)
            events = {e.event_type for e in db.scalars(select(FundLedgerEvent).where(FundLedgerEvent.donor_donation_id == a))}
            self.assertTrue({"reserved", "disbursed"} <= events)
        with self.Session() as db:
            doc = db.get(DonorDonation, a).document_no
        batch = self.client.get(f"/api/donor/reports/batches/{doc}", headers=self.donor).json()
        self.assertEqual(batch["summary"]["total_disbursed"], "3000.00")
        self.assertEqual(batch["utilization"][0]["disbursed_amount"], "3000.00")
        pdf = self.client.get(f"/api/donor/reports/batches/{doc}/report.pdf", headers=self.donor)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def reconcile(self):
        """Sum of each source's recorded disbursement equals its active transfer splits, and no contribution
        has more money disbursed or reserved than its verified amount."""
        with self.Session() as db:
            for src in db.scalars(select(AllocationFundingSource)):
                splits = db.scalar(select(func.coalesce(func.sum(FundingDisbursement.amount), 0)).where(
                    FundingDisbursement.funding_source_id == src.id, FundingDisbursement.status == "active"))
                self.assertEqual(Decimal(splits), Decimal(src.disbursed_amount))
                self.assertLessEqual(Decimal(src.disbursed_amount) + Decimal(src.released_amount),
                                     Decimal(src.allocated_amount))
            from controller.funding import committed_amounts

            donations = db.scalars(select(DonorDonation).where(DonorDonation.payment_status == "verified")).all()
            used = committed_amounts(db, [d.id for d in donations])
            for d in donations:
                self.assertLessEqual(used[d.id], Decimal(d.amount))


if __name__ == "__main__":
    unittest.main()

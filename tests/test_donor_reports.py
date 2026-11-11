"""Individual donor reports: daily / monthly figures, batch aggregation, "Not available yet" spending,
PDFs and strict per-donor authorization."""
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from pwdlib import PasswordHash
from sqlalchemy import func, select

from model.donation_model import Ngo
from model.fund_model import DonationDisbursement, DonorDonation, NgoExpenditure
from model.role_model import Role
from model.user_model import User
from tests.test_donations import DonationFixture

TODAY_PKT = (datetime.utcnow() + timedelta(hours=5)).date()


class DonorReportTests(DonationFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.Session() as db:
            role = db.scalar(select(Role.id).where(Role.name == "DONOR"))
            db.add(User(name="Other Donor", email="other@donor.pk", role_id=role, email_verified_at=func.now(),
                        password=PasswordHash.recommended().hash("other-donor-pass")))
            ngo = Ngo(name="Report NGO")
            db.add(ngo)
            db.commit()
            cls.ngo_id = ngo.id
        cls.other = cls.login("other@donor.pk", "other-donor-pass")
        cls.build_scenario()

    @classmethod
    def build_scenario(cls):
        """Request R (10,000) answered by two contributions (6,000 verified, 1,000 pending),
        plus one direct contribution (2,000 verified, 500 allocated, 300 spent verified) and one rejected."""
        c = cls.client
        req = c.post("/api/donation-requests", json={"donor_id": cls.donor_id, "type": "donor",
                                                     "reason": "Report scenario", "amount": "10000.00"},
                     headers=cls.admin).json()
        cls.request_no = req["document_no"]
        c.post(f"/api/donation-requests/{req['id']}/accept", headers=cls.donor)

        def give(amount, request_id=None, verify=None):
            body = {"amount": amount, **({"request_id": request_id} if request_id else {})}
            d = c.post("/api/donor-donations", json=body, headers=cls.donor).json()
            if verify:
                c.post(f"/api/donor-donations/{d['id']}/verify", json={"action": verify, "reason": "x"}, headers=cls.admin)
            return d

        cls.a = give("6000.00", req["id"], "verify")
        cls.b = give("1000.00", req["id"])  # pending
        cls.direct = give("2000.00", None, "verify")
        cls.rejected = give("400.00", None, "reject")
        out = c.post("/api/disbursements", json={"source_batch_id": cls.direct["id"], "recipient_type": "ngo",
                                                 "recipient_id": cls.ngo_id, "amount": "500.00",
                                                 "remarks": "Water filters"}, headers=cls.admin).json()
        out2 = c.post("/api/disbursements", json={"source_batch_id": cls.a["id"], "recipient_type": "ngo",
                                                  "recipient_id": cls.ngo_id, "amount": "2500.00"}, headers=cls.admin).json()
        with cls.Session() as db:
            db.add(NgoExpenditure(disbursement_id=out["id"], reference="EXP-1", expense_date=TODAY_PKT,
                                  purpose="Filters purchased", amount=Decimal("300.00"),
                                  evidence_reference="INV-55", verification_status="verified"))
            db.add(NgoExpenditure(disbursement_id=out["id"], reference="EXP-2", expense_date=TODAY_PKT,
                                  purpose="Transport", amount=Decimal("50.00"), verification_status="pending"))
            db.commit()
        cls.out2 = out2

    def get(self, path, headers=None, **params):
        r = self.client.get(f"/api/donor/reports{path}", params=params, headers=headers or self.donor)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_summary_figures(self):
        s = self.get("/summary")
        # donated excludes rejected; verified only verified; allocated from both verified contributions
        self.assertEqual(s["total_donated"], "9000.00")
        self.assertEqual(s["total_verified"], "8000.00")
        self.assertEqual(s["total_allocated"], "3000.00")
        self.assertEqual(s["total_spent"], "300.00")  # only the verified expenditure
        self.assertEqual(s["remaining_balance"], "5000.00")
        self.assertEqual(s["batches_count"], 3)  # the request + two direct contributions
        self.assertEqual(s["donor"], "Demo Donor")

    def test_daily_report(self):
        r = self.get("/daily", date=TODAY_PKT.isoformat())
        self.assertEqual(len(r["items"]), 4)
        row = next(i for i in r["items"] if i["document_no"] == self.a["document_no"])
        self.assertEqual(row["batch_no"], self.request_no)
        self.assertEqual(row["allocated_amount"], "2500.00")
        self.assertIsNone(row["spent_amount"])  # allocation with no expenditure: not available, not zero
        self.assertEqual(row["remaining_balance"], "3500.00")
        pending = next(i for i in r["items"] if i["document_no"] == self.b["document_no"])
        self.assertEqual(pending["remaining_balance"], "0.00")
        empty = self.get("/daily", date=(TODAY_PKT - timedelta(days=400)).isoformat())
        self.assertEqual(empty["items"], [])
        self.assertIsNone(empty["summary"]["total_spent"])

    def test_monthly_report_and_batch_table(self):
        r = self.get("/monthly", year=TODAY_PKT.year, month=TODAY_PKT.month)
        self.assertEqual(r["summary"]["transactions_count"], 4)
        self.assertEqual(r["summary"]["batches_count"], 3)
        self.assertEqual(r["daily"][0]["date"], TODAY_PKT.isoformat())
        self.assertEqual(r["daily"][0]["amount"], "9000.00")
        batch = next(b for b in r["batches"] if b["batch_no"] == self.request_no)
        self.assertEqual(batch["contributions"], 2)
        self.assertEqual(batch["amount"], "7000.00")
        self.assertEqual(batch["verified"], "6000.00")
        self.assertIsNone(batch["spent"])
        self.assertEqual(self.client.get("/api/donor/reports/monthly", params={"year": 2026, "month": 13},
                                         headers=self.donor).status_code, 422)

    def test_batch_report(self):
        b = self.get(f"/batches/{self.direct['document_no']}")
        self.assertEqual(b["summary"]["total_allocated"], "500.00")
        self.assertEqual(b["summary"]["total_spent"], "300.00")
        self.assertEqual(b["summary"]["unspent_allocated"], "200.00")
        self.assertEqual(b["allocations"][0]["ngo_name"], "Report NGO")
        self.assertEqual(b["allocations"][0]["purpose"], "Water filters")
        self.assertEqual({e["reference"] for e in b["expenditures"]}, {"EXP-1", "EXP-2"})
        req = self.get(f"/batches/{self.request_no}")
        self.assertEqual(len(req["donations"]), 2)
        self.assertEqual(req["request"]["requested_amount"], "10000.00")
        self.assertFalse(req["expenditure_available"])
        self.assertIsNone(req["summary"]["total_spent"])
        listing = self.get("/batches")
        self.assertEqual(listing["total"], 3)

    def test_pdfs(self):
        for path, params in (("/daily.pdf", {"date": TODAY_PKT.isoformat()}),
                             ("/monthly.pdf", {"year": TODAY_PKT.year, "month": TODAY_PKT.month}),
                             (f"/batches/{self.request_no}/report.pdf", {})):
            r = self.client.get(f"/api/donor/reports{path}", params=params, headers=self.donor)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.headers["content-type"], "application/pdf")
            self.assertIn("attachment", r.headers["content-disposition"])
            self.assertEqual(r.headers["cache-control"], "no-store")
            self.assertTrue(r.content.startswith(b"%PDF"))
            self.assertGreater(len(r.content), 1500)

    def test_privacy(self):
        # another donor sees none of this donor's records, and cannot open their batches or PDFs
        s = self.get("/summary", headers=self.other)
        self.assertEqual(s["total_donated"], "0.00")
        self.assertEqual(s["donor"], "Other Donor")
        for path in (f"/batches/{self.request_no}", f"/batches/{self.direct['document_no']}",
                     f"/batches/{self.request_no}/report.pdf"):
            self.assertEqual(self.client.get(f"/api/donor/reports{path}", headers=self.other).status_code, 404)
        self.assertEqual(self.get("/daily", headers=self.other, date=TODAY_PKT.isoformat())["items"], [])
        # PDMA staff and other roles are not part of this module
        self.assertEqual(self.client.get("/api/donor/reports/summary", headers=self.admin).status_code, 403)
        self.assertEqual(self.client.get("/api/donor/reports/summary", headers=self.dc).status_code, 403)
        self.assertEqual(self.client.get("/api/donor/reports/summary").status_code, 401)

    def test_report_references_are_unique(self):
        refs = set()
        with self.Session() as db:
            before = db.scalar(select(func.count()).select_from(DonorDonation))
        for _ in range(2):
            self.client.get("/api/donor/reports/daily.pdf", params={"date": TODAY_PKT.isoformat()}, headers=self.donor)
        from model.donation_model import DocumentSequence
        with self.Session() as db:
            seq = db.scalar(select(DocumentSequence).where(DocumentSequence.prefix == "RPT"))
            self.assertGreaterEqual(seq.last_no, 2)
            # generating reports never changes financial records
            self.assertEqual(db.scalar(select(func.count()).select_from(DonorDonation)), before)
            self.assertEqual(db.scalar(select(func.count()).select_from(DonationDisbursement)), 2)
        self.assertEqual(len(refs), 0)


if __name__ == "__main__":
    unittest.main()

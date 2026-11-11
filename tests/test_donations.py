"""Donor management API: requests, email, notifications (stored + live), donations, verification,
NGO allocations, balances and authorization. Runs on a disposable SQLite database; email is captured."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from config.auth_mail import AuthMailer, MailDeliveryError
from config.database import get_db
from migrations.seeders import seed_users
from model.donation_model import Donation, DonationRequest, DonorProfile
from model.notification_model import Notification
from model.role_model import Role
from model.user_model import User

ROOT = Path(__file__).resolve().parents[1]
ADMIN = ("admin@pdma.gov.pk", "admin-test-password")
DONOR = ("donor.admin@demo.com", "123456789012")
NGO = ("ngo.admin@demo.com", "210987654321")


class DonationFixture:
    """Shared disposable database, seeded users and captured email."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.engine = create_engine(f"sqlite:///{cls.tmp.name}/test.db")

        @event.listens_for(cls.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        config = Config(str(ROOT / "alembic.ini"))
        with cls.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "heads")
        with patch.dict(os.environ, {"SEED_DONOR_PASSWORD": DONOR[1], "SEED_NGO_PASSWORD": NGO[1],
                                     "APP_ENV": "development"}):
            seed_users(ADMIN[1], cls.engine, echo=lambda *_: None)
        cls.Session = sessionmaker(bind=cls.engine)

        from main import app

        def override_db():
            db = cls.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        cls.app = app
        cls.sent = []
        cls.mail_patch = patch.object(
            AuthMailer, "deliver", lambda self, to, subject, text, html: cls.sent.append((to, subject, text))
        )
        cls.mail_patch.start()
        cls.client = TestClient(app)
        cls.admin = cls.login(*ADMIN)
        cls.donor = cls.login(*DONOR)
        with cls.Session() as db:
            cls.donor_id = db.scalar(select(User.id).where(User.email == DONOR[0]))
            officer_role = db.scalar(select(Role.id).where(Role.name == "DC"))
            from pwdlib import PasswordHash
            from sqlalchemy import func

            db.add(User(name="DC User", email="dc@x.pk", password=PasswordHash.recommended().hash("dc-password-123"),
                        role_id=officer_role, email_verified_at=func.now()))
            db.commit()
        cls.dc = cls.login("dc@x.pk", "dc-password-123")

    @classmethod
    def tearDownClass(cls):
        cls.mail_patch.stop()
        cls.app.dependency_overrides.clear()
        cls.engine.dispose()
        cls.tmp.cleanup() if hasattr(cls.tmp, "cleanup") else None

    @classmethod
    def login(cls, email, password):
        from routes.auth_routes import _buckets
        _buckets.clear()  # each test class logs in several users; the IP burst limit is tested elsewhere
        r = cls.client.post("/api/auth/authenticate", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def send_request(self, amount="50000.00", reason="Flood relief for Dadu"):
        return self.client.post(
            "/api/donation-requests",
            json={"donor_id": self.donor_id, "type": "donor", "reason": reason, "amount": amount},
            headers=self.admin,
        )

    def new_ngo(self, name):
        r = self.client.post("/api/ngos", json={"name": name}, headers=self.admin)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]



class DonationApiTests(DonationFixture, unittest.TestCase):
    # 1 seeded donor login ------------------------------------------------------------------
    def test_01_seeded_donor_logs_in_with_profile(self):
        r = self.client.post("/api/auth/authenticate", json={"email": DONOR[0], "password": DONOR[1]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["role"], "DONOR")
        with self.Session() as db:
            self.assertIsNotNone(db.scalar(select(DonorProfile).where(DonorProfile.user_id == self.donor_id)))

    # 2 donor listing -----------------------------------------------------------------------
    def test_02_donor_list_search_filter_pagination(self):
        r = self.client.get("/api/donors", params={"search": "demo", "page_size": 5}, headers=self.admin)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["total"], 1)
        row = body["items"][0]
        self.assertEqual(row["email"], DONOR[0])
        self.assertEqual(row["organization"], "Demo Relief Foundation")
        self.assertEqual(row["status"], "ACTIVE")
        self.assertIsNotNone(row["registered_at"])
        self.assertEqual(self.client.get("/api/donors", params={"status": "inactive"}, headers=self.admin).json()["total"], 0)

    # 3-7 sending a request: record, document no, email, stored + live notification -----------
    def test_03_send_request_creates_record_email_and_notification(self):
        self.sent.clear()
        with patch("routes.donation_routes.push_notification") as push:
            r = self.send_request("75000.00", "School rebuilding in Buner")
        self.assertEqual(r.status_code, 201, r.text)
        req = r.json()
        self.assertRegex(req["document_no"], r"^DT-DONOR-\d{2}-\d{5}$")
        self.assertEqual(req["status"], "SENT")
        self.assertEqual(req["requested_amount"], "75000.00")
        self.assertEqual(req["total_received"], "0.00")
        self.assertIsNotNone(req["sent_at"])
        # email
        self.assertEqual(len(self.sent), 1)
        to, subject, text = self.sent[0]
        self.assertEqual(to, DONOR[0])
        self.assertIn(req["document_no"], subject)
        self.assertIn("PKR 75,000.00", text)
        self.assertIn(f"/donation-requests/{req['id']}", text)
        # live push scheduled with the stored notification
        push.assert_called_once()
        self.assertEqual(push.call_args.args[0], self.donor_id)
        self.assertEqual(push.call_args.args[1]["related_id"], req["id"])
        # stored notification (offline donors)
        n = self.client.get("/api/notifications", headers=self.donor).json()
        self.assertGreaterEqual(n["unread"], 1)
        item = next(i for i in n["items"] if i["related_id"] == req["id"])
        self.assertIn(req["document_no"], item["title"])
        # mark read
        r = self.client.post(f"/api/notifications/{item['id']}/read", headers=self.donor)
        self.assertTrue(r.json()["read"])

    def test_04_document_numbers_are_sequential_and_unique(self):
        a = self.send_request("1000.00", "Sequence check A").json()["document_no"]
        b = self.send_request("1000.00", "Sequence check B").json()["document_no"]
        self.assertEqual(int(b[-5:]), int(a[-5:]) + 1)

    def test_05_email_failure_creates_nothing(self):
        with self.Session() as db:
            before = db.query(DonationRequest).count()
            notes_before = db.query(Notification).count()

        def fail(*_a, **_k):
            raise MailDeliveryError("down")

        with patch.object(AuthMailer, "deliver", fail):
            r = self.send_request("2000.00", "Email failure check")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["code"], "EMAIL_FAILED")
        with self.Session() as db:
            self.assertEqual(db.query(DonationRequest).count(), before)
            self.assertEqual(db.query(Notification).count(), notes_before)

    def test_06_duplicate_submission_is_refused(self):
        self.assertEqual(self.send_request("3000.00", "Duplicate check").status_code, 201)
        r = self.send_request("3000.00", "Duplicate check")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "DUPLICATE_REQUEST")

    def test_07_validation(self):
        for bad in ({"amount": "0"}, {"amount": "-5"}, {"amount": "12.345"}, {"reason": "  "}):
            body = {"donor_id": self.donor_id, "type": "donor", "reason": "Valid reason", "amount": "100.00", **bad}
            self.assertEqual(self.client.post("/api/donation-requests", json=body, headers=self.admin).status_code, 422)
        r = self.client.post(
            "/api/donation-requests",
            json={"donor_id": 999999, "type": "donor", "reason": "Nobody", "amount": "100.00"},
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 404)

    # 8-11 donations, verification, allocations, balances ---------------------------------------
    def test_08_partial_donations_verification_allocations_and_balances(self):
        batch = self.send_request("100000.00", "Full money flow").json()
        bid = batch["id"]
        # partial donation, pending: not counted
        d1 = self.client.post(
            f"/api/donation-requests/{bid}/donations",
            json={"amount": "40000.00", "payment_reference": "TXN-1", "payment_method": "BANK_TRANSFER",
                  "received_at": "2026-11-10T10:00:00"},
            headers=self.admin,
        )
        self.assertEqual(d1.status_code, 201, d1.text)
        detail = self.client.get(f"/api/donation-requests/{bid}", headers=self.admin).json()
        self.assertEqual(detail["total_received"], "0.00")
        self.assertEqual(detail["pending_verification"], "40000.00")
        # no allocation possible before verification
        ngo_a, ngo_b = self.new_ngo("Relief NGO A"), self.new_ngo("Relief NGO B")
        r = self.client.post(f"/api/donation-requests/{bid}/allocations",
                             json={"ngo_id": ngo_a, "amount": "1.00", "allocation_date": "2026-11-11"}, headers=self.admin)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "OVER_ALLOCATION")
        # verify -> counted, status partially funded, donor notified
        with patch("routes.donation_routes.push_notification") as push:
            v = self.client.post(f"/api/donations/{d1.json()['id']}/verify", json={"action": "verify"}, headers=self.admin)
        self.assertEqual(v.status_code, 200, v.text)
        push.assert_called_once()
        self.assertEqual(self.client.post(f"/api/donations/{d1.json()['id']}/verify", json={"action": "verify"},
                                          headers=self.admin).status_code, 409)
        # second donation rejected: never counted
        d2 = self.client.post(
            f"/api/donation-requests/{bid}/donations",
            json={"amount": "10000.00", "payment_reference": "TXN-2", "payment_method": "CHEQUE",
                  "received_at": "2026-11-10T11:00:00"},
            headers=self.admin,
        ).json()
        self.client.post(f"/api/donations/{d2['id']}/verify", json={"action": "reject"}, headers=self.admin)
        # duplicate payment reference refused
        dup = self.client.post(
            f"/api/donation-requests/{bid}/donations",
            json={"amount": "5.00", "payment_reference": "TXN-1", "payment_method": "CASH",
                  "received_at": "2026-11-10T11:00:00"},
            headers=self.admin,
        )
        self.assertEqual(dup.status_code, 409)
        detail = self.client.get(f"/api/donation-requests/{bid}", headers=self.admin).json()
        self.assertEqual(detail["status"], "PARTIALLY_FUNDED")
        self.assertEqual(detail["total_received"], "40000.00")
        self.assertEqual(detail["outstanding_amount"], "60000.00")
        # two NGO allocations, then over-allocation refused
        for ngo, amt in ((ngo_a, "25000.00"), (ngo_b, "10000.00")):
            r = self.client.post(f"/api/donation-requests/{bid}/allocations",
                                 json={"ngo_id": ngo, "amount": amt, "allocation_date": "2026-11-11"}, headers=self.admin)
            self.assertEqual(r.status_code, 201, r.text)
        over = self.client.post(f"/api/donation-requests/{bid}/allocations",
                                json={"ngo_id": ngo_a, "amount": "5000.01", "allocation_date": "2026-11-11"},
                                headers=self.admin)
        self.assertEqual(over.status_code, 409)
        detail = self.client.get(f"/api/donation-requests/{bid}", headers=self.admin).json()
        self.assertEqual(detail["total_allocated"], "35000.00")
        self.assertEqual(detail["remaining_balance"], "5000.00")
        self.assertEqual(len(detail["allocations"]), 2)
        # cancelling an allocation returns its amount to the balance; history is kept
        alloc_id = detail["allocations"][1]["id"]
        self.assertEqual(self.client.post(f"/api/fund-allocations/{alloc_id}/cancel", headers=self.admin).status_code, 200)
        detail = self.client.get(f"/api/donation-requests/{bid}", headers=self.admin).json()
        self.assertEqual(detail["remaining_balance"], "15000.00")
        self.assertEqual(len(detail["allocations"]), 2)
        # donor sees their own batch with the same numbers
        mine = self.client.get(f"/api/donation-requests/{bid}", headers=self.donor).json()
        self.assertEqual(mine["remaining_balance"], "15000.00")
        summary = self.client.get("/api/donation-requests/summary", headers=self.donor).json()
        self.assertGreaterEqual(int(summary["requests_count"]), 1)

    # 12 authorization ----------------------------------------------------------------------------
    def test_09_authorization(self):
        batch = self.send_request("5000.00", "Authorization check").json()
        # donor cannot list donors, send requests, record or allocate
        self.assertEqual(self.client.get("/api/donors", headers=self.donor).status_code, 403)
        r = self.client.post("/api/donation-requests",
                             json={"donor_id": self.donor_id, "reason": "Self", "amount": "1.00"}, headers=self.donor)
        self.assertEqual(r.status_code, 403)
        r = self.client.post(f"/api/donation-requests/{batch['id']}/donations",
                             json={"amount": "1.00", "payment_reference": "X", "payment_method": "CASH",
                                   "received_at": "2026-11-10T10:00:00"}, headers=self.donor)
        self.assertEqual(r.status_code, 403)
        # a DC has no access to donation records at all
        self.assertEqual(self.client.get("/api/donation-requests", headers=self.dc).status_code, 403)
        self.assertEqual(self.client.get(f"/api/donation-requests/{batch['id']}", headers=self.dc).status_code, 403)
        # no token
        self.assertEqual(self.client.get("/api/donors").status_code, 401)
        # a donor cannot read another donor's batch
        with self.Session() as db:
            other = DonationRequest(document_no="DT-DONOR-99-99999", type="donor", donor_id=None, reason="Other",
                                    requested_amount=10, status="SENT", created_by=1)
            db.add(other)
            db.commit()
            other_id = other.id
        self.assertEqual(self.client.get(f"/api/donation-requests/{other_id}", headers=self.donor).status_code, 404)
        ids = [i["id"] for i in self.client.get("/api/donation-requests", headers=self.donor).json()["items"]]
        self.assertNotIn(other_id, ids)

    # WebSocket -------------------------------------------------------------------------------
    def test_10_websocket_requires_a_valid_token_and_delivers_to_that_donor(self):
        from starlette.websockets import WebSocketDisconnect

        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/api/ws/notifications?token=bad") as ws:
                ws.receive_json()
        token = self.donor["Authorization"].split()[1]
        with self.client.websocket_connect(f"/api/ws/notifications?token={token}") as ws:
            self.assertEqual(ws.receive_json()["event"], "connected")
            r = self.send_request("8000.00", "Live push check")
            self.assertEqual(r.status_code, 201)
            msg = ws.receive_json()
            self.assertEqual(msg["event"], "notification")
            self.assertEqual(msg["notification"]["related_id"], r.json()["id"])

    def test_11_donation_rows_record_who_and_when(self):
        with self.Session() as db:
            d = db.scalars(select(Donation).where(Donation.verification_status == "VERIFIED")).first()
            if d:
                self.assertIsNotNone(d.verified_by)
                self.assertIsNotNone(d.verified_at)
                self.assertIsNotNone(d.recorded_by)


class DonorResponseTests(DonationFixture, unittest.TestCase):
    """The donor accepts (sending an amount, capped by what is uncovered) or rejects a request."""

    def respond(self, request_id, **body):
        return self.client.post(f"/api/donation-requests/{request_id}/respond", json=body, headers=self.donor)

    def test_20_accept_sends_amount_capped_by_request_and_notifies_pdma(self):
        req = self.send_request("10000.00", "Response accept check").json()
        self.assertEqual(req["donor_response"], "PENDING")
        self.assertEqual(req["available_to_send"], "10000.00")
        # more than requested is refused
        over = self.respond(req["id"], action="accept", amount="10000.01")
        self.assertEqual(over.status_code, 409)
        self.assertEqual(over.json()["code"], "AMOUNT_EXCEEDS_REQUEST")
        # partial accept: pending donation, response ACCEPTED, available reduced
        with patch("routes.donation_routes.push_notification") as push:
            r = self.respond(req["id"], action="accept", amount="6000.00", payment_method="ONLINE")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["donor_response"], "ACCEPTED")
        self.assertEqual(body["pending_verification"], "6000.00")
        self.assertEqual(body["total_received"], "0.00")  # not counted until PDMA verifies
        self.assertEqual(body["available_to_send"], "4000.00")
        self.assertEqual(body["donation"]["verification_status"], "PENDING")
        push.assert_called_once()  # the PDMA sender is told live
        staff_notes = self.client.get("/api/notifications", headers=self.admin).json()["items"]
        self.assertTrue(any(n["type"] == "donation_response" and n["related_id"] == req["id"] for n in staff_notes))
        # the rest is still capped
        self.assertEqual(self.respond(req["id"], action="accept", amount="4000.01").status_code, 409)
        self.assertEqual(self.respond(req["id"], action="accept", amount="4000.00").status_code, 200)
        covered = self.respond(req["id"], action="accept", amount="1.00")
        self.assertEqual(covered.json()["code"], "REQUEST_COVERED")
        # once accepted it cannot be rejected
        self.assertEqual(self.respond(req["id"], action="reject", note="Changed mind").json()["code"], "ALREADY_ACCEPTED")
        # PDMA verifies the donor's donation: it now counts as received
        detail = self.client.get(f"/api/donation-requests/{req['id']}", headers=self.admin).json()
        first = detail["donations"][0]
        self.client.post(f"/api/donations/{first['id']}/verify", json={"action": "verify"}, headers=self.admin)
        detail = self.client.get(f"/api/donation-requests/{req['id']}", headers=self.admin).json()
        self.assertEqual(detail["total_received"], "6000.00")

    def test_21_reject_records_reason_and_blocks_sending(self):
        req = self.send_request("5000.00", "Response reject check").json()
        r = self.respond(req["id"], action="reject", note="Budget already committed this quarter")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["donor_response"], "REJECTED")
        self.assertEqual(r.json()["response_note"], "Budget already committed this quarter")
        self.assertEqual(self.respond(req["id"], action="accept", amount="10.00").json()["code"], "ALREADY_REJECTED")
        rejected = self.client.get("/api/donation-requests", params={"response": "REJECTED"}, headers=self.donor).json()
        self.assertIn(req["id"], [i["id"] for i in rejected["items"]])

    def test_22_only_the_addressed_donor_may_respond(self):
        req = self.send_request("700.00", "Response auth check").json()
        r = self.client.post(f"/api/donation-requests/{req['id']}/respond", json={"action": "reject"}, headers=self.admin)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.respond(req["id"], action="accept").status_code, 422)  # amount required


if __name__ == "__main__":
    unittest.main()

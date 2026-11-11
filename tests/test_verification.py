"""Disposable DB/files. Test doubles cover orchestration; real OCR is tested separately."""

from datetime import timedelta
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.document_classifier import classify
from agents.document_preprocessor import (
    DocumentPreprocessor,
)
from agents.document_validator import validate_content, validate_mrz
from agents.document_verification_agent import (
    DocumentVerificationAgent,
    identity_matches,
)
from config.database import Base, get_db
from config.jwt_handler import JwtHandler
from controller.authentication import now_utc
from main import app
from middlewares.auth_middleware import auth_middleware
from model.document_model import Document
from model.registration_model import PROFILE_MODELS
from model.role_model import Role
from model.user_model import User
from model.verification_model import VerificationProcess
from routes.auth_routes import _buckets


def image_bytes(fmt="PNG", text=None):
    image = Image.new("RGB", (1400, 1000), "white")
    if text:
        font_path = Path("C:/Windows/Fonts/arial.ttf")
        font = (
            ImageFont.truetype(str(font_path), 38)
            if font_path.is_file()
            else ImageFont.load_default(size=38)
        )
        ImageDraw.Draw(image).multiline_text(
            (70, 80), text, font=font, fill="black", spacing=24
        )
    output = io.BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


def registration_payload(kind="donor"):
    return {
        "user_type": kind,
        "full_name": "Synthetic Test",
        "email": f"{kind}@example.org",
        "password": "registration-password",
        "confirm_password": "registration-password",
        "phone": "+923001234567",
        "identity_type": "cnic",
        "identity_number": "1234512345671",
        "country": "PK",
        "state": "Sindh",
        "city": "Karachi",
        "postal_code": "74000",
        "address1": "12 Main Street",
        "consent": True,
        **{
            "donor": {"donor_type": "national"},
            "ngo": {
                "organization_name": "Test NGO",
                "registration_number": "TEST123",
                "designation": "Director",
            },
            "householder": {"household_size": 3, "dependents": 1},
            "shopkeeper": {
                "shop_name": "Test Shop",
                "business_number": "TAX123",
                "business_category": "Food",
            },
        }[kind],
    }


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "JWT_SECRET_KEY": "test-verification-secret-longer-than-32-characters",
                "DOCUMENT_STORAGE_DIR": self.temp.name,
                "DOCUMENT_AUTO_APPROVE": "false",
            },
        )
        self.env.start()
        self.jwt_patch = patch.object(auth_middleware, "jwt_handler", JwtHandler())
        self.jwt_patch.start()
        _buckets.clear()
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add_all([Role(id=0, name="UNASSIGNED"), Role(id=4, name="ADMIN")])
        self.db.commit()
        app.dependency_overrides[get_db] = lambda: self.db
        self.mail = patch(
            "controller.authentication.Authentication.queue_code",
            return_value={"message": "Queued"},
        )
        self.mail.start()
        self.notify = patch("config.auth_mail.AuthMailer.send")
        self.notify.start()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.notify.stop()
        self.mail.stop()
        self.jwt_patch.stop()
        self.db.close()
        self.engine.dispose()
        self.env.stop()
        self.temp.cleanup()

    def upload(
        self,
        kind="donor",
        document_type="cnic",
        content=None,
        media="image/png",
        process=None,
    ):
        data = {"user_type": kind, "document_type": document_type}
        headers = {}
        if process:
            data["process_id"] = process["id"]
            headers = self.headers(process)
        return self.client.post(
            "/api/documents/upload",
            data=data,
            headers=headers,
            files={
                "file": (
                    "test.png" if media != "application/pdf" else "test.pdf",
                    content if content is not None else image_bytes(),
                    media,
                )
            },
        )

    def headers(self, process):
        return {"X-Verification-Token": process["verification_token"]}

    def status(self, process):
        return self.client.get(
            f"/api/documents/{process['id']}/verification-status",
            headers=self.headers(process),
        )

    def start(self, process):
        return self.client.post(
            "/api/documents/verify",
            json={"document_id": process["id"]},
            headers=self.headers(process),
        )

    def fixture_pass(self, process):
        # A server-side fixture for registration transaction tests, not production AI.
        row = self.db.get(VerificationProcess, process["id"])
        self.db.refresh(row)
        row.verification_attempts = 1
        row.stage = "completed"
        row.outcome = "checks_passed"
        row.completed_stages = [
            "scanning",
            "enhancing",
            "extracting",
            "validating",
            "completing",
        ]
        row.verification_result = {
            "automated_checks_passed": True,
            "authenticity_verified": False,
        }
        self.db.commit()

    def register(self, process, kind="donor", **overrides):
        return self.client.post(
            "/api/auth/register",
            json={
                **registration_payload(kind),
                "verification_id": process["id"],
                "verification_token": process["verification_token"],
                **overrides,
            },
        )

    def user_headers(self, user):
        user.email_verified_at = now_utc()
        self.db.commit()
        return {"Authorization": "Bearer " + JwtHandler().create_token(user.id)}

    def test_all_four_profiles_create_one_pending_document_and_consume_token(self):
        for kind, model in PROFILE_MODELS.items():
            with self.subTest(kind=kind):
                process = self.upload(kind).json()
                self.fixture_pass(process)
                extra = {}
                if kind == "ngo":
                    certificate = self.upload(kind, document_type="ngo_registration").json()
                    self.fixture_pass(certificate)
                    extra = {"ngo_verification_id": certificate["id"], "ngo_verification_token": certificate["verification_token"]}
                response = self.register(process, kind, **extra)
                self.assertEqual(response.status_code, 202, response.text)
                body = response.json()
                self.assertEqual(body["registration_status"], "under_review")
                self.assertEqual(len(body["documents"]), 2 if kind == "ngo" else 1)
                doc = body["documents"][0]
                self.assertEqual(doc["status"], "Pending")
                self.assertIsNone(doc["approval_type"])
                self.assertEqual(doc["verification_attempts"], 1)
                user = self.db.get(User, body["user_id"])
                self.assertTrue(
                    PasswordHash.recommended().verify(
                        "registration-password", user.password
                    )
                )
                self.assertEqual(user.role_id, 0)
                self.assertIsNone(user.email_verified_at)
                self.assertIsNotNone(self.db.get(model, user.id))
                self.assertEqual(
                    self.register(
                        process, kind, email=f"again-{kind}@example.org", **extra
                    ).status_code,
                    409,
                )

    def test_registration_cannot_bypass_verification_or_manual_rules(self):
        process = self.upload().json()
        self.assertEqual(self.register(process).status_code, 409)
        self.assertEqual(
            self.client.post(
                f"/api/documents/{process['id']}/request-manual-review",
                headers=self.headers(process),
            ).status_code,
            409,
        )
        self.assertEqual(self.register(process, role_id=4).status_code, 422)
        self.assertEqual(
            self.register(process, verification_token="x" * 43).status_code, 404
        )
        self.assertEqual(self.db.scalar(select(func.count()).select_from(User)), 0)

    def test_three_real_unsuccessful_attempts_then_manual_registration(self):
        process = self.upload().json()
        for attempt in range(1, 4):
            self.assertEqual(self.start(process).status_code, 202)
            status = self.status(process).json()
            self.assertEqual(status["verification_attempts"], attempt)
            self.assertIn(status["outcome"], ("inconclusive", "error"))
            self.assertFalse(status["can_register"])
        self.assertEqual(self.start(process).status_code, 409)
        self.assertEqual(self.upload(process=process).status_code, 409)
        review = self.client.post(
            f"/api/documents/{process['id']}/request-manual-review",
            headers=self.headers(process),
        )
        self.assertEqual(review.status_code, 200)
        self.assertTrue(review.json()["can_register"])
        registered = self.register(process)
        self.assertEqual(registered.status_code, 202, registered.text)
        self.assertEqual(registered.json()["documents"][0]["verification_attempts"], 3)
        self.assertIsNotNone(registered.json()["documents"][0]["review_requested_at"])

    def test_replacing_file_preserves_attempt_count(self):
        process = self.upload().json()
        self.start(process)
        replacement = self.upload(process=process).json()
        self.assertEqual(replacement["id"], process["id"])
        self.assertEqual(replacement["verification_attempts"], 1)
        self.assertEqual(len(list(Path(self.temp.name).iterdir())), 1)

    def test_capability_ownership_expiry_and_preview(self):
        process = self.upload().json()
        url = f"/api/documents/{process['id']}"
        self.assertEqual(self.client.get(url + "/verification-status").status_code, 404)
        self.assertEqual(
            self.client.get(
                url + "/preview", headers={"X-Verification-Token": "wrong"}
            ).status_code,
            404,
        )
        preview = self.client.get(url + "/preview", headers=self.headers(process))
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "image/png")
        row = self.db.get(VerificationProcess, process["id"])
        row.expires_at = now_utc() - timedelta(seconds=1)
        self.db.commit()
        self.assertEqual(self.status(process).status_code, 410)

    def test_png_jpeg_pdf_and_malformed_or_active_files(self):
        self.assertEqual(
            self.upload(content=image_bytes("JPEG"), media="image/jpeg").status_code,
            201,
        )
        pdf = image_bytes("PDF")
        response = self.upload(content=pdf, media="application/pdf")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(
            self.client.get(
                f"/api/documents/{response.json()['id']}/preview",
                headers=self.headers(response.json()),
            ).headers["content-type"],
            "image/png",
        )
        for content, media in [
            (b"\x89PNG\r\n\x1a\nbroken", "image/png"),
            (b"<script>x</script>", "image/png"),
            (pdf + b"/JavaScript", "application/pdf"),
            (b"%PDF-broken", "application/pdf"),
        ]:
            self.assertEqual(self.upload(content=content, media=media).status_code, 422)
        self.assertEqual(
            self.upload(content=b"bad", media="image/svg+xml").status_code, 422
        )
        self.assertEqual(self.upload(document_type="ngo_registration").status_code, 422)

    def test_commit_rollback_preserves_unclaimed_document_for_retry(self):
        process = self.upload().json()
        self.fixture_pass(process)
        with patch.object(
            self.db, "commit", side_effect=RuntimeError("database failure")
        ):
            with self.assertRaises(RuntimeError):
                self.register(process)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(User)), 0)
        self.assertIsNone(self.db.get(VerificationProcess, process["id"]).consumed_at)
        self.assertTrue(self.status(process).json()["can_register"])

    def test_duplicate_email_and_identity_mismatch(self):
        process = self.upload().json()
        self.fixture_pass(process)
        self.assertEqual(self.register(process).status_code, 202)
        another = self.upload().json()
        self.fixture_pass(another)
        self.assertEqual(
            self.register(another, email="DONOR@EXAMPLE.ORG").status_code, 409
        )
        from agents.document_verification_agent import seal_details

        row = self.db.get(VerificationProcess, another["id"])
        row.extracted_data = seal_details({"identity_number": "9999999999999"})
        self.db.commit()
        self.assertEqual(
            self.register(another, email="different@example.org").status_code, 422
        )

    def test_admin_review_authorization_registration_status_and_notification(self):
        process = self.upload().json()
        self.fixture_pass(process)
        registered = self.register(process).json()
        owner = self.db.get(User, registered["user_id"])
        headers = self.user_headers(owner)
        doc_id = registered["documents"][0]["id"]
        url = f"/api/admin/documents/{doc_id}/review"
        self.assertEqual(
            self.client.patch(
                url, headers=headers, json={"status": "Approved"}
            ).status_code,
            403,
        )
        owner.role_id = 4
        self.db.commit()
        self.assertEqual(
            self.client.patch(
                url, headers=headers, json={"status": "Approved"}
            ).status_code,
            403,
        )
        admin = User(
            name="Admin", email="admin@example.org", password="unused", role_id=4
        )
        self.db.add(admin)
        self.db.commit()
        admin_headers = self.user_headers(admin)
        response = self.client.patch(
            url,
            headers=admin_headers,
            json={"status": "Approved", "reason": "Reviewed"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.db.refresh(owner)
        self.assertEqual(owner.registration_status, "approved")
        document = self.db.get(Document, doc_id)
        self.db.refresh(document)
        self.assertIsNotNone(document.review_notification_sent_at)
        self.assertEqual(document.approval_type, "manual")

    def test_approved_replacement_supersedes_a_rejected_version(self):
        process = self.upload().json()
        self.fixture_pass(process)
        registered = self.register(process).json()
        owner = self.db.get(User, registered["user_id"])
        owner_headers = self.user_headers(owner)
        admin = User(
            name="Admin", email="admin@example.org", password="unused", role_id=4
        )
        self.db.add(admin)
        self.db.commit()
        admin_headers = self.user_headers(admin)
        original_id = registered["documents"][0]["id"]
        rejected = self.client.patch(
            f"/api/admin/documents/{original_id}/review",
            headers=admin_headers,
            json={"status": "Rejected"},
        )
        self.assertEqual(rejected.status_code, 200)
        replaced = self.client.post(
            "/api/documents/upload",
            headers=owner_headers,
            data={"document_slot": "cnicFront"},
            files={"file": ("replacement.png", image_bytes(), "image/png")},
        )
        self.assertEqual(replaced.status_code, 201, replaced.text)
        self.db.refresh(owner)
        self.assertEqual(owner.registration_status, "under_review")
        approved = self.client.patch(
            f"/api/admin/documents/{replaced.json()['id']}/review",
            headers=admin_headers,
            json={"status": "Approved"},
        )
        self.assertEqual(approved.status_code, 200)
        self.db.refresh(owner)
        self.assertEqual(owner.registration_status, "approved")
        self.assertEqual(self.db.get(Document, original_id).status, "Rejected")

    def test_cors_and_configuration(self):
        response = self.client.options(
            "/api/documents/verify",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Verification-Token,Content-Type",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"], "http://localhost:5173"
        )
        self.assertEqual(
            self.client.get("/api/documents/config").json()["max_attempts"], 3
        )

    def test_interrupted_worker_is_recoverable_without_resetting_attempts(self):
        process = self.upload().json()
        row = self.db.get(VerificationProcess, process["id"])
        row.stage, row.verification_attempts = "extracting", 2
        self.db.commit()
        from controller.verification import recover_interrupted_jobs

        with patch("config.database.SessionLocal", sessionmaker(bind=self.engine)):
            recover_interrupted_jobs()
        status = self.status(process).json()
        self.assertEqual(status["stage"], "failed")
        self.assertEqual(status["verification_attempts"], 2)
        self.assertFalse(status["can_register"])


class RealAgentTests(unittest.TestCase):
    def test_real_ocr_content_checks_and_encryption_never_authenticate_a_synthetic_document(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "JWT_SECRET_KEY": "test-real-ocr-secret-at-least-32-characters",
                    "DOCUMENT_AUTO_APPROVE": "true",
                    "DOCUMENT_AUTHENTICITY_URL": "",
                },
            ),
        ):
            path = Path(directory) / "test.png"
            path.write_bytes(
                image_bytes(
                    text="SYNTHETIC TEST - NOT AN OFFICIAL DOCUMENT\nPAKISTAN NATIONAL IDENTITY CARD\nName: TEST PERSON\nFather Name: TEST PARENT\nCNIC: 12345-1234567-1\nDate of Birth: 01 JAN 1990\nThis is a software testing sample only."
                )
            )
            stages = []
            from agents.grok_extractor import Extraction
            extraction = Extraction(document_type="cnic", text="PAKISTAN NATIONAL IDENTITY CARD\nName: TEST PERSON\nFather Name: TEST PARENT\nCNIC: 12345-1234567-1\nDate of Birth: 01 JAN 1990", identity_number="1234512345671")
            with patch("agents.grok_extractor.extract", return_value=extraction):
                result = DocumentVerificationAgent().analyze(
                path,
                "image/png",
                "cnic",
                lambda stage, done: stages.append((stage, done)),
            )
            self.assertEqual(result["outcome"], "checks_passed", result)
            self.assertEqual(result["document_status"], "Pending")
            self.assertFalse(result["result"]["authenticity_verified"])
            self.assertNotIn("1234512345671", str(result))
            self.assertTrue(identity_matches(result["extracted_data"], "1234512345671"))
            self.assertFalse(
                identity_matches(result["extracted_data"], "9999999999999")
            )
            self.assertIn(("extracting", True), stages)

    def test_preprocessing_and_document_specific_rules(self):
        blank = np.full((600, 800, 3), 255, dtype=np.uint8)
        _, metrics, issues = DocumentPreprocessor().prepare(blank)
        self.assertTrue(issues)
        self.assertEqual(metrics["blur_score"], 0)
        text = "Utility electricity bill\nService Address: 12 Main Street Karachi Pakistan\nConsumer number 1234567"
        _, fields, issues = validate_content("home_address", text, classify(text))
        self.assertFalse(issues)
        self.assertIn("address", fields)
        text = "Registration Certificate\nOrganization Name: Test Relief Society\nRegistration Number: TEST-123\nRegistered under the society act"
        _, fields, issues = validate_content("ngo_registration", text, classify(text))
        self.assertFalse(issues)
        self.assertEqual(fields["registration_number"], "TEST-123")
        self.assertFalse(
            validate_mrz("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nINVALID")[
                "present"
            ]
        )
        valid = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"
        self.assertTrue(validate_mrz(valid)["valid"])
        self.assertFalse(validate_mrz(valid.replace("7408122", "7408123"))["valid"])


if __name__ == "__main__":
    unittest.main()

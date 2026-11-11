"""Migration lifecycle checks run exclusively against disposable SQLite databases."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from pwdlib import PasswordHash
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session

from config.database import Base
from migrations.seeders import seed_users
from model.role_model import Role
from model.user_model import User
from model.donation_model import DonorProfile
import model.notification_model  # noqa: F401  (register tables for the metadata comparison)
import model.fund_model  # noqa: F401  (includes ngo_expenditures)

ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        self.connection = self.engine.connect()
        self.config = Config(str(ROOT / "alembic.ini"))
        self.config.attributes["connection"] = self.connection

    def tearDown(self):
        self.connection.close()
        self.engine.dispose()

    def test_fresh_schema_defaults_and_model_match(self):
        command.upgrade(self.config, "head")
        self.connection.execute(text(
            "INSERT INTO users (name, email, password) VALUES ('New', 'new@example.test', 'hash')"
        ))
        user = self.connection.execute(text("SELECT * FROM users")).mappings().one()
        self.assertEqual(user["role_id"], 0)
        self.assertIsNone(user["image"])
        self.assertIsNone(user["district_id"])
        self.assertTrue(user["is_active"])
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM roles")), 5)
        self.assertEqual(inspect(self.connection).get_foreign_keys("users")[0]["referred_table"], "roles")
        self.assertEqual(compare_metadata(MigrationContext.configure(self.connection), Base.metadata), [])

    def test_existing_data_survives_upgrade_and_one_step_rollback(self):
        command.upgrade(self.config, "0001_create_users")
        self.connection.execute(text(
            "INSERT INTO users (id, name, email, password) VALUES (7, 'Existing', 'old@example.test', 'original-hash')"
        ))
        self.connection.commit()
        command.upgrade(self.config, "head")
        self.assertEqual(self.connection.scalar(text("SELECT role_id FROM users WHERE id=7")), 0)
        self.connection.commit()
        command.downgrade(self.config, "-1")  # 0007 NGO expenditures
        self.assertFalse(inspect(self.connection).has_table("ngo_expenditures"))
        command.downgrade(self.config, "-1")  # 0006 PDMA fund ledger
        for table in ("donor_donations", "donation_disbursements", "email_outbox"):
            self.assertFalse(inspect(self.connection).has_table(table))
        command.downgrade(self.config, "-1")  # 0005 donor response
        self.assertNotIn("donor_response", {c["name"] for c in inspect(self.connection).get_columns("donation_requests")})
        command.downgrade(self.config, "-1")  # 0004 donations
        for table in ("donation_requests", "donations", "fund_allocations", "notifications", "ngos"):
            self.assertFalse(inspect(self.connection).has_table(table))
        self.assertNotIn("created_at", {c["name"] for c in inspect(self.connection).get_columns("users")})
        self.assertEqual(self.connection.scalar(text("SELECT email FROM users WHERE id=7")), "old@example.test")
        command.downgrade(self.config, "-1")
        self.assertFalse(inspect(self.connection).has_table("auth_challenges"))
        self.assertNotIn("email_verified_at", {c["name"] for c in inspect(self.connection).get_columns("users")})
        command.downgrade(self.config, "-1")
        self.assertFalse(inspect(self.connection).has_table("roles"))
        row = self.connection.execute(text("SELECT * FROM users WHERE id=7")).mappings().one()
        self.assertEqual(dict(row), {"id": 7, "name": "Existing", "email": "old@example.test", "password": "original-hash"})

    def test_reset_and_reapply(self):
        command.upgrade(self.config, "head")
        command.downgrade(self.config, "base")
        self.assertFalse(inspect(self.connection).has_table("users"))
        command.upgrade(self.config, "head")
        self.assertTrue(inspect(self.connection).has_table("users"))

    def test_seed_creates_admin_and_demo_donor_once(self):
        command.upgrade(self.config, "head")
        self.connection.commit()
        env = {"SEED_ADMIN_EMAIL": "Admin@PDMA.gov.pk", "SEED_ADMIN_NAME": "PDMA Admin", "APP_ENV": "development"}
        shown = []
        with patch.dict(os.environ, env):
            os.environ.pop("SEED_DONOR_PASSWORD", None)
            self.assertEqual(seed_users("admin-test-password", self.connection, echo=shown.append), 2)
            self.assertEqual(seed_users("another-test-password", self.connection, echo=shown.append), 0)
        self.assertEqual(len(shown), 1)  # the donor password is shown once
        donor_password = shown[0].split(" / ")[1].split()[0]
        self.assertTrue(donor_password.isdigit() and len(donor_password) == 12, donor_password)
        with Session(self.connection) as session:
            users = {u.email: u for u in session.scalars(select(User)).all()}
            self.assertEqual(set(users), {"admin@pdma.gov.pk", "donor.admin@demo.com"})
            admin, donor = users["admin@pdma.gov.pk"], users["donor.admin@demo.com"]
            self.assertEqual(session.get(Role, admin.role_id).name, "ADMIN")
            self.assertEqual(session.get(Role, donor.role_id).name, "DONOR")
            self.assertIsNotNone(admin.email_verified_at)
            self.assertTrue(PasswordHash.recommended().verify("admin-test-password", admin.password))
            self.assertTrue(PasswordHash.recommended().verify(donor_password, donor.password))
            self.assertIsNotNone(session.scalar(select(DonorProfile).where(DonorProfile.user_id == donor.id)))

    def test_seed_skips_demo_donor_in_production(self):
        command.upgrade(self.config, "head")
        self.connection.commit()
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            self.assertEqual(seed_users("admin-test-password", self.connection, echo=lambda *_: None), 1)

    def test_seed_rejects_short_password(self):
        command.upgrade(self.config, "head")
        self.connection.commit()
        with self.assertRaises(ValueError):
            seed_users("short", self.connection)

    def test_legacy_baseline_preserves_table(self):
        from artisan import baseline
        # Represent an existing database without alembic_version.
        self.connection.execute(text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(50) NOT NULL, "
            "email VARCHAR(100) NOT NULL, password VARCHAR(255) NOT NULL, "
            "CONSTRAINT uq_users_email UNIQUE (email))"
        ))
        self.connection.execute(text(
            "INSERT INTO users VALUES (1, 'Legacy', 'legacy@example.test', 'legacy-hash')"
        ))
        self.connection.commit()
        with patch("config.database.engine", self.engine):
            baseline(self.config)
        self.config.attributes["connection"] = self.connection
        command.upgrade(self.config, "head")
        self.assertEqual(self.connection.scalar(text("SELECT password FROM users WHERE id=1")), "legacy-hash")


if __name__ == "__main__":
    unittest.main()

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

    def test_admin_seed_is_repeatable_and_password_is_hashed(self):
        command.upgrade(self.config, "head")
        self.connection.commit()
        with patch.dict(os.environ, {"SEED_ADMIN_EMAIL": "Admin@PDMA.gov.pk", "SEED_ADMIN_NAME": "PDMA Admin"}):
            self.assertEqual(seed_users("admin-test-password", self.connection), 1)
            self.assertEqual(seed_users("another-test-password", self.connection), 0)
        with Session(self.connection) as session:
            users = session.scalars(select(User)).all()
            self.assertEqual(len(users), 1)
            admin = users[0]
            self.assertEqual(admin.email, "admin@pdma.gov.pk")
            self.assertEqual(session.get(Role, admin.role_id).name, "ADMIN")
            self.assertIsNone(admin.district_id)
            self.assertIsNotNone(admin.email_verified_at)
            self.assertTrue(PasswordHash.recommended().verify("admin-test-password", admin.password))

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

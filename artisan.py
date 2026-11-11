"""Laravel-style commands backed by Alembic. Run with the project Python."""
import argparse
from getpass import getpass
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parent


def migration_config():
    return Config(str(ROOT / "alembic.ini"))


def baseline(config):
    from config.database import engine
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as connection:
        inspector = inspect(connection)
        if MigrationContext.configure(connection).get_current_heads():
            raise ValueError("Database already has migration history; use migrate instead.")
        if not inspector.has_table("users"):
            raise ValueError("No legacy users table found; use migrate instead.")
        columns = {column["name"] for column in inspector.get_columns("users")}
        if columns != {"id", "name", "email", "password"}:
            raise ValueError("Legacy users schema differs from baseline; reconcile it before stamping.")
        if inspector.get_pk_constraint("users")["constrained_columns"] != ["id"]:
            raise ValueError("Expected users.id primary key.")
        unique = inspector.get_unique_constraints("users") + inspector.get_indexes("users")
        if not any(item.get("column_names") == ["email"] and item.get("unique", True) for item in unique):
            raise ValueError("Expected unique users.email constraint.")
        config.attributes["connection"] = connection
        command.stamp(config, "0001_create_users")
        connection.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    migrate = commands.add_parser("migrate", help="Apply pending schema migrations")
    migrate.add_argument("--seed", action="store_true", help="Also create optional demo users")
    rollback = commands.add_parser("migrate:rollback", help="Undo the latest migration(s)")
    rollback.add_argument("--step", type=int, default=1)
    commands.add_parser("migrate:reset", help="Undo all migrations (drops migrated tables/data)")
    commands.add_parser("migrate:status", help="Show current revision and migration history")
    commands.add_parser("migrate:baseline", help="Adopt the existing four-column users table without recreating it")
    commands.add_parser("db:seed", help="Create optional demo users without overwriting existing accounts")
    make = commands.add_parser("make:migration", help="Create a migration with upgrade/downgrade functions")
    make.add_argument("name")
    make.add_argument("--autogenerate", action="store_true")
    args = parser.parse_args()
    config = migration_config()
    try:
        if args.action == "migrate":
            command.upgrade(config, "head")
            print("Migrations applied.")
        elif args.action == "migrate:rollback":
            if args.step < 1:
                parser.error("--step must be at least 1")
            command.downgrade(config, f"-{args.step}")
            print(f"Rolled back {args.step} migration(s).")
        elif args.action == "migrate:reset":
            command.downgrade(config, "base")
            print("All migrations rolled back.")
        elif args.action == "migrate:status":
            command.current(config, verbose=True)
            command.history(config, indicate_current=True)
        elif args.action == "migrate:baseline":
            baseline(config)
            print("Legacy users table adopted; now run migrate.")
        elif args.action == "make:migration":
            command.revision(config, message=args.name, autogenerate=args.autogenerate)

        if args.action == "db:seed" or (args.action == "migrate" and args.seed):
            from migrations.seeders import seed_users
            password = os.environ.get("SEED_USER_PASSWORD") or getpass("Demo users password (12+ characters): ")
            print(f"Created {seed_users(password)} demo user(s); existing accounts unchanged.")
    except Exception as exc:
        # Do not dump database connection URLs or passwords into CLI output.
        from sqlalchemy.exc import SQLAlchemyError
        if isinstance(exc, SQLAlchemyError):
            parser.exit(1, "Database operation failed. Check .env connectivity and migration status.\n")
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()

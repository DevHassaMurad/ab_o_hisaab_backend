from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from config.database import Base, Database_URL
from model.user_model import User
from model.role_model import Role

config = context.config
target_metadata = Base.metadata


def run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=Database_URL, target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run_migrations(config.attributes["connection"])
else:
    engine = create_engine(Database_URL, poolclass=NullPool)
    with engine.connect() as connection:
        run_migrations(connection)
    engine.dispose()

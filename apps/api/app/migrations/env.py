"""Alembic environment. Uses the app's engine/URL and the SQLAlchemy models as the target schema.

Create a migration after changing models:
    alembic -c app/migrations/alembic.ini revision --autogenerate -m "describe the change"
Apply (also happens automatically at API startup when DB_AUTO_MIGRATE=true):
    alembic -c app/migrations/alembic.ini upgrade head
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # called from the app with an open connection
        _run(connection)
        return
    engine = engine_from_config({"sqlalchemy.url": _url()}, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as conn:
        _run(conn)


def _run(connection) -> None:
    # render_as_batch: SQLite cannot ALTER most things in place; batch mode recreates the table safely
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

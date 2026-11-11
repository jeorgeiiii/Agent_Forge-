from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from .config import get_settings
from .models import Base


def _make_engine():
    settings = get_settings()
    url = settings.database_url
    pool = {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow, "pool_timeout": 10}
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 15},
            pool_pre_ping=True,
            **pool,
        )

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL lets readers proceed while a checkpoint write is in progress.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
        return engine

    # PostgreSQL / Supabase connection pooling configuration:
    # pool_recycle=300 keeps connections alive with Supavisor pooler
    # pool_pre_ping=True prevents stale connection crashes
    return create_engine(url, pool_recycle=300, pool_pre_ping=True, **pool)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _add_missing_columns() -> None:
    """Tiny forward-only migration: add columns that exist in the models but not in the DB.

    ``create_all`` only creates missing *tables*; this covers columns added to
    existing tables (e.g. graphs.version). New columns must be nullable or have
    a server_default. For anything more complex, adopt Alembic.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable and column.server_default is None:
                    raise RuntimeError(f"Cannot auto-add NOT NULL column {table.name}.{column.name} without a default.")
                ddl_type = column.type.compile(dialect=engine.dialect)
                default = f" DEFAULT {column.server_default.arg.text}" if column.server_default is not None else ""
                connection.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {ddl_type}{default}'))


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()

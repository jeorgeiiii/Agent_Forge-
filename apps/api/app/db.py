from pathlib import Path

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


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _alembic_config(connection):
    from alembic.config import Config

    config = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection  # env.py reuses this connection
    return config


def _adopt_legacy_columns(connection) -> None:
    """One-time bridge for databases created before Alembic (create_all + ad-hoc columns),
    so stamping them as 'head' is truthful:

    * an EMPTY table whose columns differ from the model is rebuilt from the model
      (also fixes stale unique constraints from early development);
    * a table WITH data only gets missing columns (nullable or with a default) and missing
      indexes; anything else stops with a clear error instead of guessing. Remaining
      SQLite-only differences (e.g. NOT NULL on an added column) are harmless and are
      fixed for good by the first real migration that rebuilds that table.
    """
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        rows = connection.execute(text(f'SELECT COUNT(*) FROM "{table.name}"')).scalar()
        if rows == 0:  # nothing to lose: rebuild exactly as the model says (columns, constraints, indexes)
            table.drop(connection)
            table.create(connection)
            continue
        present = {column["name"] for column in inspector.get_columns(table.name)}
        missing = [column for column in table.columns if column.name not in present]
        for column in missing:
            if not column.nullable and column.server_default is None:
                raise RuntimeError(
                    f"Cannot adopt {table.name}.{column.name}: NOT NULL without a default and the table has "
                    f"{rows} rows. Back up the database and migrate this table by hand.")
            ddl_type = column.type.compile(dialect=connection.dialect)
            default = f" DEFAULT {column.server_default.arg.text}" if column.server_default is not None else ""
            connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}{default}'))
        for index in table.indexes:  # indexes can always be added in place
            index.create(connection, checkfirst=True)


def initialize_database() -> str:
    """Bring the schema to the latest Alembic revision (cluster 20). Returns what was done.

    * new database               -> run every migration
    * pre-Alembic database        -> create missing tables/columns once, then stamp 'head'
    * already Alembic-managed     -> apply newer migrations, if any
    """
    from alembic import command

    if not get_settings().db_auto_migrate:
        return "skipped (DB_AUTO_MIGRATE=false)"
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        config = _alembic_config(connection)
        if "alembic_version" not in tables and tables & set(Base.metadata.tables):
            Base.metadata.create_all(bind=connection)
            _adopt_legacy_columns(connection)
            command.stamp(config, "head")
            return "adopted existing database and stamped head"
        command.upgrade(config, "head")
        return "upgraded to head"

"""Cluster 20: Alembic migrations - fresh upgrade matches the models, downgrade works, legacy DBs are adopted."""

import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app import db as app_db
from app.models import Base

REAL_DB = Path(__file__).resolve().parents[1] / "agentforge.db"


def _engine(path: Path):
    return create_engine(f"sqlite:///{path.as_posix()}")


def _diff(connection):
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return compare_metadata(context, Base.metadata)


def test_fresh_database_upgrade_matches_models_exactly(tmp_path):
    engine = _engine(tmp_path / "fresh.db")
    with engine.begin() as conn:
        command.upgrade(app_db._alembic_config(conn), "head")
    with engine.connect() as conn:
        assert _diff(conn) == []  # migrations and models agree: nothing left to autogenerate
        assert "alembic_version" in inspect(conn).get_table_names()


def test_downgrade_to_base_and_back(tmp_path):
    engine = _engine(tmp_path / "updown.db")
    with engine.begin() as conn:
        config = app_db._alembic_config(conn)
        command.upgrade(config, "head")
        command.downgrade(config, "base")
    with engine.connect() as conn:
        assert set(inspect(conn).get_table_names()) <= {"alembic_version"}
    with engine.begin() as conn:
        command.upgrade(app_db._alembic_config(conn), "head")


@pytest.mark.skipif(not REAL_DB.exists(), reason="no local agentforge.db to adopt")
def test_real_pre_alembic_database_is_adopted_without_data_loss(tmp_path, monkeypatch):
    copy = tmp_path / "legacy.db"
    shutil.copy(REAL_DB, copy)  # never touch the real file
    engine = _engine(copy)
    with engine.begin() as conn:  # make the copy "pre-Alembic" even if the real file was already migrated
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    with engine.connect() as conn:
        before = conn.execute(text("select count(*) from graphs")).scalar()
        assert "alembic_version" not in inspect(conn).get_table_names()
    monkeypatch.setattr(app_db, "engine", engine)
    assert app_db.initialize_database() == "adopted existing database and stamped head"
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from graphs")).scalar() == before  # graphs survived
        assert conn.execute(text("select version_num from alembic_version")).scalar()
        tables = set(inspect(conn).get_table_names())
        assert {"run_events", "audit_events", "files", "invitations"} <= tables  # new tables created
    assert app_db.initialize_database() == "upgraded to head"  # second start: normal path, nothing to do

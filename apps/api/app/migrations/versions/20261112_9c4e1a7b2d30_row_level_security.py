"""row level security on every table (Supabase hardening)

Supabase publishes tables in the "public" schema through its Data API to the roles "anon" and
"authenticated". Turning on ROW LEVEL SECURITY with NO policies means those roles can read or change
nothing, even if the Data API is switched on again by mistake.

AgentForge itself is not affected: it connects as the role that OWNS the tables, and owners bypass
RLS (we do not use FORCE ROW LEVEL SECURITY). On plain PostgreSQL without Supabase this is harmless,
and SQLite skips it entirely.

New tables in later migrations: call enable_rls(op, "<table>") at the end of their upgrade().

Revision ID: 9c4e1a7b2d30
Revises: 76046186d824
Create Date: 2026-11-12 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '9c4e1a7b2d30'
down_revision: Union[str, None] = '76046186d824'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SUPABASE_ROLES = ("anon", "authenticated")


def _tables(bind) -> list[str]:
    return [row[0] for row in bind.execute(sa.text(
        "select tablename from pg_tables where schemaname = 'public' and tableowner = current_user"))]


def _existing_roles(bind) -> list[str]:
    rows = bind.execute(sa.text("select rolname from pg_roles where rolname = any(:names)"),
                        {"names": list(SUPABASE_ROLES)})
    return [row[0] for row in rows]


def enable_rls(operations, table: str) -> None:
    """Lock one table: RLS on (no policies) and no direct rights for Supabase's public roles."""
    bind = operations.get_bind()
    if bind.dialect.name != "postgresql":
        return
    quoted = '"' + table.replace('"', '""') + '"'
    bind.execute(sa.text(f"alter table public.{quoted} enable row level security"))
    for role in _existing_roles(bind):
        bind.execute(sa.text(f'revoke all on table public.{quoted} from "{role}"'))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in _tables(bind):
        enable_rls(op, table)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in _tables(bind):
        quoted = '"' + table.replace('"', '""') + '"'
        bind.execute(sa.text(f"alter table public.{quoted} disable row level security"))
    # revoked rights are not granted back: Supabase's defaults would re-open the tables

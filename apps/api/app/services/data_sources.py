"""Company databases ("data sources") - connected READ-ONLY so agents and workflows can look things up.

How a company uses it:
  1. An owner adds the database address (best: a database user that can only read). AgentForge tests it,
     reads the list of tables/columns and stores the address encrypted with the vault key.
  2. Workflows use the "Company database" block; AI agents get it as a tool and write their own SELECTs.
  3. Every query runs HERE in the API (the worker never sees the address), after checking that the
     workflow belongs to the same workspace as the database.

Safety layers (any one of them stops a write):
  - only one statement, and it must start with SELECT / WITH / SHOW / VALUES / TABLE / DESCRIBE;
  - the connection itself is read-only (PostgreSQL default_transaction_read_only, MySQL READ ONLY
    transaction, SQLite mode=ro) - so even a sneaky "WITH ... DELETE" fails;
  - a time limit per query and a maximum number of rows returned;
  - private/local addresses are refused in production unless DATA_SOURCES_ALLOW_PRIVATE=true.
Recommend companies still create a read-only database user for AgentForge (defence in depth).
"""

from __future__ import annotations

import datetime as dt
import decimal
import ipaddress
import re
import socket
import uuid
from typing import Any

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import ArgumentError, DBAPIError, NoSuchModuleError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from agentforge_core.business_concepts import CONCEPTS

from ..config import get_settings
from ..models import DataSourceRecord, GraphRecord, RunRecord
from .authz import require_role
from .common import Forbidden, NotFound, ServiceError, now_utc
from .credentials import decrypt_value, encrypt_value

KINDS = {"postgres": "postgresql+psycopg", "mysql": "mysql+pymysql", "sqlite": "sqlite"}
_SCHEMES = {"postgres": "postgres", "postgresql": "postgres", "postgresql+psycopg": "postgres",
            "mysql": "mysql", "mysql+pymysql": "mysql", "sqlite": "sqlite"}
SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "mysql", "performance_schema", "sys"}
ALLOWED_FIRST_WORDS = {"select", "with", "show", "values", "table", "describe", "desc"}
MAX_TABLES = 300
MAX_CELL_CHARS = 2000


# ------------------------------------------------------------------ addresses


def _parse(url: str) -> tuple[str, URL]:
    """'postgresql://user:pass@host/db' -> ('postgres', SQLAlchemy URL with our driver)."""
    try:
        parsed = make_url(url.strip())
    except ArgumentError as error:
        raise ServiceError("That doesn't look like a database address. Example: "
                           "postgresql://readonly_user:password@db.company.com:5432/sales", 422) from error
    kind = _SCHEMES.get(parsed.drivername)
    if kind is None:
        raise ServiceError("Supported databases: PostgreSQL (postgresql://…) and MySQL (mysql://…).", 422)
    if kind == "sqlite" and not get_settings().data_sources_allow_sqlite:
        raise ServiceError("SQLite files can't be connected on this server.", 422)
    return kind, parsed.set(drivername=KINDS[kind])


def _check_host(kind: str, url: URL) -> None:
    """Refuse local/private addresses in production (they would reach AgentForge's own network)."""
    settings = get_settings()
    if kind == "sqlite" or settings.data_sources_allow_private or settings.app_env != "production":
        return
    host = url.host or ""
    if not host:
        raise ServiceError("The database address needs a host name.", 422)
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, url.port or 5432, type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise ServiceError(f"The host '{host}' could not be found.", 422) from error
    for address in addresses:
        if not ipaddress.ip_address(address.split("%", 1)[0]).is_global:
            raise ServiceError("This database is on a private/local address. Ask the AgentForge admin to allow "
                               "private databases (DATA_SOURCES_ALLOW_PRIVATE) if it runs inside your network.", 422)


def safe_label(kind: str, url: URL) -> str:
    """What the UI shows instead of the address: kind · host/database (never the user or password)."""
    if kind == "sqlite":
        return "sqlite · " + (url.database or "").replace("\\", "/").rsplit("/", 1)[-1]
    return f"{kind} · {url.host or '?'}{':' + str(url.port) if url.port else ''}/{url.database or ''}"


def _engine(kind: str, url: URL) -> Engine:
    """A one-off, read-only engine (no pool: company databases are queried rarely, never kept open)."""
    timeout = get_settings().data_source_query_timeout_seconds
    if url.host == "localhost":
        # Windows tries IPv6 (::1) first; a database listening only on IPv4 then costs a 10 s connect timeout
        # on EVERY query. 127.0.0.1 is the same machine without the wait.
        url = url.set(host="127.0.0.1")
    if kind == "postgres":
        return create_engine(url, poolclass=NullPool, connect_args={
            "connect_timeout": 10,
            "options": f"-c default_transaction_read_only=on -c statement_timeout={timeout * 1000}",
        })
    if kind == "mysql":
        return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10, "read_timeout": timeout})
    # sqlite (tests/demos): open the file itself read-only
    path = (url.database or "").replace("\\", "/")
    return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true", poolclass=NullPool)


def _friendly_error(error: Exception) -> str:
    """Database errors -> one short line, without the address or password."""
    original = getattr(error, "orig", None) or error
    message = str(original).strip().splitlines()[0] if str(original).strip() else type(original).__name__
    if isinstance(error, NoSuchModuleError) or "No module named" in message:
        return "This database type needs a driver that is not installed on the server (MySQL: pip install pymysql)."
    message = re.sub(r"\w+://[^\s@]+@", "<hidden>@", message)  # just in case a driver echoes the address
    return message[:300]


# ------------------------------------------------------------------ reading data


def check_read_only_sql(sql: str) -> str:
    """One read-only statement, or a clear error. (The read-only connection is the real lock; this is the
    friendly first check.)"""
    cleaned = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip().rstrip(";").strip()
    if not cleaned:
        raise ServiceError("The query is empty.", 422)
    # look for a second statement OUTSIDE quoted text ('a; b' inside a string is fine)
    outside_quotes = re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\$\$.*?\$\$", "''", cleaned, flags=re.S)
    if ";" in outside_quotes:
        raise ServiceError("Only one query at a time, please (remove the extra ';').", 422)
    first = re.match(r"[\s(]*([A-Za-z]+)", cleaned)
    if not first or first.group(1).lower() not in ALLOWED_FIRST_WORDS:
        raise ServiceError("Only reading is allowed: the query must start with SELECT or WITH. "
                           "AgentForge never changes company data.", 422)
    return cleaned


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    text_value = str(value)
    return text_value if len(text_value) <= MAX_CELL_CHARS else text_value[:MAX_CELL_CHARS] + "…"


def _run_select(kind: str, url: URL, sql: str, params: dict[str, Any] | None, max_rows: int) -> dict[str, Any]:
    engine = _engine(kind, url)
    try:
        with engine.connect() as connection:
            if kind == "mysql":
                timeout_ms = get_settings().data_source_query_timeout_seconds * 1000
                connection.execute(text(f"SET SESSION max_execution_time = {int(timeout_ms)}"))
                connection.execute(text("SET SESSION TRANSACTION READ ONLY"))
            result = connection.execute(text(sql), params or {})
            columns = list(result.keys())
            rows = result.fetchmany(max_rows + 1)
            connection.rollback()  # nothing to keep - and never commit anything
    finally:
        engine.dispose()
    truncated = len(rows) > max_rows
    records = [{col: _cell(value) for col, value in zip(columns, row)} for row in rows[:max_rows]]
    return {"columns": columns, "rows": records, "row_count": len(records), "truncated": truncated}


def _read_schema(kind: str, url: URL) -> dict[str, Any]:
    engine = _engine(kind, url)
    tables: list[dict[str, Any]] = []
    try:
        # ONE connection for the whole inspection: with NullPool every inspector call would otherwise open a new
        # connection (slow on Windows, where "localhost" tries IPv6 first; 9 tables took > 90 s).
        with engine.connect() as connection:
            inspector = inspect(connection)
            schemas = [None] if kind == "sqlite" else [s for s in inspector.get_schema_names()
                                                       if s not in SYSTEM_SCHEMAS and not s.startswith("pg_")]
            for schema in schemas:
                for name in sorted(inspector.get_table_names(schema=schema) + inspector.get_view_names(schema=schema)):
                    if len(tables) >= MAX_TABLES:
                        break
                    columns = [{"name": c["name"], "type": str(c["type"])}
                               for c in inspector.get_columns(name, schema=schema)]
                    full = name if schema in (None, "public") else f"{schema}.{name}"
                    tables.append({"name": full, "columns": columns})
            connection.rollback()
    finally:
        engine.dispose()
    return {"tables": tables, "truncated": len(tables) >= MAX_TABLES}


# ------------------------------------------------------------------ records


def source_to_dict(record: DataSourceRecord) -> dict[str, Any]:
    kind, url = _parse(decrypt_value(record.encrypted_url))
    tables = (record.schema_cache or {}).get("tables") or []
    dictionary = record.dictionary or {}
    return {
        "id": record.id, "workspace_id": record.workspace_id, "name": record.name, "kind": record.kind,
        "description": record.description, "address": safe_label(kind, url), "max_rows": record.max_rows,
        "table_count": len(tables), "last_tested_at": record.last_tested_at, "last_error": record.last_error,
        "created_at": record.created_at, "dictionary": dictionary,
        # re-checked on every read, so a renamed table/column shows up as soon as "Test connection" refreshes
        "dictionary_warnings": validate_dictionary(dictionary, record.schema_cache)[1] if dictionary else [],
    }


# ------------------------------------------------------------------ data dictionary

DICTIONARY_KEYS = {"today", "currency", "policies", "concepts", "notes"}
_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _columns_by_table(schema_cache: dict[str, Any] | None) -> dict[str, set[str]]:
    tables = (schema_cache or {}).get("tables") or []
    return {t["name"].lower().removeprefix("public."): {c["name"].lower() for c in t["columns"]} for t in tables}


def validate_dictionary(raw: Any, schema_cache: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """Checks a data dictionary. Structural mistakes -> ServiceError (422); things that don't match the real
    database (unknown table / column / concept) -> warnings, shown on the Company data page.

    Shape: {"today": "2026-11-12", "currency": "INR", "policies": {"name": "value"}, "notes": "...",
            "concepts": {"customer_payment": {"table": "receipts", "columns": {"amount": "amount_inr"},
                                              "where": "optional filter, e.g. VCH_TYPE = 'Receipt'", "notes": "..."}}}
    """
    if not isinstance(raw, dict):
        raise ServiceError("The data dictionary must be an object (JSON with { }).", 422)
    unknown_keys = set(raw) - DICTIONARY_KEYS
    if unknown_keys:
        raise ServiceError(f"Unknown data dictionary parts: {sorted(unknown_keys)}. Use {sorted(DICTIONARY_KEYS)}.", 422)
    warnings: list[str] = []
    cleaned: dict[str, Any] = {}
    if raw.get("today"):
        try:
            cleaned["today"] = dt.date.fromisoformat(str(raw["today"])).isoformat()
        except ValueError as error:
            raise ServiceError("today must be a date like 2026-11-12 (or leave it out to use the real date).", 422) from error
    if raw.get("currency"):
        cleaned["currency"] = str(raw["currency"])[:10]
    if raw.get("notes"):
        cleaned["notes"] = str(raw["notes"])[:4000]
    policies = raw.get("policies") or {}
    if not isinstance(policies, dict):
        raise ServiceError("policies must be an object of name: value pairs.", 422)
    cleaned["policies"] = {str(k)[:100]: str(v)[:500] for k, v in policies.items()}
    concepts = raw.get("concepts") or {}
    if not isinstance(concepts, dict):
        raise ServiceError("concepts must be an object of concept name: {table, columns, notes}.", 422)
    columns = _columns_by_table(schema_cache)
    cleaned["concepts"] = {}
    for name, mapping in concepts.items():
        if not isinstance(mapping, dict) or not str(mapping.get("table") or "").strip():
            raise ServiceError(f"Concept '{name}' needs at least a table.", 422)
        if name not in CONCEPTS:
            warnings.append(f"'{name}' is not a standard concept - role agents will not ask for it.")
        table = str(mapping["table"]).strip()
        mapped_columns = mapping.get("columns") or {}
        if not isinstance(mapped_columns, dict):
            raise ServiceError(f"Concept '{name}': columns must be an object of field: column name.", 422)
        entry: dict[str, Any] = {"table": table, "columns": {str(k): str(v) for k, v in mapped_columns.items()}}
        if mapping.get("where"):
            # one-big-table ERPs (Tally vouchers, SAP BSEG): the concept is a FILTER on a shared table,
            # e.g. customer_payment = VOUCHER_HDR WHERE VCH_TYPE = 'Receipt'
            entry["where"] = str(mapping["where"])[:500]
        if mapping.get("notes"):
            entry["notes"] = str(mapping["notes"])[:1000]
        table_key = table.lower().removeprefix("public.")
        if columns and table_key not in columns:
            warnings.append(f"'{name}': table '{table}' does not exist in this database.")
        elif columns:
            for field, column in entry["columns"].items():
                # a value may be an expression ("debit - credit"); only plain column names are checked
                if _PLAIN_NAME.fullmatch(column) and column.lower() not in columns[table_key]:
                    warnings.append(f"'{name}.{field}': column '{column}' is not in table '{table}'.")
        cleaned["concepts"][name] = entry
    return cleaned, warnings


def _load(db: Session, source_id: int) -> DataSourceRecord:
    record = db.get(DataSourceRecord, source_id)
    if record is None:
        raise NotFound("Database not found.")
    return record


def get_source(db: Session, user_id: int | None, source_id: int, role: str = "viewer") -> DataSourceRecord:
    record = _load(db, source_id)
    require_role(db, user_id, record.workspace_id, role)
    return record


def list_sources(db: Session, user_id: int | None, workspace_id: int) -> list[DataSourceRecord]:
    require_role(db, user_id, workspace_id, "viewer")
    return list(db.scalars(select(DataSourceRecord).where(DataSourceRecord.workspace_id == workspace_id)
                           .order_by(DataSourceRecord.name)))


def _clean_max_rows(value: Any) -> int:
    ceiling = get_settings().data_source_max_rows
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ServiceError("max_rows must be a whole number.", 422) from error
    if not 1 <= number <= ceiling:
        raise ServiceError(f"max_rows must be between 1 and {ceiling}.", 422)
    return number


def create_source(db: Session, user_id: int | None, workspace_id: int, *, name: str, url: str,
                  description: str = "", max_rows: Any = 200) -> DataSourceRecord:
    """Owners only. Tests the connection and reads the table list before saving anything."""
    require_role(db, user_id, workspace_id, "owner")
    if not name.strip():
        raise ServiceError("Give the database a name, e.g. 'Customer database'.", 422)
    kind, parsed = _parse(url)
    _check_host(kind, parsed)
    try:
        schema = _read_schema(kind, parsed)
    except (SQLAlchemyError, DBAPIError, OSError) as error:
        raise ServiceError(f"Could not connect: {_friendly_error(error)}", 422) from error
    record = DataSourceRecord(
        workspace_id=workspace_id, name=name.strip()[:200], kind=kind, description=description.strip()[:4000],
        encrypted_url=encrypt_value(url.strip()), max_rows=_clean_max_rows(max_rows), schema_cache=schema,
        last_tested_at=now_utc(), created_by=user_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def update_source(db: Session, user_id: int | None, source_id: int, changes: dict[str, Any]) -> DataSourceRecord:
    record = get_source(db, user_id, source_id, "owner")
    if "name" in changes and str(changes["name"]).strip():
        record.name = str(changes["name"]).strip()[:200]
    if "description" in changes:
        record.description = str(changes["description"] or "").strip()[:4000]
    if "max_rows" in changes:
        record.max_rows = _clean_max_rows(changes["max_rows"])
    if "dictionary" in changes:
        record.dictionary = (validate_dictionary(changes["dictionary"], record.schema_cache)[0]
                             if changes["dictionary"] else None)
    if changes.get("url"):
        kind, parsed = _parse(str(changes["url"]))
        _check_host(kind, parsed)
        record.kind, record.encrypted_url = kind, encrypt_value(str(changes["url"]).strip())
        refresh_schema(db, record)
    db.commit()
    db.refresh(record)
    return record


def delete_source(db: Session, user_id: int | None, source_id: int) -> DataSourceRecord:
    record = get_source(db, user_id, source_id, "owner")
    db.delete(record)
    db.commit()
    return record


def refresh_schema(db: Session, record: DataSourceRecord) -> DataSourceRecord:
    """'Test connection': re-reads tables/columns; remembers the error instead of failing."""
    kind, parsed = _parse(decrypt_value(record.encrypted_url))
    try:
        _check_host(kind, parsed)
        record.schema_cache, record.last_error = _read_schema(kind, parsed), None
    except ServiceError as error:
        record.last_error = error.message
    except (SQLAlchemyError, DBAPIError, OSError) as error:
        record.last_error = _friendly_error(error)
    record.last_tested_at = now_utc()
    db.commit()
    db.refresh(record)
    return record


def run_query(record: DataSourceRecord, sql: str, params: dict[str, Any] | None = None,
              max_rows: int | None = None) -> dict[str, Any]:
    """Runs one read-only query. Values from workflows go in `params` (:name placeholders) - never pasted into SQL."""
    cleaned = check_read_only_sql(sql)
    kind, parsed = _parse(decrypt_value(record.encrypted_url))
    _check_host(kind, parsed)
    limit = min(max_rows or record.max_rows, record.max_rows)
    try:
        return _run_select(kind, parsed, cleaned, params, limit)
    except (SQLAlchemyError, DBAPIError, OSError) as error:
        message = _friendly_error(error)
        if "read-only" in message.lower() or "read only" in message.lower():
            message = "Only reading is allowed - AgentForge never changes company data."
        raise ServiceError(f"The database said: {message}", 422) from error


def describe_tables(record: DataSourceRecord, table: str | None = None, only: list[str] | None = None,
                    concepts: list[str] | None = None) -> dict[str, Any]:
    """What an agent sees: the data dictionary (today, policies, where each business concept lives) and tables.

    only = explicit table focus; concepts = a role's business concepts (Treasury: customer_payment,
    bank_statement_line ...) resolved to THIS company's tables through its data dictionary. Either keeps a role on
    its ~12 tables of a 73-table database. Names match with or without 'public.'.
    """
    tables = (record.schema_cache or {}).get("tables") or []
    dictionary = record.dictionary or {}
    mapped = dictionary.get("concepts") or {}
    if concepts:
        mapped = {name: entry for name, entry in mapped.items() if name in set(concepts)}
    if only or concepts:
        wanted_names = {name.strip().lower().removeprefix("public.") for name in (only or [])}
        wanted_names |= {entry["table"].strip().lower().removeprefix("public.") for entry in mapped.values()}
        tables = [t for t in tables if t["name"].lower().removeprefix("public.") in wanted_names]
    if table:
        wanted = table.strip().lower()
        match = next((t for t in tables if t["name"].lower() in (wanted, f"public.{wanted}")), None)
        if match is None:
            raise ServiceError(f"There is no table called '{table}'. Use list_tables to see the names.", 422)
        return {"table": match["name"], "columns": match["columns"]}
    result: dict[str, Any] = {"database": record.name, "description": record.description,
                              "today": dictionary.get("today") or dt.date.today().isoformat(),
                              "currency": dictionary.get("currency"), "policies": dictionary.get("policies") or {},
                              "notes": dictionary.get("notes"), "concepts": mapped,
                              "tables": [{"name": t["name"], "columns": [c["name"] for c in t["columns"]]} for t in tables]}
    if concepts:
        result["concepts_not_in_this_database"] = sorted(set(concepts) - set(mapped))
    return {k: v for k, v in result.items() if v not in (None, "", {}, [])}


def check_source_for_run(db: Session, source_id: int, workflow_id: str | None) -> DataSourceRecord:
    """A workflow run may only use databases of its own workspace (same rule as saved keys)."""
    record = _load(db, source_id)
    run = db.query(RunRecord).filter_by(workflow_id=workflow_id).one_or_none() if workflow_id else None
    graph = db.get(GraphRecord, run.graph_id) if run else None
    if graph is None:
        raise Forbidden("The run is unknown, so it can't read company data.")
    if graph.workspace_id != record.workspace_id:
        raise Forbidden("This database belongs to another workspace.")
    return record

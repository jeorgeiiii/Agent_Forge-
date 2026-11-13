"""Company databases: connect read-only, browse tables, query safely, stay inside the workspace."""

import sqlite3
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditEventRecord, GraphRecord, RunRecord
from app.services import data_sources as svc
from app.services.common import ServiceError
from tests.test_api import _headers, _signup

INTERNAL = {"X-Internal-Token": "test-internal-token"}


@pytest.fixture
def company_db(monkeypatch):
    """A small 'finance company' database file + sqlite allowed (tests only)."""
    monkeypatch.setattr(get_settings(), "data_sources_allow_sqlite", True)
    path = Path(tempfile.mkdtemp()) / "finance.db"
    with sqlite3.connect(path) as conn:
        conn.execute("create table customers (id integer primary key, name text, email text, balance numeric)")
        conn.executemany("insert into customers (name, email, balance) values (?, ?, ?)",
                         [(f"Customer {i}", f"c{i}@bank.test", i * 100.5) for i in range(1, 31)])
        conn.execute("create table loans (id integer primary key, customer_id integer, amount numeric, status text)")
        conn.execute("insert into loans (customer_id, amount, status) values (1, 5000, 'rejected')")
    return f"sqlite:///{path.as_posix()}"


def _owner_with_source(client, email, url, **extra):
    owner = _signup(client, email)
    ws = client.get("/workspaces", headers=_headers(owner)).json()[0]["id"]
    created = client.post(f"/workspaces/{ws}/data-sources", headers=_headers(owner),
                          json={"name": "Customer database", "url": url, "description": "Bank customers", **extra})
    assert created.status_code == 201, created.text
    return owner, ws, created.json()


def test_connect_lists_tables_and_hides_the_address(client, company_db):
    owner, ws, source = _owner_with_source(client, "cfo@finance-ds.test", company_db)
    assert source["kind"] == "sqlite" and source["table_count"] == 2 and source["address"] == "sqlite · finance.db"
    assert "url" not in source and "encrypted_url" not in source
    schema = client.get(f"/data-sources/{source['id']}/schema", headers=_headers(owner)).json()
    customers = next(t for t in schema["tables"] if t["name"] == "customers")
    assert [c["name"] for c in customers["columns"]] == ["id", "name", "email", "balance"]
    with SessionLocal() as db:
        assert db.query(AuditEventRecord).filter_by(workspace_id=ws, action="data_source.create").count() == 1


def test_only_owners_connect_and_bad_addresses_are_explained(client, company_db):
    owner = _signup(client, "boss@roles-ds.test")
    ws = client.get("/workspaces", headers=_headers(owner)).json()[0]["id"]
    editor = _signup(client, "analyst@roles-ds.test")
    editor_id = client.get("/auth/me", headers=_headers(editor)).json()["id"]
    client.post(f"/workspaces/{ws}/members", json={"user_id": editor_id, "role": "editor"}, headers=_headers(owner))
    denied = client.post(f"/workspaces/{ws}/data-sources", headers=_headers(editor),
                         json={"name": "x", "url": company_db})
    assert denied.status_code == 403
    for url, words in [("not a url", "look like"), ("oracle://u:p@h/db", "Supported databases"),
                       ("sqlite:///C:/nope/missing.db", "Could not connect")]:
        bad = client.post(f"/workspaces/{ws}/data-sources", headers=_headers(owner), json={"name": "x", "url": url})
        assert bad.status_code == 422 and words in bad.json()["detail"], (url, bad.text)


def test_queries_are_read_only_limited_and_parameterised(client, company_db):
    owner, _, source = _owner_with_source(client, "risk@query-ds.test", company_db, max_rows=5)
    url = f"/data-sources/{source['id']}/query"
    rows = client.post(url, headers=_headers(owner), json={"sql": "SELECT name, balance FROM customers ORDER BY id"})
    body = rows.json()
    assert rows.status_code == 200 and body["columns"] == ["name", "balance"]
    assert body["row_count"] == 5 and body["truncated"] is True  # max_rows of this database
    assert body["rows"][0] == {"name": "Customer 1", "balance": 100.5}
    one = client.post(url, headers=_headers(owner), json={"sql": "select name from customers where email = :email",
                                                          "params": {"email": "c3@bank.test"}}).json()
    assert one["rows"] == [{"name": "Customer 3"}]
    attacks = [
        "DELETE FROM customers",
        "select 1; drop table customers",
        "UPDATE customers SET balance = 0",
        "WITH x AS (SELECT 1) INSERT INTO loans (amount) SELECT * FROM x",
        "  -- hi\n  insert into loans default values",
    ]
    for sql in attacks:
        refused = client.post(url, headers=_headers(owner), json={"sql": sql})
        assert refused.status_code == 422, sql
    # a ';' inside quoted text is fine (it is not a second query)
    quoted = client.post(url, headers=_headers(owner), json={"sql": "select 'check bank; fix status' as next_step"})
    assert quoted.status_code == 200 and quoted.json()["rows"] == [{"next_step": "check bank; fix status"}]
    assert client.post(url, headers=_headers(owner), json={"sql": "select 'x'; delete from loans"}).status_code == 422
    count = client.post(url, headers=_headers(owner), json={"sql": "select count(*) as n from customers"}).json()
    assert count["rows"] == [{"n": 30}]  # the data is untouched


def test_workflow_runs_only_reach_their_own_workspaces_database(client, company_db):
    owner, ws, source = _owner_with_source(client, "ops@runs-ds.test", company_db)
    other = _signup(client, "rival@runs-ds.test")
    other_ws = client.get("/workspaces", headers=_headers(other)).json()[0]["id"]
    with SessionLocal() as db:
        for wid, workspace in (("wf-ds-own", ws), ("wf-ds-rival", other_ws)):
            graph = GraphRecord(name=wid, definition={"nodes": [], "edges": []}, workspace_id=workspace)
            db.add(graph)
            db.flush()
            db.add(RunRecord(graph_id=graph.id, workflow_id=wid, status="running", input={}))
        db.commit()
    run_url = f"/internal/data-sources/{source['id']}/run"
    listed = client.post(f"{run_url}?workflow_id=wf-ds-own", headers=INTERNAL, json={"action": "list_tables"})
    assert listed.status_code == 200 and {t["name"] for t in listed.json()["tables"]} == {"customers", "loans"}
    assert listed.json()["description"] == "Bank customers"
    described = client.post(f"{run_url}?workflow_id=wf-ds-own", headers=INTERNAL,
                            json={"action": "describe_table", "table": "loans"}).json()
    assert [c["name"] for c in described["columns"]] == ["id", "customer_id", "amount", "status"]
    query = client.post(f"{run_url}?workflow_id=wf-ds-own", headers=INTERNAL,
                        json={"action": "query", "sql": "select status from loans where customer_id = :id",
                              "params": {"id": 1}})
    assert query.json()["rows"] == [{"status": "rejected"}]
    focused = client.post(f"{run_url}?workflow_id=wf-ds-own", headers=INTERNAL,
                          json={"action": "list_tables", "tables": ["loans"]}).json()
    assert [t["name"] for t in focused["tables"]] == ["loans"]  # an agent's focus list hides the other tables
    hidden = client.post(f"{run_url}?workflow_id=wf-ds-own", headers=INTERNAL,
                         json={"action": "describe_table", "table": "customers", "tables": ["loans"]})
    assert hidden.status_code == 422
    stolen = client.post(f"{run_url}?workflow_id=wf-ds-rival", headers=INTERNAL, json={"action": "list_tables"})
    assert stolen.status_code == 403 and "customers" not in stolen.text
    assert client.post(run_url, headers=INTERNAL, json={"action": "list_tables"}).status_code == 403  # no run id


def test_addresses_never_show_passwords_and_private_hosts_are_refused_in_production(monkeypatch):
    url = make_url("postgresql+psycopg://reader:S3cret!@db.bank.test:5432/core")
    assert svc.safe_label("postgres", url) == "postgres · db.bank.test:5432/core"
    monkeypatch.setattr(get_settings(), "app_env", "production")
    with pytest.raises(ServiceError, match="private/local"):
        svc._check_host("postgres", make_url("postgresql+psycopg://u:p@127.0.0.1:5432/x"))
    monkeypatch.setattr(get_settings(), "data_sources_allow_private", True)
    svc._check_host("postgres", make_url("postgresql+psycopg://u:p@127.0.0.1:5432/x"))  # allowed when switched on


def test_data_dictionary_maps_business_concepts_to_this_companys_tables(client, company_db):
    """The same role agent asks for 'customer' and 'loan'; the dictionary says where they live in THIS database."""
    owner, ws, source = _owner_with_source(client, "mapper@dict-ds.test", company_db)
    bad = client.patch(f"/data-sources/{source['id']}", headers=_headers(owner),
                       json={"dictionary": {"concepts": {"customer": {"columns": {"id": "id"}}}}})
    assert bad.status_code == 422 and "needs at least a table" in bad.json()["detail"]
    saved = client.patch(f"/data-sources/{source['id']}", headers=_headers(owner), json={"dictionary": {
        "today": "2026-11-12", "currency": "INR", "policies": {"large_payment_alert": "500000"},
        "concepts": {
            "customer": {"table": "customers", "columns": {"id": "id", "name": "name", "tax_id": "gstin"}},
            "loan": {"table": "loans", "columns": {"id": "id", "principal": "amount"}, "notes": "status: approved/rejected",
                     "where": "status <> 'cancelled'"},
            "made_up_thing": {"table": "nowhere"}}}})
    body = saved.json()
    assert saved.status_code == 200 and body["dictionary"]["today"] == "2026-11-12"
    warnings = " | ".join(body["dictionary_warnings"])
    assert "'customer.tax_id': column 'gstin' is not in table 'customers'" in warnings  # typo caught at setup
    assert "'made_up_thing' is not a standard concept" in warnings and "table 'nowhere' does not exist" in warnings

    with SessionLocal() as db:
        graph = GraphRecord(name="role", definition={"nodes": [], "edges": []}, workspace_id=ws)
        db.add(graph)
        db.flush()
        db.add(RunRecord(graph_id=graph.id, workflow_id="wf-dict-role", status="running", input={}))
        db.commit()
    seen = client.post(f"/internal/data-sources/{source['id']}/run?workflow_id=wf-dict-role", headers=INTERNAL,
                       json={"action": "list_tables", "concepts": ["loan", "vendor_payment"]}).json()
    assert [t["name"] for t in seen["tables"]] == ["loans"]  # only the tables behind the role's concepts
    assert seen["concepts"]["loan"]["notes"] == "status: approved/rejected" and seen["today"] == "2026-11-12"
    assert seen["concepts"]["loan"]["where"] == "status <> 'cancelled'"  # concept = a filter on a shared table
    assert seen["policies"] == {"large_payment_alert": "500000"}
    assert seen["concepts_not_in_this_database"] == ["vendor_payment"]  # the agent is told what it can't check
    assert client.get("/business-concepts").json()[0]["name"]  # vocabulary for the editor

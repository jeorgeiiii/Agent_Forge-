"""Cluster 18: retention policies, cleanup jobs (with time travel), audit pruning, GDPR erasure."""

import asyncio
from datetime import timedelta

from agentforge_core.memory import memory_key
from app.db import SessionLocal
from app.models import (
    AuditEventRecord,
    GraphRecord,
    MemorySessionRecord,
    RunEventRecord,
    RunRecord,
    UserRecord,
    WorkspaceRecord,
)
from app.services.common import now_utc
from app.services.retention import run_retention
from tests.test_api import _headers, _signup

INTERNAL = {"X-Internal-Token": "test-internal-token"}

# Built at runtime so no key-shaped literal sits in the repository (keeps secret scanners quiet).
FAKE_KEY = "gsk" + "_" + "fakefakefake" * 2


def _retention(days_ahead: float, dry_run: bool = False):
    with SessionLocal() as db:
        return asyncio.run(run_retention(db, now=now_utc() + timedelta(days=days_ahead), dry_run=dry_run))


def _owner_with_graph(client, email):
    token = _signup(client, email)
    ws = client.get("/workspaces", headers=_headers(token)).json()[0]["id"]
    gid = client.post("/graphs", json={"name": "Kept"}, headers=_headers(token)).json()["id"]
    return token, ws, gid


def _finished_run_with_events(gid, secret_prompt="hello"):
    with SessionLocal() as db:
        run = RunRecord(graph_id=gid, workflow_id=f"ret-{gid}-{now_utc().timestamp()}", status="success",
                        input={"email": "ada@example.com"}, output={"answer": 42}, finished_at=now_utc(),
                        state={"status": "success", "nodes": {"a": {"status": "success"}}, "usage": {"llm_calls": 1}})
        db.add(run)
        db.flush()
        db.add(RunEventRecord(run_id=run.id, seq=1, type="llm.call", data={"prompt_preview": secret_prompt}))
        db.commit()
        return run.id


def _login(client, email):
    """Time travel expires sessions (the cleanup job then deletes them) - log in again afterwards."""
    from tests.test_api import _headers as h

    token = client.post("/auth/login", json={"email": email, "password": "correct-horse"}, headers=h()).json()["token"]
    client.cookies.clear()
    return token


def test_policy_defaults_overrides_and_validation(client):
    token, ws, _ = _owner_with_graph(client, "ret-policy@example.com")
    policy = client.get(f"/workspaces/{ws}/retention", headers=_headers(token)).json()
    assert policy["effective"] == policy["defaults"] == {
        "run_data_days": 30, "run_rows_days": 365, "memory_days": 90, "deleted_graph_days": 30, "audit_days": 365}
    changed = client.patch(f"/workspaces/{ws}/retention", json={"run_data_days": 7, "memory_days": 14},
                           headers=_headers(token)).json()
    assert changed["effective"]["run_data_days"] == 7 and changed["overrides"] == {"run_data_days": 7, "memory_days": 14}
    reset = client.patch(f"/workspaces/{ws}/retention", json={"memory_days": None}, headers=_headers(token)).json()
    assert reset["effective"]["memory_days"] == 90
    for bad in ({"audit_days": 5}, {"run_data_days": 0}, {"nope": 1}, {"run_rows_days": 3}):
        assert client.patch(f"/workspaces/{ws}/retention", json=bad, headers=_headers(token)).status_code == 422
    editor = _signup(client, "ret-editor@example.com")
    client.post(f"/workspaces/{ws}/members", json={"email": "ret-editor@example.com", "role": "editor"}, headers=_headers(token))
    assert client.patch(f"/workspaces/{ws}/retention", json={"run_data_days": 3},
                        headers=_headers(editor)).status_code == 403  # owners only
    audit = client.get(f"/workspaces/{ws}/audit?action=retention.", headers=_headers(token)).json()
    assert len(audit) == 2  # both successful changes are audited


def test_run_payloads_are_compacted_then_rows_deleted(client):
    token, ws, gid = _owner_with_graph(client, "ret-runs@example.com")
    client.patch(f"/workspaces/{ws}/retention", json={"run_data_days": 7, "run_rows_days": 60}, headers=_headers(token))
    run_id = _finished_run_with_events(gid)

    _retention(3)
    with SessionLocal() as db:
        assert db.get(RunRecord, run_id).compacted_at is None

    preview = _retention(8, dry_run=True)
    with SessionLocal() as db:
        assert db.get(RunRecord, run_id).compacted_at is None and preview["dry_run"] is True  # dry run changes nothing

    _retention(8)
    with SessionLocal() as db:
        run = db.get(RunRecord, run_id)
        assert run.compacted_at is not None and run.output is None and run.input == {"_removed_by_retention": True}
        assert run.state == {"status": "success", "compacted": True, "usage": {"llm_calls": 1}}  # summary kept
        assert db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id).count() == 0
    token = _login(client, "ret-runs@example.com")
    detail = client.get(f"/graphs/{gid}/runs/{run_id}", headers=_headers(token))
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "success"

    _retention(61)
    with SessionLocal() as db:
        assert db.get(RunRecord, run_id) is None


def test_idle_memory_and_deleted_graphs_are_purged(client):
    token, ws, gid = _owner_with_graph(client, "ret-mem@example.com")
    client.patch(f"/workspaces/{ws}/retention", json={"memory_days": 10, "deleted_graph_days": 5}, headers=_headers(token))
    key = memory_key(gid, "bot", "s1")
    client.post(f"/internal/memory/{key}", headers=INTERNAL, json={
        "messages": [{"role": "user", "content": "hi"}], "meta": {"graph_id": gid, "scope": "bot", "session_id": "s1"}})
    doomed = client.post("/graphs", json={"name": "Doomed"}, headers=_headers(token)).json()["id"]
    run_id = _finished_run_with_events(doomed)
    client.delete(f"/graphs/{doomed}", headers=_headers(token))

    _retention(6)  # past the restore window, memory still fresh
    with SessionLocal() as db:
        assert db.get(GraphRecord, doomed) is None and db.get(RunRecord, run_id) is None  # graph + its runs gone
        assert db.get(GraphRecord, gid) is not None and db.get(MemorySessionRecord, key) is not None
    _retention(11)
    with SessionLocal() as db:
        assert db.get(MemorySessionRecord, key) is None
    assert client.get(f"/internal/memory/{key}", headers=INTERNAL).json()["messages"] == []


def test_audit_pruning_keeps_the_chain_verifiable(client):
    token, ws, gid = _owner_with_graph(client, "ret-audit@example.com")
    for i in range(3):
        client.patch(f"/graphs/{gid}/name", json={"name": f"v{i}"}, headers=_headers(token))
    before = client.get(f"/workspaces/{ws}/audit/verify", headers=_headers(token)).json()
    assert before["valid"] and before["checked"] == 4

    _retention(366)  # everything in this chain is older than audit_days now
    token = _login(client, "ret-audit@example.com")
    after = client.get(f"/workspaces/{ws}/audit/verify", headers=_headers(token)).json()
    assert after["valid"] is True and after["checked"] == 0 and after["pruned_before_position"] == 5
    client.patch(f"/graphs/{gid}/name", json={"name": "after prune"}, headers=_headers(token))  # chain continues
    again = client.get(f"/workspaces/{ws}/audit/verify", headers=_headers(token)).json()
    assert again["valid"] is True and again["checked"] == 1  # the rename (logins go to the global chain)

    with SessionLocal() as db:  # tampering after a prune is still caught
        row = db.query(AuditEventRecord).filter(AuditEventRecord.chain == f"ws:{ws}").first()
        row.details = {"forged": True}
        db.commit()
    tampered = client.get(f"/workspaces/{ws}/audit/verify", headers=_headers(token))
    assert tampered.status_code == 200, tampered.text
    assert tampered.json()["valid"] is False


def test_secret_values_are_masked_when_events_are_stored(client):
    _, _, gid = _owner_with_graph(client, "ret-mask@example.com")
    with SessionLocal() as db:
        run = RunRecord(graph_id=gid, workflow_id="mask-run", status="running", input={})
        db.add(run)
        db.commit()
    client.post("/internal/runs/mask-run/events", headers=INTERNAL, json={"events": [
        {"seq": 1, "type": "llm.call", "data": {"prompt_preview": "use key " + FAKE_KEY + " please",
                                                "nested": {"x": "Bearer abcdefghijklmnopqrstuv"}}}]})
    with SessionLocal() as db:
        data = db.query(RunEventRecord).filter(RunEventRecord.run_id == run.id).one().data
    assert "gsk_" not in str(data) and "abcdefghijklmnopqrstuv" not in str(data) and "[redacted]" in data["prompt_preview"]


def test_retention_endpoint_is_operator_only_and_dry_run_by_default(client):
    assert client.post("/admin/retention/run").status_code == 403
    report = client.post("/admin/retention/run", headers=INTERNAL).json()
    assert report["dry_run"] is True and "runs_compacted" in report


def test_gdpr_account_deletion(client):
    token, ws, gid = _owner_with_graph(client, "ret-gdpr@example.com")
    client.post("/credentials", headers=_headers(token), json={"name": "k", "type": "api_key", "workspace_id": ws,
                                                               "data": {"api_key": "x"}})
    # shared workspace where they are NOT the only owner -> membership removed
    other = _signup(client, "ret-gdpr-other@example.com")
    shared = client.get("/workspaces", headers=_headers(other)).json()[0]["id"]
    _signup(client, "ret-gdpr-third@example.com")
    client.post(f"/workspaces/{shared}/members", json={"email": "ret-gdpr@example.com", "role": "editor"}, headers=_headers(other))

    assert client.request("DELETE", "/auth/me", json={"password": "wrong"}, headers=_headers(token)).status_code == 403
    result = client.request("DELETE", "/auth/me", json={"password": "correct-horse"}, headers=_headers(token)).json()
    assert result["workspaces_deleted"] == [ws] and result["memberships_removed"] == [shared] and result["graphs_deleted"] == 1
    with SessionLocal() as db:
        assert db.query(UserRecord).filter(UserRecord.email == "ret-gdpr@example.com").count() == 0
        assert db.get(WorkspaceRecord, ws) is None and db.get(GraphRecord, gid) is None
        assert db.get(WorkspaceRecord, shared) is not None
        erasure = db.query(AuditEventRecord).filter(AuditEventRecord.action == "user.delete").one()
        assert "ret-gdpr@example.com" not in erasure.actor_label  # the erasure record itself holds no email
    assert client.get("/auth/me", headers=_headers(token)).status_code == 401


def test_last_owner_of_shared_workspace_cannot_delete_account(client):
    owner = _signup(client, "ret-lastowner@example.com")
    ws = client.get("/workspaces", headers=_headers(owner)).json()[0]["id"]
    _signup(client, "ret-member@example.com")
    client.post(f"/workspaces/{ws}/members", json={"email": "ret-member@example.com", "role": "viewer"}, headers=_headers(owner))
    response = client.request("DELETE", "/auth/me", json={"password": "correct-horse"}, headers=_headers(owner))
    assert response.status_code == 409 and "only owner" in response.json()["detail"]

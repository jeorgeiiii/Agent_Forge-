"""Cluster 17: audit log - coverage, attribution, secrecy, access control, tamper evidence."""

from app.db import SessionLocal
from app.models import AuditEventRecord
from tests.test_api import _headers, _signup


def _workspace(client, token):
    return client.get("/workspaces", headers=_headers(token)).json()[0]["id"]


def _log(client, token, ws, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    response = client.get(f"/workspaces/{ws}/audit?{query}", headers=_headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def test_security_relevant_actions_are_recorded_with_actor_and_context(client):
    owner = _signup(client, "audit-owner@example.com")
    ws = _workspace(client, owner)
    graph = client.post("/graphs", json={"name": "Audited"}, headers=_headers(owner)).json()
    client.put(f"/graphs/{graph['id']}", headers=_headers(owner),
               json={"name": "Audited v2", "description": "", "definition": graph["definition"]})
    client.post(f"/graphs/{graph['id']}/publish", json={}, headers=_headers(owner))
    _signup(client, "audit-viewer@example.com")
    client.post(f"/workspaces/{ws}/members", json={"email": "audit-viewer@example.com", "role": "viewer"},
                headers=_headers(owner))
    cred = client.post("/credentials", headers=_headers(owner), json={
        "name": "Groq", "type": "api_key", "workspace_id": ws, "data": {"api_key": "gsk_supersecretvalue123456"}}).json()
    client.delete(f"/credentials/{cred['id']}", headers=_headers(owner))
    client.delete(f"/graphs/{graph['id']}", headers=_headers(owner))

    events = _log(client, owner, ws)
    actions = [e["action"] for e in reversed(events)]  # oldest first
    assert actions == ["graph.create", "graph.update", "graph.publish", "member.add",
                       "credential.create", "credential.delete", "graph.delete"]
    update = next(e for e in events if e["action"] == "graph.update")
    assert update["actor"]["label"] == "audit-owner@example.com" and update["actor"]["type"] == "user"
    assert update["resource"] == {"type": "graph", "id": str(graph["id"])} and update["details"]["version"] == 2
    assert update["ip"] and update["user_agent"] == "testclient"
    member = next(e for e in events if e["action"] == "member.add")
    assert member["details"] == {"email": "audit-viewer@example.com", "role": "viewer"}
    # the secret never reaches the log, in any form
    assert "gsk_supersecretvalue123456" not in str(events)


def test_login_events_and_own_activity(client):
    _signup(client, "audit-login@example.com")
    client.post("/auth/login", json={"email": "audit-login@example.com", "password": "wrong-password"}, headers=_headers())
    login = client.post("/auth/login", json={"email": "audit-login@example.com", "password": "correct-horse"},
                        headers=_headers()).json()
    client.cookies.clear()
    mine = client.get("/audit", headers=_headers(login["token"])).json()
    assert [e["action"] for e in mine][:2] == ["auth.login", "auth.signup"]
    with SessionLocal() as db:
        failed = db.query(AuditEventRecord).filter(AuditEventRecord.action == "auth.login_failed").all()
    assert any(e.details.get("email") == "audit-login@example.com" and e.outcome == "denied" for e in failed)
    assert all("wrong-password" not in str(e.details) for e in failed)


def test_api_key_actor_is_identified(client):
    owner = _signup(client, "audit-key@example.com")
    ws = _workspace(client, owner)
    key = client.post("/auth/api-keys", json={"label": "ci"}, headers=_headers(owner)).json()["key"]
    client.post("/graphs", json={"name": "Via key"}, headers={"X-API-Key": key})
    event = _log(client, owner, ws, action="graph.create")[0]
    assert event["actor"]["type"] == "api_key" and event["actor"]["label"].startswith("api_key af_")


def test_only_owners_can_read_the_workspace_log(client):
    owner = _signup(client, "audit-own2@example.com")
    ws = _workspace(client, owner)
    viewer = _signup(client, "audit-view2@example.com")
    client.post(f"/workspaces/{ws}/members", json={"email": "audit-view2@example.com", "role": "editor"},
                headers=_headers(owner))
    assert client.get(f"/workspaces/{ws}/audit", headers=_headers(viewer)).status_code == 403
    assert client.get(f"/workspaces/{ws}/audit/verify", headers=_headers(viewer)).status_code == 403
    assert client.get("/audit/verify").status_code == 403  # global chain: operators (internal token) only


def test_hash_chain_detects_edits_deletions_and_truncation(client):
    owner = _signup(client, "audit-chain@example.com")
    ws = _workspace(client, owner)
    for i in range(4):
        client.post("/graphs", json={"name": f"G{i}"}, headers=_headers(owner))
    verify = lambda: client.get(f"/workspaces/{ws}/audit/verify", headers=_headers(owner)).json()  # noqa: E731
    assert verify() == {"chain": f"ws:{ws}", "valid": True, "checked": 4}

    with SessionLocal() as db:
        rows = db.query(AuditEventRecord).filter(AuditEventRecord.chain == f"ws:{ws}").order_by(AuditEventRecord.id).all()
        second, third = rows[1].id, rows[2].id
        rows[1].details = {"name": "edited afterwards"}  # someone edits a row directly in the DB
        db.commit()
    result = verify()
    assert result["valid"] is False and result["broken_at_id"] == second and "modified" in result["reason"]

    with SessionLocal() as db:
        db.delete(db.get(AuditEventRecord, second))  # ...or deletes it
        db.commit()
    result = verify()
    assert result["valid"] is False and result["broken_at_id"] == third

    owner2 = _signup(client, "audit-chain2@example.com")
    ws2 = _workspace(client, owner2)
    for i in range(3):
        client.post("/graphs", json={"name": f"H{i}"}, headers=_headers(owner2))
    with SessionLocal() as db:
        newest = db.query(AuditEventRecord).filter(AuditEventRecord.chain == f"ws:{ws2}").order_by(AuditEventRecord.id.desc()).first()
        db.delete(newest)  # deleting the newest row leaves no gap - the chain head catches it
        db.commit()
    result = client.get(f"/workspaces/{ws2}/audit/verify", headers=_headers(owner2)).json()
    assert result["valid"] is False and "missing" in result["reason"]


def test_filters_pagination_and_csv_export(client):
    owner = _signup(client, "audit-filter@example.com")
    ws = _workspace(client, owner)
    gid = client.post("/graphs", json={"name": "F"}, headers=_headers(owner)).json()["id"]
    for i in range(3):
        client.patch(f"/graphs/{gid}/name", json={"name": f"F{i}"}, headers=_headers(owner))
    assert [e["action"] for e in _log(client, owner, ws, action="graph.rename")] == ["graph.rename"] * 3
    page = client.get(f"/workspaces/{ws}/audit?limit=2", headers=_headers(owner))
    rest = client.get(f"/workspaces/{ws}/audit?limit=2&before_id={page.headers['x-next-cursor']}", headers=_headers(owner))
    assert len(page.json()) == 2 and len(rest.json()) == 2
    csv_text = client.get(f"/workspaces/{ws}/audit/export.csv", headers=_headers(owner)).text
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("id,at,action,outcome") and len(lines) == 5 and "graph.rename" in csv_text


def test_scrubbing_and_audit_failures_never_break_requests(client, monkeypatch):
    from app.services import audit as audit_service

    cleaned = audit_service.scrub({"password": "p", "input_tokens": 5, "metadata": {"x": 1}, "client_secret": "s",
                                   "note": "key gsk_abcdefghijklmnop and Bearer abcdefghijklmnopqrstuvwx"})
    assert cleaned["password"] == "[redacted]" and cleaned["client_secret"] == "[redacted]"
    assert cleaned["input_tokens"] == 5 and cleaned["metadata"] == {"x": 1}  # harmless fields untouched
    assert "gsk_" not in cleaned["note"] and "abcdefghijklmnopqrstuvwx" not in cleaned["note"]

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(audit_service, "compute_hash", boom)
    owner = _signup(client, "audit-fail@example.com")  # audit write fails inside...
    response = client.post("/graphs", json={"name": "Still works"}, headers=_headers(owner))
    assert response.status_code == 201  # ...but the action itself succeeds

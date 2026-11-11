import itertools

_ip = itertools.count(1)


def _headers(token=None):
    headers = {"X-Forwarded-For": f"10.0.0.{next(_ip)}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _signup(client, email, password="correct-horse"):
    response = client.post("/auth/signup", json={"email": email, "password": password, "name": email[:3]}, headers=_headers())
    assert response.status_code == 201, response.text
    client.cookies.clear()  # tests authenticate with explicit bearer tokens
    return response.json()["token"]


def test_health_and_node_palette(client):
    assert client.get("/health").json() == {"status": "ok"}
    types = {t["type"] for t in client.get("/node-types").json()}
    assert {"start", "end", "if", "merge", "http", "approval", "webhook_trigger"} <= types
    assert client.get("/node-types/http/schema").json()["required"] == ["url"]
    assert client.post("/node-types/http/validate", json={}).json()["valid"] is False
    assert client.post("/expressions/validate", json={"expression": "{{ $nope }}"}).json()["valid"] is False


def test_legacy_single_user_mode_still_works(client):
    """The current web app sends no auth: graph CRUD must keep working."""
    created = client.post("/api/graphs", json={"name": "Legacy"})
    assert created.status_code == 201, created.text
    graph = created.json()
    assert graph["version"] == 1 and graph["workspace_id"] is None
    assert [n["type"] for n in graph["definition"]["nodes"]] == ["start", "end"]
    assert any(g["id"] == graph["id"] for g in client.get("/api/graphs").json())


def test_signup_login_session_and_api_keys(client):
    token = _signup(client, "ada@example.com")
    assert client.get("/auth/me", headers=_headers(token)).json()["email"] == "ada@example.com"

    bad = client.post("/auth/login", json={"email": "ada@example.com", "password": "wrong-pass"}, headers=_headers())
    assert bad.status_code == 401 and bad.json()["detail"] == "Invalid email or password."
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong-pass"}, headers=_headers())
    assert unknown.json()["detail"] == bad.json()["detail"]  # no user enumeration

    duplicate = client.post("/auth/signup", json={"email": "ADA@example.com", "password": "another-pass"}, headers=_headers())
    assert duplicate.status_code == 409

    key = client.post("/auth/api-keys", json={"label": "ci"}, headers=_headers(token)).json()
    assert key["key"].startswith("af_")
    assert client.get("/auth/me", headers={"X-API-Key": key["key"]}).status_code == 200
    client.delete(f"/auth/api-keys/{key['id']}", headers=_headers(token))
    assert client.get("/auth/me", headers={"X-API-Key": key["key"]}).status_code == 401

    login = client.post("/auth/login", json={"email": "ada@example.com", "password": "correct-horse"}, headers=_headers())
    second = login.json()["token"]
    client.cookies.clear()
    client.post("/auth/logout", headers=_headers(second))
    assert client.get("/auth/me", headers=_headers(second)).status_code == 401
    assert client.get("/auth/me", headers=_headers(token)).status_code == 200  # other session untouched


def test_workspace_permissions_and_graph_versions(client):
    owner = _signup(client, "owner@example.com")
    viewer = _signup(client, "viewer@example.com")
    stranger = _signup(client, "stranger@example.com")
    workspace = client.get("/workspaces", headers=_headers(owner)).json()[0]
    assert workspace["role"] == "owner"

    added = client.post(f"/workspaces/{workspace['id']}/members",
                        json={"email": "viewer@example.com", "role": "viewer"}, headers=_headers(owner))
    assert added.status_code == 201, added.text

    graph = client.post("/graphs", json={"name": "Team graph"}, headers=_headers(owner)).json()
    assert graph["workspace_id"] == workspace["id"]
    gid = graph["id"]

    assert client.get(f"/graphs/{gid}", headers=_headers(viewer)).status_code == 200
    assert client.get(f"/graphs/{gid}", headers=_headers(stranger)).status_code == 404
    edit = {"name": "Edited", "description": "", "definition": graph["definition"]}
    assert client.put(f"/graphs/{gid}", json=edit, headers=_headers(viewer)).status_code == 403

    branching = {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "check", "type": "if", "config": {"condition": "{{ $json.ok }}"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "check"},
            {"source": "check", "target": "end", "sourceHandle": "true"},
        ],
    }
    updated = client.put(f"/graphs/{gid}", json={**edit, "definition": branching}, headers=_headers(owner))
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    invalid = {**branching, "edges": [{"source": "start", "target": "check"}, {"source": "check", "target": "end"}]}
    assert client.put(f"/graphs/{gid}", json={**edit, "definition": invalid}, headers=_headers(owner)).status_code == 422

    versions = client.get(f"/graphs/{gid}/versions", headers=_headers(owner)).json()
    assert [v["version"] for v in versions] == [2, 1]
    rolled = client.post(f"/graphs/{gid}/versions/1/rollback", headers=_headers(owner)).json()
    assert rolled["version"] == 3 and len(rolled["definition"]["nodes"]) == 2

    compiled = client.get(f"/graphs/{gid}/compiled", headers=_headers(owner)).json()
    assert compiled["supersteps"] == [["start"], ["end"]]

    copy = client.post(f"/graphs/{gid}/duplicate", json={}, headers=_headers(owner)).json()
    assert copy["name"] == "Team graph (copy)"  # rollback restored v1 name
    assert client.delete(f"/graphs/{gid}", headers=_headers(owner)).status_code == 204
    assert client.get(f"/graphs/{gid}", headers=_headers(owner)).status_code == 404
    assert client.post(f"/graphs/{gid}/restore", headers=_headers(owner)).status_code == 200


def test_run_without_temporal_fails_cleanly_and_checkpoint_updates_run(client):
    graph = client.post("/graphs", json={"name": "Runner"}).json()
    response = client.post(f"/graphs/{graph['id']}/runs", json={"input": {"x": 1}})
    assert response.status_code == 503  # Temporal is not running in tests

    runs = client.get(f"/graphs/{graph['id']}/runs").json()
    assert runs[0]["status"] == "failed"
    workflow_id = runs[0]["workflow_id"]

    denied = client.post(f"/internal/runs/{workflow_id}/checkpoint", json={"status": "running"})
    assert denied.status_code == 403
    state = {"status": "success", "superstep": 2, "nodes": {"start": {"status": "success"}}, "output": {"x": 1}}
    ok = client.post(f"/internal/runs/{workflow_id}/checkpoint", json=state, headers={"X-Internal-Token": "test-internal-token"})
    assert ok.status_code == 200, ok.text
    run = client.get(f"/graphs/{graph['id']}/runs/{runs[0]['id']}").json()
    assert run["status"] == "success" and run["output"] == {"x": 1} and run["finished_at"]
    assert client.get(f"/graphs/{graph['id']}/runs/{run['id']}/output").json()["output"] == {"x": 1}


def test_credentials_are_encrypted_and_never_returned(client):
    created = client.post("/credentials", json={"name": "OpenAI", "type": "api_key", "data": {"api_key": "sk-secret"}})
    assert created.status_code == 201, created.text
    body = created.json()
    assert "data" not in body and "sk-secret" not in created.text
    listed = client.get("/credentials").json()
    assert "sk-secret" not in str(listed)

    internal = client.get(f"/internal/credentials/{body['id']}", headers={"X-Internal-Token": "test-internal-token"})
    assert internal.json()["data"] == {"api_key": "sk-secret"}

    from app.db import SessionLocal
    from app.models import CredentialRecord

    with SessionLocal() as db:
        stored = db.get(CredentialRecord, body["id"]).encrypted_data
    assert stored.startswith("v1:k1:") and "sk-secret" not in stored


def test_copilot_patch_and_validation_need_no_llm(client):
    definition = client.post("/graphs", json={"name": "Patch me"}).json()["definition"]
    patch = {"operations": [
        {"op": "remove_edge", "source": "start", "target": "end"},
        {"op": "add_node", "node": {"id": "greet", "type": "template", "config": {"template": "hi"}}},
        {"op": "add_edge", "source": "start", "target": "greet"},
        {"op": "add_edge", "source": "greet", "target": "end"},
    ]}
    result = client.post("/copilot/apply-patch", json={"definition": definition, "patch": patch}).json()
    assert result["errors"] == []
    assert {n["id"] for n in result["definition"]["nodes"]} == {"start", "greet", "end"}
    assert client.post("/copilot/validate", json={"definition": {"nodes": []}}).json()["valid"] is False
    assert client.post("/copilot/generate", json={"prompt": "x"}).status_code == 503  # no provider configured


def test_forwarded_for_only_trusted_from_known_proxies(monkeypatch):
    from types import SimpleNamespace

    from app.config import get_settings
    from app.rate_limiter import client_ip

    def req(peer, xff=None):
        return SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": xff} if xff else {})

    monkeypatch.setattr(get_settings(), "trusted_proxies", "10.0.0.5")
    assert client_ip(req("6.6.6.6", "1.2.3.4")) == "6.6.6.6"           # spoof from the internet: ignored
    assert client_ip(req("10.0.0.5", "1.2.3.4")) == "1.2.3.4"           # via our proxy: honoured
    assert client_ip(req("10.0.0.5", "9.9.9.9, 1.2.3.4")) == "1.2.3.4"  # client-supplied prefix ignored


def test_login_is_limited_per_email_across_ips(client):
    _signup(client, "target@example.com")
    codes = [client.post("/auth/login", json={"email": "target@example.com", "password": "wrong-pass"},
                         headers=_headers()).status_code for _ in range(12)]  # new IP every time
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]


def test_stale_checkpoint_does_not_overwrite_newer_state(client):
    graph = client.post("/graphs", json={"name": "Seq"}).json()
    client.post(f"/graphs/{graph['id']}/runs", json={"input": {}})  # 503 (no Temporal) but row exists
    wid = client.get(f"/graphs/{graph['id']}/runs").json()[0]["workflow_id"]
    internal = {"X-Internal-Token": "test-internal-token"}
    client.post(f"/internal/runs/{wid}/checkpoint", json={"seq": 5, "status": "success", "output": "new"}, headers=internal)
    client.post(f"/internal/runs/{wid}/checkpoint", json={"seq": 3, "status": "running"}, headers=internal)
    run = client.get(f"/graphs/{graph['id']}/runs").json()[0]
    assert run["status"] == "success" and run["output"] == "new"

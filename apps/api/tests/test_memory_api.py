"""Cluster 12: memory storage (summary + verbatim tail) and per-graph session management."""

from agentforge_core.memory import memory_key

INTERNAL = {"X-Internal-Token": "test-internal-token"}


def _append(client, key, pairs, meta):
    messages = [m for q, a in pairs for m in ({"role": "user", "content": q}, {"role": "assistant", "content": a})]
    return client.post(f"/internal/memory/{key}", headers=INTERNAL, json={"messages": messages, "meta": meta})


def test_summary_replaces_folded_messages(client):
    key = memory_key(1, "bot", "sum-1")
    _append(client, key, [("q1", "a1"), ("q2", "a2"), ("q3", "a3")], {"graph_id": 1, "scope": "bot", "session_id": "sum-1"})
    loaded = client.get(f"/internal/memory/{key}?limit=50", headers=INTERNAL).json()
    assert loaded["summary"] is None and [m["content"] for m in loaded["messages"]] == ["q1", "a1", "q2", "a2", "q3", "a3"]
    covers = loaded["messages"][3]["id"]  # fold q1..a2
    client.post(f"/internal/memory/{key}/summary", headers=INTERNAL, json={"content": "S1", "covers_until": covers})
    client.post(f"/internal/memory/{key}/summary", headers=INTERNAL, json={"content": "stale", "covers_until": covers - 1})
    loaded = client.get(f"/internal/memory/{key}?limit=50", headers=INTERNAL).json()
    assert loaded["summary"] == "S1" and loaded["covers_until"] == covers  # stale/out-of-order summary ignored
    assert [m["content"] for m in loaded["messages"]] == ["q3", "a3"]


def test_internal_memory_endpoints_are_worker_only_and_validate_keys(client):
    key = memory_key(1, "bot", "x")
    assert client.get(f"/internal/memory/{key}").status_code == 403
    assert client.get("/internal/memory/not-a-mem-key", headers=INTERNAL).status_code == 422
    _append(client, key, [("q", "a")], {})
    client.post(f"/internal/memory/{key}", headers=INTERNAL,
                json={"messages": [{"role": "system", "content": "ignored"}, {"role": "user", "content": ""}]})
    assert len(client.get(f"/internal/memory/{key}", headers=INTERNAL).json()["messages"]) == 2


def test_list_read_and_clear_sessions(client):
    gid = client.post("/graphs", json={"name": "Chatbot"}).json()["id"]
    other = client.post("/graphs", json={"name": "Other"}).json()["id"]
    k1, k2 = memory_key(gid, "bot", "alice"), memory_key(gid, "bot", "bob")
    _append(client, k1, [("hi I'm Alice", "hello Alice")], {"graph_id": gid, "scope": "bot", "session_id": "alice"})
    _append(client, k2, [("hi I'm Bob", "hello Bob"), ("bye", "bye")], {"graph_id": gid, "scope": "bot", "session_id": "bob"})

    sessions = client.get(f"/graphs/{gid}/memory/sessions").json()
    assert {s["session_id"]: s["messages"] for s in sessions} == {"alice": 2, "bob": 4}
    assert sessions[0]["session_id"] == "bob"  # most recent first
    detail = client.get(f"/graphs/{gid}/memory/sessions/{k1}").json()
    assert [m["content"] for m in detail["messages"]] == ["hi I'm Alice", "hello Alice"] and detail["agent"] == "bot"

    # a session is only reachable through the graph it belongs to
    assert client.get(f"/graphs/{other}/memory/sessions/{k1}").status_code == 404
    assert client.get(f"/graphs/{other}/memory/sessions").json() == []

    assert client.delete(f"/graphs/{gid}/memory/sessions/{k1}").status_code == 204
    assert client.get(f"/internal/memory/{k1}", headers=INTERNAL).json()["messages"] == []
    assert [s["session_id"] for s in client.get(f"/graphs/{gid}/memory/sessions").json()] == ["bob"]
    assert client.delete(f"/graphs/{gid}/memory/sessions").json() == {"deleted_messages": 4}
    assert client.get(f"/graphs/{gid}/memory/sessions").json() == []


def test_memory_sessions_respect_permissions(client):
    from tests.test_api import _headers, _signup

    owner = _signup(client, "mem-owner@example.com")
    gid = client.post("/graphs", json={"name": "Private bot"}, headers=_headers(owner)).json()["id"]
    key = memory_key(gid, "bot", "s")
    _append(client, key, [("secret", "ok")], {"graph_id": gid, "scope": "bot", "session_id": "s"})
    stranger = _signup(client, "mem-stranger@example.com")
    assert client.get(f"/graphs/{gid}/memory/sessions", headers=_headers(stranger)).status_code == 404
    assert client.delete(f"/graphs/{gid}/memory/sessions/{key}", headers=_headers(stranger)).status_code == 404
    assert len(client.get(f"/graphs/{gid}/memory/sessions", headers=_headers(owner)).json()) == 1

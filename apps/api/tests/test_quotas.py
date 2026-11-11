"""Cluster 13: per-workspace quotas, limits endpoint, LLM quota check, RateLimit headers, fail-closed."""

import uuid

from app.config import get_settings
from app.db import SessionLocal
from app.models import RunEventRecord, RunRecord
from tests.test_api import _headers, _signup

INTERNAL = {"X-Internal-Token": "test-internal-token"}


def _setup(client, email):
    token = _signup(client, email)
    ws = client.get("/workspaces", headers=_headers(token)).json()[0]["id"]
    gid = client.post("/graphs", json={"name": "Q"}, headers=_headers(token)).json()["id"]
    return token, ws, gid


def _add_run(gid, status="running", workflow_id=None, tokens=0):
    with SessionLocal() as db:
        run = RunRecord(graph_id=gid, workflow_id=workflow_id or f"q-{gid}-{uuid.uuid4()}",
                        status=status, input={})
        db.add(run)
        db.flush()
        if tokens:
            db.add(RunEventRecord(run_id=run.id, seq=1, type="llm.call",
                                  data={"model": "m", "input_tokens": tokens, "output_tokens": 0}))
        db.commit()
        return run.workflow_id


def test_limits_endpoint_and_operator_overrides(client):
    token, ws, gid = _setup(client, "quota-limits@example.com")
    status = client.get(f"/workspaces/{ws}/limits", headers=_headers(token)).json()
    assert status["limits"] == {"max_concurrent_runs": 20, "runs_per_day": 0, "tokens_per_minute": 0, "tokens_per_day": 0}
    assert status["usage"] == {"concurrent_runs": 0, "runs_today": 0, "tokens_last_minute": 0, "tokens_today": 0}
    assert client.patch(f"/admin/workspaces/{ws}/limits", json={"runs_per_day": 5}).status_code == 403  # operators only
    changed = client.patch(f"/admin/workspaces/{ws}/limits", json={"runs_per_day": 5, "tokens_per_day": 1000},
                           headers=INTERNAL).json()
    assert changed["runs_per_day"] == 5 and changed["tokens_per_day"] == 1000
    for bad in ({"runs_per_day": -1}, {"nope": 1}, {"runs_per_day": "5"}):
        assert client.patch(f"/admin/workspaces/{ws}/limits", json=bad, headers=INTERNAL).status_code == 422
    reset = client.patch(f"/admin/workspaces/{ws}/limits", json={"runs_per_day": None}, headers=INTERNAL).json()
    assert reset["runs_per_day"] == 0
    stranger = _signup(client, "quota-stranger@example.com")
    assert client.get(f"/workspaces/{ws}/limits", headers=_headers(stranger)).status_code == 403


def test_run_start_is_refused_over_quota(client):
    token, ws, gid = _setup(client, "quota-runs@example.com")
    client.patch(f"/admin/workspaces/{ws}/limits", json={"max_concurrent_runs": 2}, headers=INTERNAL)
    _add_run(gid)
    _add_run(gid)
    refused = client.post(f"/graphs/{gid}/runs", json={"input": {}}, headers=_headers(token))
    assert refused.status_code == 429 and "Too many runs in progress (2/2)" in refused.json()["detail"]

    client.patch(f"/admin/workspaces/{ws}/limits", json={"max_concurrent_runs": 0, "tokens_per_day": 500}, headers=INTERNAL)
    _add_run(gid, status="success", tokens=600)
    refused = client.post(f"/graphs/{gid}/runs", json={"input": {}}, headers=_headers(token))
    assert refused.status_code == 429 and "Daily LLM token limit" in refused.json()["detail"]
    usage = client.get(f"/workspaces/{ws}/limits", headers=_headers(token)).json()["usage"]
    assert usage["tokens_today"] == 600 and usage["concurrent_runs"] == 2


def test_llm_quota_check_for_the_worker(client):
    token, ws, gid = _setup(client, "quota-llm@example.com")
    workflow_id = _add_run(gid, tokens=0)
    assert client.get(f"/internal/quota/llm?workflow_id={workflow_id}", headers=INTERNAL).json()["allowed"] is True
    client.patch(f"/admin/workspaces/{ws}/limits", json={"tokens_per_minute": 100}, headers=INTERNAL)
    _add_run(gid, status="success", tokens=150)
    verdict = client.get(f"/internal/quota/llm?workflow_id={workflow_id}", headers=INTERNAL).json()
    assert verdict["allowed"] is False and "tokens-per-minute" in verdict["reason"]
    # other workspaces are not affected
    _, _, other_gid = _setup(client, "quota-llm-other@example.com")
    other_run = _add_run(other_gid)
    assert client.get(f"/internal/quota/llm?workflow_id={other_run}", headers=INTERNAL).json()["allowed"] is True


def test_ratelimit_headers_and_fail_closed(client, monkeypatch):
    ok = client.post("/auth/signup", json={"email": "quota-hdr@example.com", "password": "correct-horse"},
                     headers=_headers())
    assert ok.headers["ratelimit-limit"] == "5" and ok.headers["ratelimit-policy"] == "5;w=60"
    assert ok.headers["ratelimit-remaining"] == "4"
    client.cookies.clear()
    same_ip = {"X-Forwarded-For": "10.200.0.9"}
    codes = [client.post("/auth/signup", json={"email": f"quota-burst{i}@example.com", "password": "correct-horse"},
                         headers=same_ip) for i in range(6)]
    client.cookies.clear()
    limited = codes[-1]
    assert limited.status_code == 429 and limited.headers["ratelimit-remaining"] == "0"  # 429 carries them too
    monkeypatch.setattr(get_settings(), "rate_limit_fail_closed", True)  # Redis is not running in tests
    refused = client.post("/auth/login", json={"email": "x@example.com", "password": "whatever"}, headers=_headers())
    assert refused.status_code == 503 and refused.headers["retry-after"] == "5"
    assert client.get("/health").status_code == 200  # only strict (auth) limits fail closed

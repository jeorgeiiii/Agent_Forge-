"""Cluster 11 part 2: run history filters, details, cost, usage, retry, OpenTelemetry export."""

import httpx

from app.db import SessionLocal
from app.models import RunRecord

INTERNAL = {"X-Internal-Token": "test-internal-token"}


def _seed_runs(graph_id, rows):
    ids = []
    with SessionLocal() as db:
        for i, (status, trigger) in enumerate(rows):
            run = RunRecord(graph_id=graph_id, workflow_id=f"seed-{graph_id}-{i}-{status}", status=status,
                            input={"i": i}, trigger_kind=trigger, graph_version=1)
            db.add(run)
            db.flush()
            ids.append(run.id)
        db.commit()
    return ids


def _t(second):
    return f"2026-11-11T10:00:0{second}+00:00"


def _seed_agent_run(client, name):
    """A finished run with node state + llm/tool events, delivered the way the worker does."""
    gid = client.post("/graphs", json={"name": name}).json()["id"]
    run_id, = _seed_runs(gid, [("running", "manual")])
    workflow_id = client.get(f"/graphs/{gid}/runs/{run_id}").json()["workflow_id"]
    events = [
        {"seq": 1, "type": "run.started", "at": _t(0), "data": {}},
        {"seq": 2, "type": "node.started", "node_id": "boss", "at": _t(0), "data": {"node_type": "agent"}},
        {"seq": 3, "type": "llm.call", "node_id": "boss", "at": _t(2), "data": {
            "model": "llama-test", "input_tokens": 1000, "output_tokens": 100, "latency_ms": 1500, "step": 1,
            "finish_reason": "tool_calls"}},
        {"seq": 4, "type": "tool.call", "node_id": "boss", "at": _t(5), "data": {
            "tool": "helper", "ok": True, "kind": "agent", "duration_ms": 2500}},
        {"seq": 5, "type": "node.finished", "node_id": "boss", "at": _t(6), "data": {"status": "success"}},
        {"seq": 6, "type": "run.finished", "at": _t(6), "data": {"status": "success"}},
    ]
    client.post(f"/internal/runs/{workflow_id}/events", headers=INTERNAL, json={"source": "child-1", "events": [
        {"seq": 1, "type": "llm.call", "node_id": "boss/helper", "at": _t(4), "data": {
            "model": "other-model", "input_tokens": 50, "output_tokens": 50, "latency_ms": 900, "step": 1}}]})
    client.post(f"/internal/runs/{workflow_id}/checkpoint", headers=INTERNAL, json={
        "seq": 1, "status": "success", "output": "done", "usage": {"llm_calls": 2},
        "nodes": {"start": {"status": "success", "outputs": {"main": {}}, "started_at": _t(0), "finished_at": _t(0)},
                  "boss": {"status": "success", "outputs": {"main": {"text": "done"}}, "started_at": _t(0),
                           "finished_at": _t(6)}},
        "events": events})
    return gid, run_id


def test_run_history_filters_and_cursor_pagination(client):
    gid = client.post("/graphs", json={"name": "History"}).json()["id"]
    _seed_runs(gid, [("success", "manual"), ("failed", "webhook"), ("success", "schedule"),
                     ("failed", "manual"), ("cancelled", "manual")])
    assert len(client.get(f"/graphs/{gid}/runs").json()) == 5  # old behaviour unchanged
    failed = client.get(f"/graphs/{gid}/runs?status=failed,cancelled").json()
    assert sorted(r["status"] for r in failed) == ["cancelled", "failed", "failed"]
    assert [r["trigger_kind"] for r in client.get(f"/graphs/{gid}/runs?trigger=webhook").json()] == ["webhook"]
    page1 = client.get(f"/graphs/{gid}/runs?limit=2")
    page2 = client.get(f"/graphs/{gid}/runs?limit=2&before_id={page1.headers['x-next-cursor']}")
    page3 = client.get(f"/graphs/{gid}/runs?limit=2&before_id={page2.headers['x-next-cursor']}")
    ids = [r["id"] for p in (page1, page2, page3) for r in p.json()]
    assert len(ids) == 5 and ids == sorted(ids, reverse=True) and "x-next-cursor" not in page3.headers
    assert client.get(f"/graphs/{gid}/runs?since=2999-01-01T00:00:00Z").json() == []


def test_run_details_and_cost_breakdown(client):
    gid, run_id = _seed_agent_run(client, "Details")
    details = client.get(f"/graphs/{gid}/runs/{run_id}/details").json()
    assert details["run"]["status"] == "success" and details["can_retry"] is False
    boss = next(n for n in details["nodes"] if n["node_id"] == "boss")
    assert boss["duration_ms"] == 6000 and boss["output"] == {"text": "done"}
    assert details["event_counts"]["llm.call"] == 2 and details["last_event_id"] > 0
    assert [n["node_id"] for n in details["nodes"]] == ["start", "boss"]  # execution order
    cost = client.get(f"/graphs/{gid}/runs/{run_id}/cost").json()
    assert cost["llm_calls"] == 2 and cost["input_tokens"] == 1050
    assert cost["by_model"]["llama-test"]["cost_usd"] == 0.0012  # 1000*$1 + 100*$2 per 1M
    assert cost["by_agent"]["boss/helper"]["calls"] == 1
    assert cost["unpriced_models"] == ["other-model"] and cost["total_cost_usd"] == 0.0012


def test_usage_summary_groups(client):
    gid, _ = _seed_agent_run(client, "Usage A")
    by_model = client.get(f"/usage?graph_id={gid}&group_by=model").json()
    assert {g["key"]: g["llm_calls"] for g in by_model["groups"]} == {"llama-test": 1, "other-model": 1}
    assert by_model["totals"]["runs"] == 1 and by_model["totals"]["cost_usd"] == 0.0012
    by_day = client.get(f"/usage?graph_id={gid}&group_by=day").json()
    assert len(by_day["groups"]) == 1 and by_day["groups"][0]["runs"] == 1
    by_graph = client.get(f"/usage?graph_id={gid}&group_by=graph").json()
    assert by_graph["groups"][0]["key"] == f"{gid}:Usage A"
    assert client.get("/usage?group_by=nope").status_code == 422
    assert client.get("/usage?graph_id=999999").status_code == 404


def test_retry_resumes_from_failed_node_on_same_version(client, monkeypatch):
    from app.services import runs as run_service

    started = []

    class FakeTemporal:
        async def start_workflow(self, name, payload, id, task_queue):
            started.append(payload)

    async def fake_client():
        return FakeTemporal()

    monkeypatch.setattr(run_service, "get_temporal_client", fake_client)
    gid = client.post("/graphs", json={"name": "Retry"}).json()["id"]
    run_id, ok_id = _seed_runs(gid, [("failed", "manual"), ("success", "manual")])
    with SessionLocal() as db:
        run = db.get(RunRecord, run_id)
        run.state = {"status": "failed", "superstep": 1, "seq": 9, "event_seq": 30, "usage": {"llm_calls": 3},
                     "error": "boom", "nodes": {"start": {"status": "success", "outputs": {"main": {"x": 1}}},
                                               "end": {"status": "failed", "error": "boom"}}}
        db.commit()
    response = client.post(f"/graphs/{gid}/runs/{run_id}/retry")
    assert response.status_code == 202, response.text
    new = response.json()
    assert new["trigger_kind"] == "retry" and new["retry_of_run_id"] == run_id and new["graph_version"] == 1
    checkpoint = started[0]["checkpoint"]
    assert list(checkpoint["nodes"]) == ["start"]  # the failed node is re-run, the finished one is not
    assert checkpoint["superstep"] == 1 and "usage" not in checkpoint and "event_seq" not in checkpoint
    assert client.post(f"/graphs/{gid}/runs/{ok_id}/retry").status_code == 409  # successful runs cannot be retried


def test_opentelemetry_trace_and_export(client, monkeypatch):
    from app.config import get_settings
    from app.services import observability

    gid, run_id = _seed_agent_run(client, "Trace")
    trace = client.get(f"/graphs/{gid}/runs/{run_id}/trace").json()
    spans = trace["resourceSpans"][0]["scopeSpans"][0]["spans"]
    by_name = {s["name"]: s for s in spans}
    root = by_name["run Trace"]
    assert by_name["node boss"]["parentSpanId"] == root["spanId"]
    assert by_name["invoke_agent boss"]["parentSpanId"] == by_name["node boss"]["spanId"]
    assert by_name["invoke_agent boss/helper"]["parentSpanId"] == by_name["invoke_agent boss"]["spanId"]
    chat = by_name["chat llama-test"]
    attrs = {a["key"]: list(a["value"].values())[0] for a in chat["attributes"]}
    assert attrs["gen_ai.operation.name"] == "chat" and attrs["gen_ai.usage.input_tokens"] == "1000"
    assert int(chat["endTimeUnixNano"]) - int(chat["startTimeUnixNano"]) == 1_500_000_000  # = latency
    assert by_name["execute_tool helper"]["parentSpanId"] == by_name["invoke_agent boss"]["spanId"]
    assert len({s["traceId"] for s in spans}) == 1 and len({s["spanId"] for s in spans}) == len(spans)
    assert client.get(f"/graphs/{gid}/runs/{run_id}/trace").json() == trace  # stable ids: re-export is idempotent

    assert client.post(f"/graphs/{gid}/runs/{run_id}/trace/export").status_code == 503  # not configured
    received = []
    real_client = httpx.AsyncClient
    monkeypatch.setattr(get_settings(), "otel_exporter_otlp_endpoint", "http://collector.test/v1/traces")
    monkeypatch.setattr(get_settings(), "otel_exporter_otlp_headers", "Authorization=Bearer abc")
    monkeypatch.setattr(observability.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(
        lambda request: received.append(request) or httpx.Response(200, json={}))))
    result = client.post(f"/graphs/{gid}/runs/{run_id}/trace/export").json()
    assert result["exported_spans"] == len(spans) and received[0].headers["authorization"] == "Bearer abc"

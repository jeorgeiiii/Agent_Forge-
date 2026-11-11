"""Run events: what the live view will show, delivered with checkpoints."""

import json
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

import activities  # noqa: E402
from activities import checkpoint_state, emit_events, execute_node, llm_chat, load_memory, save_memory  # noqa: E402
from agentforge_core.compiler import compile_graph  # noqa: E402
from workflow import AgentWorkflow, RunGraphWorkflow  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
def api(monkeypatch):
    """Captures every checkpoint the workflow sends (state + events)."""
    checkpoints: list[dict] = []
    script: dict[str, list] = {}

    def model(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        message = script[body["messages"][0]["content"]].pop(0)
        return httpx.Response(200, json={"model": "llama-test", "choices": [{"message": message, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    async def fake_api(method, path, json=None, timeout=10):
        if path.endswith("/checkpoint"):
            checkpoints.append({**json, "_kind": "checkpoint"})
            return None
        if path.endswith("/events"):  # live, events-only delivery (sub-agents use their own source)
            checkpoints.append({"_kind": "events", "source": json["source"], "events": json["events"]})
            return None
        if path.startswith("/internal/credentials/"):
            return {"type": "api_key", "data": {"api_key": "k"}}
        raise AssertionError(path)

    async def public_ok(url):
        return None

    monkeypatch.setattr(activities, "_llm_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(model)))
    monkeypatch.setattr(activities.api_client, "call", fake_api)
    monkeypatch.setattr(activities, "validate_public_http_url", public_ok)
    monkeypatch.setattr(activities.api_client, "is_configured", lambda: True)
    return checkpoints, script


def delivered(checkpoints, source=""):
    """Events in arrival order, de-duplicated like the API does on (source, seq)."""
    seen, out = set(), []
    for c in checkpoints:
        src = c.get("source", "")
        for e in c.get("events", []):
            if (src, e["seq"]) in seen or (source is not None and src != source):
                continue
            seen.add((src, e["seq"]))
            out.append({**e, "source": src})
    return out


async def run(graph, run_input=None):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = f"test-{uuid.uuid4()}"
        async with Worker(env.client, task_queue=queue, workflows=[RunGraphWorkflow, AgentWorkflow],
                          activities=[execute_node, llm_chat, load_memory, save_memory, emit_events, checkpoint_state]):
            return await env.client.execute_workflow(
                RunGraphWorkflow.execute_graph_workflow,
                {"ir": compile_graph(graph), "input": run_input or {}, "run": {}},
                id=f"wf-{uuid.uuid4()}", task_queue=queue,
            )


def n(id, type, **config):
    return {"id": id, "type": type, "label": id.title(), "config": config}


async def test_branching_run_emits_ordered_node_events(api):
    checkpoints, _ = api
    graph = {
        "nodes": [n("start", "start"), n("check", "if", condition="{{ $json.ok }}"),
                  n("yes", "set", fields={"r": 1}), n("no", "set", fields={"r": 0}), n("end", "end")],
        "edges": [{"source": "start", "target": "check"}, {"source": "check", "target": "yes", "sourceHandle": "true"},
                  {"source": "check", "target": "no", "sourceHandle": "false"}, {"source": "yes", "target": "end"}],
    }
    await run(graph, {"ok": True})
    events = delivered(checkpoints)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)) and seqs[0] == 1  # each event exactly once, in order
    kinds = [(e["type"], e["node_id"]) for e in events]
    assert kinds[0] == ("run.started", None) and kinds[-1] == ("run.finished", None)
    assert ("node.skipped", "no") in kinds
    assert kinds.index(("node.started", "check")) < kinds.index(("node.finished", "check")) < kinds.index(("node.started", "yes"))
    finished = {e["node_id"]: e["data"] for e in events if e["type"] == "node.finished"}
    assert finished["yes"]["status"] == "success" and finished["yes"]["duration_ms"] >= 0
    assert events[-1]["data"]["status"] == "success"
    # nothing left behind: everything was delivered by the final checkpoint
    final = [c for c in checkpoints if c["_kind"] == "checkpoint"][-1]
    assert final["status"] == "success" and final["event_seq"] == seqs[-1]


async def test_agent_and_sub_agent_events_with_llm_trace(api):
    checkpoints, script = api
    tool_call = lambda name, args, cid: {"role": "assistant", "content": None, "tool_calls": [  # noqa: E731
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
    script["BOSS"] = [tool_call("helper", {"task": "do X"}, "c1"), {"role": "assistant", "content": "all done"}]
    script["HELPER"] = [{"role": "assistant", "content": "X done"}]
    helper = {"name": "helper", "description": "helps", "node": {"type": "agent", "config": {
        "credential_id": 1, "model": "llama-test", "system": "HELPER", "prompt": "{{ $args.task }}"}}}
    graph = {"nodes": [n("start", "start"),
                       n("boss", "agent", credential_id=1, model="llama-test", system="BOSS", prompt="go", tools=[helper]),
                       n("end", "end")],
             "edges": [{"source": "start", "target": "boss"}, {"source": "boss", "target": "end"}]}
    await run(graph)
    events = delivered(checkpoints, source=None)  # run + sub-agent events, in arrival order
    llm = [e for e in events if e["type"] == "llm.call"]
    assert [(e["node_id"], e["data"]["step"], e["data"]["depth"]) for e in llm] == [
        ("boss", 1, 1), ("boss/helper", 1, 2), ("boss", 2, 1)]  # sub-agent call appears in order, tagged with its path
    first = llm[0]["data"]
    assert first["model"] == "llama-test" and first["input_tokens"] == 100 and first["output_tokens"] == 20
    assert first["tool_calls"] == ["helper"] and first["prompt_preview"] == "go" and "latency_ms" in first
    tool = next(e for e in events if e["type"] == "tool.call")
    assert tool["data"]["tool"] == "helper" and tool["data"]["ok"] is True and tool["data"]["kind"] == "agent"
    sub = next(e for e in events if e["type"] == "subagent.finished")
    assert sub["data"]["stopped_reason"] == "final" and sub["data"]["usage"]["llm_calls"] == 1
    run_done = events[-1]
    assert run_done["type"] == "run.finished" and run_done["data"]["usage"]["llm_calls"] == 3


async def test_failed_run_still_reports_its_events(api):
    checkpoints, _ = api
    graph = {"nodes": [n("start", "start"), n("t", "template", template="{{ $json.missing.x }}"), n("end", "end")],
             "edges": [{"source": "start", "target": "t"}, {"source": "t", "target": "end"}]}
    with pytest.raises(Exception):
        await run(graph)
    events = delivered(checkpoints)
    failed = next(e for e in events if e["type"] == "node.finished" and e["node_id"] == "t")
    assert failed["data"]["status"] == "failed" and "missing" in failed["data"]["error"]
    assert events[-1]["type"] == "run.finished" and events[-1]["data"]["status"] == "failed"


async def test_events_arrive_live_not_at_the_end(api):
    """The supervisor's first LLM step must be delivered BEFORE its sub-agent starts working."""
    checkpoints, script = api
    script["BOSS"] = [{"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                       "function": {"name": "helper", "arguments": '{"task": "x"}'}}]}, {"role": "assistant", "content": "ok"}]
    script["HELPER"] = [{"role": "assistant", "content": "done"}]
    helper = {"name": "helper", "description": "h", "node": {"type": "agent", "config": {
        "credential_id": 1, "model": "llama-test", "system": "HELPER", "prompt": "{{ $args.task }}"}}}
    graph = {"nodes": [n("start", "start"),
                       n("boss", "agent", credential_id=1, model="llama-test", system="BOSS", prompt="go", tools=[helper]),
                       n("end", "end")],
             "edges": [{"source": "start", "target": "boss"}, {"source": "boss", "target": "end"}]}
    await run(graph)
    kinds = [c["_kind"] for c in checkpoints]
    first_boss_llm = next(i for i, c in enumerate(checkpoints)
                          if any(e["type"] == "llm.call" and e["node_id"] == "boss" for e in c.get("events", [])))
    first_helper = next(i for i, c in enumerate(checkpoints) if c.get("source"))
    assert first_boss_llm < first_helper < len(checkpoints) - 1  # boss step 1 shown before the helper's events
    assert kinds[first_boss_llm] == "events" and checkpoints[first_helper]["source"].endswith("/helper-c1")


async def test_retry_checkpoint_skips_finished_nodes_and_reuses_their_output(api):
    checkpoints, _ = api
    graph = {"nodes": [n("start", "start"), n("a", "set", fields={"v": "{{ $json.x }}"}),
                       n("b", "template", template="b got {{ $node.a.json.v }}"), n("end", "end")],
             "edges": [{"source": "start", "target": "a"}, {"source": "a", "target": "b"}, {"source": "b", "target": "end"}]}
    # what the API's retry_checkpoint() sends: finished nodes kept, failed node 'b' dropped
    checkpoint = {"status": "running", "superstep": 2,
                  "nodes": {"start": {"status": "success", "outputs": {"main": {"x": 41}}},
                            "a": {"status": "success", "outputs": {"main": {"v": "from-the-failed-run"}}}}}
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = f"test-{uuid.uuid4()}"
        async with Worker(env.client, task_queue=queue, workflows=[RunGraphWorkflow, AgentWorkflow],
                          activities=[execute_node, llm_chat, load_memory, save_memory, emit_events, checkpoint_state]):
            result = await env.client.execute_workflow(
                RunGraphWorkflow.execute_graph_workflow,
                {"ir": compile_graph(graph), "input": {"x": 1}, "run": {}, "checkpoint": checkpoint},
                id=f"wf-{uuid.uuid4()}", task_queue=queue)
    assert result["output"] == "b got from-the-failed-run"  # 'a' was NOT re-executed; its saved output was used
    events = delivered(checkpoints)
    started = [e["node_id"] for e in events if e["type"] == "node.started"]
    assert started == ["b", "end"] and events[0]["data"]["resumed"] is True

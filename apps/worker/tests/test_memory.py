"""Agent memory: conversation history carried across runs of the same chat session."""

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
def world(monkeypatch):
    """Fake model (echoes how many messages it saw) + fake API memory store."""
    store: dict[str, list] = {}
    requests: list[dict] = []
    flaky = {"memory_down": False}

    def model(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        last = body["messages"][-1]["content"]
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"role": "assistant", "content": f"reply to: {last}"},
                                                                    "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    async def fake_api(method, path, json=None, timeout=10):
        if path.startswith("/internal/credentials/"):
            return {"type": "api_key", "data": {"api_key": "sk-test"}}
        if path.startswith("/internal/memory/"):
            if flaky["memory_down"]:
                raise activities.api_client.InternalApiError("API down")
            key, _, query = path[len("/internal/memory/"):].partition("?")
            if method == "GET":
                limit = int(query.split("=")[1])
                return {"messages": store.get(key, [])[-limit:]}
            store.setdefault(key, []).extend(json["messages"])
            return {"added": len(json["messages"])}
        if path.startswith("/internal/runs/"):
            return None
        raise AssertionError(path)

    async def public_ok(url):
        return None

    monkeypatch.setattr(activities, "_llm_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(model)))
    monkeypatch.setattr(activities.api_client, "call", fake_api)
    monkeypatch.setattr(activities, "validate_public_http_url", public_ok)
    return store, requests, flaky


def chat_graph(memory, graph_tools=None):
    return {
        "nodes": [
            {"id": "start", "type": "chat_trigger", "config": {}},
            {"id": "bot", "type": "agent", "label": "Bot", "config": {
                "credential_id": 1, "model": "m", "system": "SYS", "prompt": "{{ $json.message }}",
                "memory": memory, "tools": graph_tools or []}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"source": "start", "target": "bot"}, {"source": "bot", "target": "end"}],
    }


async def chat(env, queue, graph, message, session="s1", graph_id=7):
    handle = await env.client.start_workflow(
        RunGraphWorkflow.execute_graph_workflow,
        {"ir": compile_graph(graph), "input": {"message": message, "session_id": session}, "run": {"graph_id": graph_id}},
        id=f"wf-{uuid.uuid4()}", task_queue=queue,
    )
    return (await handle.result())["output"]


async def with_worker(test):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = f"test-{uuid.uuid4()}"
        async with Worker(env.client, task_queue=queue, workflows=[RunGraphWorkflow, AgentWorkflow],
                          activities=[execute_node, llm_chat, load_memory, save_memory, emit_events, checkpoint_state]):
            await test(env, queue)


async def test_agent_remembers_earlier_messages_in_the_same_session(world):
    store, requests, _ = world
    graph = chat_graph({"type": "buffer"})

    async def scenario(env, queue):
        first = await chat(env, queue, graph, "My name is Ada.")
        assert first["memory"] == {"loaded_messages": 0}
        second = await chat(env, queue, graph, "What is my name?")
        assert second["memory"] == {"loaded_messages": 2}

    await with_worker(scenario)
    # second LLM call saw: system, the first exchange, then the new question
    assert [m["role"] for m in requests[1]["messages"]] == ["system", "user", "assistant", "user"]
    assert requests[1]["messages"][1]["content"] == "My name is Ada."
    assert requests[1]["messages"][2]["content"] == "reply to: My name is Ada."
    (key, history), = store.items()
    assert key.startswith("mem:") and len(history) == 4


async def test_sessions_and_graphs_are_isolated(world):
    store, requests, _ = world
    graph = chat_graph({"type": "buffer"})

    async def scenario(env, queue):
        await chat(env, queue, graph, "secret for s1", session="s1")
        other_session = await chat(env, queue, graph, "hi", session="s2")
        other_graph = await chat(env, queue, graph, "hi", session="s1", graph_id=8)
        assert other_session["memory"]["loaded_messages"] == 0
        assert other_graph["memory"]["loaded_messages"] == 0

    await with_worker(scenario)
    assert len(store) == 3  # three separate conversations


async def test_window_keeps_only_the_last_n_messages(world):
    _, requests, _ = world
    graph = chat_graph({"type": "window", "window": 2})

    async def scenario(env, queue):
        for i in range(3):
            await chat(env, queue, graph, f"msg {i}")

    await with_worker(scenario)
    third = requests[2]["messages"]
    assert [m["content"] for m in third] == ["SYS", "msg 1", "reply to: msg 1", "msg 2"]  # only last exchange


async def test_memory_off_by_default_and_missing_session_is_handled(world):
    store, requests, _ = world

    async def scenario(env, queue):
        out = await chat(env, queue, chat_graph(None), "hello")
        assert "memory" not in out
        no_session = chat_graph({"type": "buffer", "session_id": "{{ $trigger.nope }}"})
        out = await chat(env, queue, no_session, "hello")
        assert out["memory"]["note"].startswith("memory skipped")

    await with_worker(scenario)
    assert store == {}


async def test_memory_outage_does_not_fail_the_run(world):
    _, _, flaky = world
    flaky["memory_down"] = True

    async def scenario(env, queue):
        out = await chat(env, queue, chat_graph({"type": "buffer"}), "hello")
        assert out["text"] == "reply to: hello"
        assert "memory" in out and "unavailable" in out["memory"]["note"] or "not saved" in out["memory"]["note"]

    await with_worker(scenario)

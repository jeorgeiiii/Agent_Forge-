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
from activities import (checkpoint_state, emit_events, execute_node, llm_chat, load_memory,  # noqa: E402
                        save_memory, save_summary)
from agentforge_core.compiler import compile_graph  # noqa: E402
from workflow import AgentWorkflow, RunGraphWorkflow  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
def world(monkeypatch):
    """Fake model (echoes how many messages it saw) + fake API memory store."""
    store: dict[str, list] = {}       # key -> [{"id", "role", "content"}]
    summaries: dict[str, dict] = {}   # key -> {"content", "covers_until"}
    metas: dict[str, dict] = {}
    requests: list[dict] = []
    flaky = {"memory_down": False}
    ids = {"next": 1}

    def model(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "maintain the memory" in body["messages"][0]["content"]:  # the summarisation call
            reply = "SUMMARY: user is Ada, likes teal, lives in Pune"
        else:
            reply = f"reply to: {body['messages'][-1]['content']}"
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"role": "assistant", "content": reply},
                                                                    "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    async def fake_api(method, path, json=None, timeout=10):
        if path.startswith("/internal/credentials/"):
            return {"type": "api_key", "data": {"api_key": "sk-test"}}
        if path.startswith("/internal/memory/"):
            if flaky["memory_down"]:
                raise activities.api_client.InternalApiError("API down")
            key, _, query = path[len("/internal/memory/"):].partition("?")
            if key.endswith("/summary"):
                key = key[: -len("/summary")]
                summaries[key] = {"content": json["content"], "covers_until": json["covers_until"]}
                return {"ok": True}
            if method == "GET":
                limit = int(query.split("=")[1])
                covers = summaries.get(key, {}).get("covers_until", 0)
                tail = [m for m in store.get(key, []) if m["id"] > covers][-limit:]
                return {"summary": summaries.get(key, {}).get("content"), "covers_until": covers, "messages": tail}
            for m in json["messages"]:
                store.setdefault(key, []).append({"id": ids["next"], **m})
                ids["next"] += 1
            metas[key] = json.get("meta")
            return {"added": len(json["messages"])}
        if path.startswith("/internal/runs/"):
            return None
        raise AssertionError(path)

    async def public_ok(url):
        return None

    monkeypatch.setattr(activities, "_llm_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(model)))
    monkeypatch.setattr(activities.api_client, "call", fake_api)
    monkeypatch.setattr(activities, "validate_public_http_url", public_ok)
    return store, requests, flaky, summaries, metas


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
                          activities=[execute_node, llm_chat, load_memory, save_memory, save_summary, emit_events, checkpoint_state]):
            await test(env, queue)


async def test_agent_remembers_earlier_messages_in_the_same_session(world):
    store, requests, *_ = world
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
    store, requests, *_ = world
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
    _, requests, *_ = world
    graph = chat_graph({"type": "window", "window": 2})

    async def scenario(env, queue):
        for i in range(3):
            await chat(env, queue, graph, f"msg {i}")

    await with_worker(scenario)
    third = requests[2]["messages"]
    assert [m["content"] for m in third] == ["SYS", "msg 1", "reply to: msg 1", "msg 2"]  # only last exchange


async def test_memory_off_by_default_and_missing_session_is_handled(world):
    store, requests, *_ = world

    async def scenario(env, queue):
        out = await chat(env, queue, chat_graph(None), "hello")
        assert "memory" not in out
        no_session = chat_graph({"type": "buffer", "session_id": "{{ $trigger.nope }}"})
        out = await chat(env, queue, no_session, "hello")
        assert out["memory"]["note"].startswith("memory skipped")

    await with_worker(scenario)
    assert store == {}


async def test_memory_outage_does_not_fail_the_run(world):
    _, _, flaky, *_ = world
    flaky["memory_down"] = True

    async def scenario(env, queue):
        out = await chat(env, queue, chat_graph({"type": "buffer"}), "hello")
        assert out["text"] == "reply to: hello"
        assert "memory" in out and "unavailable" in out["memory"]["note"] or "not saved" in out["memory"]["note"]

    await with_worker(scenario)


async def test_token_budget_trims_oldest_messages(world):
    _, requests, *_ = world
    graph = chat_graph({"type": "buffer", "max_tokens": 100})

    async def scenario(env, queue):
        await chat(env, queue, graph, "x" * 200)      # ~54 tokens each way: the pair exceeds the budget
        out = await chat(env, queue, graph, "short")
        assert out["memory"]["trimmed_messages"] >= 1

    await with_worker(scenario)
    sent = requests[1]["messages"]
    assert sent[0]["role"] == "system" and sent[-1]["content"] == "short"
    assert sent[1]["role"] != "assistant"  # history never starts with an orphan assistant reply


async def test_summary_memory_folds_old_messages_and_keeps_recent(world):
    _, requests, _, summaries, metas = world
    graph = chat_graph({"type": "summary", "max_tokens": 120, "keep_recent": 2})
    outputs = []

    async def scenario(env, queue):
        outputs.append(await chat(env, queue, graph, "My name is Ada and I like teal. " * 3))
        outputs.append(await chat(env, queue, graph, "I live in Pune and work on robots. " * 3))
        outputs.append(await chat(env, queue, graph, "What do you know about me?"))

    await with_worker(scenario)
    (key, summary), = summaries.items()
    assert summary["content"].startswith("SUMMARY") and summary["covers_until"] >= 2
    assert any(o["memory"].get("summarized_messages") for o in outputs)
    answer_calls = [r for r in requests if "maintain the memory" not in r["messages"][0]["content"]]
    last = answer_calls[-1]["messages"]
    assert any(m["role"] == "system" and "Summary of the earlier conversation" in m["content"] for m in last)
    assert metas[key] == {"graph_id": 7, "scope": "bot", "session_id": "s1"}


async def test_pii_is_redacted_before_saving(world):
    store, *_ = world
    graph = chat_graph({"type": "buffer", "redact_pii": True})

    async def scenario(env, queue):
        await chat(env, queue, graph, "mail me at ada@example.com or call +91 98765 43210, card 4111 1111 1111 1111")

    await with_worker(scenario)
    (history,) = store.values()
    saved = history[0]["content"]
    assert "ada@example.com" not in saved and "4111" not in saved and "98765" not in saved
    assert "[email]" in saved and "[card]" in saved and "[phone]" in saved

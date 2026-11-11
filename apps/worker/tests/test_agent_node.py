"""Agent node: tool-calling loop against a scripted fake OpenAI-compatible model."""

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
from activities import checkpoint_state, execute_node, llm_chat  # noqa: E402
from agentforge_core.compiler import GraphValidationError, compile_graph  # noqa: E402
from workflow import RunGraphWorkflow  # noqa: E402

pytestmark = pytest.mark.asyncio


def tool_call(name, args, call_id="call_1"):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def final(text):
    return {"role": "assistant", "content": text}


@pytest.fixture
def model(monkeypatch):
    """Replies from a script, one per LLM call; records every request body."""
    script: list[dict] = []
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        message = script.pop(0) if script else final("(script exhausted)")
        return httpx.Response(200, json={
            "model": body["model"],
            "choices": [{"message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    async def fake_credential(method, path, json=None, timeout=10):
        return {"type": "api_key", "data": {"api_key": "sk-test"}}

    async def public_ok(url):
        return None

    monkeypatch.setattr(activities, "_llm_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(activities.api_client, "call", fake_credential)
    monkeypatch.setattr(activities, "validate_public_http_url", public_ok)
    return script, requests


WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Current weather for a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    "node": {"type": "template", "config": {"template": "{{ $args.city }}: 31C and sunny"}},
}


def agent_graph(**overrides):
    config = {"credential_id": 1, "model": "llama-3.3-70b", "system": "You are a travel helper.",
              "prompt": "Should I pack an umbrella for {{ $json.city }}?", "tools": [WEATHER_TOOL], **overrides}
    return {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "agent", "type": "agent", "label": "Travel Agent", "config": config},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"source": "start", "target": "agent"}, {"source": "agent", "target": "end"}],
    }


async def run(graph, run_input):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = f"test-{uuid.uuid4()}"
        async with Worker(env.client, task_queue=queue, workflows=[RunGraphWorkflow],
                          activities=[execute_node, llm_chat, checkpoint_state]):
            handle = await env.client.start_workflow(
                RunGraphWorkflow.execute_graph_workflow,
                {"ir": compile_graph(graph), "input": run_input, "run": {}},
                id=f"wf-{uuid.uuid4()}", task_queue=queue,
            )
            result = await handle.result()
            return result, await handle.query("state")


async def test_agent_calls_tool_then_answers(model):
    script, requests = model
    script += [tool_call("get_weather", {"city": "Delhi"}), final("No umbrella needed - it's sunny.")]

    result, state = await run(agent_graph(), {"city": "Delhi"})

    out = result["output"]
    assert out["text"] == "No umbrella needed - it's sunny."
    assert out["stopped_reason"] == "final" and out["steps"] == 2
    assert out["usage"] == {"input_tokens": 200, "output_tokens": 40, "llm_calls": 2}
    assert result["usage"] == out["usage"]  # rolled up to the run

    first, second = requests
    assert first["messages"][0] == {"role": "system", "content": "You are a travel helper."}
    assert first["messages"][1]["content"] == "Should I pack an umbrella for Delhi?"
    assert first["tools"][0]["function"]["name"] == "get_weather"
    tool_msg = second["messages"][-1]
    assert tool_msg == {"role": "tool", "tool_call_id": "call_1", "content": json.dumps("Delhi: 31C and sunny")}
    assert state["nodes"]["agent"]["agent"]["steps"][0]["tool_calls"] == ["get_weather"]


async def test_tool_errors_go_back_to_the_model_not_the_run(model):
    script, requests = model
    script += [tool_call("teleport", {}), final("Sorry, I can't do that.")]
    result, _ = await run(agent_graph(), {"city": "Delhi"})
    assert result["status"] == "success"
    assert "Unknown tool 'teleport'" in requests[1]["messages"][-1]["content"]


async def test_max_steps_stops_a_looping_agent(model):
    script, _ = model
    script += [tool_call("get_weather", {"city": "X"}, f"c{i}") for i in range(10)]
    result, _ = await run(agent_graph(max_steps=3), {"city": "X"})
    assert result["output"]["stopped_reason"] == "max_steps" and result["output"]["steps"] == 3


async def test_token_budget_stops_the_agent(model):
    script, _ = model
    script += [tool_call("get_weather", {"city": "X"}, f"c{i}") for i in range(10)]
    result, _ = await run(agent_graph(max_total_tokens=200), {"city": "X"})  # 120 tokens per call
    assert result["output"]["stopped_reason"] == "token_budget" and result["output"]["steps"] == 2


async def test_parallel_tool_calls_run_together(model):
    script, requests = model
    both = {"role": "assistant", "content": None, "tool_calls": [
        tool_call("get_weather", {"city": "Delhi"}, "a")["tool_calls"][0],
        tool_call("get_weather", {"city": "Pune"}, "b")["tool_calls"][0],
    ]}
    script += [both, final("Both sunny.")]
    await run(agent_graph(), {"city": "Delhi"})
    replies = [m for m in requests[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in replies] == ["a", "b"]
    assert "Pune: 31C" in replies[1]["content"]


async def test_agent_config_is_validated_at_compile_time():
    dup = agent_graph(tools=[WEATHER_TOOL, WEATHER_TOOL])
    with pytest.raises(GraphValidationError, match="Duplicate tool name"):
        compile_graph(dup)
    bad_type = agent_graph(tools=[{**WEATHER_TOOL, "node": {"type": "webhook_trigger"}}])
    with pytest.raises(GraphValidationError, match="must be one of"):
        compile_graph(bad_type)
    bad_tool_config = agent_graph(tools=[{**WEATHER_TOOL, "node": {"type": "http", "config": {}}}])
    with pytest.raises(GraphValidationError, match="url is required"):
        compile_graph(bad_tool_config)

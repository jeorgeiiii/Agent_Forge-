"""Agent-as-tool: a supervisor delegates to worker agents running as child workflows."""

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
from workflow import AgentWorkflow, RunGraphWorkflow  # noqa: E402

pytestmark = pytest.mark.asyncio


def call(name, args, call_id):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def final(text):
    return {"role": "assistant", "content": text}


@pytest.fixture
def models(monkeypatch):
    """One script per agent, chosen by the agent's system prompt. Records each agent's requests."""
    scripts: dict[str, list] = {}
    seen: dict[str, list] = {}
    fail_for: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        who = body["messages"][0]["content"]  # system prompt identifies the agent
        seen.setdefault(who, []).append(body)
        if who in fail_for:
            return httpx.Response(401, json={"error": "bad key"})
        message = scripts[who].pop(0)
        return httpx.Response(200, json={
            "model": body["model"], "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    async def fake_credential(method, path, json=None, timeout=10):
        return {"type": "api_key", "data": {"api_key": "sk-test"}}

    async def public_ok(url):
        return None

    monkeypatch.setattr(activities, "_llm_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(activities.api_client, "call", fake_credential)
    monkeypatch.setattr(activities, "validate_public_http_url", public_ok)
    return scripts, seen, fail_for


def agent_cfg(system, prompt="{{ $args.task }}", tools=None):
    return {"credential_id": 1, "model": "m", "system": system, "prompt": prompt, "tools": tools or []}


FACT_TOOL = {"name": "get_fact", "description": "Look up a fact.",
             "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}},
             "node": {"type": "template", "config": {"template": "{{ $args.topic }} is the capital of India."}}}

RESEARCHER = {"name": "researcher", "description": "Finds facts. Give it a research task.",
              "node": {"type": "agent", "config": agent_cfg("RESEARCHER", tools=[FACT_TOOL])}}
WRITER = {"name": "writer", "description": "Writes polished text from notes.",
          "node": {"type": "agent", "config": agent_cfg("WRITER")}}


def team_graph(tools):
    return {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "boss", "type": "agent", "label": "Supervisor",
             "config": agent_cfg("SUPERVISOR", prompt="Write one line about {{ $json.city }}.", tools=tools)},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"source": "start", "target": "boss"}, {"source": "boss", "target": "end"}],
    }


async def run(graph, run_input):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = f"test-{uuid.uuid4()}"
        async with Worker(env.client, task_queue=queue, workflows=[RunGraphWorkflow, AgentWorkflow],
                          activities=[execute_node, llm_chat, checkpoint_state]):
            handle = await env.client.start_workflow(
                RunGraphWorkflow.execute_graph_workflow,
                {"ir": compile_graph(graph), "input": run_input, "run": {}},
                id=f"wf-{uuid.uuid4()}", task_queue=queue,
            )
            return await handle.result(), await handle.query("state")


async def test_supervisor_delegates_to_researcher_and_writer(models):
    scripts, seen, _ = models
    scripts["SUPERVISOR"] = [
        call("researcher", {"task": "Find a fact about Delhi"}, "r1"),
        call("writer", {"task": "Write one line from: Delhi is the capital of India."}, "w1"),
        final("Delhi: the beating heart of India's capital."),
    ]
    scripts["RESEARCHER"] = [call("get_fact", {"topic": "Delhi"}, "f1"), final("Delhi is the capital of India.")]
    scripts["WRITER"] = [final("Delhi: the beating heart of India's capital.")]

    result, state = await run(team_graph([RESEARCHER, WRITER]), {"city": "Delhi"})

    out = result["output"]
    assert out["text"] == "Delhi: the beating heart of India's capital." and out["stopped_reason"] == "final"
    # 3 supervisor + 2 researcher + 1 writer LLM calls, all rolled up to the agent node and the run
    assert out["usage"] == {"input_tokens": 60, "output_tokens": 30, "llm_calls": 6}
    assert result["usage"] == out["usage"]
    # the sub-agent's prompt was the task the supervisor gave it ({{ $args.task }})
    assert seen["RESEARCHER"][0]["messages"][1]["content"] == "Find a fact about Delhi"
    # the researcher used its own tool, and its answer came back to the supervisor
    assert json.loads(seen["RESEARCHER"][1]["messages"][-1]["content"]) == "Delhi is the capital of India."
    assert json.loads(seen["SUPERVISOR"][1]["messages"][-1]["content"]) == {"text": "Delhi is the capital of India."}
    # sub-agent tools get a default {"task": ...} schema
    params = seen["SUPERVISOR"][0]["tools"][0]["function"]["parameters"]
    assert params["required"] == ["task"]
    # trace shows who delegated to whom
    steps = state["nodes"]["boss"]["agent"]["steps"]
    assert steps[0]["sub_agents"][0]["tool"] == "researcher" and steps[0]["sub_agents"][0]["steps"] == 2
    assert steps[1]["sub_agents"][0]["tool"] == "writer"


async def test_parallel_delegation(models):
    scripts, seen, _ = models
    both = {"role": "assistant", "content": None, "tool_calls": [
        call("researcher", {"task": "fact A"}, "a")["tool_calls"][0],
        call("writer", {"task": "draft B"}, "b")["tool_calls"][0],
    ]}
    scripts["SUPERVISOR"] = [both, final("merged")]
    scripts["RESEARCHER"] = [final("A done")]
    scripts["WRITER"] = [final("B done")]
    result, _ = await run(team_graph([RESEARCHER, WRITER]), {"city": "X"})
    replies = [m for m in seen["SUPERVISOR"][1]["messages"] if m["role"] == "tool"]
    assert [json.loads(m["content"])["text"] for m in replies] == ["A done", "B done"]
    assert result["output"]["usage"]["llm_calls"] == 4


async def test_failing_sub_agent_is_reported_to_supervisor_not_fatal(models):
    scripts, seen, fail_for = models
    fail_for.add("WRITER")  # provider rejects the writer (e.g. bad key)
    scripts["SUPERVISOR"] = [call("writer", {"task": "x"}, "w1"), final("I could not get the writer to help.")]
    result, _ = await run(team_graph([WRITER]), {"city": "X"})
    assert result["status"] == "success"
    error = json.loads(seen["SUPERVISOR"][1]["messages"][-1]["content"])["error"]
    assert "401" in error


async def test_nesting_limit_is_checked_before_running():
    def nest(level):
        if level == 0:
            return agent_cfg("LEAF")
        return agent_cfg(f"L{level}", tools=[{"name": f"sub{level}", "description": "d", "node": {"type": "agent", "config": nest(level - 1)}}])

    compile_graph(team_graph([{"name": "a", "description": "d", "node": {"type": "agent", "config": nest(1)}}]))  # depth 3: ok
    too_deep = team_graph([{"name": "a", "description": "d", "node": {"type": "agent", "config": nest(2)}}])  # depth 4
    with pytest.raises(GraphValidationError, match="nested 4 levels"):
        compile_graph(too_deep)
    bad_sub = team_graph([{"name": "a", "description": "d", "node": {"type": "agent", "config": {"model": "m"}}}])
    with pytest.raises(GraphValidationError, match="prompt is required"):
        compile_graph(bad_sub)

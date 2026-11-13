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
from activities import checkpoint_state, emit_events, execute_node, llm_chat  # noqa: E402
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
                          activities=[execute_node, llm_chat, emit_events, checkpoint_state]):
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


async def test_max_steps_stops_a_looping_agent_but_still_answers(model):
    script, requests = model
    script += [tool_call("get_weather", {"city": "X"}, f"c{i}") for i in range(3)] + [final("Best answer so far: sunny.")]
    result, _ = await run(agent_graph(max_steps=3), {"city": "X"})
    out = result["output"]
    assert out["stopped_reason"] == "max_steps_final_answer" and out["steps"] == 3
    assert out["text"] == "Best answer so far: sunny."  # never an empty answer after a long investigation
    assert "tools" not in requests[-1] and "used all your tool steps" in requests[-1]["messages"][-1]["content"]


async def test_old_tool_results_are_shortened_but_recent_ones_kept():
    from workflow import KEEP_FULL_TOOL_RESULTS, OLD_TOOL_RESULT_CHARS, compact_tool_results

    big = "x" * 5000
    messages = [{"role": "user", "content": "q"}] + [{"role": "tool", "tool_call_id": str(i), "content": big}
                                                       for i in range(5)]
    compacted = compact_tool_results(messages)
    lengths = [len(m["content"]) for m in compacted[1:]]
    assert all(n < OLD_TOOL_RESULT_CHARS + 80 for n in lengths[:-KEEP_FULL_TOOL_RESULTS])
    assert lengths[-KEEP_FULL_TOOL_RESULTS:] == [5000] * KEEP_FULL_TOOL_RESULTS
    assert messages[1]["content"] == big  # the agent's own memory is untouched; only the request is smaller


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
    with pytest.raises(GraphValidationError, match="can.t be used as a tool"):
        compile_graph(bad_type)
    bad_tool_config = agent_graph(tools=[{**WEATHER_TOOL, "node": {"type": "http", "config": {}}}])
    with pytest.raises(GraphValidationError, match="url is required"):
        compile_graph(bad_tool_config)


# ------------------------------------------------------------------ integrations as agent tools

@pytest.fixture
def services(monkeypatch):
    """Fake third-party APIs (Wikipedia, Slack ...) used by integration tools."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "json": json.loads(request.content) if request.content else None,
                      "auth": request.headers.get("authorization")})
        if "wikipedia" in str(request.url):
            return httpx.Response(200, json={"title": "Alan Turing", "extract": "English mathematician."})
        return httpx.Response(200, json={"ok": True, "ts": "1.1"})

    monkeypatch.setattr(activities, "_connector_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return calls


WIKI_TOOL = {"name": "wiki", "node": {"type": "wikipedia", "config": {"action": "summary"}}}


async def test_agent_uses_an_integration_tool_and_cannot_change_fixed_settings(model, services):
    script, requests = model
    # the model also tries to switch the action and pass a key: both must be ignored
    script += [tool_call("wiki", {"title": "Alan_Turing", "action": "search", "credential_id": 99}),
               final("Alan Turing was an English mathematician.")]
    result, _ = await run(agent_graph(tools=[WIKI_TOOL], prompt="Who was Alan Turing?"), {})

    tool_def = requests[0]["tools"][0]["function"]
    assert tool_def["name"] == "wiki" and tool_def["description"].startswith("Wikipedia: Article summary")
    assert tool_def["parameters"]["required"] == ["title"] and set(tool_def["parameters"]["properties"]) == {"title"}
    assert services[0]["url"] == "https://en.wikipedia.org/api/rest_v1/page/summary/Alan_Turing"  # still "summary"
    assert "English mathematician." in requests[1]["messages"][-1]["content"]
    assert result["output"]["text"] == "Alan Turing was an English mathematician."


async def test_sending_tools_need_allow_writes_and_fixed_fields_win(model, services):
    slack = {"name": "notify", "node": {"type": "slack", "config": {"action": "send_message", "credential_id": 4,
                                                                    "channel": "C-FIXED"}}}
    with pytest.raises(GraphValidationError, match="Allow tools that send or change things"):
        compile_graph(agent_graph(tools=[slack]))

    script, requests = model
    script += [tool_call("notify", {"channel": "C-ATTACKER", "text": "Deploy finished"}), final("Sent.")]
    result, _ = await run(agent_graph(tools=[slack], allow_writes=True, prompt="Tell the team"), {})
    assert set(requests[0]["tools"][0]["function"]["parameters"]["properties"]) == {"text", "thread_ts"}
    assert services[0]["json"] == {"channel": "C-FIXED", "text": "Deploy finished"}  # the AI can't redirect it
    assert services[0]["auth"] == "Bearer sk-test"
    assert result["output"]["text"] == "Sent."


async def test_integration_tool_needs_an_action_and_a_key():
    with pytest.raises(GraphValidationError, match="choose which Slack action"):
        compile_graph(agent_graph(tools=[{"name": "s", "node": {"type": "slack", "config": {}}}]))
    with pytest.raises(GraphValidationError, match="pick the saved GitHub key"):
        compile_graph(agent_graph(tools=[{"name": "g", "node": {"type": "github", "config": {"action": "get_repo"}}}]))


# ------------------------------------------------------------------ company database as an agent tool

@pytest.fixture
def company_api(monkeypatch):
    """Fake API: the vault (AI key) + the company-database endpoint."""
    calls: list[tuple[str, dict]] = []

    async def fake_call(method, path, json=None, timeout=10):
        if path.startswith("/internal/credentials/"):
            return {"type": "api_key", "data": {"api_key": "sk-test"}}
        calls.append((path, json))
        if json.get("action") == "list_tables":
            return {"database": "Bank", "tables": [{"name": "loans", "columns": ["customer_id", "status"]}]}
        return {"columns": ["status"], "rows": [{"status": "rejected"}], "row_count": 1, "truncated": False}

    monkeypatch.setattr(activities.api_client, "call", fake_call)
    return calls


async def test_agent_reads_the_company_database_with_its_own_sql(model, company_api):
    script, requests = model
    tables = {"name": "tables", "node": {"type": "database", "config": {"data_source_id": 7, "action": "list_tables"}}}
    query = {"name": "query", "node": {"type": "database", "config": {"data_source_id": 7, "action": "query"}}}
    script += [tool_call("tables", {}, "c1"),
               tool_call("query", {"sql": "SELECT status FROM loans WHERE customer_id = 1",
                                   "data_source_id": 99, "action": "list_tables"}, "c2"),  # tries to switch: ignored
               final("Customer 1's loan was rejected.")]
    result, _ = await run(agent_graph(tools=[tables, query], prompt="Why was customer 1's loan rejected?"), {})

    defs = {t["function"]["name"]: t["function"] for t in requests[0]["tools"]}
    assert defs["tables"]["description"].startswith("Company database: list the tables")
    assert defs["query"]["parameters"]["required"] == ["sql"]
    (first_path, first_body), (second_path, second_body) = company_api
    assert first_path.startswith("/internal/data-sources/7/run?workflow_id=") and first_body == {"action": "list_tables"}
    assert second_path.startswith("/internal/data-sources/7/run")  # still database 7
    assert second_body == {"action": "query", "sql": "SELECT status FROM loans WHERE customer_id = 1"}
    assert "rejected" in requests[2]["messages"][-1]["content"]
    assert result["output"]["text"] == "Customer 1's loan was rejected."


async def test_database_tool_needs_a_database_and_action():
    with pytest.raises(GraphValidationError, match="pick the company database"):
        compile_graph(agent_graph(tools=[{"name": "q", "node": {"type": "database", "config": {"action": "query"}}}]))


async def test_requests_are_fitted_to_the_ai_plan_limit():
    from workflow import _request_tokens, fit_request

    messages = [{"role": "system", "content": "rules " * 300}] + [
        {"role": "tool", "tool_call_id": str(i), "content": "row," * 3000} for i in range(6)]
    tools = [{"type": "function", "function": {"name": "q", "parameters": {}}}]
    request, max_tokens = fit_request(messages, tools, 8000, 4000)
    assert _request_tokens(request, tools) + max_tokens <= 8000  # input + reserved answer fit the plan
    assert 256 <= max_tokens <= 4000
    unlimited, wanted = fit_request(messages, tools, None, 4000)
    assert wanted == 4000 and len(unlimited) == len(messages)


async def test_data_dictionary_result_is_never_shortened():
    from workflow import compact_tool_results

    big = "d" * 5000
    messages = [{"role": "tool", "tool_call_id": "dict", "content": big}] + [
        {"role": "tool", "tool_call_id": str(i), "content": "x" * 5000} for i in range(6)]
    compacted = compact_tool_results(messages, keep_full=1, old_chars=100, pinned={"dict"})
    assert compacted[0]["content"] == big                     # the company's data dictionary stays in full
    assert len(compacted[1]["content"]) < 200                  # other old results are shortened


async def test_answer_cut_off_by_the_token_limit_gets_a_second_chance(monkeypatch):
    """Reasoning models can spend all of max_tokens 'thinking' and return empty text (finish_reason=length)."""
    import activities

    replies = [{"role": "assistant", "content": ""}, {"role": "assistant", "content": "Here is the full answer."}]
    finishes = ["length", "stop"]
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"model": body["model"], "choices": [
            {"message": replies.pop(0), "finish_reason": finishes.pop(0)}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    async def fake_credential(method, path, json=None, timeout=10):
        return {"type": "api_key", "data": {"api_key": "sk-test"}}

    async def public_ok(url):
        return None

    monkeypatch.setattr(activities, "_llm_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(activities.api_client, "call", fake_credential)
    monkeypatch.setattr(activities, "validate_public_http_url", public_ok)
    result, _ = await run(agent_graph(), {"city": "X"})
    assert result["output"]["text"] == "Here is the full answer."
    assert result["output"]["stopped_reason"] == "final_after_cut_off"
    assert "tools" not in requests[1]  # the retry asks for the answer only

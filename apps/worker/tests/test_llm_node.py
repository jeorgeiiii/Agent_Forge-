"""LLM node tests against a fake OpenAI-compatible provider (no network, no API key)."""

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
from activities import checkpoint_state, execute_node  # noqa: E402
from agentforge_core.compiler import compile_graph  # noqa: E402
from workflow import RunGraphWorkflow  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
def provider(monkeypatch):
    """Fake provider: records requests, replies with a scripted response."""
    calls: list[dict] = []
    state = {"status": 200, "content": "Hello Ada!"}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "auth": request.headers.get("authorization"),
                      "body": json.loads(request.content)})
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": "nope"})
        return httpx.Response(200, json={
            "model": "llama-3.3-70b",
            "choices": [{"message": {"role": "assistant", "content": state["content"]}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5},
        })

    async def fake_credential(method, path, json=None, timeout=10):
        assert path.split("?")[0] == "/internal/credentials/7"
        return {"type": "api_key", "data": {"api_key": "sk-test", "base_url": "https://api.groq.com/openai/v1"}}

    async def public_ok(url):
        return None

    monkeypatch.setattr(activities, "_llm_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(activities.api_client, "call", fake_credential)
    monkeypatch.setattr(activities, "validate_public_http_url", public_ok)
    return calls, state


def llm_request(**config):
    node = {"id": "ask", "type": "llm", "label": "Ask", "config": {"credential_id": 7, "model": "llama-3.3-70b", **config}}
    return {"node": node, "input": {"name": "Ada"}, "run_input": {"name": "Ada"}}


async def test_llm_call_renders_prompt_and_reports_usage(provider):
    calls, _ = provider
    result = await execute_node(llm_request(prompt="Greet {{ $json.name }}", system="Be brief.", temperature=0.2))
    out = result["outputs"]["main"]
    assert out["text"] == "Hello Ada!" and out["finish_reason"] == "stop"
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 5, "llm_calls": 1}
    sent = calls[0]
    assert sent["url"] == "https://api.groq.com/openai/v1/chat/completions"  # base_url from credential
    assert sent["auth"] == "Bearer sk-test"
    assert sent["body"]["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Greet Ada"},
    ]
    assert sent["body"]["temperature"] == 0.2


async def test_json_output_is_parsed(provider):
    calls, state = provider
    state["content"] = '{"sentiment": "positive"}'
    result = await execute_node(llm_request(prompt="Classify", json_output=True))
    assert result["outputs"]["main"]["json"] == {"sentiment": "positive"}
    assert calls[0]["body"]["response_format"] == {"type": "json_object"}


async def test_client_errors_are_not_retried_but_rate_limits_are(provider):
    from temporalio.exceptions import ApplicationError

    _, state = provider
    state["status"] = 401
    with pytest.raises(ApplicationError) as info:
        await execute_node(llm_request(prompt="hi"))
    assert info.value.non_retryable

    state["status"] = 429
    with pytest.raises(activities.LLMProviderError):  # transient -> Temporal retry policy applies
        await execute_node(llm_request(prompt="hi"))


async def test_graph_with_llm_node_runs_and_totals_usage(provider):
    graph = {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "a", "type": "llm", "label": "Writer", "config": {"credential_id": 7, "model": "m", "prompt": "{{ $json.topic }}"}},
            {"id": "b", "type": "llm", "label": "Critic", "config": {"credential_id": 7, "model": "m", "prompt": "{{ $json.topic }}"}},
            {"id": "merge", "type": "merge", "config": {"mode": "wait_for_all"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "a"}, {"source": "start", "target": "b"},
            {"source": "a", "target": "merge"}, {"source": "b", "target": "merge"},
            {"source": "merge", "target": "end"},
        ],
    }
    ir = compile_graph(graph)
    assert ir["supersteps"][1] == ["a", "b"]  # two LLM calls in parallel
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = f"test-{uuid.uuid4()}"
        async with Worker(env.client, task_queue=queue, workflows=[RunGraphWorkflow], activities=[execute_node, checkpoint_state]):
            result = await env.client.execute_workflow(
                RunGraphWorkflow.execute_graph_workflow,
                {"ir": ir, "input": {"topic": "agents"}, "run": {}},
                id=f"wf-{uuid.uuid4()}", task_queue=queue,
            )
    assert set(result["output"]) == {"a", "b"}
    assert result["usage"] == {"input_tokens": 24, "output_tokens": 10, "llm_calls": 2}


async def test_llm_node_schema_requires_prompt_model_and_credential():
    from agentforge_core.registry import validate_node_config

    errors = validate_node_config("llm", {})
    assert {"prompt is required.", "model is required.", "credential_id is required."} <= set(errors)

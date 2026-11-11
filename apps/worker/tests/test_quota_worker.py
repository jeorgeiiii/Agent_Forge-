"""Cluster 13 in the worker: an exhausted LLM quota stops the call before any tokens are spent."""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import activities  # noqa: E402
from temporalio.exceptions import ApplicationError  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
def world(monkeypatch):
    calls = {"provider": 0, "verdict": {"allowed": True}}

    def provider(request):
        calls["provider"] += 1
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    async def fake_api(method, path, json=None, timeout=10):
        if path.startswith("/internal/quota/llm"):
            return calls["verdict"]
        return {"type": "api_key", "data": {"api_key": "k"}}

    async def public_ok(url):
        return None

    monkeypatch.setattr(activities, "_llm_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(provider)))
    monkeypatch.setattr(activities.api_client, "call", fake_api)
    monkeypatch.setattr(activities.api_client, "is_configured", lambda: True)
    monkeypatch.setattr(activities, "validate_public_http_url", public_ok)
    return calls


async def test_llm_node_and_agent_steps_respect_the_quota(world):
    node = {"node": {"id": "a", "type": "llm", "config": {"credential_id": 1, "model": "m", "prompt": "hi"}},
            "input": {}, "run": {"workflow_id": "wf-q"}}
    assert (await activities.execute_node(node))["outputs"]["main"]["text"] == "hi"
    assert world["provider"] == 1

    world["verdict"] = {"allowed": False, "reason": "LLM tokens-per-minute limit reached (150/100)."}
    with pytest.raises(ApplicationError) as info:
        await activities.execute_node(node)
    assert info.value.non_retryable and "tokens-per-minute" in str(info.value)
    with pytest.raises(ApplicationError) as info:  # the agent loop's per-step call
        await activities.llm_chat({"credential_id": 1, "model": "m", "messages": [], "workflow_id": "wf-q"})
    assert info.value.type == "QuotaExceeded" and info.value.non_retryable
    assert world["provider"] == 1  # nothing was sent to the provider once the quota was exhausted

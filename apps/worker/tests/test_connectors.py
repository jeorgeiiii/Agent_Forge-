"""Integration nodes (Slack, GitHub, ...) against fake services - no network, no real keys."""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import activities  # noqa: E402
from activities import ConnectorRetryableError, execute_node  # noqa: E402
from temporalio.exceptions import ApplicationError  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service(monkeypatch):
    """Fake third-party API + fake vault. `state["reply"]` = (status, json) the service answers with."""
    calls: list[dict] = []
    vault_calls: list[str] = []
    state = {"reply": (200, {"ok": True})}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"method": request.method, "url": str(request.url), "auth": request.headers.get("authorization"),
                      "headers": dict(request.headers),
                      "json": json.loads(request.content) if request.content else None})
        status, body = state["reply"]
        return httpx.Response(status, json=body)

    async def fake_vault(method, path, json=None, timeout=10):
        vault_calls.append(path)
        return {"type": "api_key", "data": {"api_key": "test-key-123"}}

    async def public_ok(url):
        return None

    monkeypatch.setattr(activities, "_connector_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(activities.api_client, "call", fake_vault)
    monkeypatch.setattr(activities, "validate_public_http_url", public_ok)
    return {"calls": calls, "vault": vault_calls, "state": state}


def _request(node_type, config, run_input=None):
    return {"node": {"id": "n1", "type": node_type, "config": config}, "input": run_input,
            "run_input": run_input or {}, "run": {"workflow_id": "wf-42"}}


async def test_slack_send_message_uses_expressions_key_and_checks_workspace(service):
    service["state"]["reply"] = (200, {"ok": True, "ts": "1.2", "channel": "C1"})
    result = await execute_node(_request("slack", {
        "action": "send_message", "credential_id": 5, "channel": "C1", "text": "Hi {{ $input.name }}"},
        {"name": "Ada"}))
    call = service["calls"][0]
    assert call["method"] == "POST" and call["url"] == "https://slack.com/api/chat.postMessage"
    assert call["auth"] == "Bearer test-key-123"
    assert call["json"] == {"channel": "C1", "text": "Hi Ada"}  # empty thread_ts is not sent
    assert service["vault"] == ["/internal/credentials/5?workflow_id=wf-42"]  # API checks the run's workspace
    assert result["outputs"]["main"]["data"]["ts"] == "1.2"


async def test_slack_ok_false_is_a_clear_error(service):
    service["state"]["reply"] = (200, {"ok": False, "error": "channel_not_found"})
    with pytest.raises(ApplicationError, match="channel_not_found"):
        await execute_node(_request("slack", {"action": "send_message", "credential_id": 5, "channel": "C9",
                                              "text": "x"}))


async def test_github_issue_path_headers_and_result(service):
    service["state"]["reply"] = (201, {"number": 7, "html_url": "https://github.com/o/r/issues/7"})
    result = await execute_node(_request("github", {"action": "create_issue", "credential_id": 1, "owner": "octo cat",
                                                    "repo": "hello", "title": "Bug", "labels": ["bug"]}))
    call = service["calls"][0]
    assert call["url"] == "https://api.github.com/repos/octo%20cat/hello/issues"
    assert call["headers"]["x-github-api-version"] == "2022-11-28" and call["headers"]["user-agent"] == "AgentForge"
    assert result["outputs"]["main"]["data"]["number"] == 7


async def test_get_request_query_and_result_key(service):
    service["state"]["reply"] = (200, {"data": [{"id": "cus_1"}], "has_more": False})
    result = await execute_node(_request("stripe", {"action": "list_customers", "credential_id": 2,
                                                    "email": "a@b.test", "limit": 3}))
    call = service["calls"][0]
    assert call["method"] == "GET" and call["json"] is None
    assert call["url"] == "https://api.stripe.com/v1/customers?email=a%40b.test&limit=3"
    assert result["outputs"]["main"]["data"] == [{"id": "cus_1"}]


async def test_errors_are_explained_and_rate_limits_retry(service):
    service["state"]["reply"] = (401, {"message": "Bad credentials"})
    with pytest.raises(ApplicationError) as error:
        await execute_node(_request("github", {"action": "get_repo", "credential_id": 1, "owner": "o", "repo": "r"}))
    assert "rejected" in str(error.value) and error.value.non_retryable
    service["state"]["reply"] = (429, {"message": "slow down"})
    with pytest.raises(ConnectorRetryableError):  # not an ApplicationError -> Temporal retries with backoff
        await execute_node(_request("github", {"action": "get_repo", "credential_id": 1, "owner": "o", "repo": "r"}))


async def test_missing_required_field_fails_before_any_call(service):
    with pytest.raises(ApplicationError, match="Channel is required"):
        await execute_node(_request("slack", {"action": "send_message", "credential_id": 5, "text": "x"}))
    assert service["calls"] == [] and service["vault"] == []


async def test_keyless_connector_never_touches_the_vault(service):
    service["state"]["reply"] = (200, {"title": "Alan Turing", "extract": "English mathematician"})
    result = await execute_node(_request("wikipedia", {"action": "summary", "title": "Alan Turing"}))
    call = service["calls"][0]
    assert call["url"] == "https://en.wikipedia.org/api/rest_v1/page/summary/Alan%20Turing"
    assert call["auth"] is None and service["vault"] == []
    assert call["headers"]["user-agent"].startswith("AgentForge")
    assert result["outputs"]["main"]["data"]["extract"] == "English mathematician"


async def test_discord_bot_prefix_and_graphql_errors(service):
    service["state"]["reply"] = (200, {"id": "m1"})
    await execute_node(_request("discord", {"action": "send_message", "credential_id": 3, "channel_id": "99",
                                            "content": "hi"}))
    assert service["calls"][-1]["auth"] == "Bot test-key-123"
    service["state"]["reply"] = (200, {"errors": [{"message": "Argument Validation Error"}], "data": None})
    with pytest.raises(ApplicationError, match="Argument Validation Error"):
        await execute_node(_request("linear", {"action": "create_issue", "credential_id": 3, "team_id": "T",
                                               "title": "x"}))

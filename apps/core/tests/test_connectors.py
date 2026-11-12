"""Connector engine + every built-in connector definition."""

import pytest

import agentforge_core  # noqa: F401
from agentforge_core.connector_catalog import CONNECTORS
from agentforge_core.connectors import (Connector, ConnectorAction, ConnectorField, build_request, check_connector,
                                        get_connector, read_result)
from agentforge_core.registry import get_node_type, validate_node_config


def _sample(f: ConnectorField):
    return {"integer": 3, "number": 1.5, "boolean": True, "object": {"k": "v"}, "array": ["a"]}.get(f.type, "x")


@pytest.mark.parametrize("connector", CONNECTORS, ids=lambda c: c.id)
def test_every_action_builds_a_request_to_its_own_host(connector):
    check_connector(connector)
    assert get_node_type(connector.id).category == "integration"
    for action in connector.actions:
        config = {"action": action.id, "credential_id": 1, **{f.name: _sample(f) for f in action.fields if f.required}}
        assert validate_node_config(connector.id, config) == [], (connector.id, action.id)
        request = build_request(connector, config)
        assert request["url"].startswith(connector.base_url + "/") and request["method"] == action.method
        assert "Authorization" not in request["headers"]  # the key is only added by the worker
        assert ("json" in request) == (action.method != "GET")


def test_missing_and_unknown_action_and_required_fields():
    errors = validate_node_config("github", {"credential_id": 1, "action": "create_issue", "owner": "o"})
    assert "Repository is required for 'Create issue'." in errors and "Title is required for 'Create issue'." in errors
    assert validate_node_config("github", {"credential_id": 1, "action": "delete_everything"}) == [
        "action must be one of ['create_issue', 'comment_issue', 'list_issues', 'get_repo']."]
    assert any("credential_id" in e for e in validate_node_config("slack", {"action": "list_channels"}))


def test_path_values_are_encoded_and_dot_segments_refused():
    github = get_connector("github")
    request = build_request(github, {"action": "get_repo", "credential_id": 1, "owner": "a/b?x", "repo": "r"})
    assert request["url"] == "https://api.github.com/repos/a%2Fb%3Fx/r"
    with pytest.raises(ValueError):
        build_request(github, {"action": "get_repo", "credential_id": 1, "owner": "..", "repo": "r"})


def test_nested_body_consts_and_empty_values_skipped():
    request = build_request(get_connector("notion"), {"action": "create_page", "credential_id": 1,
                                                       "database_id": "db1", "properties": {"Name": {}}})
    assert request["json"] == {"parent": {"database_id": "db1"}, "properties": {"Name": {}}}
    airtable = build_request(get_connector("airtable"), {"action": "list_records", "credential_id": 1,
                                                         "base_id": "app1", "table": "Leads", "formula": ""})
    assert airtable["params"] == {} and "json" not in airtable


def test_ok_false_and_result_picking():
    slack = get_connector("slack")
    with pytest.raises(ValueError, match="invalid_auth"):
        read_result(slack, "send_message", {"ok": False, "error": "invalid_auth"})
    assert read_result(slack, "list_channels", {"ok": True, "channels": [1]}) == [1]


def test_schema_marks_which_action_uses_each_field():
    props = get_node_type("github").parameters["properties"]
    assert props["action"]["x-enum-labels"]["create_issue"] == "Create issue"
    assert set(props["title"]["x-actions"]) == {"create_issue"}
    assert set(props["owner"]["x-actions"]) == {"create_issue", "comment_issue", "list_issues", "get_repo"}


def test_bad_definitions_are_refused():
    base = dict(id="x", name="X", category="Test", description="")
    action = ConnectorAction("a", "A", "", "GET", "/items/{id}", fields=(ConnectorField("id", "ID", required=True),))
    with pytest.raises(ValueError, match="https"):
        check_connector(Connector(base_url="http://x.test", actions=(action,), **base))
    with pytest.raises(ValueError, match="unknown fields"):
        check_connector(Connector(base_url="https://x.test", **base,
                                  actions=(ConnectorAction("a", "A", "", "GET", "/items/{nope}"),)))
    with pytest.raises(ValueError, match="must be required"):
        check_connector(Connector(base_url="https://x.test", **base, actions=(
            ConnectorAction("a", "A", "", "GET", "/items/{id}", fields=(ConnectorField("id", "ID"),)),)))


def test_auth_styles_and_keyless_connectors():
    from agentforge_core.connectors import auth_headers

    assert auth_headers(get_connector("slack"), "k") == {"Authorization": "Bearer k"}
    assert auth_headers(get_connector("discord"), "k") == {"Authorization": "Bot k"}
    assert auth_headers(get_connector("linear"), "k") == {"Authorization": "k"}
    assert auth_headers(get_connector("serper"), "k") == {"X-API-KEY": "k"}
    assert auth_headers(get_connector("wikipedia"), "k") == {}
    with pytest.raises(ValueError):
        auth_headers(get_connector("slack"), "k\r\nX-Evil: 1")
    wiki = get_node_type("wikipedia").parameters
    assert "credential_id" not in wiki["properties"] and wiki["required"] == ["action"]
    assert validate_node_config("wikipedia", {"action": "summary", "title": "Alan_Turing"}) == []


def test_graphql_connectors_send_query_and_variables_and_raise_errors():
    request = build_request(get_connector("linear"), {"action": "create_issue", "credential_id": 1,
                                                      "team_id": "T1", "title": "Bug"})
    assert request["url"] == "https://api.linear.app/graphql"
    assert "issueCreate" in request["json"]["query"]
    assert request["json"]["variables"] == {"input": {"teamId": "T1", "title": "Bug"}}
    with pytest.raises(ValueError, match="Team not found"):
        read_result(get_connector("linear"), "create_issue", {"errors": [{"message": "Team not found"}]})
    assert read_result(get_connector("linear"), "list_teams", {"data": {"teams": {"nodes": []}}}) == {"teams": {"nodes": []}}


def test_gitlab_project_path_is_one_encoded_segment():
    request = build_request(get_connector("gitlab"), {"action": "list_issues", "credential_id": 1,
                                                      "project": "my-group/my-project"})
    assert request["url"] == "https://gitlab.com/api/v4/projects/my-group%2Fmy-project/issues"


def test_catalog_has_no_money_moving_actions():
    """Policy: Stripe and Cloudflare stay read-only; nothing in the catalog charges, refunds or trades."""
    for connector_id in ("stripe", "cloudflare"):
        assert all(a.method == "GET" for a in get_connector(connector_id).actions)
    banned = ("charge", "refund", "payout", "transfer", "trade", "order")
    assert not [a.id for c in CONNECTORS for a in c.actions if any(b in a.id for b in banned)]

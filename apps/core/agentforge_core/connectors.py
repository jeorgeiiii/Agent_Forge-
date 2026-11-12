"""Integrations (connectors) - one engine, many services.

Each service (Slack, GitHub, ...) is a plain data definition in ``connector_catalog.py``:
  - how to sign in (an API key / token saved in the vault),
  - a fixed base URL and fixed headers,
  - a list of ACTIONS. Each action = input fields + an HTTP request template + which part of the reply to return.

This module turns every definition into one node type (``slack``, ``github`` ...) whose settings are
``action`` + ``credential_id`` + the action's fields, and builds the HTTP request for a run.
Pure Python, no I/O: the worker sends the request (activities.py ``_connector``), the API lists the catalog.

Request template mini-language (per action):
  path:   "/repos/{owner}/{repo}/issues"      {field} = URL-encoded field value
  query:  {"per_page": "limit"}               query key  <- field name (skipped when the field is empty)
  body:   {"parent.database_id": "database_id"}  JSON key (dots = nested) <- field name (skipped when empty)
  const_body / const_query: fixed values always sent
  result: "channels"                          return only this key of the JSON reply ("" = everything)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .registry import NodeTypeSpec, _validate, register_config_validator, register_node_type

LINE_BREAKS = ("\r", "\n")  # a key or header name with these could inject extra HTTP headers
BAD_HEADER_CHARS = (":", " ", *LINE_BREAKS)
FIELD_TYPES = {"string", "text", "integer", "number", "boolean", "object", "array"}
_JSON_TYPE = {"text": "string"}  # editor-only type -> JSON schema type


@dataclass(frozen=True)
class ConnectorField:
    name: str
    label: str
    type: str = "string"  # string | text (multi-line) | integer | number | boolean | object | array
    required: bool = False
    help: str = ""
    enum: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorAction:
    id: str
    name: str
    description: str
    method: str
    path: str
    fields: tuple[ConnectorField, ...] = ()
    query: dict[str, str] = field(default_factory=dict)
    body: dict[str, str] = field(default_factory=dict)
    const_query: dict[str, Any] = field(default_factory=dict)
    const_body: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    # changes or sends something (vs. only reads) - shown in the UI, used later for approval rules
    writes: bool = False


@dataclass(frozen=True)
class Connector:
    id: str                     # node type, e.g. "slack"
    name: str
    category: str               # Communication | Developer | Productivity | Databases | Email | Commerce ...
    description: str
    base_url: str               # fixed https:// host - users can't point a connector anywhere else
    # How the saved key is sent: header `auth_header` with value `auth_prefix + key`.
    #   default "Authorization: Bearer <key>"; Discord prefix "Bot "; Linear/ClickUp prefix "";
    #   Serper auth_header "X-API-KEY", prefix "". needs_key=False: public API, no key (Wikipedia).
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    needs_key: bool = True
    headers: dict[str, str] = field(default_factory=dict)
    key_label: str = "API key"
    key_help: str = ""          # where to find the key, shown on the Connect form
    docs_url: str = ""
    # Slack-style APIs answer 200 with {"ok": false, "error": "..."}: name the flag + error keys here
    ok_field: str = ""
    error_field: str = ""
    # GraphQL-style APIs answer 200 with {"errors": [...]}: name that key here
    errors_field: str = ""
    actions: tuple[ConnectorAction, ...] = ()

    def action(self, action_id: str) -> ConnectorAction:
        for action in self.actions:
            if action.id == action_id:
                return action
        raise ValueError(f"{self.name} has no action '{action_id}'.")


_CONNECTORS: dict[str, Connector] = {}


def _field_schema(f: ConnectorField, action_ids: list[str]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": _JSON_TYPE.get(f.type, f.type), "title": f.label, "x-actions": action_ids}
    if f.help:
        schema["description"] = f.help
    if f.enum:
        schema["enum"] = list(f.enum)
    if f.type == "text":
        schema["x-multiline"] = True
    if f.type == "array":
        schema["items"] = {"type": "string"}
    return schema


def connector_schema(connector: Connector) -> dict[str, Any]:
    """JSON schema for the node: action + credential + every action's fields.

    Each field carries "x-actions" (the actions that use it) so the editor only shows the fields
    of the chosen action. Fields used by several actions with different types are not allowed.
    """
    props: dict[str, Any] = {
        "action": {"type": "string", "title": "Action", "enum": [a.id for a in connector.actions],
                   "x-enum-labels": {a.id: a.name for a in connector.actions}},
    }
    if connector.needs_key:
        props["credential_id"] = {"type": "integer", "title": f"{connector.name} {connector.key_label.lower()}",
                                  "description": f"A saved key for {connector.name} (Saved keys page or Integrations page)."}
    seen: dict[str, ConnectorField] = {}
    for action in connector.actions:
        for f in action.fields:
            if f.name in ("action", "credential_id"):
                raise ValueError(f"{connector.id}.{action.id}: field name '{f.name}' is reserved.")
            if f.type not in FIELD_TYPES:
                raise ValueError(f"{connector.id}.{action.id}.{f.name}: unknown field type '{f.type}'.")
            if f.name in seen and seen[f.name].type != f.type:
                raise ValueError(f"{connector.id}: field '{f.name}' has different types in different actions.")
            seen[f.name] = f
            users = [a.id for a in connector.actions if any(x.name == f.name for x in a.fields)]
            props[f.name] = _field_schema(f, users)
    required = ["action", "credential_id"] if connector.needs_key else ["action"]
    return {"type": "object", "required": required, "properties": props}


def _validator(connector: Connector):
    def validate(config: dict[str, Any]) -> list[str]:
        try:
            action = connector.action(str(config.get("action", "")))
        except ValueError as error:
            return [str(error)]
        return [f"{f.label} is required for '{action.name}'." for f in action.fields
                if f.required and config.get(f.name) in (None, "", [], {})]
    return validate


def check_connector(connector: Connector) -> None:
    """Catches mistakes in a definition when it is registered (tests run this for every connector)."""
    if not connector.base_url.startswith("https://") or connector.base_url.endswith("/"):
        raise ValueError(f"{connector.id}: base_url must be https:// without a trailing slash.")
    if connector.needs_key and (not connector.auth_header or any(c in connector.auth_header for c in BAD_HEADER_CHARS)):
        raise ValueError(f"{connector.id}: auth_header must be a plain header name.")
    ids = [a.id for a in connector.actions]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError(f"{connector.id}: actions must exist and have unique ids.")
    for action in connector.actions:
        names = {f.name for f in action.fields}
        if action.method not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not action.path.startswith("/"):
            raise ValueError(f"{connector.id}.{action.id}: bad method or path.")
        placeholders = {part.split("}")[0] for part in action.path.split("{")[1:]}
        missing = (placeholders | set(action.query.values()) | set(action.body.values())) - names
        if missing:
            raise ValueError(f"{connector.id}.{action.id}: template uses unknown fields {sorted(missing)}.")
        if action.method == "GET" and (action.body or action.const_body):
            raise ValueError(f"{connector.id}.{action.id}: GET requests can't have a body.")
        for name in placeholders:
            if not next(f for f in action.fields if f.name == name).required:
                raise ValueError(f"{connector.id}.{action.id}: path field '{name}' must be required.")
    connector_schema(connector)


def register_connector(connector: Connector) -> None:
    check_connector(connector)
    _CONNECTORS[connector.id] = connector
    register_node_type(NodeTypeSpec(connector.id, connector.name, "integration", connector.description,
                                    parameters=connector_schema(connector)), replace=True)
    register_config_validator(connector.id, _validator(connector))


def get_connector(connector_id: str) -> Connector:
    try:
        return _CONNECTORS[connector_id]
    except KeyError as error:
        raise ValueError(f"Unknown integration '{connector_id}'.") from error


def list_connectors() -> list[Connector]:
    return sorted(_CONNECTORS.values(), key=lambda c: (c.category, c.name))


def connector_to_dict(connector: Connector) -> dict[str, Any]:
    """Catalog entry for the Integrations page (no secrets anywhere in a definition)."""
    return {
        "id": connector.id, "name": connector.name, "category": connector.category,
        "description": connector.description, "needs_key": connector.needs_key,
        "key_label": connector.key_label, "key_help": connector.key_help,
        "docs_url": connector.docs_url,
        "actions": [{"id": a.id, "name": a.name, "description": a.description, "writes": a.writes,
                     "fields": [{"name": f.name, "label": f.label, "type": f.type, "required": f.required,
                                 "help": f.help} for f in a.fields]} for a in connector.actions],
    }


# --------------------------------------------------------------------------- request building


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _path_segment(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text or text in (".", ".."):
        raise ValueError(f"'{name}' is empty or not allowed in a URL.")
    return quote(text, safe="")  # "/" and "?" are encoded: a value can never change the path


def _set_nested(target: dict[str, Any], dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = value


def build_request(connector: Connector, config: dict[str, Any]) -> dict[str, Any]:
    """Settings (already rendered, expressions resolved) -> {method, url, params, json, headers}.

    Does not add the key: the worker adds the Authorization header after loading it from the vault.
    """
    action = connector.action(str(config.get("action", "")))
    errors = _validator(connector)(config)
    if errors:
        raise ValueError(" ".join(errors))
    path = action.path
    for f in action.fields:
        if "{" + f.name + "}" in path:
            path = path.replace("{" + f.name + "}", _path_segment(config.get(f.name), f.label))
    params = dict(action.const_query)
    params.update({key: config[name] for key, name in action.query.items() if _present(config.get(name))})
    body: dict[str, Any] = {}
    for key, value in action.const_body.items():
        _set_nested(body, key, value)
    for key, name in action.body.items():
        if _present(config.get(name)):
            _set_nested(body, key, config[name])
    request: dict[str, Any] = {"method": action.method, "url": connector.base_url + path,
                               "params": {k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items()},
                               "headers": dict(connector.headers)}
    if action.method != "GET":
        request["json"] = body
    return request


def auth_headers(connector: Connector, key: str) -> dict[str, str]:
    """The header that carries the saved key (the worker calls this after loading the key from the vault)."""
    if not connector.needs_key:
        return {}
    if any(c in key for c in LINE_BREAKS):
        raise ValueError(f"The saved key for {connector.name} contains a line break.")
    return {connector.auth_header: connector.auth_prefix + key}


def read_result(connector: Connector, action_id: str, payload: Any) -> Any:
    """Checks Slack-style {"ok": false} and GraphQL {"errors": [...]} replies, then picks the action's result key."""
    if connector.ok_field and isinstance(payload, dict) and payload.get(connector.ok_field) is False:
        raise ValueError(f"{connector.name} said: {payload.get(connector.error_field) or 'request failed'}")
    if connector.errors_field and isinstance(payload, dict) and payload.get(connector.errors_field):
        errors = payload[connector.errors_field]
        first = errors[0] if isinstance(errors, list) and errors else errors
        message = first.get("message") if isinstance(first, dict) else first
        raise ValueError(f"{connector.name} said: {str(message)[:300]}")
    key = connector.action(action_id).result
    if key and isinstance(payload, dict):
        return payload.get(key)
    return payload


# --------------------------------------------------------------------------- integrations as AI-agent tools
#
# A tool entry on an AI Agent:  {"name": "wiki", "node": {"type": "wikipedia", "config": {"action": "summary"}}}
#   - YOU fix the action and the saved key (credential_id) - the AI can never choose or change them;
#   - any other field you fill in is fixed too (e.g. always post to channel C123);
#   - the AI fills in the remaining fields of that action as the tool's arguments.
# Actions that send or change something (writes=True) need "allow_writes": true on the agent.


def is_connector(node_type: str) -> bool:
    return node_type in _CONNECTORS


def _json_field(f: ConnectorField) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": _JSON_TYPE.get(f.type, f.type), "description": f.help or f.label}
    if f.enum:
        schema["enum"] = list(f.enum)
    if f.type == "array":
        schema["items"] = {"type": "string"}
    return schema


def tool_parameters(connector: Connector, config: dict[str, Any]) -> dict[str, Any]:
    """JSON schema the AI sees: the action's fields that are not already fixed in the tool's config."""
    action = connector.action(str(config.get("action", "")))
    free = [f for f in action.fields if config.get(f.name) in (None, "")]
    return {"type": "object", "properties": {f.name: _json_field(f) for f in free},
            "required": [f.name for f in free if f.required]}


def tool_description(connector: Connector, config: dict[str, Any]) -> str:
    action = connector.action(str(config.get("action", "")))
    return f"{connector.name}: {action.name}. {action.description}".strip()


def merge_tool_arguments(connector: Connector, config: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Final node config for one tool call: fixed settings always win; the AI may only fill the
    action's own empty fields (it can't set action, credential_id or anything unknown)."""
    action = connector.action(str(config.get("action", "")))
    allowed = {f.name for f in action.fields if config.get(f.name) in (None, "")}
    return {**config, **{k: v for k, v in arguments.items() if k in allowed}}


def tool_config_errors(connector: Connector, config: dict[str, Any], allow_writes: bool) -> list[str]:
    """Checks a tool entry when the workflow is saved (and again before each call)."""

    try:
        action = connector.action(str(config.get("action", "")))
    except ValueError:
        return [f"choose which {connector.name} action this tool runs (node.config.action)."]
    errors: list[str] = []
    if connector.needs_key and not config.get("credential_id"):
        errors.append(f"pick the saved {connector.name} key (node.config.credential_id) - the AI never chooses keys.")
    if action.writes and not allow_writes:
        errors.append(f"'{action.name}' sends or changes something. Tick 'Allow tools that send or change things' "
                      "(allow_writes) on the agent to let the AI use it.")
    _validate(config, connector_schema(connector), "", errors)  # types of the fixed values
    return errors

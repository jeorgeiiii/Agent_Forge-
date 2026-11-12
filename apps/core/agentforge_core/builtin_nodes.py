"""Specs for the built-in node types. Executors are attached by the worker."""

import re
from typing import Any

from .connectors import get_connector, is_connector, tool_config_errors
from .registry import NodeTypeSpec, register_config_validator, register_node_type, validate_node_config

_STR = {"type": "string"}
# Node types an agent may use as tools (no triggers / flow control).
MAX_AGENT_DEPTH = 3  # supervisor -> worker -> sub-worker (enforced at compile time and at runtime)
AGENT_TOOL_NODE_TYPES = ("http", "template", "set", "llm", "agent")  # "agent" = sub-agent
_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

BUILTIN_SPECS: list[NodeTypeSpec] = [
    # ----- entry / exit ---------------------------------------------------
    NodeTypeSpec("start", "Start", "trigger", "Entry point; passes the run input through.", is_entry=True),
    NodeTypeSpec("trigger", "Trigger (legacy)", "trigger", "Alias of Start kept for older graphs.", is_entry=True),
    NodeTypeSpec("manual_trigger", "Manual Trigger", "trigger", "Started from the editor's Run button.", is_entry=True),
    NodeTypeSpec(
        "webhook_trigger", "Webhook Trigger", "trigger",
        "Starts the graph when its unique URL receives an HTTP request.",
        parameters={"type": "object", "properties": {
            "method": {"type": "string", "enum": ["GET", "POST", "ANY"]},
            "response_mode": {"type": "string", "enum": ["immediately", "last_node"]},
        }},
        is_entry=True,
    ),
    NodeTypeSpec(
        "schedule_trigger", "Schedule Trigger", "trigger",
        "Starts the graph on a cron expression or fixed interval.",
        parameters={"type": "object", "properties": {
            "cron": {"type": "string", "minLength": 1},
            "interval_seconds": {"type": "integer", "minimum": 60},
        }},
        is_entry=True,
    ),
    NodeTypeSpec(
        "chat_trigger", "Chat Trigger", "trigger",
        "Starts the graph for each message sent to its chat endpoint.",
        is_entry=True,
    ),
    NodeTypeSpec("end", "End", "core", "Marks where a run finishes; returns its input as the run output.", is_end=True),
    NodeTypeSpec("output", "Output (legacy)", "core", "Alias of End kept for older graphs.", is_end=True),
    # ----- core / data ----------------------------------------------------
    NodeTypeSpec(
        "template", "Template", "data", "Renders a Jinja template.",
        parameters={"type": "object", "required": ["template"], "properties": {"template": _STR}},
    ),
    NodeTypeSpec(
        "http", "HTTP Request", "integration", "Calls a public HTTP(S) endpoint.",
        parameters={"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string", "minLength": 1},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
            "headers": {"type": "object"},
            "body": {},
            "credential_id": {"type": "integer"},
            "auth_type": {"type": "string", "enum": ["none", "bearer", "header", "basic"]},
            "auth_header_name": _STR,
            # files (cluster 19): save the response as a file / send a stored file as the body
            "response_format": {"type": "string", "enum": ["auto", "file"]},
            "body_file": {},  # a {"$file": {...}} reference, usually an expression like {{ $json.file }}
        }},
    ),
    NodeTypeSpec(
        "set", "Set Fields", "data", "Builds a new object from key/value pairs (values may be expressions).",
        parameters={"type": "object", "required": ["fields"], "properties": {
            "fields": {"type": "object"},
            "keep_input": {"type": "boolean"},
        }},
    ),
    NodeTypeSpec("noop", "No-op", "core", "Passes its input through unchanged."),
    # ----- AI -------------------------------------------------------------
    NodeTypeSpec(
        "llm", "LLM Chat Model", "ai",
        "Sends a prompt to an OpenAI-compatible chat-completions API (Groq, OpenAI, ...) "
        "and returns the reply plus token usage.",
        parameters={"type": "object", "required": ["prompt", "model", "credential_id"], "properties": {
            "prompt": {"type": "string", "minLength": 1},
            "system": _STR,
            "model": {"type": "string", "minLength": 1},
            "base_url": {"type": "string", "minLength": 1},  # default https://api.openai.com/v1
            "credential_id": {"type": "integer"},
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            "max_tokens": {"type": "integer", "minimum": 1, "maximum": 128_000},
            "json_output": {"type": "boolean"},
        }},
    ),
    NodeTypeSpec(
        "agent", "AI Agent", "ai",
        "An LLM that can call tools in a loop until it has an answer. Each tool is a node "
        "(HTTP, Template, Set, LLM) whose parameters can use the model's arguments as $args.",
        parameters={"type": "object", "required": ["prompt", "model", "credential_id"], "properties": {
            "prompt": {"type": "string", "minLength": 1},
            "system": _STR,
            "model": {"type": "string", "minLength": 1},
            "base_url": {"type": "string", "minLength": 1},
            "credential_id": {"type": "integer"},
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            "max_tokens": {"type": "integer", "minimum": 1, "maximum": 128_000},
            "max_steps": {"type": "integer", "minimum": 1, "maximum": 50},
            "max_total_tokens": {"type": "integer", "minimum": 100},
            "json_output": {"type": "boolean"},
            # integration tools that send or change things (Slack message, GitHub issue ...) need this
            "allow_writes": {"type": "boolean"},
            # Conversation memory across runs, per chat session.
            "memory": {"type": "object", "properties": {
                # window = last N messages | buffer = whole chat (token-trimmed) |
                # summary = running LLM summary of older messages + the newest ones verbatim
                "type": {"type": "string", "enum": ["none", "window", "buffer", "summary"]},
                # which conversation this run belongs to; default {{ $trigger.session_id }} (chat trigger)
                "session_id": {"type": "string", "minLength": 1},
                "window": {"type": "integer", "minimum": 1, "maximum": 100},  # messages kept for "window"
                "max_tokens": {"type": "integer", "minimum": 100, "maximum": 200_000},  # history budget
                "keep_recent": {"type": "integer", "minimum": 2, "maximum": 50},  # "summary": raw tail kept
                "redact_pii": {"type": "boolean"},  # mask emails / phones / card numbers before saving
            }},
            "tools": {"type": "array", "items": {
                # description may be left out for integration tools (it is generated from the action)
                "type": "object", "required": ["name", "node"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "description": {"type": "string", "minLength": 1},
                    "parameters": {"type": "object"},  # JSON schema of the arguments
                    "node": {"type": "object", "required": ["type"], "properties": {
                        # a built-in tool type (AGENT_TOOL_NODE_TYPES) or any integration (slack, wikipedia ...)
                        "type": {"type": "string", "minLength": 1},
                        "config": {"type": "object"},
                    }},
                },
            }},
        }},
        runs_in="workflow",
    ),
    # ----- flow control ---------------------------------------------------
    NodeTypeSpec(
        "if", "IF", "flow", "Routes data to the 'true' or 'false' output based on a condition.",
        parameters={"type": "object", "required": ["condition"], "properties": {"condition": {}}},
        outputs=("true", "false"),
    ),
    NodeTypeSpec(
        "switch", "Switch", "flow",
        "Routes data to the first matching case output (case id), or 'default'.",
        parameters={"type": "object", "required": ["cases"], "properties": {
            "cases": {"type": "array", "items": {"type": "object", "required": ["id", "condition"],
                                                  "properties": {"id": _STR, "condition": {}}}},
        }},
        outputs=("default",),  # case ids are dynamic outputs
    ),
    NodeTypeSpec(
        "merge", "Merge", "flow", "Combines outputs from several branches.",
        parameters={"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["append", "combine_by_key", "wait_for_all", "first"]},
            "key": _STR,
        }},
    ),
    NodeTypeSpec(
        "wait", "Wait", "flow", "Pauses the run for a duration (durable timer).",
        parameters={"type": "object", "required": ["seconds"], "properties": {
            "seconds": {"type": "number", "minimum": 0, "maximum": 31_536_000},
        }},
        runs_in="workflow",
    ),
    NodeTypeSpec(
        "approval", "Human Approval", "flow",
        "Pauses the run until a person approves or rejects it.",
        parameters={"type": "object", "properties": {
            "message": _STR,
            "timeout_seconds": {"type": "number", "minimum": 1},
        }},
        outputs=("approved", "rejected"),
        runs_in="workflow",
    ),
]


def agent_depth(config: dict[str, Any]) -> int:
    """1 for an agent with no sub-agents, 2 if it delegates to one, ..."""
    subs = [t["node"].get("config") or {} for t in config.get("tools") or []
            if isinstance(t, dict) and isinstance(t.get("node"), dict) and t["node"].get("type") == "agent"]
    return 1 + max((agent_depth(sub) for sub in subs), default=0)


def validate_agent_config(config: dict[str, Any]) -> list[str]:
    """Tool names must be unique and API-safe; each tool's node config must be valid
    (recursively for sub-agents); agent nesting may not exceed MAX_AGENT_DEPTH."""
    errors: list[str] = []
    depth = agent_depth(config)
    if depth > MAX_AGENT_DEPTH:
        return [f"Agents are nested {depth} levels deep; the maximum is {MAX_AGENT_DEPTH}."]
    seen: set[str] = set()
    for index, tool in enumerate(config.get("tools") or []):
        name = tool.get("name", "")
        if not _TOOL_NAME.match(name):
            errors.append(f"tools[{index}].name must use only letters, digits, _ or - (max 64).")
        if name in seen:
            errors.append(f"Duplicate tool name '{name}'.")
        seen.add(name)
        node = tool.get("node") or {}
        node_type = str(node.get("type", ""))
        if is_connector(node_type):
            problems = tool_config_errors(get_connector(node_type), node.get("config") or {},
                                          bool(config.get("allow_writes")))
        elif node_type in AGENT_TOOL_NODE_TYPES:
            problems = validate_node_config(node_type, node.get("config") or {})
            if not str(tool.get("description") or "").strip():
                problems.append("description is required (tell the AI when to use this tool).")
        else:
            problems = [f"'{node_type}' can't be used as a tool."]
        errors.extend(f"tools[{index}] ({name}): {e}" for e in problems)
    return errors


def register_builtin_nodes() -> None:
    for spec in BUILTIN_SPECS:
        register_node_type(spec, replace=True)
    register_config_validator("agent", validate_agent_config)

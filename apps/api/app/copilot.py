"""Cluster 10 - AI Copilot: generate, edit, explain and repair graphs.

All providers are OpenAI-compatible chat-completions endpoints (configured
COPILOT_* first, Groq as fallback).
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from fastapi import HTTPException

import agentforge_core  # noqa: F401
from agentforge_core.compiler import GraphValidationError, compile_graph
from agentforge_core.registry import list_node_types

from .config import get_settings

MAX_REPAIR_ATTEMPTS = 3


class CopilotError(Exception):
    pass


def _provider() -> tuple[str, str, dict[str, str]] | None:
    settings = get_settings()
    if settings.copilot_api_url and settings.copilot_api_key:
        return settings.copilot_api_url, settings.copilot_model, {"Authorization": f"Bearer {settings.copilot_api_key}"}
    if settings.groq_api_key:
        return (
            "https://api.groq.com/openai/v1/chat/completions",
            settings.groq_model or "openai/gpt-oss-120b",
            {"Authorization": f"Bearer {settings.groq_api_key}"},
        )
    return None


async def _chat(messages: list[dict[str, str]], *, temperature: float | None = None) -> str:
    provider = _provider()
    if provider is None:
        raise HTTPException(status_code=503, detail="Copilot or Groq suggestions are not configured. Set appropriate API keys.")
    api_url, model, headers = provider
    body: dict[str, Any] = {"model": model, "messages": messages}
    if temperature is not None:
        body["temperature"] = temperature
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(api_url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Provider returned error {error.response.status_code}: {error.response.text[:500]}",
        ) from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"The configured provider request failed: {error}") from error
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise HTTPException(status_code=502, detail="The provider returned an invalid response.") from error


def _extract_json(text: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start = min((i for i in (candidate.find("{"), candidate.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise CopilotError("The model did not return JSON.")
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(candidate[start:])
    except json.JSONDecodeError as error:
        raise CopilotError(f"The model returned invalid JSON: {error}") from error
    return value


# --------------------------------------------------------------------- context


def _node_catalogue() -> str:
    lines = []
    for spec in list_node_types():
        if spec.type in ("trigger", "output"):
            continue  # legacy aliases
        params = json.dumps(spec.parameters.get("properties", {}), separators=(",", ":"))
        lines.append(f"- {spec.type} ({spec.category}; outputs: {', '.join(spec.outputs)}): {spec.description} params={params}")
    return "\n".join(lines)


GRAPH_RULES = """Graph JSON format:
{"nodes":[{"id":"start","type":"start","label":"Start","config":{}}, ...],
 "edges":[{"source":"start","target":"end","source_handle":"main"}]}
Rules: exactly one start/trigger node with no incoming edges; at least one end node;
no cycles; every node reachable from the start; edges from IF nodes use source_handle
"true"/"false", Switch uses its case ids or "default", Approval uses "approved"/"rejected".
Parameters may use expressions such as {{ $json.field }}, {{ $trigger.body }},
{{ $node["Label"].json.x }}."""


def build_copilot_context(
    prompt: str,
    *,
    graph: dict[str, Any] | None = None,
    selected_node_id: str | None = None,
    extra: str = "",
) -> list[dict[str, str]]:
    """Assemble system + user messages: node docs, rules, current graph, selected node and the prompt."""
    system = (
        "You are the AgentForge Copilot. You design workflow graphs for an n8n-style agent studio.\n\n"
        f"Available node types:\n{_node_catalogue()}\n\n{GRAPH_RULES}"
    )
    parts = []
    if graph is not None:
        parts.append("Current graph:\n```json\n" + json.dumps(graph, indent=1)[:20000] + "\n```")
    if selected_node_id and graph is not None:
        node = next((n for n in graph.get("nodes", []) if n.get("id") == selected_node_id), None)
        if node:
            parts.append(f"Selected node:\n```json\n{json.dumps(node, indent=1)}\n```")
    if extra:
        parts.append(extra)
    parts.append(prompt)
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


# ---------------------------------------------------------- generate (legacy)


async def suggest_graph(prompt: str, graph: dict[str, Any] | None = None) -> str:
    """Free-form answer for a prompt (kept compatible with the original endpoint)."""
    if graph is None:
        return await _chat([{"role": "user", "content": prompt}])
    return await _chat(build_copilot_context(prompt, graph=graph))


async def stream_suggest_graph(prompt: str, graph: dict[str, Any] | None = None) -> AsyncGenerator[str, None]:
    """Same as suggest_graph but streams tokens as SSE events."""
    provider = _provider()
    if provider is None:
        yield f"data: {json.dumps({'type': 'error', 'error': 'Provider not configured'})}\n\n"
        return
    api_url, model, headers = provider
    messages = [{"role": "user", "content": prompt}] if graph is None else build_copilot_context(prompt, graph=graph)
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            async with client.stream(
                "POST", api_url, headers=headers, json={"model": model, "messages": messages, "stream": True}
            ) as response:
                response.raise_for_status()
                accumulated = ""
                async for line in response.aiter_lines():
                    trimmed = line.strip()
                    if not trimmed.startswith("data:"):
                        continue
                    data_str = trimmed[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    except (json.JSONDecodeError, IndexError, AttributeError):
                        continue
                    if delta:
                        accumulated += delta
                        yield f"data: {json.dumps({'type': 'token', 'content': delta})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'text': accumulated})}\n\n"
                yield "data: [DONE]\n\n"
    except Exception as exc:  # stream errors are reported to the client as an event
        yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"


# --------------------------------------------------------- validated generate


def validate_generated_graph(definition: Any) -> list[str]:
    """Compile a model-generated graph; returns errors ([] when it is runnable)."""
    if not isinstance(definition, dict):
        return ["The graph must be a JSON object with 'nodes' and 'edges'."]
    try:
        compile_graph(definition)
    except GraphValidationError as error:
        return error.errors
    except (TypeError, AttributeError, KeyError) as error:
        return [f"Malformed graph JSON: {error}"]
    return []


async def generate_valid_graph(prompt: str, *, max_attempts: int = MAX_REPAIR_ATTEMPTS) -> dict[str, Any]:
    """Generate a graph and self-repair: compiler errors are sent back to the model to fix."""
    messages = build_copilot_context(
        f"Build a graph for this request. Reply with ONLY the graph JSON.\n\nRequest: {prompt}"
    )
    errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        reply = await _chat(messages, temperature=0.2)
        try:
            definition = _extract_json(reply)
            errors = validate_generated_graph(definition)
        except CopilotError as error:
            definition, errors = None, [str(error)]
        if not errors:
            return {"definition": definition, "attempts": attempt}
        messages += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": "That graph is invalid:\n- " + "\n- ".join(errors) + "\nReturn the corrected graph JSON only."},
        ]
    raise HTTPException(status_code=422, detail={"message": "Copilot could not produce a valid graph.", "errors": errors})


# ------------------------------------------------------------------- editing

PATCH_FORMAT = """Reply with ONLY a JSON patch:
{"summary":"what you changed","operations":[
  {"op":"add_node","node":{"id":"...","type":"...","label":"...","config":{}}},
  {"op":"update_node","id":"...","changes":{"label":"...","config":{...}}},
  {"op":"remove_node","id":"..."},
  {"op":"add_edge","source":"...","target":"...","source_handle":"main"},
  {"op":"remove_edge","source":"...","target":"..."}]}"""


def apply_graph_patch(definition: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply patch operations to a copy of the graph definition and return it."""
    graph = copy.deepcopy(definition)
    nodes: list[dict[str, Any]] = graph.setdefault("nodes", [])
    edges: list[dict[str, Any]] = graph.setdefault("edges", [])
    for operation in patch.get("operations", []):
        op = operation.get("op")
        if op == "add_node":
            node = dict(operation["node"])
            if any(n["id"] == node["id"] for n in nodes):
                raise CopilotError(f"Node '{node['id']}' already exists.")
            node.setdefault("config", {})
            nodes.append(node)
        elif op == "update_node":
            node = next((n for n in nodes if n["id"] == operation["id"]), None)
            if node is None:
                raise CopilotError(f"Node '{operation['id']}' not found.")
            changes = operation.get("changes", {})
            if "config" in changes:
                node["config"] = {**node.get("config", {}), **changes["config"]}
            for key, value in changes.items():
                if key not in ("config", "id"):
                    node[key] = value
        elif op == "remove_node":
            nodes[:] = [n for n in nodes if n["id"] != operation["id"]]
            edges[:] = [e for e in edges if operation["id"] not in (e.get("source"), e.get("target"))]
            if isinstance(graph.get("positions"), dict):
                graph["positions"].pop(operation["id"], None)
        elif op == "add_edge":
            edge = {"source": operation["source"], "target": operation["target"]}
            if operation.get("source_handle"):
                edge["source_handle"] = operation["source_handle"]
            edges.append(edge)
        elif op == "remove_edge":
            edges[:] = [
                e for e in edges if not (e.get("source") == operation["source"] and e.get("target") == operation["target"])
            ]
        else:
            raise CopilotError(f"Unknown patch operation '{op}'.")
    return graph


async def edit_graph_from_prompt(
    definition: dict[str, Any], instruction: str, *, selected_node_id: str | None = None
) -> dict[str, Any]:
    """Turn an instruction ("add a Slack step after HTTP") into a validated patch + preview graph."""
    messages = build_copilot_context(
        f"Instruction: {instruction}\n\n{PATCH_FORMAT}", graph=definition, selected_node_id=selected_node_id
    )
    errors: list[str] = []
    for _ in range(MAX_REPAIR_ATTEMPTS):
        reply = await _chat(messages, temperature=0.2)
        try:
            patch = _extract_json(reply)
            if not isinstance(patch, dict):
                raise CopilotError("Patch must be a JSON object.")
            preview = apply_graph_patch(definition, patch)
            errors = validate_generated_graph(preview)
        except (CopilotError, KeyError, TypeError) as error:
            errors = [str(error)]
        if not errors:
            return {"patch": patch, "definition": preview, "summary": patch.get("summary", "")}
        messages += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": "Applying that patch fails:\n- " + "\n- ".join(errors) + "\nReturn a corrected patch only."},
        ]
    raise HTTPException(status_code=422, detail={"message": "Copilot could not produce a valid edit.", "errors": errors})


async def explain_node(definition: dict[str, Any], node_id: str) -> str:
    """Plain-English explanation of what a node does, for beginners."""
    if not any(n.get("id") == node_id for n in definition.get("nodes", [])):
        raise HTTPException(status_code=404, detail="Node not found in graph.")
    return await _chat(build_copilot_context(
        "Explain in simple words (max 120 words) what the selected node does, what data it receives "
        "and what it outputs in this graph.",
        graph=definition, selected_node_id=node_id,
    ))


async def fix_failed_run(definition: dict[str, Any], run_state: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    """Diagnose a failed run and suggest a fix: a graph patch, a config change, or a plain retry."""
    failed = {
        node_id: info for node_id, info in ((run_state or {}).get("nodes") or {}).items()
        if info.get("status") == "failed" or info.get("error")
    }
    extra = (
        f"The run failed with: {error or 'unknown error'}\n"
        f"Failed nodes:\n```json\n{json.dumps(failed, indent=1, default=str)[:6000]}\n```"
    )
    messages = build_copilot_context(
        'Diagnose the failure. Reply with ONLY JSON: {"explanation":"...","action":"edit_graph|change_config|retry",'
        '"patch":<patch object or null>}\n' + PATCH_FORMAT,
        graph=definition, extra=extra,
    )
    reply = await _chat(messages, temperature=0.2)
    try:
        result = _extract_json(reply)
    except CopilotError:
        return {"explanation": reply, "action": "retry", "patch": None}
    patch = result.get("patch") if isinstance(result, dict) else None
    if patch:
        try:
            preview = apply_graph_patch(definition, patch)
            result["validation_errors"] = validate_generated_graph(preview)
            result["definition"] = preview
        except (CopilotError, KeyError, TypeError) as err:
            result["validation_errors"] = [str(err)]
    return result

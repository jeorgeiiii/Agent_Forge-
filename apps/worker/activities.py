"""Temporal activities: run a single node, checkpoint run state, fire schedules."""

import base64
import mimetypes
import json
import logging
import time
from typing import Any

from urllib.parse import quote

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

import agentforge_core  # noqa: F401 - registers built-in node specs
from agentforge_core.expressions import ExpressionError, build_expression_context, render_parameter, render_template
from agentforge_core.connectors import auth_headers, build_request, get_connector, list_connectors, read_result
from agentforge_core.registry import get_node_executor, register_node_executor
from agentforge_core.runtime import evaluate_condition, merge_branches

import api_client
from ssrf import validate_public_http_url

log = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 1_000_000
MAX_FILE_BYTES = 25 * 1024 * 1024  # keep in line with the API's MAX_FILE_BYTES
RESTRICTED_HEADERS = {"host", "content-length", "transfer-encoding"}


class NodeContext:
    """Everything an executor needs to run one node."""

    def __init__(self, request: dict[str, Any]):
        self.node: dict[str, Any] = request["node"]
        self.input: Any = request.get("input")
        self.inputs: dict[str, Any] = request.get("inputs") or {}
        self.run_input: Any = request.get("run_input")
        self.order: list[str] = request.get("order") or []
        self.usage: dict[str, int] | None = None  # set by nodes that call an LLM
        self.workflow_id: str = (request.get("run") or {}).get("workflow_id", "")
        self.expressions = build_expression_context(
            run_input=self.run_input,
            node_outputs=request.get("node_outputs") or {},
            labels=request.get("labels") or {},
            current_input=self.input,
            run_meta=request.get("run") or {},
            variables=request.get("variables") or {},
            tool_args=request.get("tool_args"),
        )

    @property
    def raw_config(self) -> dict[str, Any]:
        return self.node.get("config") or {}

    def param(self, name: str, default: Any = None) -> Any:
        return render_parameter(self.raw_config.get(name, default), self.expressions)


# --------------------------------------------------------------------------- #
# Node executors - each returns {output_handle: value}
# --------------------------------------------------------------------------- #


async def _passthrough(ctx: NodeContext) -> dict[str, Any]:
    return {"main": ctx.input}


async def _template(ctx: NodeContext) -> dict[str, Any]:
    return {"main": render_template(str(ctx.raw_config.get("template", "")), ctx.expressions)}


async def _set(ctx: NodeContext) -> dict[str, Any]:
    fields = ctx.param("fields", {}) or {}
    if not isinstance(fields, dict):
        raise ValueError("Set node 'fields' must be an object.")
    base = dict(ctx.input) if ctx.raw_config.get("keep_input") and isinstance(ctx.input, dict) else {}
    base.update(fields)
    return {"main": base}


async def _if(ctx: NodeContext) -> dict[str, Any]:
    branch = "true" if evaluate_condition(ctx.raw_config.get("condition"), ctx.expressions) else "false"
    return {branch: ctx.input}


async def _switch(ctx: NodeContext) -> dict[str, Any]:
    for case in ctx.raw_config.get("cases") or []:
        if evaluate_condition(case.get("condition"), ctx.expressions):
            return {str(case.get("id")): ctx.input}
    return {"default": ctx.input}


async def _merge(ctx: NodeContext) -> dict[str, Any]:
    mode = ctx.raw_config.get("mode", "append")
    return {"main": merge_branches(ctx.inputs, mode=mode, key=ctx.raw_config.get("key"), order=ctx.order)}


# --------------------------------------------------------------------------- files


def is_file_ref(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("$file"), dict) and "id" in value["$file"]


async def store_file(data: bytes, name: str, mime: str, workflow_id: str) -> dict[str, Any]:
    """Store bytes through the API; returns the {"$file": ...} reference nodes pass around."""
    response = await api_client.call_raw("POST", "/internal/files", content=data, headers={
        "X-File-Name": name.encode("ascii", "replace").decode(), "X-File-Mime": mime, "X-Workflow-Id": workflow_id})
    return response.json()


async def read_file_ref(ref: Any, workflow_id: str) -> tuple[bytes, str]:
    if not is_file_ref(ref):
        raise ValueError('body_file must be a file reference like {"$file": {"id": ...}}.')
    response = await api_client.call_raw("GET", f"/internal/files/{ref['$file']['id']}?workflow_id={workflow_id}")
    return response.content, response.headers.get("content-type", "application/octet-stream").split(";")[0]


def _filename_from(response: httpx.Response, url: str) -> str:
    """Name from Content-Disposition, else the URL; always with an extension matching the type."""
    disposition = response.headers.get("content-disposition", "")
    if "filename=" in disposition:
        name = disposition.split("filename=", 1)[1].strip().strip('"').split(";")[0]
    else:
        name = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    name = name or "download"
    if "." not in name:
        mime = response.headers.get("content-type", "").split(";")[0].strip()
        name += mimetypes.guess_extension(mime) or ""
    return name


async def fetch_credential(credential_id: Any, workflow_id: str) -> dict[str, Any]:
    """Decrypted saved key from the API. The run's workflow_id lets the API refuse keys that
    belong to a different workspace than the workflow being run."""
    query = f"?workflow_id={quote(workflow_id, safe='')}" if workflow_id else ""
    return await api_client.call("GET", f"/internal/credentials/{int(credential_id)}{query}")


async def _auth_headers(config: dict[str, Any], workflow_id: str = "") -> dict[str, str]:
    credential_id = config.get("credential_id")
    auth_type = config.get("auth_type", "bearer" if credential_id else "none")
    if not credential_id or auth_type == "none":
        return {}
    credential = await fetch_credential(credential_id, workflow_id)
    data: dict[str, Any] = credential.get("data") or {}
    secret = data.get("access_token") or data.get("token") or data.get("api_key") or data.get("value")
    if auth_type == "bearer":
        if not secret:
            raise ValueError("Credential has no token/api_key for bearer auth.")
        return {"Authorization": f"Bearer {secret}"}
    if auth_type == "header":
        name = config.get("auth_header_name") or data.get("header_name") or "X-API-Key"
        if not secret:
            raise ValueError("Credential has no value for header auth.")
        return {str(name): str(secret)}
    if auth_type == "basic":
        raw = f"{data.get('username', '')}:{data.get('password', '')}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
    raise ValueError(f"Unsupported auth_type '{auth_type}'.")


async def _http(ctx: NodeContext) -> dict[str, Any]:
    url = str(ctx.param("url", ""))
    await validate_public_http_url(url)
    method = str(ctx.param("method", "GET")).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError("HTTP node method must be GET, POST, PUT, PATCH or DELETE.")
    headers = ctx.param("headers", {}) or {}
    if not isinstance(headers, dict) or any(str(k).lower() in RESTRICTED_HEADERS for k in headers):
        raise ValueError("HTTP node headers must be a mapping without restricted headers.")
    headers = {str(k): str(v) for k, v in headers.items()}
    headers.update(await _auth_headers(ctx.raw_config, ctx.workflow_id))

    body = ctx.param("body")
    request_kwargs: dict[str, Any] = {}
    body_file = ctx.param("body_file")
    if body_file:
        content, file_mime = await read_file_ref(body_file, ctx.workflow_id)
        request_kwargs["content"] = content
        headers.setdefault("Content-Type", file_mime)
    elif isinstance(body, (dict, list)):
        request_kwargs["json"] = body
    elif body is not None:
        request_kwargs["content"] = str(body)
    as_file = ctx.raw_config.get("response_format") == "file"
    limit = MAX_FILE_BYTES if as_file else MAX_RESPONSE_BYTES

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            async with client.stream(method, url, headers=headers, **request_kwargs) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise ValueError(f"HTTP node response exceeded the {limit // (1024 * 1024)} MB limit."
                                         + ("" if as_file else ' Use response_format "file" for large/binary responses.'))
                    chunks.append(chunk)
                if as_file:
                    name = _filename_from(response, url)
                    mime = response.headers.get("content-type", "application/octet-stream").split(";")[0]
                    ref = await store_file(b"".join(chunks), name, mime, ctx.workflow_id)
                    return {"main": {"status_code": response.status_code, "body": ref, "file": ref}}
                text = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                try:
                    data = json.loads(text) if "json" in response.headers.get("content-type", "") else text
                except json.JSONDecodeError:
                    data = text
                return {"main": {"status_code": response.status_code, "body": data}}
    except httpx.HTTPError as error:
        raise ValueError(f"HTTP node request failed: {error}") from error


class ConnectorRetryableError(RuntimeError):
    """Rate limited (429) or the service is down (5xx): Temporal retries the step with backoff."""


CONNECTOR_TIMEOUT_SECONDS = 20
_CONNECTOR_STATUS_HELP = {
    401: "the saved key was rejected - check it is correct and not expired",
    403: "the key is not allowed to do this - check its permissions/scopes",
    404: "not found - check the IDs/names, and that the key can see this item",
}


def _connector_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=CONNECTOR_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False)


async def _connector(ctx: NodeContext) -> dict[str, Any]:
    """Integration node (Slack, GitHub, ...): build the request from the catalog, add the saved key, send it."""
    connector = get_connector(ctx.node["type"])
    config = {name: ctx.param(name) for name in ctx.raw_config}  # resolve {{ expressions }}
    request = build_request(connector, config)
    await validate_public_http_url(request["url"])
    headers = dict(request["headers"])
    if connector.needs_key:
        credential = await fetch_credential(ctx.raw_config.get("credential_id"), ctx.workflow_id)
        data: dict[str, Any] = credential.get("data") or {}
        key = data.get("api_key") or data.get("token") or data.get("access_token") or data.get("value")
        if not key:
            raise ValueError(f"The saved key for {connector.name} is empty.")
        headers.update(auth_headers(connector, str(key)))
    try:
        async with _connector_client() as client:
            response = await client.request(request["method"], request["url"], params=request["params"],
                                            json=request.get("json"), headers=headers)
    except httpx.HTTPError as error:
        raise ConnectorRetryableError(f"{connector.name} could not be reached: {error}") from error
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError(f"{connector.name} reply exceeded the 1 MB limit; ask for fewer results.")
    if response.status_code == 429 or response.status_code >= 500:
        raise ConnectorRetryableError(f"{connector.name} returned {response.status_code} (will retry).")
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text
    if response.status_code >= 400:
        detail = payload.get("message") or payload.get("error") if isinstance(payload, dict) else str(payload)[:200]
        hint = _CONNECTOR_STATUS_HELP.get(response.status_code, "the request was refused")
        raise ValueError(f"{connector.name} {response.status_code}: {hint}. {str(detail or '')[:300]}".strip())
    action = str(config.get("action"))
    return {"main": {"ok": True, "status_code": response.status_code, "data": read_result(connector, action, payload)}}


async def _database(ctx: NodeContext) -> dict[str, Any]:
    """'Company database' block / agent tool. The query runs inside the API (which holds the encrypted address
    and checks this run's workspace); the worker only sends the request and gets rows back."""
    config = {name: ctx.param(name) for name in ctx.raw_config}
    source_id = config.get("data_source_id")
    if not source_id:
        raise ValueError("Pick a company database for this block.")
    body = {k: config.get(k) for k in ("action", "sql", "params", "table", "max_rows", "tables", "concepts")
            if config.get(k) is not None}
    query = f"?workflow_id={quote(ctx.workflow_id, safe='')}"
    try:
        result = await api_client.call("POST", f"/internal/data-sources/{int(source_id)}/run{query}", json=body,
                                       timeout=60)
    except api_client.InternalApiError as error:
        if error.status_code and 400 <= error.status_code < 500:  # the database's/validation message, not retryable
            raise ValueError(_api_detail(str(error))) from error
        raise
    return {"main": result}


def _api_detail(message: str) -> str:
    """'POST /x failed with 422: {"detail":"..."}' -> just the detail sentence."""
    _, _, tail = message.partition(": ")
    try:
        detail = json.loads(tail).get("detail")
        return detail if isinstance(detail, str) else message
    except (ValueError, AttributeError):
        return message


DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
LLM_TIMEOUT_SECONDS = 60


def _llm_client() -> httpx.AsyncClient:
    """Factory so tests can swap in a mock transport."""
    return httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False)


async def ensure_llm_quota(workflow_id: str) -> None:
    """Cluster 13: stop before spending tokens the workspace no longer has (non-retryable)."""
    if not workflow_id or not api_client.is_configured():
        return
    try:
        verdict = await api_client.call("GET", f"/internal/quota/llm?workflow_id={workflow_id}")
    except api_client.InternalApiError as error:  # quota service down: allow, but say so
        log.warning("LLM quota check unavailable: %s", error)
        return
    if verdict and not verdict.get("allowed", True):
        raise ApplicationError(verdict.get("reason") or "LLM quota exceeded.", type="QuotaExceeded", non_retryable=True)


class LLMProviderError(RuntimeError):
    """Transient provider failure (429 / 5xx / network) - Temporal may retry it."""


async def chat_completion(credential_id: Any, base_url: Any, body: dict[str, Any],
                          workflow_id: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """POST one chat-completions request. Returns (first choice, usage).

    The API key is fetched from the vault here, inside the activity, so it never
    appears in Temporal workflow history.
    """
    if not credential_id:
        raise ValueError("A credential_id with the provider API key is required.")
    credential = await fetch_credential(credential_id, workflow_id)
    data: dict[str, Any] = credential.get("data") or {}
    api_key = data.get("api_key") or data.get("token") or data.get("access_token") or data.get("value")
    if not api_key:
        raise ValueError("The LLM credential has no api_key.")

    url = f"{str(base_url or data.get('base_url') or DEFAULT_LLM_BASE_URL).rstrip('/')}/chat/completions"
    await validate_public_http_url(url)
    try:
        async with _llm_client() as client:
            response = await client.post(url, json=body, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as error:
        raise LLMProviderError(f"LLM request failed: {error}") from error
    if response.status_code == 429 or response.status_code >= 500:
        raise LLMProviderError(f"LLM provider returned {response.status_code}: {response.text[:300]}")
    if response.status_code >= 400:
        raise ValueError(f"LLM provider rejected the request ({response.status_code}): {response.text[:300]}")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError("LLM response exceeded the 1 MB limit.")
    try:
        payload = response.json()
        choice = payload["choices"][0]
        choice["message"]  # noqa: B018 - shape check
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ValueError("LLM provider returned an unexpected response shape.") from error
    raw_usage = payload.get("usage") or {}
    usage = {
        "input_tokens": int(raw_usage.get("prompt_tokens") or 0),
        "output_tokens": int(raw_usage.get("completion_tokens") or 0),
        "llm_calls": 1,
    }
    choice.setdefault("model", payload.get("model", body.get("model")))
    return choice, usage


def _sampling(config: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    for key in ("temperature", "max_tokens"):
        if config.get(key) is not None:
            body[key] = config[key]
    if config.get("json_output"):
        body["response_format"] = {"type": "json_object"}
    return body


async def _llm(ctx: NodeContext) -> dict[str, Any]:
    """LLM node: one chat-completions call to an OpenAI-compatible endpoint."""
    config = ctx.raw_config
    messages = []
    system = ctx.param("system")
    if system:
        messages.append({"role": "system", "content": str(system)})
    messages.append({"role": "user", "content": str(ctx.param("prompt", ""))})
    rendered = {k: ctx.param(k) for k in ("temperature", "max_tokens") if config.get(k) is not None}
    body = _sampling({**rendered, "json_output": config.get("json_output")}, {"model": str(ctx.param("model")), "messages": messages})
    await ensure_llm_quota(ctx.workflow_id)
    choice, ctx.usage = await chat_completion(config.get("credential_id"), ctx.param("base_url"), body, ctx.workflow_id)
    text = choice["message"].get("content") or ""
    result: dict[str, Any] = {
        "text": text,
        "model": choice["model"],
        "finish_reason": choice.get("finish_reason"),
        "usage": ctx.usage,
    }
    if config.get("json_output"):
        try:
            result["json"] = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"LLM did not return valid JSON: {error}") from error
    return {"main": result}


for _type in ("start", "trigger", "manual_trigger", "webhook_trigger", "schedule_trigger",
              "chat_trigger", "end", "output", "noop"):
    register_node_executor(_type, _passthrough)
register_node_executor("template", _template)
register_node_executor("set", _set)
register_node_executor("if", _if)
register_node_executor("switch", _switch)
register_node_executor("merge", _merge)
register_node_executor("http", _http)
register_node_executor("llm", _llm)
register_node_executor("database", _database)
for _connector_spec in list_connectors():
    register_node_executor(_connector_spec.id, _connector)


# --------------------------------------------------------------------------- #
# Activities
# --------------------------------------------------------------------------- #


@activity.defn
async def execute_node(request: dict[str, Any]) -> dict[str, Any]:
    """Run one node with its resolved inputs; returns outputs and timing."""
    ctx = NodeContext(request)
    started = time.perf_counter()
    log.info("node %s (%s) started", ctx.node.get("id"), ctx.node.get("type"),
             extra={"request_id": (request.get("run") or {}).get("request_id"), "workflow_id": ctx.workflow_id,
                    "node_id": ctx.node.get("id")})
    try:
        executor = get_node_executor(ctx.node["type"])
        outputs = await executor(ctx)
    except (ExpressionError, ValueError, KeyError, TypeError) as error:
        # Configuration/data errors will not fix themselves on retry.
        raise ApplicationError(str(error), type=type(error).__name__, non_retryable=True) from error
    result = {"outputs": outputs, "duration_ms": round((time.perf_counter() - started) * 1000, 2)}
    if ctx.usage:
        result["usage"] = ctx.usage
    return result


@activity.defn
async def llm_chat(request: dict[str, Any]) -> dict[str, Any]:
    """One step of an agent loop: send the conversation + tool definitions, get the next message.

    request: {"credential_id", "base_url", "model", "messages", "tools", "temperature", "max_tokens", "json_output"}
    """
    body: dict[str, Any] = {"model": request["model"], "messages": request["messages"]}
    if request.get("tools"):
        body["tools"] = request["tools"]
    _sampling(request, body)
    await ensure_llm_quota(request.get("workflow_id", ""))
    try:
        choice, usage = await chat_completion(request.get("credential_id"), request.get("base_url"), body,
                                              request.get("workflow_id", ""))
    except ValueError as error:
        raise ApplicationError(str(error), type="ValueError", non_retryable=True) from error
    message = choice["message"]
    return {
        "message": {k: message[k] for k in ("role", "content", "tool_calls") if message.get(k) is not None},
        "finish_reason": choice.get("finish_reason"),
        "model": choice["model"],
        "usage": usage,
    }


@activity.defn
async def load_memory(key: str, limit: int) -> dict[str, Any]:
    """{"summary", "covers_until", "messages": [{id, role, content}]} of a conversation, via the API."""
    result = await api_client.call("GET", f"/internal/memory/{key}?limit={int(limit)}")
    return {"summary": result.get("summary"), "covers_until": result.get("covers_until", 0),
            "messages": result.get("messages") or []}


@activity.defn
async def save_memory(key: str, messages: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> None:
    """Append this run's user message + agent answer to the conversation."""
    await api_client.call("POST", f"/internal/memory/{key}", json={"messages": messages, "meta": meta or {}})


@activity.defn
async def save_summary(key: str, content: str, covers_until: int, meta: dict[str, Any] | None = None) -> None:
    """Store the new running summary (replaces every message with id <= covers_until)."""
    await api_client.call("POST", f"/internal/memory/{key}/summary",
                          json={"content": content, "covers_until": covers_until, "meta": meta or {}})


@activity.defn
async def emit_events(workflow_id: str, source: str, events: list[dict[str, Any]]) -> None:
    """Deliver run events right away (live view) without sending the whole run state."""
    if not api_client.is_configured():
        return
    try:
        await api_client.call("POST", f"/internal/runs/{workflow_id}/events", json={"source": source, "events": events})
    except api_client.InternalApiError as error:
        if error.status_code == 404:  # no run row: nothing to attach the events to
            return
        raise


@activity.defn
async def checkpoint_state(workflow_id: str, state: dict[str, Any]) -> None:
    """Persist run state to Postgres (through the API) so it survives crashes and can be inspected."""
    if not api_client.is_configured():
        log.warning("INTERNAL_API_TOKEN not set; skipping checkpoint for %s", workflow_id)
        return
    try:
        await api_client.call("POST", f"/internal/runs/{workflow_id}/checkpoint", json=state)
    except api_client.InternalApiError as error:
        if error.status_code == 404:  # no run row (e.g. started outside the API): retrying cannot help
            log.warning("No run row for %s; checkpoint dropped", workflow_id)
            return
        raise


@activity.defn
async def fire_schedule_trigger(trigger_id: int) -> dict[str, Any]:
    """Called when a Temporal Schedule fires: asks the API to start a run of the published graph."""
    return await api_client.call("POST", f"/internal/triggers/{trigger_id}/fire", json={})

"""Cluster 11 part 2 - run history queries, cost/usage reporting, retry, OpenTelemetry export.

Part 1 (services/events.py + the SSE stream) records what happens; this module
answers questions about it.
"""

from __future__ import annotations

import copy
import hashlib
from collections import defaultdict
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import GraphRecord, RunEventRecord, RunRecord
from .common import NotFound, ServiceError, as_utc
from .events import run_cost
from .graphs import list_graphs_for_workspace
from ..utils import paginate

RETRYABLE_STATUSES = {"failed", "cancelled"}


# --------------------------------------------------------------------------- #
# list_runs_for_graph - filters + cursor pagination
# --------------------------------------------------------------------------- #


def list_runs_for_graph(
    db: Session,
    graph: GraphRecord,
    *,
    status: str | None = None,
    trigger: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> tuple[list[RunRecord], int | None]:
    """Runs of a graph, newest first. Returns (page, next_cursor); pass next_cursor as before_id."""
    limit = max(1, min(limit, 200))
    query = select(RunRecord).where(RunRecord.graph_id == graph.id)
    if status:
        query = query.where(RunRecord.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if trigger:
        query = query.where(RunRecord.trigger_kind == trigger)
    if since:
        query = query.where(RunRecord.created_at >= since)
    if until:
        query = query.where(RunRecord.created_at < until)
    return paginate(db, query, RunRecord.id, before_id=before_id, limit=limit, max_limit=200)


# --------------------------------------------------------------------------- #
# get_run_details / get_run_cost
# --------------------------------------------------------------------------- #


def _events(db: Session, run_id: int, types: tuple[str, ...] | None = None) -> list[RunEventRecord]:
    query = select(RunEventRecord).where(RunEventRecord.run_id == run_id)
    if types:
        query = query.where(RunEventRecord.type.in_(types))
    return list(db.scalars(query.order_by(RunEventRecord.id)))


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return round((as_utc(end) - as_utc(start)).total_seconds() * 1000)


def get_run_cost(db: Session, run: RunRecord) -> dict[str, Any]:
    """Cost and tokens of a run, broken down by model and by agent (node path)."""
    by_model: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": None})
    by_agent: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": None})
    for event in _events(db, run.id, ("llm.call",)):
        data = event.data or {}
        for bucket in (by_model[data.get("model") or "unknown"], by_agent[event.node_id or "unknown"]):
            bucket["calls"] += 1
            bucket["input_tokens"] += int(data.get("input_tokens", 0))
            bucket["output_tokens"] += int(data.get("output_tokens", 0))
            if data.get("cost_usd") is not None:
                bucket["cost_usd"] = round((bucket["cost_usd"] or 0) + data["cost_usd"], 8)
    unpriced = sorted(m for m, b in by_model.items() if b["cost_usd"] is None)
    return {
        "run_id": run.id,
        "total_cost_usd": run_cost(db, run.id),
        "input_tokens": sum(b["input_tokens"] for b in by_model.values()),
        "output_tokens": sum(b["output_tokens"] for b in by_model.values()),
        "llm_calls": sum(b["calls"] for b in by_model.values()),
        "by_model": dict(by_model),
        "by_agent": dict(by_agent),
        "unpriced_models": unpriced,  # add these to MODEL_PRICES to price them
    }


def get_run_details(db: Session, run: RunRecord) -> dict[str, Any]:
    """Everything the run inspector needs in one call."""
    state = run.state or {}
    events = _events(db, run.id)
    counts: dict[str, int] = defaultdict(int)
    for event in events:
        counts[event.type] += 1
    nodes = []
    for node_id, info in (state.get("nodes") or {}).items():
        started = info.get("started_at")
        finished = info.get("finished_at")
        nodes.append({
            "node_id": node_id,
            "status": info.get("status"),
            "error": info.get("error"),
            "started_at": started,
            "finished_at": finished,
            "duration_ms": _duration_ms(datetime.fromisoformat(started) if started else None,
                                        datetime.fromisoformat(finished) if finished else None),
            "output": _first_output(info.get("outputs")),
            "agent": info.get("agent"),
        })
    nodes.sort(key=lambda n: (n["started_at"] or "~", n["finished_at"] or "~", n["node_id"]))  # execution order; skipped last
    cost = get_run_cost(db, run)
    return {
        "run": {
            "id": run.id, "graph_id": run.graph_id, "graph_version": run.graph_version, "status": run.status,
            "trigger": run.trigger_kind, "input": run.input, "output": run.output, "error": run.error,
            "created_at": run.created_at, "finished_at": run.finished_at,
            "duration_ms": _duration_ms(run.created_at, run.finished_at), "retry_of_run_id": run.retry_of_run_id,
            "waiting_for": state.get("waiting_for"),
        },
        "nodes": nodes,
        "usage": {**(state.get("usage") or {}), "cost_usd": cost["total_cost_usd"]},
        "cost": cost,
        "event_counts": dict(counts),
        "last_event_id": events[-1].id if events else 0,  # start the live stream from here
        "can_retry": run.status in RETRYABLE_STATUSES,
    }


def _first_output(outputs: Any) -> Any:
    if isinstance(outputs, dict) and outputs:
        return next(iter(outputs.values()))
    return None


# --------------------------------------------------------------------------- #
# get_usage_summary
# --------------------------------------------------------------------------- #

GROUPS = ("day", "graph", "model", "trigger")


def get_usage_summary(
    db: Session,
    user_id: int | None,
    *,
    workspace_id: int | None = None,
    graph_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    group_by: str = "day",
) -> dict[str, Any]:
    """Runs, tokens and cost aggregated by day / graph / model / trigger, over the graphs the caller can see."""
    if group_by not in GROUPS:
        raise ServiceError(f"group_by must be one of {list(GROUPS)}.", 422)
    graphs = {g.id: g for g in list_graphs_for_workspace(db, user_id, workspace_id)}
    if graph_id is not None:
        if graph_id not in graphs:
            raise NotFound("Graph not found.")
        graphs = {graph_id: graphs[graph_id]}
    if not graphs:
        return {"group_by": group_by, "totals": _empty_bucket(), "groups": []}

    run_query = select(RunRecord).where(RunRecord.graph_id.in_(graphs))
    if since:
        run_query = run_query.where(RunRecord.created_at >= since)
    if until:
        run_query = run_query.where(RunRecord.created_at < until)
    runs = {r.id: r for r in db.scalars(run_query)}

    groups: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    totals = _empty_bucket()

    def key_for(run: RunRecord, model: str | None = None) -> str:
        if group_by == "day":
            return as_utc(run.created_at).date().isoformat()
        if group_by == "graph":
            return f"{run.graph_id}:{graphs[run.graph_id].name}"
        if group_by == "trigger":
            return run.trigger_kind
        return model or "(no LLM)"

    for run in runs.values():
        if group_by != "model":
            for bucket in (groups[key_for(run)], totals):
                bucket["runs"] += 1
                bucket["failed_runs"] += run.status == "failed"
        else:
            totals["runs"] += 1
            totals["failed_runs"] += run.status == "failed"
    if runs:
        llm_events = db.scalars(select(RunEventRecord).where(
            RunEventRecord.run_id.in_(runs), RunEventRecord.type == "llm.call"))
        for event in llm_events:
            data = event.data or {}
            run = runs[event.run_id]
            for bucket in (groups[key_for(run, data.get("model"))], totals):
                bucket["llm_calls"] += 1
                bucket["input_tokens"] += int(data.get("input_tokens", 0))
                bucket["output_tokens"] += int(data.get("output_tokens", 0))
                bucket["cost_usd"] = round(bucket["cost_usd"] + float(data.get("cost_usd") or 0), 8)
    return {
        "group_by": group_by,
        "since": since, "until": until,
        "totals": totals,
        "groups": [{"key": k, **v} for k, v in sorted(groups.items())],
    }


def _empty_bucket() -> dict[str, Any]:
    return {"runs": 0, "failed_runs": 0, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


# --------------------------------------------------------------------------- #
# retry_run - resume from the node that failed
# --------------------------------------------------------------------------- #


def retry_checkpoint(run: RunRecord) -> dict[str, Any]:
    """The failed run's saved state, cleaned so a new run can resume from it.

    Successful nodes keep their outputs (they are not executed again); failed,
    running and waiting nodes are dropped so the workflow re-runs them.
    """
    state = copy.deepcopy(run.state or {})
    if not state.get("nodes"):
        raise ServiceError("This run has no saved progress to resume from; start a new run instead.", 409)
    state["nodes"] = {k: v for k, v in state["nodes"].items() if v.get("status") in ("success", "skipped")}
    for key in ("usage", "error", "output", "waiting_for", "seq", "event_seq"):
        state.pop(key, None)  # new run = fresh usage, cost and event numbering
    state["status"] = "running"
    return state


async def retry_run(db: Session, user_id: int | None, run: RunRecord) -> RunRecord:
    """Start a new run of the SAME graph version that skips everything the old run finished."""
    from .runs import start_run  # local import: runs imports this module's siblings

    if run.status not in RETRYABLE_STATUSES:
        raise ServiceError(f"Only failed or cancelled runs can be retried (this one is {run.status}).", 409)
    checkpoint = retry_checkpoint(run)
    return await start_run(
        db, run.graph, run.input, user_id=user_id, trigger_kind="retry",
        checkpoint=checkpoint, version=run.graph_version, retry_of=run.id,
    )


# --------------------------------------------------------------------------- #
# OpenTelemetry (OTLP/HTTP JSON) export
# --------------------------------------------------------------------------- #


def _hex_id(seed: str, length: int) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:length]


def _nanos(value: str | datetime | None) -> int:
    if value is None:
        return 0
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    return int(as_utc(dt).timestamp() * 1e9)


def _attrs(values: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out.append({"key": key, "value": {"boolValue": value}})
        elif isinstance(value, int):
            out.append({"key": key, "value": {"intValue": str(value)}})
        elif isinstance(value, float):
            out.append({"key": key, "value": {"doubleValue": value}})
        elif isinstance(value, (list, tuple)):
            out.append({"key": key, "value": {"arrayValue": {"values": [{"stringValue": str(v)} for v in value]}}})
        else:
            out.append({"key": key, "value": {"stringValue": str(value)}})
    return out


def build_otlp_trace(db: Session, run: RunRecord) -> dict[str, Any]:
    """The run as an OpenTelemetry trace (OTLP/HTTP JSON), using the GenAI semantic conventions.

    Span tree: run -> node -> agent (per agent path, e.g. boss/researcher)
    -> llm "chat <model>" / tool "execute_tool <name>". IDs are derived from the
    run's workflow id, so exporting twice yields the same trace.
    """
    trace_id = _hex_id(run.workflow_id, 32)
    root_id = _hex_id(f"{run.workflow_id}/run", 16)
    events = _events(db, run.id)
    spans: list[dict[str, Any]] = []
    error_status = {"code": 2}  # STATUS_CODE_ERROR
    ok_status = {"code": 1}

    run_start = next((e.at for e in events if e.type == "run.started"), None) or run.created_at
    run_end = next((e.at for e in reversed(events) if e.type == "run.finished"), None) or run.finished_at or run_start
    spans.append({
        "traceId": trace_id, "spanId": root_id, "name": f"run {run.graph.name}", "kind": 1,
        "startTimeUnixNano": str(_nanos(run_start)), "endTimeUnixNano": str(_nanos(run_end)),
        "attributes": _attrs({"agentforge.run.id": run.id, "agentforge.graph.id": run.graph_id,
                              "agentforge.graph.version": run.graph_version, "agentforge.trigger": run.trigger_kind,
                              "agentforge.run.status": run.status}),
        "status": error_status if run.status == "failed" else ok_status,
    })

    node_span: dict[str, str] = {}
    starts = {e.node_id: e for e in events if e.type == "node.started"}
    for event in events:
        if event.type != "node.finished" or event.node_id not in starts:
            continue
        data = event.data or {}
        span_id = _hex_id(f"{run.workflow_id}/node/{event.node_id}", 16)
        node_span[event.node_id] = span_id
        spans.append({
            "traceId": trace_id, "spanId": span_id, "parentSpanId": root_id,
            "name": f"node {event.node_id}", "kind": 1,
            "startTimeUnixNano": str(_nanos(starts[event.node_id].at)), "endTimeUnixNano": str(_nanos(event.at)),
            "attributes": _attrs({"agentforge.node.id": event.node_id,
                                  "agentforge.node.type": (starts[event.node_id].data or {}).get("node_type"),
                                  "agentforge.node.status": data.get("status")}),
            "status": {"code": 2, "message": data.get("error") or ""} if data.get("status") == "failed" else ok_status,
        })

    # agent spans: one per agent path, covering its llm/tool events
    agent_events: dict[str, list[RunEventRecord]] = defaultdict(list)
    for event in events:
        if event.type in ("llm.call", "tool.call") and event.node_id:
            agent_events[event.node_id].append(event)
    agent_span: dict[str, str] = {}
    for path in sorted(agent_events, key=lambda p: p.count("/")):
        items = agent_events[path]
        parent_path = path.rsplit("/", 1)[0] if "/" in path else None
        parent = agent_span.get(parent_path) if parent_path else node_span.get(path)
        span_id = _hex_id(f"{run.workflow_id}/agent/{path}", 16)
        agent_span[path] = span_id
        first = min(_nanos(e.at) - int((e.data or {}).get("latency_ms", (e.data or {}).get("duration_ms", 0)) * 1e6) for e in items)
        spans.append({
            "traceId": trace_id, "spanId": span_id, "parentSpanId": parent or root_id,
            "name": f"invoke_agent {path}", "kind": 1,
            "startTimeUnixNano": str(first), "endTimeUnixNano": str(max(_nanos(e.at) for e in items)),
            "attributes": _attrs({"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": path}),
            "status": ok_status,
        })
        for event in items:
            data = event.data or {}
            end = _nanos(event.at)
            if event.type == "llm.call":
                name, start = f"chat {data.get('model')}", end - int(data.get("latency_ms", 0) * 1e6)
                attrs = {"gen_ai.operation.name": "chat", "gen_ai.request.model": data.get("model"),
                         "gen_ai.response.model": data.get("model"),
                         "gen_ai.usage.input_tokens": int(data.get("input_tokens", 0)),
                         "gen_ai.usage.output_tokens": int(data.get("output_tokens", 0)),
                         "gen_ai.response.finish_reasons": [data.get("finish_reason")] if data.get("finish_reason") else None,
                         "agentforge.llm.cost_usd": data.get("cost_usd"), "agentforge.agent.step": data.get("step")}
                status = ok_status
            else:
                name, start = f"execute_tool {data.get('tool')}", end - int(data.get("duration_ms", 0) * 1e6)
                attrs = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": data.get("tool"),
                         "agentforge.tool.kind": data.get("kind")}
                status = ok_status if data.get("ok") else {"code": 2, "message": data.get("error") or ""}
            spans.append({
                "traceId": trace_id, "spanId": _hex_id(f"{run.workflow_id}/{event.source}/{event.seq}", 16),
                "parentSpanId": span_id, "name": name, "kind": 3,  # CLIENT
                "startTimeUnixNano": str(start), "endTimeUnixNano": str(end),
                "attributes": _attrs(attrs), "status": status,
            })
    return {"resourceSpans": [{
        "resource": {"attributes": _attrs({"service.name": "agentforge", "agentforge.workflow_id": run.workflow_id})},
        "scopeSpans": [{"scope": {"name": "agentforge.runs", "version": "1"}, "spans": spans}],
    }]}


def _otlp_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    for pair in filter(None, (p.strip() for p in get_settings().otel_exporter_otlp_headers.split(","))):
        key, _, value = pair.partition("=")
        if key and value:
            headers[key.strip()] = value.strip()
    return headers


async def export_trace(db: Session, run: RunRecord) -> dict[str, Any]:
    """Push the run's trace to the configured OTLP/HTTP endpoint (Jaeger, Tempo, Langfuse, ...)."""
    endpoint = get_settings().otel_exporter_otlp_endpoint
    if not endpoint:
        raise ServiceError("Set OTEL_EXPORTER_OTLP_ENDPOINT (e.g. http://localhost:4318/v1/traces) to export traces.", 503)
    trace = build_otlp_trace(db, run)
    span_count = len(trace["resourceSpans"][0]["scopeSpans"][0]["spans"])
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(endpoint, json=trace, headers=_otlp_headers())
    except httpx.HTTPError as error:
        raise ServiceError(f"Trace export failed: {error}", 502) from error
    if response.status_code >= 400:
        raise ServiceError(f"Trace collector returned {response.status_code}: {response.text[:200]}", 502)
    return {"exported_spans": span_count, "trace_id": trace["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"]}

"""Cluster 8 - Triggers: webhooks, schedules (Temporal Schedules) and chat.

Production triggers are created from the *published* version's trigger node
when a graph is published, and they always run that published version.
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleSpec,
)
from temporalio.service import RPCError

from ..config import get_settings
from ..models import GraphRecord, RunRecord, TriggerRecord
from .common import NotFound, ServiceError
from .runs import get_temporal_client, start_run, wait_for_run_result

log = logging.getLogger(__name__)

TRIGGER_KINDS = {"webhook_trigger": "webhook", "schedule_trigger": "schedule", "chat_trigger": "chat"}
SAFE_HEADER_PREFIXES = ("content-type", "user-agent", "x-")
HIDDEN_HEADERS = {"x-internal-token", "x-api-key", "authorization", "cookie"}
FIRST_RUN_DELAY_SECONDS = 5  # interval schedules: first run ~5 s after publish


def _schedule_id(trigger: TriggerRecord) -> str:
    return f"agentforge-schedule-{trigger.id}"


def webhook_url(trigger: TriggerRecord) -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/{'webhooks' if trigger.kind == 'webhook' else 'chat'}/{trigger.token}"


# ------------------------------------------------------------------ webhooks


def create_webhook_for_graph(db: Session, graph: GraphRecord, node: dict[str, Any], kind: str = "webhook") -> TriggerRecord:
    """Create (or refresh) the trigger row for a webhook/chat node with an unguessable URL token."""
    trigger = db.scalar(select(TriggerRecord).where(TriggerRecord.graph_id == graph.id, TriggerRecord.node_id == node["id"]))
    if trigger is None:
        trigger = TriggerRecord(graph_id=graph.id, node_id=node["id"], kind=kind, token=secrets.token_urlsafe(24))
        db.add(trigger)
    trigger.kind = kind
    trigger.config = node.get("config") or {}
    trigger.active = True
    db.flush()
    return trigger


def _active_trigger(db: Session, token: str, kind: str) -> tuple[TriggerRecord, GraphRecord]:
    trigger = db.scalar(select(TriggerRecord).where(TriggerRecord.token == token, TriggerRecord.kind == kind))
    graph = db.get(GraphRecord, trigger.graph_id) if trigger else None
    if trigger is None or graph is None or not trigger.active or not graph.is_active or graph.deleted_at is not None:
        raise NotFound("No active trigger at this URL.")
    return trigger, graph


async def handle_incoming_webhook(
    db: Session, token: str, *, method: str, body: Any, query: dict[str, str], headers: dict[str, str]
) -> tuple[RunRecord, TriggerRecord]:
    """Validate the webhook token and start a run with the request as input."""
    trigger, graph = _active_trigger(db, token, "webhook")
    allowed = str(trigger.config.get("method", "ANY")).upper()
    if allowed != "ANY" and method.upper() != allowed:
        raise ServiceError(f"This webhook only accepts {allowed}.", 405)
    safe_headers = {
        k: v for k, v in headers.items()
        if k.lower() not in HIDDEN_HEADERS and k.lower().startswith(SAFE_HEADER_PREFIXES)
    }
    run_input = {"method": method.upper(), "body": body, "query": query, "headers": safe_headers}
    run = await start_run(db, graph, run_input, check_access=False, trigger_kind="webhook", use_published=True)
    return run, trigger


async def respond_to_webhook(run: RunRecord, trigger: TriggerRecord) -> dict[str, Any]:
    """Sync mode returns the graph's output; async mode just acknowledges the run."""
    if trigger.config.get("response_mode") == "last_node":
        timeout = float(trigger.config.get("response_timeout_seconds", 30))
        return {"run_id": run.id, "output": await wait_for_run_result(run, timeout_seconds=min(timeout, 120))}
    return {"run_id": run.id, "status": run.status}


# ----------------------------------------------------------------- schedules


async def create_schedule_for_graph(trigger: TriggerRecord) -> str:
    """Register a cron/interval Temporal Schedule that fires ``ScheduleFireWorkflow``."""
    cron = trigger.config.get("cron")
    interval = trigger.config.get("interval_seconds")
    if cron:
        spec = ScheduleSpec(cron_expressions=[str(cron)])
    elif interval:
        every = max(60, int(interval))
        # Temporal fires intervals at epoch multiples of `every` (+offset). Without an
        # offset the first run waits for the next boundary (up to a full interval).
        # Phase-shift the schedule so the first run lands FIRST_RUN_DELAY s from now.
        offset = (int(time.time()) + FIRST_RUN_DELAY_SECONDS) % every
        spec = ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=every), offset=timedelta(seconds=offset))])
    else:
        raise ServiceError("Schedule trigger needs 'cron' or 'interval_seconds'.", 422)
    client = await get_temporal_client()
    schedule_id = _schedule_id(trigger)
    try:
        await client.get_schedule_handle(schedule_id).delete()
    except RPCError:
        pass  # did not exist
    await client.create_schedule(
        schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                "ScheduleFireWorkflow",
                trigger.id,
                id=f"{schedule_id}-fire",
                task_queue=get_settings().temporal_task_queue,
            ),
            spec=spec,
        ),
    )
    trigger.schedule_id = schedule_id
    return schedule_id


async def delete_schedule_for_graph(db: Session, graph: GraphRecord) -> int:
    """Remove every Temporal Schedule belonging to the graph's schedule triggers."""
    removed = 0
    triggers = db.scalars(select(TriggerRecord).where(TriggerRecord.graph_id == graph.id, TriggerRecord.kind == "schedule"))
    for trigger in triggers:
        if trigger.schedule_id:
            try:
                client = await get_temporal_client()
                await client.get_schedule_handle(trigger.schedule_id).delete()
                removed += 1
            except (RPCError, ServiceError) as error:
                log.warning("Could not delete schedule %s: %s", trigger.schedule_id, error)
            trigger.schedule_id = None
    db.commit()
    return removed


async def handle_schedule_fire(db: Session, trigger_id: int) -> RunRecord:
    """Called (via the worker) when a schedule fires: start a run of the published version."""
    trigger = db.get(TriggerRecord, trigger_id)
    graph = db.get(GraphRecord, trigger.graph_id) if trigger else None
    if trigger is None or graph is None:
        raise NotFound("Trigger not found.")
    if not trigger.active or not graph.is_active or graph.deleted_at is not None:
        raise ServiceError("Trigger is inactive; skipping.", 409)
    return await start_run(
        db, graph, {"fired_by": "schedule", "trigger_id": trigger.id}, check_access=False,
        trigger_kind="schedule", use_published=True,
    )


# ---------------------------------------------------------------------- chat


async def handle_chat_message(
    db: Session, token: str, *, message: str, session_id: str, wait: bool = True
) -> dict[str, Any]:
    """Start a run for a chat message sent to a published chat agent."""
    trigger, graph = _active_trigger(db, token, "chat")
    run = await start_run(
        db, graph, {"message": message, "session_id": session_id}, check_access=False,
        trigger_kind="chat", use_published=True,
    )
    if not wait:
        return {"run_id": run.id, "status": run.status}
    output = await wait_for_run_result(run, timeout_seconds=float(trigger.config.get("response_timeout_seconds", 60)))
    return {"run_id": run.id, "output": output}


# ------------------------------------------------------- publish / (de)activate


async def sync_triggers_for_graph(db: Session, graph: GraphRecord, ir: dict[str, Any]) -> list[TriggerRecord]:
    """After publishing: make trigger rows match the published version's trigger node."""
    wanted: dict[str, dict[str, Any]] = {
        node_id: node for node_id, node in ir["nodes"].items() if node["type"] in TRIGGER_KINDS
    }
    existing = {t.node_id: t for t in db.scalars(select(TriggerRecord).where(TriggerRecord.graph_id == graph.id))}
    for node_id, trigger in existing.items():
        if node_id not in wanted or TRIGGER_KINDS[wanted[node_id]["type"]] != trigger.kind:
            if trigger.schedule_id:
                try:
                    client = await get_temporal_client()
                    await client.get_schedule_handle(trigger.schedule_id).delete()
                except (RPCError, ServiceError):
                    pass
            db.delete(trigger)
    db.flush()
    result = []
    for node in wanted.values():
        kind = TRIGGER_KINDS[node["type"]]
        trigger = create_webhook_for_graph(db, graph, node, kind=kind)
        if kind == "schedule":
            await create_schedule_for_graph(trigger)
        result.append(trigger)
    db.commit()
    return result


async def deactivate_graph_triggers(db: Session, graph: GraphRecord) -> None:
    """Turn production triggers off (schedules paused) without deleting them."""
    client = None
    for trigger in db.scalars(select(TriggerRecord).where(TriggerRecord.graph_id == graph.id)):
        trigger.active = False
        if trigger.schedule_id:
            try:
                client = client or await get_temporal_client()
                await client.get_schedule_handle(trigger.schedule_id).pause(note="Deactivated in AgentForge")
            except (RPCError, ServiceError) as error:
                log.warning("Could not pause schedule %s: %s", trigger.schedule_id, error)
    graph.is_active = False
    db.commit()


async def activate_graph_triggers(db: Session, graph: GraphRecord) -> None:
    if graph.published_version is None:
        raise ServiceError("Publish the graph before activating its triggers.", 409)
    client = None
    for trigger in db.scalars(select(TriggerRecord).where(TriggerRecord.graph_id == graph.id)):
        trigger.active = True
        if trigger.schedule_id:
            client = client or await get_temporal_client()
            await client.get_schedule_handle(trigger.schedule_id).unpause(note="Activated in AgentForge")
    graph.is_active = True
    db.commit()


def list_triggers_for_graph(db: Session, graph: GraphRecord) -> list[TriggerRecord]:
    return list(db.scalars(select(TriggerRecord).where(TriggerRecord.graph_id == graph.id).order_by(TriggerRecord.id)))

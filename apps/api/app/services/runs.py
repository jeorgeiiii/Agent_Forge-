"""Cluster 6 - Execution engine, API side: start / inspect / control runs.

The workflow itself (supersteps, branching, approvals) runs in the worker
(apps/worker/workflow.py). The worker reports progress back through
``checkpoint_state`` so the runs table always reflects the latest state.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
from temporalio.service import RPCError

from ..compiler import GraphValidationError, compile_graph
from ..config import get_settings
from ..models import GraphRecord, RunRecord
from .authz import can_user_edit_graph, can_user_view_graph
from .common import Forbidden, NotFound, ServiceError, now_utc
from .graphs import get_latest_version_of_graph, get_published_version

TERMINAL_STATUSES = {"success", "failed", "cancelled"}
_TEMPORAL_TO_STATUS = {
    WorkflowExecutionStatus.COMPLETED: "success",
    WorkflowExecutionStatus.FAILED: "failed",
    WorkflowExecutionStatus.CANCELED: "cancelled",
    WorkflowExecutionStatus.TERMINATED: "cancelled",
    WorkflowExecutionStatus.TIMED_OUT: "failed",
}


def release_connection(db: Session) -> None:
    """End the open transaction so its DB connection goes back to the pool.

    Call before awaiting Temporal / network I/O: holding a pooled connection
    across an await is what exhausted the pool at 50+ concurrent runs.
    (expire_on_commit=False keeps loaded attributes usable afterwards.)
    """
    db.commit()
_client: Client | None = None
_client_lock = asyncio.Lock()


async def get_temporal_client() -> Client:
    global _client
    async with _client_lock:
        if _client is None:
            try:
                _client = await Client.connect(get_settings().temporal_address)
            except (OSError, RPCError, RuntimeError) as error:
                raise ServiceError("Unable to reach Temporal.", 503) from error
    return _client


def _ir_for(db: Session, graph: GraphRecord, use_published: bool) -> tuple[dict[str, Any], int]:
    if use_published:
        version = get_published_version(db, graph)
        if version is None or not version.compiled_ir:
            raise ServiceError("This graph has no published version.", 409)
        return version.compiled_ir, version.version
    try:
        ir = compile_graph(graph.definition)
    except GraphValidationError as error:
        raise ServiceError(f"Stored graph is invalid: {error}", 422) from error
    return ir, get_latest_version_of_graph(db, graph).version


async def start_run(
    db: Session,
    graph: GraphRecord,
    run_input: dict[str, Any] | None = None,
    *,
    user_id: int | None = None,
    check_access: bool = True,
    trigger_kind: str = "manual",
    use_published: bool = False,
    checkpoint: dict[str, Any] | None = None,
) -> RunRecord:
    """Create a run row and start the Temporal workflow. Returns immediately."""
    if check_access and not can_user_edit_graph(db, user_id, graph):
        raise Forbidden("You cannot run this graph.")
    ir, version = _ir_for(db, graph, use_published)
    run_input = run_input or {}
    run = RunRecord(
        graph_id=graph.id,
        workflow_id=f"agentforge-run-{uuid4()}",
        status="queued",
        input=run_input,
        graph_version=version,
        trigger_kind=trigger_kind,
    )
    db.add(run)
    db.commit()  # also releases the connection before the Temporal call below
    settings = get_settings()
    payload = {
        "ir": ir,
        "input": run_input,
        "run": {"id": run.id, "graph_id": graph.id, "workflow_id": run.workflow_id, "trigger": trigger_kind},
        "checkpoint": checkpoint,
    }
    try:
        client = await get_temporal_client()
        await client.start_workflow("RunGraphWorkflow", payload, id=run.workflow_id, task_queue=settings.temporal_task_queue)
    except (ServiceError, OSError, RPCError) as error:
        run.status = "failed"
        run.error = "Unable to start the Temporal workflow."
        db.commit()
        raise ServiceError("Unable to start the Temporal workflow.", 503) from error
    run.status = "running"
    db.commit()
    return run


def get_run(db: Session, user_id: int | None, graph_id: int, run_id: int) -> RunRecord:
    run = db.scalar(select(RunRecord).where(RunRecord.id == run_id, RunRecord.graph_id == graph_id))
    if run is None or not can_user_view_graph(db, user_id, run.graph):
        raise NotFound("Run not found.")
    return run


def list_runs(db: Session, user_id: int | None, graph: GraphRecord) -> list[RunRecord]:
    if not can_user_view_graph(db, user_id, graph):
        raise NotFound("Graph not found.")
    return list(db.scalars(select(RunRecord).where(RunRecord.graph_id == graph.id).order_by(RunRecord.created_at.desc())))


def checkpoint_state(db: Session, workflow_id: str, state: dict[str, Any]) -> RunRecord:
    """Persist the worker's run state after every superstep (called via the internal API)."""
    run = db.scalar(select(RunRecord).where(RunRecord.workflow_id == workflow_id))
    if run is None:
        raise NotFound("Run not found.")
    if (run.state or {}).get("seq", 0) > state.get("seq", 0):
        return run  # a newer save already landed (retries can arrive out of order)
    run.state = state
    status = state.get("status") or run.status
    run.status = status
    if status == "success":
        run.output = state.get("output")
    if status == "failed":
        run.error = state.get("error")
    if status in TERMINAL_STATUSES and run.finished_at is None:
        run.finished_at = now_utc()
    db.commit()
    return run


def load_checkpoint(run: RunRecord) -> dict[str, Any] | None:
    """Last saved state of a run - used to resume or retry it."""
    return run.state


async def get_run_status(db: Session, run: RunRecord) -> dict[str, Any]:
    """Current status (queued/running/paused/success/failed/cancelled), reconciled with Temporal."""
    status = run.status
    if status not in TERMINAL_STATUSES:
        release_connection(db)
        try:
            client = await get_temporal_client()
            description = await client.get_workflow_handle(run.workflow_id).describe()
            mapped = _TEMPORAL_TO_STATUS.get(description.status)
            if mapped and mapped != status:
                run.status = status = mapped
                run.finished_at = run.finished_at or now_utc()
                db.commit()
        except (ServiceError, RPCError):
            pass  # Temporal unreachable: report what the DB knows
    state = run.state or {}
    return {
        "run_id": run.id,
        "status": status,
        "waiting_for": state.get("waiting_for"),
        "usage": state.get("usage"),
        "nodes": {k: v.get("status") for k, v in state.get("nodes", {}).items()},
        "error": run.error,
    }


def get_run_output(run: RunRecord) -> dict[str, Any]:
    if run.status != "success":
        raise ServiceError(f"Run is {run.status}; output is available once it succeeds.", 409)
    return {"run_id": run.id, "output": run.output}


async def wait_for_run_result(run: RunRecord, timeout_seconds: float = 30) -> Any:
    """Block until the workflow finishes (used by synchronous webhooks and chat)."""
    client = await get_temporal_client()
    handle = client.get_workflow_handle(run.workflow_id)
    try:
        result = await asyncio.wait_for(handle.result(), timeout=timeout_seconds)
    except asyncio.TimeoutError as error:
        raise ServiceError("The run is still in progress.", 504) from error
    except WorkflowFailureError as error:
        raise ServiceError(f"The run failed: {error.cause or error}", 502) from error
    return result.get("output") if isinstance(result, dict) else result


async def cancel_run(db: Session, user_id: int | None, run: RunRecord) -> RunRecord:
    if not can_user_edit_graph(db, user_id, run.graph):
        raise Forbidden("You cannot cancel this run.")
    if run.status in TERMINAL_STATUSES:
        raise ServiceError(f"Run already {run.status}.", 409)
    release_connection(db)
    client = await get_temporal_client()
    await client.get_workflow_handle(run.workflow_id).cancel()
    run.status = "cancelling"
    db.commit()
    return run


async def resume_run_with_signal(
    db: Session, user_id: int | None, run: RunRecord, *, node_id: str, approved: bool, comment: str = ""
) -> RunRecord:
    """Send a human's approve/reject decision to a run paused at an approval node."""
    if not can_user_edit_graph(db, user_id, run.graph):
        raise Forbidden("You cannot approve this run.")
    waiting = (run.state or {}).get("waiting_for") or {}
    if run.status != "paused" or waiting.get("node_id") != node_id:
        raise ServiceError("This run is not waiting for approval at that node.", 409)
    release_connection(db)
    client = await get_temporal_client()
    await client.get_workflow_handle(run.workflow_id).signal(
        "approval", {"node_id": node_id, "approved": approved, "comment": comment, "user_id": user_id}
    )
    return run


async def reconcile_stale_runs(db: Session, *, older_than_seconds: int = 60, limit: int = 100) -> int:
    """Background repair: runs the DB still shows as active but Temporal has finished
    (e.g. the final checkpoint never arrived). Returns how many rows were fixed."""
    from datetime import timedelta

    cutoff = now_utc() - timedelta(seconds=older_than_seconds)
    stale = list(db.scalars(
        select(RunRecord)
        .where(RunRecord.status.not_in(TERMINAL_STATUSES), RunRecord.updated_at < cutoff)
        .order_by(RunRecord.id)
        .limit(limit)
    ))
    release_connection(db)
    if not stale:
        return 0
    client = await get_temporal_client()
    fixed = 0
    for run in stale:
        try:
            handle = client.get_workflow_handle(run.workflow_id)
            mapped = _TEMPORAL_TO_STATUS.get((await handle.describe()).status)
            if not mapped:
                continue
            if mapped == "success":
                result = await handle.result()
                run.output = result.get("output") if isinstance(result, dict) else result
            run.status = mapped
            run.finished_at = run.finished_at or now_utc()
            fixed += 1
        except (RPCError, WorkflowFailureError):
            continue
    db.commit()
    return fixed

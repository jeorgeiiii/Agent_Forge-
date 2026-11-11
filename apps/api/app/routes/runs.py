import asyncio
import json
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from ..db import SessionLocal
from ..deps import current_user_id, get_db
from ..models import RunRecord
from ..services import events
from ..services import observability as obs
from ..services.audit import audit
from ..schemas import ApprovalDecision, RunCreate, RunDetail, RunRead
from ..services import runs as svc
from ..services.graphs import get_graph_by_id

router = APIRouter(prefix="/graphs/{graph_id}/runs", tags=["runs"])
POLL_SECONDS = 0.3        # how often the stream checks for new events
HEARTBEAT_SECONDS = 15
STREAM_MAX_SECONDS = 1800  # clients reconnect (with Last-Event-ID) after this


def _audit(request: Request, db: Session, action: str, run, workspace_id, **details) -> None:
    audit(request, db, action, resource_type="run", resource_id=run.id, workspace_id=workspace_id,
          details={"graph_id": run.graph_id, **details})


@router.post("", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def start_run(graph_id: int, payload: RunCreate, request: Request, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    graph = get_graph_by_id(db, user_id, graph_id)
    run = await svc.start_run(db, graph, payload.input, user_id=user_id, trigger_kind="manual")
    _audit(request, db, "run.start", run, graph.workspace_id)
    return run


@router.get("", response_model=list[RunRead])
def list_runs(
    graph_id: int,
    response: Response,
    status_filter: str | None = Query(default=None, alias="status", description="e.g. failed or failed,cancelled"),
    trigger: str | None = Query(default=None, description="manual | webhook | schedule | chat | retry"),
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = Query(default=None, description="cursor: the X-Next-Cursor of the previous page"),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Run history, newest first. More pages: repeat with before_id = X-Next-Cursor header."""
    graph = get_graph_by_id(db, user_id, graph_id)
    page, next_cursor = obs.list_runs_for_graph(
        db, graph, status=status_filter, trigger=trigger, since=since, until=until, before_id=before_id, limit=limit)
    if next_cursor:
        response.headers["X-Next-Cursor"] = str(next_cursor)
    return page


@router.get("/{run_id}/details")
def get_run_details(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Run inspector: per-node status/duration/output, usage, cost breakdown, event counts."""
    return obs.get_run_details(db, svc.get_run(db, user_id, graph_id, run_id))


@router.get("/{run_id}/cost")
def get_run_cost(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return obs.get_run_cost(db, svc.get_run(db, user_id, graph_id, run_id))


@router.post("/{run_id}/retry", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def retry_run(graph_id: int, run_id: int, request: Request, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """New run of the same graph version that resumes from the node that failed."""
    old = svc.get_run(db, user_id, graph_id, run_id)
    run = await obs.retry_run(db, user_id, old)
    _audit(request, db, "run.retry", run, old.graph.workspace_id, retry_of=run_id)
    return run


@router.get("/{run_id}/trace")
def get_run_trace(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """The run as an OpenTelemetry trace (OTLP/HTTP JSON, GenAI semantic conventions)."""
    return obs.build_otlp_trace(db, svc.get_run(db, user_id, graph_id, run_id))


@router.post("/{run_id}/trace/export")
async def export_run_trace(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Send the trace to OTEL_EXPORTER_OTLP_ENDPOINT (Jaeger / Grafana Tempo / Langfuse ...)."""
    run = svc.get_run(db, user_id, graph_id, run_id)
    return await obs.export_trace(db, run)


@router.get("/{run_id}", response_model=RunDetail)
def get_run(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.get_run(db, user_id, graph_id, run_id)


@router.get("/{run_id}/status")
async def get_run_status(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return await svc.get_run_status(db, svc.get_run(db, user_id, graph_id, run_id))


@router.get("/{run_id}/output")
def get_run_output(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.get_run_output(svc.get_run(db, user_id, graph_id, run_id))


@router.get("/{run_id}/events")
def get_run_events(graph_id: int, run_id: int, after: int = 0, limit: int = 500,
                   user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Event log of a run (page with ?after=<last id>)."""
    run = svc.get_run(db, user_id, graph_id, run_id)
    return [events.event_to_dict(e) for e in events.list_events(db, run.id, after, limit)]


@router.get("/{run_id}/events/stream")
def stream_run_events(graph_id: int, run_id: int, request: Request, after: int = 0,
                      user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Live run view over Server-Sent Events.

    Each event carries ``id:`` - a reconnecting EventSource sends it back as
    ``Last-Event-ID`` and the stream resumes exactly where it stopped. Ends with
    an ``end`` event once the run is finished and every event was delivered.
    """
    run = svc.get_run(db, user_id, graph_id, run_id)  # access check happens here, before streaming
    resume_from = int(request.headers.get("last-event-id") or after or 0)
    svc.release_connection(db)
    return StreamingResponse(
        _event_stream(run.id, resume_from, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


async def _event_stream(run_id: int, last_id: int, request: Request):
    started = time.monotonic()
    last_beat = started
    yield "retry: 2000\n\n"  # browser reconnect delay
    while time.monotonic() - started < STREAM_MAX_SECONDS:
        if await request.is_disconnected():
            return
        with SessionLocal() as session:  # short-lived: never hold a pooled connection while sleeping
            batch = events.list_events(session, run_id, last_id)
            run = session.get(RunRecord, run_id)
            status = run.status if run else "failed"
        for event in batch:
            last_id = event.id
            yield f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(events.event_to_dict(event), default=str)}\n\n"
        if not batch and status in svc.TERMINAL_STATUSES:
            yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
            return
        if time.monotonic() - last_beat > HEARTBEAT_SECONDS:
            last_beat = time.monotonic()
            yield ": ping\n\n"  # keeps proxies from closing an idle stream
        await asyncio.sleep(POLL_SECONDS)


@router.post("/{run_id}/cancel", response_model=RunRead)
async def cancel_run(graph_id: int, run_id: int, request: Request, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    run = await svc.cancel_run(db, user_id, svc.get_run(db, user_id, graph_id, run_id))
    _audit(request, db, "run.cancel", run, run.graph.workspace_id)
    return run


@router.post("/{run_id}/approval", response_model=RunRead)
async def approve(graph_id: int, run_id: int, payload: ApprovalDecision, request: Request,
                  user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    run = svc.get_run(db, user_id, graph_id, run_id)
    run = await svc.resume_run_with_signal(
        db, user_id, run, node_id=payload.node_id, approved=payload.approved, comment=payload.comment
    )
    _audit(request, db, "run.approve" if payload.approved else "run.reject", run, run.graph.workspace_id,
           node_id=payload.node_id, comment=payload.comment)
    return run

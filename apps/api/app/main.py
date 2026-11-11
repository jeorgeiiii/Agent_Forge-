import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError

from .config import get_settings
from .db import SessionLocal, initialize_database
from .services.runs import reconcile_stale_runs
from .routes import (
    audit,
    auth,
    copilot,
    credentials,
    files,
    graphs,
    internal,
    memory,
    messages,
    node_types,
    public_triggers,
    retention,
    runs,
    usage,
    workspaces,
)
from .services.common import ServiceError
from .utils import Timer, accept_request_id, configure_logging, log_structured_event, request_id_var


log = logging.getLogger("agentforge")


async def _reconcile_loop(interval: int) -> None:
    """Every `interval` s, fix runs the DB shows as active but Temporal has finished."""
    while True:
        await asyncio.sleep(interval)
        try:
            with SessionLocal() as db:
                fixed = await reconcile_stale_runs(db)
            if fixed:
                log.warning("Reconciled %d run(s) whose final status was missing.", fixed)
        except ServiceError:
            pass  # Temporal unreachable; try again next round
        except Exception:  # never let the background job die
            log.exception("Run reconciliation failed")


async def _retention_loop(hours: float) -> None:
    """Data retention (cluster 18): run every `hours`; every job is idempotent."""
    from .services.retention import run_retention

    while True:
        await asyncio.sleep(hours * 3600)
        try:
            with SessionLocal() as db:
                report = await run_retention(db)
            log.info("Retention run: %s", report)
        except Exception:  # never let the background job die
            log.exception("Retention run failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    settings = get_settings()
    tasks = []
    if settings.run_reconcile_interval_seconds > 0:
        tasks.append(asyncio.create_task(_reconcile_loop(settings.run_reconcile_interval_seconds)))
    if settings.retention_interval_hours > 0:
        tasks.append(asyncio.create_task(_retention_loop(settings.retention_interval_hours)))
    yield
    for task in tasks:
        task.cancel()


settings = get_settings()
configure_logging(settings.log_format, settings.log_level)
app = FastAPI(title="AgentForge API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Request id for every request (+ X-Request-ID response header) and one structured access-log line."""
    request_id = accept_request_id(request.headers.get("x-request-id"))
    token = request_id_var.set(request_id)
    status = 500
    try:
        with Timer() as timer:
            response = await call_next(request)
            status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        user = getattr(request.state, "user", None)
        log_structured_event(
            logging.getLogger("agentforge.access"), "request", method=request.method, path=request.url.path,
            status=status, duration_ms=getattr(timer, "ms", None) if "timer" in locals() else None,
            user_id=getattr(user, "id", None), ip=request.client.host if request.client else None)
        request_id_var.reset(token)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_request: Request, error: IntegrityError) -> JSONResponse:
    """handle_db_error: a unique/foreign-key conflict is the client's problem, not a crash."""
    log.warning("Database conflict: %s", str(error.orig)[:300])
    return JSONResponse(status_code=409, content={"detail": "This conflicts with existing data (it may already exist).",
                                                  "request_id": request_id_var.get()})


@app.exception_handler(OperationalError)
async def operational_error_handler(_request: Request, error: OperationalError) -> JSONResponse:
    """handle_db_error: database busy/unreachable -> 503 + Retry-After instead of a bare 500."""
    log.error("Database unavailable: %s", str(error.orig)[:300])
    return JSONResponse(status_code=503, headers={"Retry-After": "2"},
                        content={"detail": "The database is busy or unavailable. Please retry.",
                                 "request_id": request_id_var.get()})


@app.exception_handler(ServiceError)
async def service_error_handler(_request: Request, error: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={"detail": error.message})


# Support both /api/<x> and /<x> prefixes for flexibility with Next.js rewrites
for router in (
    audit.router,
    auth.router,
    workspaces.router,
    workspaces.invitations_router,
    graphs.router,
    runs.router,
    retention.router,
    memory.router,
    usage.router,
    usage.limits_router,
    node_types.router,
    credentials.router,
    files.router,
    copilot.router,
    messages.router,
):
    app.include_router(router, prefix="/api")
    app.include_router(router)

# Public trigger URLs and worker-only endpoints are mounted once, without /api.
app.include_router(public_triggers.router)
app.include_router(internal.router)


@app.get("/health")
def health():
    return {"status": "ok"}

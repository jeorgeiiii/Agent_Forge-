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
    integrations,
    internal,
    memory,
    messages,
    node_types,
    public_triggers,
    retention,
    runs,
    sso,
    usage,
    workspaces,
)
from .services.common import ServiceError
from .services.production import check_production_settings
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
    settings = get_settings()
    check_production_settings(settings)  # APP_ENV=production: stop here if anything is unsafe
    initialize_database()
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
IS_PRODUCTION = settings.app_env == "production"
# the interactive API docs are handy locally but map the whole API for attackers: off in production
app = FastAPI(title="AgentForge API", version="0.2.0", lifespan=lifespan,
              docs_url=None if IS_PRODUCTION else "/docs", redoc_url=None if IS_PRODUCTION else "/redoc",
              openapi_url=None if IS_PRODUCTION else "/openapi.json")

# Sent on every API response (a route may set its own stricter value first, e.g. file downloads).
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",          # browsers must not guess file types
    "X-Frame-Options": "DENY",                    # API pages can't be put inside someone else's frame
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

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
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if IS_PRODUCTION:  # browsers remember to always use HTTPS for this site (1 year)
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
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
    sso.router,
    integrations.router,
):
    app.include_router(router, prefix="/api")
    app.include_router(router)

# Public trigger URLs and worker-only endpoints are mounted once, without /api.
app.include_router(public_triggers.router)
app.include_router(internal.router)


@app.get("/health")
def health():
    """Liveness: the process is up. Load balancers restart the container when this fails."""
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    """Readiness: can this server do real work right now? 503 = send traffic elsewhere.

    database: required (SELECT 1). redis: reported; required only with RATE_LIMIT_FAIL_CLOSED=true.
    """
    from sqlalchemy import text

    from .rate_limiter import limiter

    checks: dict[str, str] = {}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as error:  # report, never crash the probe
        log.error("Readiness: database check failed: %s", str(error)[:200])
        checks["database"] = "unavailable"
    redis = await limiter.get_redis()
    checks["redis"] = "ok" if redis is not None else "unavailable"
    healthy = checks["database"] == "ok" and (checks["redis"] == "ok" or not settings.rate_limit_fail_closed)
    return JSONResponse(status_code=200 if healthy else 503,
                        content={"status": "ok" if healthy else "unavailable", "checks": checks})

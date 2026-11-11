import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import SessionLocal, initialize_database
from .services.runs import reconcile_stale_runs
from .routes import (
    auth,
    copilot,
    credentials,
    graphs,
    internal,
    messages,
    node_types,
    public_triggers,
    runs,
    usage,
    workspaces,
)
from .services.common import ServiceError


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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    interval = get_settings().run_reconcile_interval_seconds
    task = asyncio.create_task(_reconcile_loop(interval)) if interval > 0 else None
    yield
    if task:
        task.cancel()


settings = get_settings()
app = FastAPI(title="AgentForge API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ServiceError)
async def service_error_handler(_request: Request, error: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={"detail": error.message})


# Support both /api/<x> and /<x> prefixes for flexibility with Next.js rewrites
for router in (
    auth.router,
    workspaces.router,
    graphs.router,
    runs.router,
    usage.router,
    node_types.router,
    credentials.router,
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

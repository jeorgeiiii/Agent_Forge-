from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import initialize_database
from .routes import copilot, graphs, runs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


settings = get_settings()
app = FastAPI(title="AgentForge API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(graphs.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(copilot.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}

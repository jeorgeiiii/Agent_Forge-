"""Cluster 13 - per-workspace quotas (0 = unlimited).

  max_concurrent_runs   queued + running runs at the same time
  runs_per_day          runs started in the last 24 h
  tokens_per_minute     LLM input+output tokens in the last 60 s
  tokens_per_day        LLM input+output tokens in the last 24 h

Usage is counted from the database (runs + llm.call events), so it is correct
across any number of API processes. Defaults come from settings; operators
override per workspace (cluster 14 plans will map onto these).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import GraphRecord, RunEventRecord, RunRecord, WorkspaceRecord
from .authz import require_role
from .common import NotFound, ServiceError, now_utc

LIMIT_KEYS = {
    "max_concurrent_runs": "workspace_max_concurrent_runs",
    "runs_per_day": "workspace_runs_per_day",
    "tokens_per_minute": "workspace_tokens_per_minute",
    "tokens_per_day": "workspace_tokens_per_day",
}
ACTIVE = ("queued", "running")


class QuotaExceeded(ServiceError):
    def __init__(self, message: str):
        super().__init__(message, 429)


def effective_limits(workspace: WorkspaceRecord | None) -> dict[str, int]:
    settings = get_settings()
    limits = {key: int(getattr(settings, setting)) for key, setting in LIMIT_KEYS.items()}
    if workspace is not None and workspace.limits:
        limits.update({k: int(v) for k, v in workspace.limits.items() if k in LIMIT_KEYS and v is not None})
    return limits


def _graph_ids(workspace_id: int | None):
    condition = GraphRecord.workspace_id.is_(None) if workspace_id is None else GraphRecord.workspace_id == workspace_id
    return select(GraphRecord.id).where(condition)


def _tokens_since(db: Session, workspace_id: int | None, seconds: int) -> int:
    since = now_utc() - timedelta(seconds=seconds)
    rows = db.scalars(
        select(RunEventRecord.data)
        .join(RunRecord, RunRecord.id == RunEventRecord.run_id)
        .where(RunRecord.graph_id.in_(_graph_ids(workspace_id)), RunEventRecord.type == "llm.call",
               RunEventRecord.created_at >= since)
    )
    return sum(int((d or {}).get("input_tokens", 0)) + int((d or {}).get("output_tokens", 0)) for d in rows)


def usage(db: Session, workspace_id: int | None) -> dict[str, int]:
    graphs = _graph_ids(workspace_id)
    return {
        "concurrent_runs": db.scalar(select(func.count()).select_from(RunRecord).where(
            RunRecord.graph_id.in_(graphs), RunRecord.status.in_(ACTIVE))) or 0,
        "runs_today": db.scalar(select(func.count()).select_from(RunRecord).where(
            RunRecord.graph_id.in_(graphs), RunRecord.created_at >= now_utc() - timedelta(days=1))) or 0,
        "tokens_last_minute": _tokens_since(db, workspace_id, 60),
        "tokens_today": _tokens_since(db, workspace_id, 86_400),
    }


def check_run_quota(db: Session, graph: GraphRecord) -> None:
    """Before starting a run. Raises QuotaExceeded (HTTP 429) with a clear reason."""
    workspace = db.get(WorkspaceRecord, graph.workspace_id) if graph.workspace_id else None
    limits = effective_limits(workspace)
    if not any(limits.values()):
        return
    used = usage(db, graph.workspace_id)
    if limits["max_concurrent_runs"] and used["concurrent_runs"] >= limits["max_concurrent_runs"]:
        raise QuotaExceeded(
            f"Too many runs in progress ({used['concurrent_runs']}/{limits['max_concurrent_runs']}). "
            "Wait for some to finish.")
    if limits["runs_per_day"] and used["runs_today"] >= limits["runs_per_day"]:
        raise QuotaExceeded(f"Daily run limit reached ({limits['runs_per_day']}).")
    if limits["tokens_per_day"] and used["tokens_today"] >= limits["tokens_per_day"]:
        raise QuotaExceeded(f"Daily LLM token limit reached ({limits['tokens_per_day']}).")


def check_llm_quota(db: Session, workflow_id: str) -> dict[str, Any]:
    """Before each LLM call (asked by the worker). Returns {"allowed": bool, "reason": str}."""
    run = db.scalar(select(RunRecord).where(RunRecord.workflow_id == workflow_id))
    if run is None:
        return {"allowed": True, "reason": "unknown run"}
    graph = db.get(GraphRecord, run.graph_id)
    workspace_id = graph.workspace_id if graph else None
    limits = effective_limits(db.get(WorkspaceRecord, workspace_id) if workspace_id else None)
    for key, seconds, label in (("tokens_per_minute", 60, "tokens-per-minute"), ("tokens_per_day", 86_400, "daily token")):
        if limits[key]:
            used = _tokens_since(db, workspace_id, seconds)
            if used >= limits[key]:
                return {"allowed": False, "reason": f"LLM {label} limit reached ({used}/{limits[key]})."}
    return {"allowed": True, "reason": ""}


def get_limits_status(db: Session, user_id: int | None, workspace_id: int) -> dict[str, Any]:
    require_role(db, user_id, workspace_id, "viewer")
    workspace = db.get(WorkspaceRecord, workspace_id)
    return {"workspace_id": workspace_id, "limits": effective_limits(workspace), "usage": usage(db, workspace_id),
            "note": "0 means unlimited"}


def set_workspace_limits(db: Session, workspace_id: int, changes: dict[str, Any]) -> dict[str, int]:
    """Operators only (internal token). null removes an override."""
    workspace = db.get(WorkspaceRecord, workspace_id)
    if workspace is None:
        raise NotFound("Workspace not found.")
    overrides = dict(workspace.limits or {})
    for key, value in changes.items():
        if key not in LIMIT_KEYS:
            raise ServiceError(f"Unknown limit '{key}'. Use one of {sorted(LIMIT_KEYS)}.", 422)
        if value is None:
            overrides.pop(key, None)
        elif not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ServiceError(f"{key} must be a whole number >= 0 (0 = unlimited).", 422)
        else:
            overrides[key] = value
    workspace.limits = overrides or None
    db.commit()
    return effective_limits(workspace)

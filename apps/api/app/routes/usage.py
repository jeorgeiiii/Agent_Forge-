from datetime import datetime

from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ..deps import current_user_id, get_db, require_internal_token
from ..services import observability as obs
from ..services import quotas

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("")
def usage_summary(
    workspace_id: int | None = None,
    graph_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    group_by: str = Query(default="day", description="day | graph | model | trigger"),
    user_id: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Runs, failures, LLM calls, tokens and cost over the graphs you can see."""
    return obs.get_usage_summary(db, user_id, workspace_id=workspace_id, graph_id=graph_id,
                                 since=since, until=until, group_by=group_by)


limits_router = APIRouter(tags=["usage"])


@limits_router.get("/workspaces/{workspace_id}/limits")
def workspace_limits(workspace_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Quotas (0 = unlimited) and current usage: concurrent runs, runs today, tokens last minute / today."""
    return quotas.get_limits_status(db, user_id, workspace_id)


@limits_router.patch("/admin/workspaces/{workspace_id}/limits", dependencies=[Depends(require_internal_token)])
def set_limits(workspace_id: int, changes: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Operators: e.g. {"tokens_per_day": 200000, "max_concurrent_runs": 5}; null removes an override."""
    return quotas.set_workspace_limits(db, workspace_id, changes)

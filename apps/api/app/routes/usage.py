from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import current_user_id, get_db
from ..services import observability as obs

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

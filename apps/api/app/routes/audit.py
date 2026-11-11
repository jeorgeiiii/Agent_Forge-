"""Cluster 17 - read, verify and export the audit log."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ..deps import current_user_id, get_db, require_internal_token
from ..services import audit as svc

router = APIRouter(tags=["audit"])


def _filters(action, actor_user_id, resource_type, resource_id, outcome, since, until, before_id):
    return {"action": action, "actor_user_id": actor_user_id, "resource_type": resource_type,
            "resource_id": resource_id, "outcome": outcome, "since": since, "until": until, "before_id": before_id}


@router.get("/workspaces/{workspace_id}/audit")
def workspace_audit(
    workspace_id: int,
    response: Response,
    action: str | None = Query(default=None, description='prefix, e.g. "graph." or "auth.login_failed"'),
    actor_user_id: int | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    outcome: str | None = Query(default=None, description="success | denied | failure"),
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    user_id: int | None = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Workspace audit log, newest first (workspace owners only). Next page: before_id = X-Next-Cursor."""
    rows, cursor = svc.list_audit_events(
        db, user_id, workspace_id=workspace_id, limit=limit,
        **_filters(action, actor_user_id, resource_type, resource_id, outcome, since, until, before_id))
    if cursor:
        response.headers["X-Next-Cursor"] = str(cursor)
    return [svc.event_to_dict(e) for e in rows]


@router.get("/workspaces/{workspace_id}/audit/verify")
def verify_workspace_audit(workspace_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Recompute the hash chain: proves no entry was edited, deleted or inserted afterwards."""
    return svc.verify_audit_chain(db, user_id, workspace_id)


@router.get("/workspaces/{workspace_id}/audit/export.csv")
def export_workspace_audit(workspace_id: int, since: datetime | None = None, until: datetime | None = None,
                           user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    rows, _ = svc.list_audit_events(db, user_id, workspace_id=workspace_id, limit=500,
                                    **_filters(None, None, None, None, None, since, until, None))
    return Response(svc.export_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="audit-workspace-{workspace_id}.csv"'})


@router.get("/audit")
def my_audit(response: Response, action: str | None = None, before_id: int | None = None,
             limit: int = Query(default=100, ge=1, le=500),
             user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Your own activity (signed in) - or, in single-user mode, the global log."""
    rows, cursor = svc.list_audit_events(db, user_id, limit=limit,
                                         **_filters(action, None, None, None, None, None, None, before_id))
    if cursor:
        response.headers["X-Next-Cursor"] = str(cursor)
    return [svc.event_to_dict(e) for e in rows]


@router.get("/audit/verify", dependencies=[Depends(require_internal_token)])
def verify_global_audit(db: Session = Depends(get_db)):
    """Operators only (internal token): verify the global chain (events with no workspace)."""
    return svc.verify_audit_chain(db, None, None)

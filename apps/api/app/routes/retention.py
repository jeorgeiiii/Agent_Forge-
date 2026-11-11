"""Cluster 18 - retention settings, cleanup job trigger, account deletion."""

from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from ..deps import current_user_id, get_current_user, get_db, require_internal_token
from ..models import UserRecord
from ..services import retention as svc
from ..services.audit import actor_from_request, audit

router = APIRouter(tags=["retention"])


@router.get("/workspaces/{workspace_id}/retention")
def get_retention(workspace_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Effective retention (days) for the workspace, its overrides and the platform defaults."""
    return svc.get_workspace_retention(db, user_id, workspace_id)


@router.patch("/workspaces/{workspace_id}/retention")
def set_retention(workspace_id: int, request: Request, changes: dict[str, Any] = Body(...),
                  user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Owners only. Body e.g. {"run_data_days": 7, "memory_days": null}  (null = back to default)."""
    result = svc.set_workspace_retention(db, user_id, workspace_id, changes)
    audit(request, db, "retention.update", resource_type="workspace", resource_id=workspace_id,
          workspace_id=workspace_id, details={"changes": changes})
    return result


@router.post("/admin/retention/run", dependencies=[Depends(require_internal_token)])
async def run_retention(dry_run: bool = True, db: Session = Depends(get_db)):
    """Operators: run the cleanup now. Defaults to dry_run=true (report only) - pass dry_run=false to delete."""
    return await svc.run_retention(db, dry_run=dry_run)


@router.delete("/auth/me")
async def delete_my_account(request: Request, payload: dict[str, Any] = Body(...),
                            user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    """GDPR erasure. Body: {"password": "..."} to confirm. Irreversible."""
    return await svc.delete_user_data(db, user, str(payload.get("password") or ""), actor_from_request(request, user))

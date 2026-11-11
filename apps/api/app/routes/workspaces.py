from fastapi import APIRouter, Body, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import UserRecord
from ..schemas import MemberAdd, MemberRead, MemberRoleUpdate, WorkspaceCreate, WorkspaceRead
from ..services import auth, authz, teams
from ..services.audit import audit
from ..services.common import NotFound, ServiceError

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceRead])
def list_workspaces(user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    return [
        WorkspaceRead(id=w.id, name=w.name, role=role, created_at=w.created_at)
        for w, role in authz.list_workspaces_for_user(db, user.id)
    ]


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, request: Request, user: UserRecord = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    workspace = authz.create_workspace(db, owner=user, name=payload.name)
    audit(request, db, "workspace.create", resource_type="workspace", resource_id=workspace.id,
          workspace_id=workspace.id, details={"name": workspace.name})
    return WorkspaceRead(id=workspace.id, name=workspace.name, role="owner", created_at=workspace.created_at)


@router.get("/{workspace_id}/members", response_model=list[MemberRead])
def list_members(workspace_id: int, user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    return [
        MemberRead(user_id=u.id, email=u.email, name=u.name, role=role)
        for u, role in authz.list_members_of_workspace(db, user.id, workspace_id)
    ]


@router.post("/{workspace_id}/members", response_model=MemberRead, status_code=status.HTTP_201_CREATED)
def add_member(workspace_id: int, payload: MemberAdd, request: Request, user: UserRecord = Depends(get_current_user),
               db: Session = Depends(get_db)):
    target = auth.find_user_by_id(db, payload.user_id) if payload.user_id else (
        auth.find_user_by_email(db, payload.email) if payload.email else None
    )
    if target is None:
        raise NotFound("User not found.") if (payload.user_id or payload.email) else ServiceError("Give user_id or email.", 422)
    membership = authz.add_member_to_workspace(db, user.id, workspace_id, target.id, payload.role)
    audit(request, db, "member.add", resource_type="user", resource_id=target.id, workspace_id=workspace_id,
          details={"email": target.email, "role": membership.role})
    return MemberRead(user_id=target.id, email=target.email, name=target.name, role=membership.role)


@router.patch("/{workspace_id}/members/{user_id}", response_model=MemberRead)
def change_role(workspace_id: int, user_id: int, payload: MemberRoleUpdate, request: Request,
                user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    membership = authz.change_member_role(db, user.id, workspace_id, user_id, payload.role)
    audit(request, db, "member.role_change", resource_type="user", resource_id=user_id, workspace_id=workspace_id,
          details={"role": membership.role})
    target = auth.find_user_by_id(db, user_id)
    return MemberRead(user_id=user_id, email=target.email, name=target.name, role=membership.role)


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(workspace_id: int, user_id: int, request: Request, user: UserRecord = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    authz.remove_member_from_workspace(db, user.id, workspace_id, user_id)
    audit(request, db, "member.remove", resource_type="user", resource_id=user_id, workspace_id=workspace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------- cluster 16: teams

@router.patch("/{workspace_id}")
def update_workspace(workspace_id: int, request: Request, payload: dict = Body(...),
                     user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    """Owners: rename the workspace. Body: {"name": "..."}"""
    workspace = teams.update_workspace_settings(db, user.id, workspace_id, name=payload.get("name"))
    audit(request, db, "workspace.update", resource_type="workspace", resource_id=workspace_id,
          workspace_id=workspace_id, details={"name": workspace.name})
    return {"id": workspace.id, "name": workspace.name}


@router.delete("/{workspace_id}")
async def delete_workspace(workspace_id: int, request: Request, payload: dict = Body(...),
                           user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    """Owners: soft-delete (restorable until retention purges it). Body: {"confirm_name": "<exact name>"}"""
    workspace = await teams.delete_workspace(db, user.id, workspace_id, str(payload.get("confirm_name") or ""))
    audit(request, db, "workspace.delete", resource_type="workspace", resource_id=workspace_id,
          workspace_id=workspace_id, details={"name": workspace.name})
    return {"id": workspace.id, "deleted_at": workspace.deleted_at}


@router.post("/{workspace_id}/restore")
def restore_workspace(workspace_id: int, request: Request, user: UserRecord = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    workspace = teams.restore_workspace(db, user.id, workspace_id)
    audit(request, db, "workspace.restore", resource_type="workspace", resource_id=workspace_id, workspace_id=workspace_id)
    return {"id": workspace.id, "name": workspace.name}


@router.post("/{workspace_id}/transfer-ownership")
def transfer_ownership(workspace_id: int, request: Request, payload: dict = Body(...),
                       user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    """Body: {"user_id": <member>}. They become owner; you become editor."""
    result = teams.transfer_ownership(db, user.id, workspace_id, int(payload.get("user_id") or 0))
    audit(request, db, "workspace.transfer_ownership", resource_type="user", resource_id=result["owner"],
          workspace_id=workspace_id)
    return result


@router.post("/{workspace_id}/invitations", status_code=status.HTTP_201_CREATED)
async def invite(workspace_id: int, request: Request, payload: dict = Body(...),
                 user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    """Owners: invite by email. Body: {"email": "...", "role": "editor"}. accept_url is shown once."""
    result = await teams.invite_member_by_email(db, user, workspace_id, str(payload.get("email") or ""),
                                               str(payload.get("role") or "editor"))
    audit(request, db, "invitation.create", resource_type="invitation", resource_id=result["id"],
          workspace_id=workspace_id, details={"email": result["email"], "role": result["role"],
                                              "email_sent": result["email_sent"]})
    return result


@router.get("/{workspace_id}/invitations")
def list_invitations(workspace_id: int, user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    return teams.list_invitations(db, user.id, workspace_id)


@router.delete("/{workspace_id}/invitations/{invitation_id}")
def revoke_invitation(workspace_id: int, invitation_id: int, request: Request,
                      user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    invite = teams.revoke_invitation(db, user.id, workspace_id, invitation_id)
    audit(request, db, "invitation.revoke", resource_type="invitation", resource_id=invitation_id,
          workspace_id=workspace_id, details={"email": invite.email})
    return teams.invitation_to_dict(invite)


invitations_router = APIRouter(prefix="/invitations", tags=["workspaces"])


@invitations_router.get("/preview")
def preview_invitation(token: str, db: Session = Depends(get_db)):
    """Public: what the accept page shows (workspace, role, inviter, status). The token is the secret."""
    return teams.preview_invitation(db, token)


@invitations_router.post("/accept")
def accept_invitation(request: Request, payload: dict = Body(...), user: UserRecord = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Signed-in user accepts. Body: {"token": "..."}. Account email must match the invite."""
    invite = teams.accept_invitation(db, user, str(payload.get("token") or ""))
    audit(request, db, "invitation.accept", resource_type="invitation", resource_id=invite.id,
          workspace_id=invite.workspace_id, details={"role": invite.role})
    return {"workspace_id": invite.workspace_id, "role": invite.role}

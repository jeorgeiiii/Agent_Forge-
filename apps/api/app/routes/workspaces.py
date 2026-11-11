from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import UserRecord
from ..schemas import MemberAdd, MemberRead, MemberRoleUpdate, WorkspaceCreate, WorkspaceRead
from ..services import auth, authz
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

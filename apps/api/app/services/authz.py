"""Cluster 2 - Authorization & tenancy (Postgres)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import GraphRecord, MembershipRecord, UserRecord, WorkspaceRecord
from .common import Conflict, Forbidden, NotFound, ServiceError

ROLES = ("viewer", "editor", "owner")
_RANK = {role: i for i, role in enumerate(ROLES)}


def _check_role(role: str) -> str:
    if role not in _RANK:
        raise ServiceError(f"Role must be one of {list(ROLES)}.", 422)
    return role


def role_at_least(role: str | None, required: str) -> bool:
    return role is not None and _RANK.get(role, -1) >= _RANK[required]


def create_workspace(db: Session, owner: UserRecord, name: str, *, commit: bool = True) -> WorkspaceRecord:
    """Insert a workspace and make the creator its Owner."""
    workspace = WorkspaceRecord(name=name.strip()[:200] or "Workspace")
    db.add(workspace)
    db.flush()
    db.add(MembershipRecord(workspace_id=workspace.id, user_id=owner.id, role="owner"))
    if commit:
        db.commit()
        db.refresh(workspace)
    return workspace


def get_user_role_in_workspace(db: Session, user_id: int | None, workspace_id: int) -> str | None:
    """The user's role in a workspace, or None if they are not a member."""
    if user_id is None:
        return None
    return db.scalar(
        select(MembershipRecord.role)
        .join(WorkspaceRecord, WorkspaceRecord.id == MembershipRecord.workspace_id)
        .where(MembershipRecord.workspace_id == workspace_id, MembershipRecord.user_id == user_id,
               WorkspaceRecord.deleted_at.is_(None))  # a deleted workspace grants no access
    )


def require_role(db: Session, user_id: int | None, workspace_id: int, required: str) -> str:
    role = get_user_role_in_workspace(db, user_id, workspace_id)
    if not role_at_least(role, required):
        raise Forbidden(f"This action needs the '{required}' role in the workspace.")
    return role  # type: ignore[return-value]


def _count_owners(db: Session, workspace_id: int) -> int:
    return len(db.scalars(
        select(MembershipRecord.id).where(MembershipRecord.workspace_id == workspace_id, MembershipRecord.role == "owner")
    ).all())


def add_member_to_workspace(db: Session, acting_user_id: int, workspace_id: int, user_id: int, role: str) -> MembershipRecord:
    """Link a user to a workspace with a role (owners only)."""
    require_role(db, acting_user_id, workspace_id, "owner")
    _check_role(role)
    if db.get(UserRecord, user_id) is None:
        raise NotFound("User not found.")
    membership = MembershipRecord(workspace_id=workspace_id, user_id=user_id, role=role)
    db.add(membership)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise Conflict("That user is already a member of this workspace.") from error
    db.refresh(membership)
    return membership


def _membership(db: Session, workspace_id: int, user_id: int) -> MembershipRecord:
    membership = db.scalar(
        select(MembershipRecord).where(MembershipRecord.workspace_id == workspace_id, MembershipRecord.user_id == user_id)
    )
    if membership is None:
        raise NotFound("Membership not found.")
    return membership


def remove_member_from_workspace(db: Session, acting_user_id: int, workspace_id: int, user_id: int) -> None:
    """Delete the membership; members may remove themselves, owners may remove anyone."""
    if acting_user_id != user_id:
        require_role(db, acting_user_id, workspace_id, "owner")
    membership = _membership(db, workspace_id, user_id)
    if membership.role == "owner" and _count_owners(db, workspace_id) <= 1:
        raise ServiceError("A workspace must keep at least one owner.", 409)
    db.delete(membership)
    db.commit()


def change_member_role(db: Session, acting_user_id: int, workspace_id: int, user_id: int, role: str) -> MembershipRecord:
    require_role(db, acting_user_id, workspace_id, "owner")
    _check_role(role)
    membership = _membership(db, workspace_id, user_id)
    if membership.role == "owner" and role != "owner" and _count_owners(db, workspace_id) <= 1:
        raise ServiceError("A workspace must keep at least one owner.", 409)
    membership.role = role
    db.commit()
    db.refresh(membership)
    return membership


def _graph_role(db: Session, user_id: int | None, graph: GraphRecord) -> str | None:
    if graph.workspace_id is None:
        # Legacy graphs without a workspace are open only in single-user mode.
        return None if get_settings().auth_required else "owner"
    return get_user_role_in_workspace(db, user_id, graph.workspace_id)


def can_user_edit_graph(db: Session, user_id: int | None, graph: GraphRecord) -> bool:
    return role_at_least(_graph_role(db, user_id, graph), "editor")


def can_user_view_graph(db: Session, user_id: int | None, graph: GraphRecord) -> bool:
    return role_at_least(_graph_role(db, user_id, graph), "viewer")


def list_workspaces_for_user(db: Session, user_id: int) -> list[tuple[WorkspaceRecord, str]]:
    """Every workspace the user belongs to, with their role (for the sidebar)."""
    rows = db.execute(
        select(WorkspaceRecord, MembershipRecord.role)
        .join(MembershipRecord, MembershipRecord.workspace_id == WorkspaceRecord.id)
        .where(MembershipRecord.user_id == user_id, WorkspaceRecord.deleted_at.is_(None))
        .order_by(WorkspaceRecord.id)
    ).all()
    return [(workspace, role) for workspace, role in rows]


def list_members_of_workspace(db: Session, acting_user_id: int, workspace_id: int) -> list[tuple[UserRecord, str]]:
    require_role(db, acting_user_id, workspace_id, "viewer")
    rows = db.execute(
        select(UserRecord, MembershipRecord.role)
        .join(MembershipRecord, MembershipRecord.user_id == UserRecord.id)
        .where(MembershipRecord.workspace_id == workspace_id)
        .order_by(UserRecord.id)
    ).all()
    return [(user, role) for user, role in rows]


def default_workspace_for_user(db: Session, user_id: int) -> int | None:
    """First workspace where the user can edit - used when no workspace is specified."""
    for workspace, role in list_workspaces_for_user(db, user_id):
        if role_at_least(role, "editor"):
            return workspace.id
    return None

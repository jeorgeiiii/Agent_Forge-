"""Cluster 16 - workspace & team management: invitations, settings, ownership, delete/restore."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import GraphRecord, InvitationRecord, MembershipRecord, UserRecord, WorkspaceRecord
from .authz import ROLES, require_role
from .common import Conflict, Forbidden, NotFound, ServiceError, as_utc, now_utc
from .mailer import MailError, send_email


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _workspace(db: Session, workspace_id: int, *, include_deleted: bool = False) -> WorkspaceRecord:
    workspace = db.get(WorkspaceRecord, workspace_id)
    if workspace is None or (workspace.deleted_at is not None and not include_deleted):
        raise NotFound("Workspace not found.")
    return workspace


# ---------------------------------------------------------------- invitations


def invitation_status(inv: InvitationRecord) -> str:
    if inv.accepted_at:
        return "accepted"
    if inv.revoked_at:
        return "revoked"
    if as_utc(inv.expires_at) <= now_utc():
        return "expired"
    return "pending"


def invitation_to_dict(inv: InvitationRecord) -> dict[str, Any]:
    return {"id": inv.id, "workspace_id": inv.workspace_id, "email": inv.email, "role": inv.role,
            "status": invitation_status(inv), "invited_by": inv.invited_by,
            "created_at": inv.created_at, "expires_at": inv.expires_at, "accepted_at": inv.accepted_at}


async def invite_member_by_email(db: Session, acting: UserRecord, workspace_id: int, email: str, role: str) -> dict[str, Any]:
    """Owner invites someone by email. The signed token is emailed (and returned once to the owner)."""
    require_role(db, acting.id, workspace_id, "owner")
    workspace = _workspace(db, workspace_id)
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise ServiceError("A valid email address is required.", 422)
    if role not in ROLES:
        raise ServiceError(f"Role must be one of {list(ROLES)}.", 422)
    existing_member = db.scalar(
        select(MembershipRecord.id).join(UserRecord, UserRecord.id == MembershipRecord.user_id)
        .where(MembershipRecord.workspace_id == workspace_id, func.lower(UserRecord.email) == email))
    if existing_member:
        raise Conflict("That person is already a member of this workspace.")
    # one open invite per email: re-inviting replaces the old link
    db.execute(update(InvitationRecord)
               .where(InvitationRecord.workspace_id == workspace_id, InvitationRecord.email == email,
                      InvitationRecord.accepted_at.is_(None), InvitationRecord.revoked_at.is_(None))
               .values(revoked_at=now_utc()))
    token = secrets.token_urlsafe(32)
    invite = InvitationRecord(
        workspace_id=workspace_id, email=email, role=role, token_hash=_sha256(token), invited_by=acting.id,
        expires_at=now_utc() + timedelta(hours=get_settings().invitation_ttl_hours),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    accept_url = f"{get_settings().public_app_url.rstrip('/')}/invite?token={token}"
    text = (f"{acting.name or acting.email} invited you to join the workspace \"{workspace.name}\" on AgentForge "
            f"as {role}.\n\nAccept the invitation (valid {get_settings().invitation_ttl_hours // 24} days):\n{accept_url}\n\n"
            "If you did not expect this, you can ignore this email.")
    try:
        delivered = await send_email(email, f"You're invited to {workspace.name} on AgentForge", text)
        mail_error = None
    except MailError as error:
        delivered, mail_error = False, str(error)
    return {**invitation_to_dict(invite), "accept_url": accept_url, "email_sent": delivered,
            **({"email_error": mail_error} if mail_error else {})}


def _invite_by_token(db: Session, token: str) -> InvitationRecord:
    invite = db.scalar(select(InvitationRecord).where(InvitationRecord.token_hash == _sha256(token or "")))
    if invite is None:
        raise NotFound("Invitation not found.")
    return invite


def preview_invitation(db: Session, token: str) -> dict[str, Any]:
    """What the accept page shows before sign-in: workspace name, role, inviter, status."""
    invite = _invite_by_token(db, token)
    workspace = db.get(WorkspaceRecord, invite.workspace_id)
    inviter = db.get(UserRecord, invite.invited_by) if invite.invited_by else None
    return {"workspace": workspace.name if workspace else None, "role": invite.role, "email": invite.email,
            "invited_by": (inviter.name or inviter.email) if inviter else None, "status": invitation_status(invite),
            "expires_at": invite.expires_at}


def accept_invitation(db: Session, user: UserRecord, token: str) -> InvitationRecord:
    """Join the workspace. The signed-in account's email must match the invited email."""
    invite = _invite_by_token(db, token)
    status = invitation_status(invite)
    if status != "pending":
        raise ServiceError(f"This invitation is {status}.", 410)
    if user.email.lower() != invite.email:
        raise Forbidden("This invitation was sent to a different email address. Sign in with that account.")
    workspace = db.get(WorkspaceRecord, invite.workspace_id)
    if workspace is None or workspace.deleted_at is not None:
        raise ServiceError("This workspace no longer exists.", 410)
    already = db.scalar(select(MembershipRecord).where(MembershipRecord.workspace_id == invite.workspace_id,
                                                       MembershipRecord.user_id == user.id))
    if already is None:
        db.add(MembershipRecord(workspace_id=invite.workspace_id, user_id=user.id, role=invite.role))
    invite.accepted_at = now_utc()
    invite.accepted_by = user.id
    db.commit()
    db.refresh(invite)
    return invite


def list_invitations(db: Session, user_id: int | None, workspace_id: int) -> list[dict[str, Any]]:
    require_role(db, user_id, workspace_id, "owner")
    rows = db.scalars(select(InvitationRecord).where(InvitationRecord.workspace_id == workspace_id)
                      .order_by(InvitationRecord.id.desc()))
    return [invitation_to_dict(i) for i in rows]


def revoke_invitation(db: Session, user_id: int | None, workspace_id: int, invitation_id: int) -> InvitationRecord:
    require_role(db, user_id, workspace_id, "owner")
    invite = db.get(InvitationRecord, invitation_id)
    if invite is None or invite.workspace_id != workspace_id:
        raise NotFound("Invitation not found.")
    if invitation_status(invite) == "pending":
        invite.revoked_at = now_utc()
        db.commit()
    return invite


# ------------------------------------------------------------ settings & ownership


def update_workspace_settings(db: Session, user_id: int | None, workspace_id: int, *, name: str | None = None) -> WorkspaceRecord:
    require_role(db, user_id, workspace_id, "owner")
    workspace = _workspace(db, workspace_id)
    if name is not None:
        if not name.strip():
            raise ServiceError("Workspace name cannot be empty.", 422)
        workspace.name = name.strip()[:200]
    db.commit()
    db.refresh(workspace)
    return workspace


def transfer_ownership(db: Session, user_id: int | None, workspace_id: int, new_owner_id: int) -> dict[str, Any]:
    """Make another member the owner; the current owner becomes an editor."""
    require_role(db, user_id, workspace_id, "owner")
    if new_owner_id == user_id:
        raise ServiceError("You already own this workspace.", 422)
    target = db.scalar(select(MembershipRecord).where(MembershipRecord.workspace_id == workspace_id,
                                                      MembershipRecord.user_id == new_owner_id))
    if target is None:
        raise NotFound("The new owner must already be a member of the workspace.")
    me = db.scalar(select(MembershipRecord).where(MembershipRecord.workspace_id == workspace_id,
                                                  MembershipRecord.user_id == user_id))
    target.role = "owner"
    me.role = "editor"
    db.commit()
    return {"workspace_id": workspace_id, "owner": new_owner_id, "previous_owner_role": "editor"}


async def delete_workspace(db: Session, user_id: int | None, workspace_id: int, confirm_name: str) -> WorkspaceRecord:
    """Soft-delete: hidden immediately, triggers stopped, open invites revoked; restorable until purged."""
    from .triggers import deactivate_graph_triggers

    require_role(db, user_id, workspace_id, "owner")
    workspace = _workspace(db, workspace_id)
    if confirm_name != workspace.name:
        raise ServiceError("Type the workspace name exactly to confirm deletion.", 422)
    personal = db.scalar(select(func.count()).select_from(MembershipRecord).where(MembershipRecord.user_id == user_id)) or 0
    if personal <= 1:
        raise ServiceError("You cannot delete your only workspace.", 409)
    for graph in db.scalars(select(GraphRecord).where(GraphRecord.workspace_id == workspace_id,
                                                      GraphRecord.deleted_at.is_(None))):
        await deactivate_graph_triggers(db, graph)  # no webhook/schedule keeps firing
    db.execute(update(InvitationRecord)
               .where(InvitationRecord.workspace_id == workspace_id, InvitationRecord.accepted_at.is_(None),
                      InvitationRecord.revoked_at.is_(None))
               .values(revoked_at=now_utc()))
    workspace.deleted_at = now_utc()
    db.commit()
    db.refresh(workspace)
    return workspace


def restore_workspace(db: Session, user_id: int | None, workspace_id: int) -> WorkspaceRecord:
    workspace = _workspace(db, workspace_id, include_deleted=True)
    if workspace.deleted_at is None:
        raise ServiceError("Workspace is not deleted.", 409)
    role = db.scalar(select(MembershipRecord.role).where(MembershipRecord.workspace_id == workspace_id,
                                                         MembershipRecord.user_id == user_id))
    if role != "owner":
        raise Forbidden("Only an owner can restore a workspace.")
    workspace.deleted_at = None
    db.commit()
    db.refresh(workspace)
    return workspace  # triggers stay off: re-activate graphs deliberately

"""Cluster 18 - data retention, cleanup jobs and account deletion (GDPR).

What is removed, and when (defaults from settings, overridable per workspace):

  run_data_days     run input / output / state / events are deleted; the run row keeps
                    status, timings, error and token usage ("compacted")
  run_rows_days     the run row itself is deleted
  memory_days       agent conversations idle this long are deleted
  deleted_graph_days  soft-deleted graphs are purged after this restore window
  audit_days        audit entries older than this are pruned (chain stays verifiable)

Every job is idempotent and supports dry_run (count only).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    ApiKeyRecord,
    AuditChainHead,
    ChatMessageRecord,
    CredentialRecord,
    GraphRecord,
    MembershipRecord,
    MemorySessionRecord,
    OAuthStateRecord,
    RunEventRecord,
    RunRecord,
    SessionRecord,
    UserRecord,
    WorkspaceRecord,
)
from .audit import SYSTEM, Actor, prune_audit_chain, record_audit_event
from .auth import verify_password
from .authz import require_role
from .common import Conflict, ServiceError, as_utc, now_utc

POLICY_KEYS = {  # key -> (setting name, min days, max days)
    "run_data_days": ("retention_run_data_days", 1, 3650),
    "run_rows_days": ("retention_run_rows_days", 1, 3650),
    "memory_days": ("retention_memory_days", 1, 3650),
    "deleted_graph_days": ("retention_deleted_graph_days", 0, 3650),
    "audit_days": ("retention_audit_days", 30, 3650),  # security logs: never less than 30 days
}


# ------------------------------------------------------------------- policies


def default_policy() -> dict[str, int]:
    settings = get_settings()
    return {key: int(getattr(settings, setting)) for key, (setting, _, _) in POLICY_KEYS.items()}


def effective_policy(workspace: WorkspaceRecord | None) -> dict[str, int]:
    policy = default_policy()
    if workspace is not None and workspace.retention:
        policy.update({k: int(v) for k, v in workspace.retention.items() if k in POLICY_KEYS and v is not None})
    return policy


def get_workspace_retention(db: Session, user_id: int | None, workspace_id: int) -> dict[str, Any]:
    require_role(db, user_id, workspace_id, "viewer")
    workspace = db.get(WorkspaceRecord, workspace_id)
    return {"workspace_id": workspace_id, "effective": effective_policy(workspace),
            "overrides": workspace.retention or {}, "defaults": default_policy()}


def set_workspace_retention(db: Session, user_id: int | None, workspace_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    """Owners override retention days (null removes an override, back to the default)."""
    require_role(db, user_id, workspace_id, "owner")
    workspace = db.get(WorkspaceRecord, workspace_id)
    overrides = dict(workspace.retention or {})
    for key, value in changes.items():
        if key not in POLICY_KEYS:
            raise ServiceError(f"Unknown retention setting '{key}'. Use one of {sorted(POLICY_KEYS)}.", 422)
        if value is None:
            overrides.pop(key, None)
            continue
        _, low, high = POLICY_KEYS[key]
        if not isinstance(value, int) or not low <= value <= high:
            raise ServiceError(f"{key} must be a whole number of days between {low} and {high}.", 422)
        overrides[key] = value
    merged = {**default_policy(), **overrides}
    if merged["run_rows_days"] < merged["run_data_days"]:
        raise ServiceError("run_rows_days must be >= run_data_days.", 422)
    workspace.retention = overrides or None
    db.commit()
    return get_workspace_retention(db, user_id, workspace_id)


# --------------------------------------------------------------------- jobs


def _workspaces(db: Session) -> list[WorkspaceRecord | None]:
    return [None, *db.scalars(select(WorkspaceRecord))]  # None = graphs without a workspace (single-user mode)


def _graph_ids(db: Session, workspace: WorkspaceRecord | None) -> list[int]:
    condition = GraphRecord.workspace_id.is_(None) if workspace is None else GraphRecord.workspace_id == workspace.id
    return list(db.scalars(select(GraphRecord.id).where(condition)))


def compact_old_runs(db: Session, now: datetime, *, dry_run: bool = False) -> int:
    """Delete run payloads (input/output/state/events) after run_data_days; keep the summary row."""
    total = 0
    for workspace in _workspaces(db):
        cutoff = now - timedelta(days=effective_policy(workspace)["run_data_days"])
        graph_ids = _graph_ids(db, workspace)
        if not graph_ids:
            continue
        runs = list(db.scalars(select(RunRecord).where(
            RunRecord.graph_id.in_(graph_ids), RunRecord.compacted_at.is_(None),
            RunRecord.finished_at.is_not(None), RunRecord.finished_at < cutoff)))
        total += len(runs)
        if dry_run or not runs:
            continue
        ids = [r.id for r in runs]
        db.execute(delete(RunEventRecord).where(RunEventRecord.run_id.in_(ids)))
        for run in runs:
            usage = (run.state or {}).get("usage")
            run.state = {"status": run.status, "compacted": True, **({"usage": usage} if usage else {})}
            run.input = {"_removed_by_retention": True}
            run.output = None
            run.compacted_at = now
        db.commit()
    return total


def delete_old_runs(db: Session, now: datetime, *, dry_run: bool = False) -> int:
    total = 0
    for workspace in _workspaces(db):
        cutoff = now - timedelta(days=effective_policy(workspace)["run_rows_days"])
        graph_ids = _graph_ids(db, workspace)
        if not graph_ids:
            continue
        ids = list(db.scalars(select(RunRecord.id).where(
            RunRecord.graph_id.in_(graph_ids), RunRecord.created_at < cutoff,
            RunRecord.status.in_(("success", "failed", "cancelled")))))
        total += len(ids)
        if ids and not dry_run:
            db.execute(delete(RunEventRecord).where(RunEventRecord.run_id.in_(ids)))
            db.execute(delete(RunRecord).where(RunRecord.id.in_(ids)))
            db.commit()
    return total


def _delete_memory_keys(db: Session, keys: list[str]) -> int:
    if not keys:
        return 0
    removed = db.execute(delete(ChatMessageRecord).where(ChatMessageRecord.session_id.in_(keys))).rowcount
    db.execute(delete(MemorySessionRecord).where(MemorySessionRecord.key.in_(keys)))
    return removed


def cleanup_memory(db: Session, now: datetime, *, dry_run: bool = False) -> int:
    """Delete agent conversations with no activity for memory_days. Returns sessions removed."""
    total = 0
    for workspace in _workspaces(db):
        cutoff = now - timedelta(days=effective_policy(workspace)["memory_days"])
        graph_ids = _graph_ids(db, workspace)
        query = select(MemorySessionRecord.key).where(MemorySessionRecord.last_message_at < cutoff)
        query = query.where(MemorySessionRecord.graph_id.in_(graph_ids) if workspace is not None
                            else (MemorySessionRecord.graph_id.in_(graph_ids) | MemorySessionRecord.graph_id.is_(None)))
        keys = list(db.scalars(query))
        total += len(keys)
        if keys and not dry_run:
            _delete_memory_keys(db, keys)
            db.commit()
    return total


async def _hard_delete_graph(db: Session, graph: GraphRecord) -> None:
    from .triggers import delete_schedule_for_graph

    try:
        await delete_schedule_for_graph(db, graph)  # no orphan Temporal schedules
    except ServiceError:
        pass
    keys = list(db.scalars(select(MemorySessionRecord.key).where(MemorySessionRecord.graph_id == graph.id)))
    _delete_memory_keys(db, keys)
    run_ids = list(db.scalars(select(RunRecord.id).where(RunRecord.graph_id == graph.id)))
    if run_ids:
        db.execute(delete(RunEventRecord).where(RunEventRecord.run_id.in_(run_ids)))
    db.delete(graph)  # ORM cascade: versions, runs; DB cascade: triggers
    db.commit()


async def purge_deleted_graphs(db: Session, now: datetime, *, dry_run: bool = False) -> int:
    """Permanently remove soft-deleted graphs whose restore window has passed."""
    total = 0
    for workspace in _workspaces(db):
        cutoff = now - timedelta(days=effective_policy(workspace)["deleted_graph_days"])
        condition = GraphRecord.workspace_id.is_(None) if workspace is None else GraphRecord.workspace_id == workspace.id
        graphs = list(db.scalars(select(GraphRecord).where(
            condition, GraphRecord.deleted_at.is_not(None), GraphRecord.deleted_at < cutoff)))
        total += len(graphs)
        if not dry_run:
            for graph in graphs:
                await _hard_delete_graph(db, graph)
    return total


def prune_audit(db: Session, now: datetime, *, dry_run: bool = False) -> int:
    total = 0
    workspaces = {w.id: w for w in db.scalars(select(WorkspaceRecord))}
    for head in list(db.scalars(select(AuditChainHead))):
        workspace = workspaces.get(int(head.chain[3:])) if head.chain.startswith("ws:") else None
        cutoff = now - timedelta(days=effective_policy(workspace)["audit_days"])
        total += prune_audit_chain(db, head.chain, cutoff, dry_run=dry_run)
    return total


def cleanup_auth_leftovers(db: Session, now: datetime, *, dry_run: bool = False) -> dict[str, int]:
    """Expired/revoked sessions and expired OAuth handshakes."""
    sessions = select(SessionRecord.id).where((SessionRecord.expires_at < now) | SessionRecord.revoked_at.is_not(None))
    oauth = select(OAuthStateRecord.id).where(OAuthStateRecord.expires_at < now)
    counts = {"sessions": len(db.scalars(sessions).all()), "oauth_states": len(db.scalars(oauth).all())}
    if not dry_run:
        db.execute(delete(SessionRecord).where(SessionRecord.id.in_(sessions)))
        db.execute(delete(OAuthStateRecord).where(OAuthStateRecord.id.in_(oauth)))
        db.commit()
    return counts


async def run_retention(db: Session, *, now: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Run every retention job once. Returns what was (or, with dry_run, would be) removed."""
    now = as_utc(now) if now else now_utc()
    report: dict[str, Any] = {
        "at": now.isoformat(), "dry_run": dry_run,
        "runs_compacted": compact_old_runs(db, now, dry_run=dry_run),
        "runs_deleted": delete_old_runs(db, now, dry_run=dry_run),
        "memory_sessions_deleted": cleanup_memory(db, now, dry_run=dry_run),
        "graphs_purged": await purge_deleted_graphs(db, now, dry_run=dry_run),
        "audit_entries_pruned": prune_audit(db, now, dry_run=dry_run),
        **{f"{k}_deleted": v for k, v in cleanup_auth_leftovers(db, now, dry_run=dry_run).items()},
    }
    if not dry_run and any(v for k, v in report.items() if k.endswith(("_compacted", "_deleted", "_purged", "_pruned"))):
        record_audit_event(db, SYSTEM, "retention.run", resource_type="system",
                           details={k: v for k, v in report.items() if k not in ("at", "dry_run")})
    return report


# --------------------------------------------------------- account deletion (GDPR)


async def delete_user_data(db: Session, user: UserRecord, password: str, actor: Actor) -> dict[str, Any]:
    """GDPR right to erasure: delete the account and everything only this user owns.

    * Workspaces where they are the ONLY member are deleted with all their graphs,
      runs, memory, credentials and audit chain.
    * In shared workspaces their membership is removed - unless they are the last
      owner, which is refused (transfer ownership first).
    * Audit entries that mention them in shared workspaces are kept (legitimate
      interest: security records) until audit retention prunes them.
    """
    if not verify_password(password, user.password_hash):
        raise ServiceError("Password is incorrect.", 403)
    memberships = list(db.scalars(select(MembershipRecord).where(MembershipRecord.user_id == user.id)))
    solo, shared = [], []
    for membership in memberships:
        members = db.scalar(select(func.count()).select_from(MembershipRecord)
                            .where(MembershipRecord.workspace_id == membership.workspace_id)) or 0
        if members <= 1:
            solo.append(membership.workspace_id)
            continue
        owners = db.scalar(select(func.count()).select_from(MembershipRecord).where(
            MembershipRecord.workspace_id == membership.workspace_id, MembershipRecord.role == "owner")) or 0
        if membership.role == "owner" and owners <= 1:
            raise Conflict(f"You are the only owner of workspace {membership.workspace_id}, which has other members. "
                           "Make someone else an owner first.")
        shared.append(membership)

    removed_graphs = 0
    for workspace_id in solo:
        for graph in list(db.scalars(select(GraphRecord).where(GraphRecord.workspace_id == workspace_id))):
            await _hard_delete_graph(db, graph)
            removed_graphs += 1
        db.execute(delete(CredentialRecord).where(CredentialRecord.workspace_id == workspace_id))
        chain = f"ws:{workspace_id}"
        from ..models import AuditEventRecord

        db.execute(delete(AuditEventRecord).where(AuditEventRecord.chain == chain))
        db.execute(delete(AuditChainHead).where(AuditChainHead.chain == chain))
        db.execute(delete(MembershipRecord).where(MembershipRecord.workspace_id == workspace_id))
        db.execute(delete(WorkspaceRecord).where(WorkspaceRecord.id == workspace_id))
    for membership in shared:
        db.delete(membership)
    db.execute(delete(SessionRecord).where(SessionRecord.user_id == user.id))
    db.execute(delete(ApiKeyRecord).where(ApiKeyRecord.user_id == user.id))
    user_id = user.id
    db.delete(user)
    db.commit()
    record_audit_event(db, Actor(None, "system", f"deleted user #{user_id}", actor.ip, actor.user_agent),
                       "user.delete", resource_type="user", resource_id=user_id,
                       details={"workspaces_deleted": len(solo), "memberships_removed": len(shared),
                                "graphs_deleted": removed_graphs})
    return {"deleted_user_id": user_id, "workspaces_deleted": solo,
            "memberships_removed": [m.workspace_id for m in shared], "graphs_deleted": removed_graphs}

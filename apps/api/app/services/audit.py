"""Cluster 17 - audit log: who did what, to which resource, when, from where.

Tamper evidence: rows are hash-chained per workspace (see AuditEventRecord).
Secrets never reach the log: details are scrubbed of password/token/key/secret
fields and credential payloads before hashing.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..models import AuditChainHead, AuditEventRecord, UserRecord
from .authz import require_role
from .common import ServiceError, now_utc

log = logging.getLogger("agentforge.audit")
GENESIS = "0" * 64
SENSITIVE_EXACT = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie", "key",
                   "private_key", "client_secret", "access_token", "refresh_token", "data", "value", "code"}
SENSITIVE_SUFFIXES = ("_password", "_secret", "_token", "_api_key", "_private_key")
SECRET_VALUE = re.compile(r"(gsk_|sk-|sk_live_|af_|ghp_|xox[abp]-|AKIA)[A-Za-z0-9_\-]{8,}|Bearer\s+\S{12,}")
MAX_DETAIL_CHARS = 4000
MAX_APPEND_ATTEMPTS = 50


@dataclass
class Actor:
    user_id: int | None
    type: str          # user | api_key | anonymous | system
    label: str         # email, "api_key af_xxxx", "", "scheduler"
    ip: str = ""
    user_agent: str = ""


def actor_from_request(request: Request | None, user: UserRecord | None = None) -> Actor:
    """Who is acting: the signed-in user (session or API key) or anonymous, plus IP / user agent."""
    from ..rate_limiter import client_ip  # local: avoid import cycle at startup

    ip = client_ip(request) if request is not None else ""
    agent = (request.headers.get("user-agent", "") if request is not None else "")[:300]
    if user is None:
        return Actor(None, "anonymous", "", ip, agent)
    method = getattr(request.state, "auth_method", "session") if request is not None else "session"
    label = user.email if method != "api_key" else f"api_key {getattr(request.state, 'api_key_prefix', '')} ({user.email})"
    return Actor(user.id, "api_key" if method == "api_key" else "user", label, ip, agent)


SYSTEM = Actor(None, "system", "system")


def audit(request: Request | None, db: Session, action: str, *, user: UserRecord | None = None,
          resource_type: str = "", resource_id: Any = "", workspace_id: int | None = None,
          details: dict[str, Any] | None = None, outcome: str = "success") -> AuditEventRecord | None:
    """Route helper: attribute the event to the request's user (set by deps.get_optional_user)."""
    if user is None and request is not None:
        user = getattr(request.state, "user", None)
    return record_audit_event(db, actor_from_request(request, user), action, resource_type=resource_type,
                              resource_id=resource_id, workspace_id=workspace_id, details=details, outcome=outcome)


def scrub(value: Any, depth: int = 0) -> Any:
    """Remove secret-looking fields (recursively) so they never enter the log."""
    if depth > 5:
        return "..."
    if isinstance(value, dict):
        return {k: ("[redacted]" if _sensitive(k) else scrub(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, depth + 1) for v in value[:50]]
    if isinstance(value, str):
        value = SECRET_VALUE.sub("[redacted]", value)  # key-shaped strings, wherever they appear
        return value[:500] + "..." if len(value) > 500 else value
    return value


def _sensitive(key: Any) -> bool:
    name = str(key).lower()
    return name in SENSITIVE_EXACT or name.endswith(SENSITIVE_SUFFIXES)


def _canonical(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def _hash_fields(event: AuditEventRecord) -> dict[str, Any]:
    return {
        "chain": event.chain, "position": event.position, "workspace_id": event.workspace_id, "actor_user_id": event.actor_user_id,
        "actor_type": event.actor_type, "actor_label": event.actor_label, "action": event.action,
        "resource_type": event.resource_type, "resource_id": event.resource_id, "outcome": event.outcome,
        "ip_address": event.ip_address, "user_agent": event.user_agent, "details": event.details,
        "created_at": event.created_at,
    }


def compute_hash(prev_hash: str, event: AuditEventRecord) -> str:
    return hashlib.sha256((prev_hash + _canonical(_hash_fields(event))).encode("utf-8")).hexdigest()


def _chain_for(workspace_id: int | None) -> str:
    return f"ws:{workspace_id}" if workspace_id is not None else "global"


def record_audit_event(
    db: Session,
    actor: Actor,
    action: str,
    *,
    resource_type: str = "",
    resource_id: Any = "",
    workspace_id: int | None = None,
    details: dict[str, Any] | None = None,
    outcome: str = "success",
) -> AuditEventRecord | None:
    """Append one event to its workspace's hash chain and commit.

    Concurrency-safe on any database and across API processes:
    * the event takes position head+1, and (chain, position) is UNIQUE, and
    * the head is advanced with compare-and-swap (UPDATE ... WHERE last_hash = <what we read>).
    If another writer got there first, either check fails and we retry with the new head.

    Auditing must never break the action being audited: on failure the error is
    logged loudly and None is returned.
    """
    chain = _chain_for(workspace_id)
    cleaned = scrub(details or {})
    if len(json.dumps(cleaned, default=str)) > MAX_DETAIL_CHARS:
        cleaned = {"truncated": True}
    for attempt in range(MAX_APPEND_ATTEMPTS):
        try:
            head = db.get(AuditChainHead, chain, populate_existing=True)
            if head is None:
                db.add(AuditChainHead(chain=chain, last_position=0, last_hash=GENESIS))
                db.commit()  # IntegrityError if another writer created it first -> retry
                continue
            prev_hash, position = head.last_hash, head.last_position + 1
            event = AuditEventRecord(
                chain=chain, position=position, workspace_id=workspace_id, actor_user_id=actor.user_id,
                actor_type=actor.type, actor_label=actor.label[:320], action=action[:60],
                resource_type=resource_type[:40], resource_id=str(resource_id if resource_id is not None else "")[:100],
                outcome=outcome, ip_address=actor.ip[:64], user_agent=actor.user_agent[:300], details=cleaned,
                created_at=now_utc().isoformat(), prev_hash=prev_hash,
            )
            event.hash = compute_hash(prev_hash, event)
            db.add(event)
            db.flush()  # IntegrityError if this position was taken meanwhile
            moved = db.execute(
                update(AuditChainHead)
                .where(AuditChainHead.chain == chain, AuditChainHead.last_hash == prev_hash)
                .values(last_position=position, last_hash=event.hash)
            ).rowcount
            if moved != 1:  # head moved under us: someone else appended first
                db.rollback()
                time.sleep(random.uniform(0, 0.005 * (attempt + 1)))
                continue
            db.commit()
            return event
        except (IntegrityError, OperationalError):
            db.rollback()
            time.sleep(random.uniform(0, 0.005 * (attempt + 1)))
        except Exception:  # noqa: BLE001 - audit failures are reported, never raised into the request
            db.rollback()
            log.exception("AUDIT WRITE FAILED for action %s", action)
            return None
    log.error("AUDIT WRITE FAILED for action %s: chain %s too contended", action, chain)
    return None


# --------------------------------------------------------------------------- reading


def _query(workspace_id: int | None, *, action: str | None, actor_user_id: int | None, resource_type: str | None,
           resource_id: str | None, outcome: str | None, since: datetime | None, until: datetime | None,
           before_id: int | None):
    query = select(AuditEventRecord)
    query = query.where(AuditEventRecord.workspace_id == workspace_id) if workspace_id is not None else query
    if action:
        query = query.where(AuditEventRecord.action.startswith(action.rstrip("*")))  # "graph." matches graph.*
    if actor_user_id is not None:
        query = query.where(AuditEventRecord.actor_user_id == actor_user_id)
    if resource_type:
        query = query.where(AuditEventRecord.resource_type == resource_type)
    if resource_id:
        query = query.where(AuditEventRecord.resource_id == str(resource_id))
    if outcome:
        query = query.where(AuditEventRecord.outcome == outcome)
    if since:
        query = query.where(AuditEventRecord.created_at >= since.isoformat())
    if until:
        query = query.where(AuditEventRecord.created_at < until.isoformat())
    if before_id:
        query = query.where(AuditEventRecord.id < before_id)
    return query


def list_audit_events(
    db: Session, user_id: int | None, *, workspace_id: int | None = None, limit: int = 100, **filters: Any,
) -> tuple[list[AuditEventRecord], int | None]:
    """Workspace log (owners only) or, without workspace_id, the caller's own activity."""
    if workspace_id is not None:
        require_role(db, user_id, workspace_id, "owner")
        query = _query(workspace_id, **filters)
    elif user_id is not None:
        query = _query(None, **{**filters, "actor_user_id": user_id})
    else:
        from ..config import get_settings

        if get_settings().auth_required:
            raise ServiceError("Sign in to read the audit log.", 401)
        query = _query(None, **filters).where(AuditEventRecord.chain == "global")  # single-user mode
    limit = max(1, min(limit, 500))
    rows = list(db.scalars(query.order_by(AuditEventRecord.id.desc()).limit(limit + 1)))
    return rows[:limit], (rows[limit - 1].id if len(rows) > limit else None)


def event_to_dict(event: AuditEventRecord) -> dict[str, Any]:
    return {
        "id": event.id, "at": event.created_at, "action": event.action, "outcome": event.outcome,
        "actor": {"user_id": event.actor_user_id, "type": event.actor_type, "label": event.actor_label},
        "resource": {"type": event.resource_type, "id": event.resource_id},
        "workspace_id": event.workspace_id, "ip": event.ip_address, "user_agent": event.user_agent,
        "details": event.details, "hash": event.hash,
    }


def verify_audit_chain(db: Session, user_id: int | None, workspace_id: int | None) -> dict[str, Any]:
    """Recompute every hash of a chain; reports the first row that was altered or removed."""
    if workspace_id is not None:
        require_role(db, user_id, workspace_id, "owner")
    chain = _chain_for(workspace_id)
    head = db.get(AuditChainHead, chain)
    # after retention pruning, the chain starts at the anchor left by the pruner
    prev = head.pruned_hash if head else GENESIS
    checked = 0
    expected_position = (head.pruned_position if head else 0) + 1
    for event in db.scalars(select(AuditEventRecord).where(AuditEventRecord.chain == chain).order_by(AuditEventRecord.position)):
        if event.position != expected_position:
            return {"chain": chain, "valid": False, "checked": checked, "broken_at_id": event.id,
                    "reason": f"position {expected_position} is missing (an entry was deleted)"}
        expected_position += 1
        if event.prev_hash != prev:
            return {"chain": chain, "valid": False, "checked": checked, "broken_at_id": event.id,
                    "reason": "previous_hash mismatch (a row before this one was changed or deleted)"}
        if compute_hash(prev, event) != event.hash:
            return {"chain": chain, "valid": False, "checked": checked, "broken_at_id": event.id,
                    "reason": "content does not match its hash (this row was modified)"}
        prev = event.hash
        checked += 1
    if head and (head.last_hash != prev or head.last_position != head.pruned_position + checked):
        return {"chain": chain, "valid": False, "checked": checked, "broken_at_id": None,
                "reason": "newest rows are missing (chain head does not match)"}
    result = {"chain": chain, "valid": True, "checked": checked}
    if head and head.pruned_position:
        result["pruned_before_position"] = head.pruned_position + 1  # older entries removed by retention
    return result


def prune_audit_chain(db: Session, chain: str, older_than: datetime, *, dry_run: bool = False) -> int:
    """Delete a chain's oldest entries (created before `older_than`) while keeping it verifiable.

    Only a *prefix* is removed; the hash of the last removed entry becomes the
    chain's new anchor (head.pruned_hash), so verify_audit_chain still proves
    nothing after it was changed.
    """
    head = db.get(AuditChainHead, chain)
    if head is None:
        return 0
    old = list(db.scalars(
        select(AuditEventRecord)
        .where(AuditEventRecord.chain == chain, AuditEventRecord.created_at < older_than.isoformat())
        .order_by(AuditEventRecord.position)
    ))
    # stop at the first gap in time order so the removed part is exactly a prefix
    prefix = []
    expected = head.pruned_position + 1
    for event in old:
        if event.position != expected:
            break
        prefix.append(event)
        expected += 1
    if dry_run or not prefix:
        return len(prefix)
    last = prefix[-1]
    db.execute(update(AuditChainHead).where(AuditChainHead.chain == chain)
               .values(pruned_position=last.position, pruned_hash=last.hash))
    for event in prefix:
        db.delete(event)
    db.commit()
    return len(prefix)


def mask_secret_values(value: Any, depth: int = 0) -> Any:
    """Replace key-shaped strings (gsk_..., sk-..., Bearer ...) anywhere in a JSON-like value."""
    if depth > 8:
        return value
    if isinstance(value, dict):
        return {k: mask_secret_values(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secret_values(v, depth + 1) for v in value]
    if isinstance(value, str):
        return SECRET_VALUE.sub("[redacted]", value)
    return value


def export_csv(events: list[AuditEventRecord]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "at", "action", "outcome", "actor_type", "actor", "resource_type", "resource_id",
                     "workspace_id", "ip", "user_agent", "details", "hash"])
    for e in events:
        writer.writerow([e.id, e.created_at, e.action, e.outcome, e.actor_type, e.actor_label, e.resource_type,
                         e.resource_id, e.workspace_id, e.ip_address, e.user_agent,
                         json.dumps(e.details, default=str), e.hash])
    return buffer.getvalue()

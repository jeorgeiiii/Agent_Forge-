"""Cluster 12 - agent conversation memory (stored in chat_messages).

A conversation is addressed by an opaque key (see agentforge_core.memory.memory_key),
so different graphs/agents/sessions never mix. ``memory_sessions`` indexes the keys
so a graph's conversations can be listed, read and cleared.

Summary memory: a row with role "summary" holds the running summary; its
graph_data["covers_until"] is the id of the newest message folded into it.
Messages with a higher id are the verbatim tail.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import ChatMessageRecord, GraphRecord, MemorySessionRecord
from .authz import can_user_edit_graph, can_user_view_graph
from .common import Forbidden, NotFound, ServiceError, now_utc

KEY_PREFIX = "mem:"
MAX_MESSAGE_CHARS = 50_000
MAX_LOAD = 200
ROLES = {"user", "assistant"}


def _check_key(key: str) -> str:
    if not key.startswith(KEY_PREFIX) or len(key) > 64:
        raise ServiceError("Invalid memory key.", 422)
    return key


def _latest_summary(db: Session, key: str) -> ChatMessageRecord | None:
    return db.scalar(
        select(ChatMessageRecord)
        .where(ChatMessageRecord.session_id == key, ChatMessageRecord.role == "summary")
        .order_by(ChatMessageRecord.id.desc())
        .limit(1)
    )


def load_memory(db: Session, key: str, limit: int = 20) -> dict[str, Any]:
    """Latest summary (if any) + up to `limit` newest verbatim messages after it, oldest first."""
    _check_key(key)
    limit = max(1, min(int(limit), MAX_LOAD))
    summary = _latest_summary(db, key)
    covers_until = int((summary.graph_data or {}).get("covers_until", 0)) if summary else 0
    rows = db.scalars(
        select(ChatMessageRecord)
        .where(ChatMessageRecord.session_id == key, ChatMessageRecord.role.in_(ROLES),
               ChatMessageRecord.id > covers_until)
        .order_by(ChatMessageRecord.id.desc())
        .limit(limit)
    ).all()
    return {
        "summary": summary.content if summary else None,
        "covers_until": covers_until,
        "messages": [{"id": r.id, "role": r.role, "content": r.content} for r in reversed(rows)],
    }


def _touch_session(db: Session, key: str, meta: dict[str, Any] | None, *, messages: int = 0, summaries: int = 0) -> None:
    session = db.get(MemorySessionRecord, key)
    if session is None:
        meta = meta or {}
        graph_id = meta.get("graph_id")
        session = MemorySessionRecord(
            key=key, graph_id=int(graph_id) if str(graph_id or "").isdigit() else None,
            scope=str(meta.get("scope") or "")[:250], session_id=str(meta.get("session_id") or "")[:200],
            message_count=0, summary_count=0,
        )
        db.add(session)
    session.message_count += messages
    session.summary_count += summaries
    session.last_message_at = now_utc()


def append_memory(db: Session, key: str, messages: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> int:
    """Add user/assistant messages to the end of a conversation."""
    _check_key(key)
    added = 0
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role not in ROLES or not isinstance(content, str) or not content:
            continue
        db.add(ChatMessageRecord(session_id=key, role=role, content=content[:MAX_MESSAGE_CHARS]))
        added += 1
    if added:
        _touch_session(db, key, meta, messages=added)
    db.commit()
    return added


def save_summary(db: Session, key: str, content: str, covers_until: int, meta: dict[str, Any] | None = None) -> None:
    """Store a new running summary that replaces every message with id <= covers_until."""
    _check_key(key)
    if not content.strip():
        raise ServiceError("Summary is empty.", 422)
    current = _latest_summary(db, key)
    if current and int((current.graph_data or {}).get("covers_until", 0)) >= covers_until:
        return  # a newer summary already exists (e.g. retried delivery)
    db.add(ChatMessageRecord(session_id=key, role="summary", content=content[:MAX_MESSAGE_CHARS],
                             graph_data={"covers_until": int(covers_until)}))
    _touch_session(db, key, meta, summaries=1)
    db.commit()


def clear_memory(db: Session, key: str) -> int:
    _check_key(key)
    result = db.execute(delete(ChatMessageRecord).where(ChatMessageRecord.session_id == key))
    session = db.get(MemorySessionRecord, key)
    if session:
        db.delete(session)
    db.commit()
    return result.rowcount


# ----------------------------------------------------------- user-facing (per graph)


def _graph(db: Session, user_id: int | None, graph_id: int, *, edit: bool = False) -> GraphRecord:
    graph = db.get(GraphRecord, graph_id)
    if graph is None or graph.deleted_at is not None or not can_user_view_graph(db, user_id, graph):
        raise NotFound("Graph not found.")
    if edit and not can_user_edit_graph(db, user_id, graph):
        raise Forbidden("You cannot change this graph's memory.")
    return graph


def _session(db: Session, graph: GraphRecord, key: str) -> MemorySessionRecord:
    session = db.get(MemorySessionRecord, key)
    if session is None or session.graph_id != graph.id:
        raise NotFound("Memory session not found.")
    return session


def list_sessions(db: Session, user_id: int | None, graph_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    graph = _graph(db, user_id, graph_id)
    rows = db.scalars(
        select(MemorySessionRecord).where(MemorySessionRecord.graph_id == graph.id)
        .order_by(MemorySessionRecord.last_message_at.desc()).limit(max(1, min(limit, 500)))
    )
    return [{"key": s.key, "agent": s.scope, "session_id": s.session_id, "messages": s.message_count,
             "summaries": s.summary_count, "created_at": s.created_at, "last_message_at": s.last_message_at}
            for s in rows]


def get_session(db: Session, user_id: int | None, graph_id: int, key: str) -> dict[str, Any]:
    graph = _graph(db, user_id, graph_id)
    session = _session(db, graph, key)
    rows = db.scalars(select(ChatMessageRecord).where(ChatMessageRecord.session_id == key)
                      .order_by(ChatMessageRecord.id)).all()
    return {
        "key": key, "agent": session.scope, "session_id": session.session_id,
        "summary": next((r.content for r in reversed(rows) if r.role == "summary"), None),
        "messages": [{"id": r.id, "role": r.role, "content": r.content, "created_at": r.created_at}
                     for r in rows if r.role in ROLES],
    }


def clear_session(db: Session, user_id: int | None, graph_id: int, key: str) -> int:
    graph = _graph(db, user_id, graph_id, edit=True)
    _session(db, graph, key)
    return clear_memory(db, key)


def clear_all_sessions(db: Session, user_id: int | None, graph_id: int) -> int:
    graph = _graph(db, user_id, graph_id, edit=True)
    keys = list(db.scalars(select(MemorySessionRecord.key).where(MemorySessionRecord.graph_id == graph.id)))
    removed = 0
    for key in keys:
        removed += clear_memory(db, key)
    return removed


def count_messages(db: Session, key: str) -> int:
    return db.scalar(select(func.count()).select_from(ChatMessageRecord).where(ChatMessageRecord.session_id == key)) or 0

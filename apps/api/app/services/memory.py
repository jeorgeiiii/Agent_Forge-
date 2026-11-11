"""Agent conversation memory, stored in the existing chat_messages table.

The worker addresses memory by an opaque key (a hash of graph + agent + session),
so different graphs/agents never read each other's conversations and the key
always fits the column. Keys are prefixed "mem:" to stay apart from the editor's
own Copilot chat sessions in the same table.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import ChatMessageRecord
from .common import ServiceError

KEY_PREFIX = "mem:"
MAX_MESSAGE_CHARS = 50_000
MAX_LOAD = 200
ROLES = {"user", "assistant"}


def _check_key(key: str) -> str:
    if not key.startswith(KEY_PREFIX) or len(key) > 100:
        raise ServiceError("Invalid memory key.", 422)
    return key


def load_memory(db: Session, key: str, limit: int = 20) -> list[dict[str, Any]]:
    """The last `limit` messages of a conversation, oldest first."""
    limit = max(1, min(int(limit), MAX_LOAD))
    rows = db.scalars(
        select(ChatMessageRecord)
        .where(ChatMessageRecord.session_id == _check_key(key))
        .order_by(ChatMessageRecord.id.desc())
        .limit(limit)
    ).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def append_memory(db: Session, key: str, messages: list[dict[str, Any]]) -> int:
    """Add user/assistant messages to the end of a conversation."""
    _check_key(key)
    added = 0
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role not in ROLES or not isinstance(content, str) or not content:
            continue
        db.add(ChatMessageRecord(session_id=key, role=role, content=content[:MAX_MESSAGE_CHARS]))
        added += 1
    db.commit()
    return added


def clear_memory(db: Session, key: str) -> int:
    result = db.execute(delete(ChatMessageRecord).where(ChatMessageRecord.session_id == _check_key(key)))
    db.commit()
    return result.rowcount

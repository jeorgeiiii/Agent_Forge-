"""Cluster 12 - manage an agent's conversation memory per graph (list / read / clear)."""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from ..deps import current_user_id, get_db
from ..services import memory as svc

router = APIRouter(prefix="/graphs/{graph_id}/memory", tags=["memory"])


@router.get("/sessions")
def list_sessions(graph_id: int, limit: int = 100, user_id: int | None = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    """Conversations the graph's agents remember: agent, session id, message count, last activity."""
    return svc.list_sessions(db, user_id, graph_id, limit=limit)


@router.get("/sessions/{key}")
def get_session(graph_id: int, key: str, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.get_session(db, user_id, graph_id, key)


@router.delete("/sessions/{key}", status_code=status.HTTP_204_NO_CONTENT)
def clear_session(graph_id: int, key: str, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """'Clear memory' for one conversation."""
    svc.clear_session(db, user_id, graph_id, key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/sessions")
def clear_all_sessions(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Forget every conversation of this graph."""
    return {"deleted_messages": svc.clear_all_sessions(db, user_id, graph_id)}

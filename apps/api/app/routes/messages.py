from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import ChatMessageRecord
from ..schemas import ChatMessageCreate, ChatMessageRead

router = APIRouter(prefix="/chat", tags=["chat_messages"])


@router.get("/{session_id}/messages", response_model=list[ChatMessageRead])
def get_chat_history(session_id: str, db: Session = Depends(get_db)):
    """Fetch persistent chat memory from PostgreSQL for a given session."""
    query = (
        select(ChatMessageRecord)
        .where(ChatMessageRecord.session_id == session_id)
        .order_by(ChatMessageRecord.created_at.asc())
    )
    return db.scalars(query).all()


@router.post("/{session_id}/messages", response_model=ChatMessageRead, status_code=status.HTTP_201_CREATED)
def save_chat_message(session_id: str, payload: ChatMessageCreate, db: Session = Depends(get_db)):
    """Persist a message (user or assistant) to PostgreSQL chat_messages table."""
    record = ChatMessageRecord(
        session_id=session_id,
        role=payload.role,
        content=payload.content,
        graph_data=payload.graph_data,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.delete("/{session_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
def clear_chat_history(session_id: str, db: Session = Depends(get_db)):
    """Clear memory for a given session."""
    db.execute(delete(ChatMessageRecord).where(ChatMessageRecord.session_id == session_id))
    db.commit()
    return None

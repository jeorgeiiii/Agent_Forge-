"""Public trigger endpoints: webhooks and chat. No session needed - the URL token is the secret."""

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..deps import get_db
from ..rate_limiter import rate_limit
from ..schemas import ChatTriggerMessage
from ..services import triggers

router = APIRouter(tags=["triggers"])
MAX_WEBHOOK_BODY = 1_000_000


async def _read_body(request: Request):
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BODY:
        from ..services.common import ServiceError

        raise ServiceError("Webhook body exceeds 1 MB.", 413)
    if not raw:
        return None
    if "json" in request.headers.get("content-type", ""):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    if "form" in request.headers.get("content-type", ""):
        form = await request.form()
        return {k: str(v) for k, v in form.items()}
    return raw.decode("utf-8", errors="replace")


@router.api_route(
    "/webhooks/{token}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def handle_incoming_webhook(token: str, request: Request, db: Session = Depends(get_db)):
    run, trigger = await triggers.handle_incoming_webhook(
        db,
        token,
        method=request.method,
        body=await _read_body(request),
        query=dict(request.query_params),
        headers=dict(request.headers),
    )
    return await triggers.respond_to_webhook(run, trigger)


@router.post("/chat/{token}", dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))])
async def handle_chat_message(token: str, payload: ChatTriggerMessage, db: Session = Depends(get_db)):
    return await triggers.handle_chat_message(
        db, token, message=payload.message, session_id=payload.session_id, wait=payload.wait
    )

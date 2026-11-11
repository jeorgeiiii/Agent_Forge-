"""Endpoints only the worker calls (guarded by X-Internal-Token)."""

import json
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..deps import get_db, require_internal_token
from ..services import credentials, runs, triggers

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_token)], include_in_schema=False)


@router.post("/runs/{workflow_id}/checkpoint")
def checkpoint(workflow_id: str, state: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    run = runs.checkpoint_state(db, workflow_id, state)
    return {"run_id": run.id, "status": run.status}


@router.get("/credentials/{credential_id}")
async def credential(credential_id: int, db: Session = Depends(get_db)):
    value = credentials.get_credential(db, credential_id)
    if value["type"] == "oauth2" and credentials.oauth_token_needs_refresh(value["data"]):
        record = await credentials.refresh_oauth_token(db, credential_id)
        value["data"] = json.loads(credentials.decrypt_value(record.encrypted_data))
    return value


@router.post("/triggers/{trigger_id}/fire")
async def fire(trigger_id: int, db: Session = Depends(get_db)):
    run = await triggers.handle_schedule_fire(db, trigger_id)
    return {"run_id": run.id, "workflow_id": run.workflow_id}

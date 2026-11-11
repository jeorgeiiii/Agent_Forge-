"""Endpoints only the worker calls (guarded by X-Internal-Token)."""

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, Request, Response
from sqlalchemy.orm import Session

from ..deps import get_db, require_internal_token
from ..config import get_settings
from ..services import credentials, events, files, memory, runs, triggers
from ..services.common import ServiceError

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_token)], include_in_schema=False)


@router.post("/runs/{workflow_id}/checkpoint")
def checkpoint(workflow_id: str, state: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    run = runs.checkpoint_state(db, workflow_id, state)
    return {"run_id": run.id, "status": run.status}


@router.post("/runs/{workflow_id}/events")
def events_append(workflow_id: str, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Events-only delivery (agents flush these live; sub-agents tag them with their source)."""
    run = runs.run_by_workflow_id(db, workflow_id)
    added = events.ingest_events(db, run, payload.get("events") or [], source=str(payload.get("source") or ""))
    return {"added": added}


@router.post("/files")
async def file_store(request: Request, db: Session = Depends(get_db)):
    """Worker stores bytes a node produced. Headers: X-File-Name, X-File-Mime, X-Workflow-Id. Returns the $file ref."""
    limit = get_settings().max_file_bytes
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise ServiceError(f"File is larger than the {limit // (1024 * 1024)} MB limit.", 413)
    workspace_id, graph_id, run_id = files.resolve_run_context(db, request.headers.get("x-workflow-id", ""))
    record = files.upload_file(db, None, data=data, name=request.headers.get("x-file-name", "file"),
                               mime=request.headers.get("x-file-mime"), workspace_id=workspace_id,
                               graph_id=graph_id, run_id=run_id, check_access=False)
    return files.file_ref(record)


@router.get("/files/{file_id}")
def file_bytes(file_id: str, workflow_id: str = "", db: Session = Depends(get_db)):
    """Worker reads a file a node references - only files of the same workspace as the run."""
    record = db.get(files.FileRecord, file_id)
    workspace_id, _, _ = files.resolve_run_context(db, workflow_id)
    if record is None or (workflow_id and record.workspace_id != workspace_id):
        raise ServiceError("File not found.", 404)
    return Response(files.read_file_bytes(record), media_type=record.mime,
                    headers={"X-File-Name": record.name.encode("ascii", "replace").decode()})


@router.get("/credentials/{credential_id}")
async def credential(credential_id: int, db: Session = Depends(get_db)):
    value = credentials.get_credential(db, credential_id)
    if value["type"] == "oauth2" and credentials.oauth_token_needs_refresh(value["data"]):
        record = await credentials.refresh_oauth_token(db, credential_id)
        value["data"] = json.loads(credentials.decrypt_value(record.encrypted_data))
    return value


@router.get("/memory/{key}")
def memory_load(key: str, limit: int = 20, db: Session = Depends(get_db)):
    """{"summary": str|None, "covers_until": id, "messages": [{id, role, content}]}"""
    return memory.load_memory(db, key, limit)


@router.post("/memory/{key}")
def memory_append(key: str, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Body: {"messages": [...], "meta": {"graph_id", "scope", "session_id"}}"""
    return {"added": memory.append_memory(db, key, payload.get("messages") or [], payload.get("meta"))}


@router.post("/memory/{key}/summary")
def memory_summary(key: str, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Body: {"content": str, "covers_until": message id, "meta": {...}}"""
    memory.save_summary(db, key, str(payload.get("content") or ""), int(payload.get("covers_until") or 0), payload.get("meta"))
    return {"ok": True}


@router.post("/triggers/{trigger_id}/fire")
async def fire(trigger_id: int, db: Session = Depends(get_db)):
    run = await triggers.handle_schedule_fire(db, trigger_id)
    return {"run_id": run.id, "workflow_id": run.workflow_id}

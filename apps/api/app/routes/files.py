"""Cluster 19 - file upload / download / signed links."""

from fastapi import APIRouter, Body, Depends, File, Form, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..deps import current_user_id, get_db
from ..services import files as svc
from ..services.audit import audit
from ..services.common import ServiceError
from ..services.storage import content_disposition, get_storage, verify_token

router = APIRouter(prefix="/files", tags=["files"])
SAFE_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "private"}


def _stream(record):
    headers = {**SAFE_HEADERS, "Content-Disposition": content_disposition(record.name, record.mime),
               "Content-Length": str(record.size)}
    return StreamingResponse(get_storage().get(record.storage_key), media_type=record.mime, headers=headers)


async def _read_limited(upload: UploadFile) -> bytes:
    limit = get_settings().max_file_bytes
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise ServiceError(f"File is larger than the {limit // (1024 * 1024)} MB limit.", 413)
    return data


# ---------- signed links (no session needed: the HMAC token is the permission) ----------

@router.get("/signed/{token}")
def signed_download(token: str):
    payload = verify_token(token, "get")
    with SessionLocal() as db:
        record = db.get(svc.FileRecord, payload["file"])
        if record is None or record.status != "ready":
            raise ServiceError("File not found.", 404)
        db.expunge(record)
    return _stream(record)


@router.put("/signed/{token}")
async def signed_upload(token: str, request: Request):
    payload = verify_token(token, "put")
    limit = get_settings().max_file_bytes
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise ServiceError(f"File is larger than the {limit // (1024 * 1024)} MB limit.", 413)
    with SessionLocal() as db:
        record = svc.store_signed_put(db, payload["file"], data)
        return svc.file_to_dict(record)


# ------------------------------------------------------------ authenticated API

@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(request: Request, file: UploadFile = File(...), workspace_id: int | None = Form(default=None),
                 graph_id: int | None = Form(default=None), user_id: int | None = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    """Multipart upload (field "file"). Returns the file and the {"$file": ...} reference for nodes."""
    data = await _read_limited(file)
    record = svc.upload_file(db, user_id, data=data, name=file.filename or "file", mime=file.content_type,
                             workspace_id=workspace_id, graph_id=graph_id)
    audit(request, db, "file.upload", resource_type="file", resource_id=record.id, workspace_id=workspace_id,
          details={"name": record.name, "mime": record.mime, "size": record.size})
    return svc.file_to_dict(record)


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
def start_direct_upload(payload: dict = Body(...), user_id: int | None = Depends(current_user_id),
                        db: Session = Depends(get_db)):
    """Big files: get a short-lived PUT URL, upload straight to storage, then POST /files/{id}/complete."""
    return svc.start_direct_upload(db, user_id, name=str(payload.get("name") or "file"),
                                   mime=str(payload.get("mime") or ""), size=int(payload.get("size") or 0),
                                   workspace_id=payload.get("workspace_id"))


@router.get("")
def list_files(workspace_id: int | None = None, graph_id: int | None = None, run_id: int | None = None,
               user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return [svc.file_to_dict(f) for f in svc.list_files(db, user_id, workspace_id=workspace_id,
                                                        graph_id=graph_id, run_id=run_id)]


@router.get("/{file_id}")
def file_metadata(file_id: str, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.file_to_dict(svc.get_file(db, user_id, file_id))


@router.get("/{file_id}/content")
def download(file_id: str, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    record = svc.get_file(db, user_id, file_id)
    if record.status != "ready":
        raise ServiceError("File upload has not completed.", 409)
    return _stream(record)


@router.post("/{file_id}/url")
def presigned_url(file_id: str, ttl: int | None = None, user_id: int | None = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    """A short-lived link anyone can open (share it, or let the browser download without auth headers)."""
    ttl = max(30, min(ttl or get_settings().signed_url_ttl_seconds, 7 * 24 * 3600))
    return svc.generate_presigned_url(svc.get_file(db, user_id, file_id), ttl)


@router.post("/{file_id}/complete")
def complete_upload(file_id: str, request: Request, user_id: int | None = Depends(current_user_id),
                    db: Session = Depends(get_db)):
    record = svc.complete_direct_upload(db, user_id, file_id)
    audit(request, db, "file.upload", resource_type="file", resource_id=record.id, workspace_id=record.workspace_id,
          details={"name": record.name, "mime": record.mime, "size": record.size, "direct": True})
    return svc.file_to_dict(record)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(file_id: str, request: Request, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    record = svc.delete_file(db, user_id, file_id)
    audit(request, db, "file.delete", resource_type="file", resource_id=file_id, workspace_id=record.workspace_id,
          details={"name": record.name})
    return Response(status_code=status.HTTP_204_NO_CONTENT)

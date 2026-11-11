"""Cluster 19 - files & binary data.

Between nodes a file travels as a small *reference*, never as bytes (Temporal
payloads stay small and history stays clean):

    {"$file": {"id": "...", "name": "report.pdf", "mime": "application/pdf", "size": 12345}}
"""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import FileRecord, GraphRecord, RunRecord
from .authz import role_at_least, get_user_role_in_workspace
from .common import Forbidden, NotFound, ServiceError
from .storage import get_storage

ALLOWED_PREFIXES = ("text/", "image/", "audio/", "video/")
ALLOWED_TYPES = {
    "application/pdf", "application/json", "application/xml", "application/zip", "application/x-zip-compressed",
    "application/gzip", "application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/octet-stream", "application/x-ndjson",
}
BLOCKED_EXTENSIONS = {".exe", ".dll", ".bat", ".cmd", ".com", ".msi", ".scr", ".ps1", ".vbs", ".jar", ".sh",
                      ".app", ".dmg", ".apk", ".so", ".dylib"}
EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                    b"\xca\xfe\xba\xbe", b"#!")


# ------------------------------------------------------------------ helpers


def file_ref(record: FileRecord) -> dict[str, Any]:
    return {"$file": {"id": record.id, "name": record.name, "mime": record.mime, "size": record.size}}


def file_to_dict(record: FileRecord) -> dict[str, Any]:
    return {"id": record.id, "name": record.name, "mime": record.mime, "size": record.size, "sha256": record.sha256,
            "status": record.status, "workspace_id": record.workspace_id, "graph_id": record.graph_id,
            "run_id": record.run_id, "created_at": record.created_at, "ref": file_ref(record)}


def _clean_name(name: str) -> str:
    name = (name or "file").replace("\\", "/").rsplit("/", 1)[-1].strip() or "file"  # no paths
    return "".join(ch for ch in name if ch.isprintable())[:255]


def validate_upload(name: str, mime: str | None, head: bytes, size: int) -> str:
    """Size / type / executable checks. Returns the MIME type to store."""
    if size > get_settings().max_file_bytes:
        raise ServiceError(f"File is larger than the {get_settings().max_file_bytes // (1024 * 1024)} MB limit.", 413)
    if size == 0:
        raise ServiceError("File is empty.", 422)
    extension = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if extension in BLOCKED_EXTENSIONS:
        raise ServiceError(f"{extension} files are not allowed.", 415)
    if head.startswith(EXECUTABLE_MAGIC):
        raise ServiceError("Executable files are not allowed.", 415)
    mime = (mime or "").split(";")[0].strip().lower() or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not (mime in ALLOWED_TYPES or mime.startswith(ALLOWED_PREFIXES)):
        raise ServiceError(f"File type {mime} is not allowed.", 415)
    return mime


def _check(db: Session, user_id: int | None, workspace_id: int | None, required: str) -> None:
    if workspace_id is None:
        if get_settings().auth_required:
            raise Forbidden("Files must belong to a workspace.")
        return
    if not role_at_least(get_user_role_in_workspace(db, user_id, workspace_id), required):
        raise NotFound("File not found.") if required == "viewer" else Forbidden("You cannot change files here.")


def get_file(db: Session, user_id: int | None, file_id: str, *, required: str = "viewer") -> FileRecord:
    record = db.get(FileRecord, file_id)
    if record is None:
        raise NotFound("File not found.")
    _check(db, user_id, record.workspace_id, required)
    return record


def _key(workspace_id: int | None, file_id: str) -> str:
    return f"ws-{workspace_id if workspace_id is not None else 'none'}/{file_id[:2]}/{file_id}"


# ------------------------------------------------------------------ operations


def upload_file(db: Session, user_id: int | None, *, data: bytes, name: str, mime: str | None,
                workspace_id: int | None = None, graph_id: int | None = None, run_id: int | None = None,
                check_access: bool = True) -> FileRecord:
    """Validate, store the bytes, record metadata. Returns the file (use file_ref() to pass it to nodes)."""
    if check_access:
        _check(db, user_id, workspace_id, "editor")
    name = _clean_name(name)
    mime = validate_upload(name, mime, data[:16], len(data))
    file_id = str(uuid.uuid4())
    record = FileRecord(id=file_id, workspace_id=workspace_id, graph_id=graph_id, run_id=run_id, name=name, mime=mime,
                        size=len(data), sha256=hashlib.sha256(data).hexdigest(), storage_key=_key(workspace_id, file_id),
                        status="ready", created_by=user_id)
    get_storage().put(record.storage_key, data, mime)  # bytes first: metadata never points at nothing
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def read_file_bytes(record: FileRecord) -> bytes:
    if record.status != "ready":
        raise ServiceError("File upload has not completed.", 409)
    return b"".join(get_storage().get(record.storage_key))


def generate_presigned_url(record: FileRecord, ttl: int | None = None) -> dict[str, Any]:
    ttl = ttl or get_settings().signed_url_ttl_seconds
    if record.status != "ready":
        raise ServiceError("File upload has not completed.", 409)
    url = get_storage().presigned_get(record.storage_key, file_id=record.id, name=record.name, mime=record.mime, ttl=ttl)
    return {"url": url, "expires_in": ttl}


def start_direct_upload(db: Session, user_id: int | None, *, name: str, mime: str, size: int,
                        workspace_id: int | None = None) -> dict[str, Any]:
    """Step 1 of a direct (browser -> storage) upload: a pending file + a short-lived PUT URL."""
    _check(db, user_id, workspace_id, "editor")
    name = _clean_name(name)
    mime = validate_upload(name, mime, b"", max(size, 1))
    file_id = str(uuid.uuid4())
    record = FileRecord(id=file_id, workspace_id=workspace_id, name=name, mime=mime, size=size, sha256="",
                        storage_key=_key(workspace_id, file_id), status="pending", created_by=user_id)
    db.add(record)
    db.commit()
    ttl = get_settings().signed_url_ttl_seconds
    url = get_storage().presigned_put(record.storage_key, file_id=file_id, mime=mime, ttl=ttl)
    return {"file_id": file_id, "upload_url": url, "method": "PUT", "headers": {"Content-Type": mime}, "expires_in": ttl}


def complete_direct_upload(db: Session, user_id: int | None, file_id: str) -> FileRecord:
    """Step 2: verify what actually landed in storage (size, type, content) and mark the file ready."""
    record = get_file(db, user_id, file_id, required="editor")
    if record.status == "ready":
        return record
    storage = get_storage()
    if not storage.exists(record.storage_key):
        raise ServiceError("Nothing was uploaded yet.", 409)
    data = b"".join(storage.get(record.storage_key))
    try:
        validate_upload(record.name, record.mime, data[:16], len(data))
    except ServiceError:
        storage.delete(record.storage_key)  # reject: never keep a file that failed the checks
        db.delete(record)
        db.commit()
        raise
    record.size, record.sha256, record.status = len(data), hashlib.sha256(data).hexdigest(), "ready"
    db.commit()
    db.refresh(record)
    return record


def store_signed_put(db: Session, file_id: str, data: bytes) -> FileRecord:
    """Local backend: receive the bytes of a presigned PUT."""
    record = db.get(FileRecord, file_id)
    if record is None or record.status != "pending":
        raise ServiceError("This upload link is no longer valid.", 410)
    validate_upload(record.name, record.mime, data[:16], len(data))
    get_storage().put(record.storage_key, data, record.mime)
    record.size, record.sha256, record.status = len(data), hashlib.sha256(data).hexdigest(), "ready"
    db.commit()
    db.refresh(record)
    return record


def delete_file(db: Session, user_id: int | None, file_id: str) -> FileRecord:
    record = get_file(db, user_id, file_id, required="editor")
    get_storage().delete(record.storage_key)
    db.delete(record)
    db.commit()
    return record


def list_files(db: Session, user_id: int | None, *, workspace_id: int | None = None, graph_id: int | None = None,
               run_id: int | None = None, limit: int = 100) -> list[FileRecord]:
    _check(db, user_id, workspace_id, "viewer")
    query = select(FileRecord).where(FileRecord.workspace_id == workspace_id if workspace_id is not None
                                     else FileRecord.workspace_id.is_(None))
    if graph_id is not None:
        query = query.where(FileRecord.graph_id == graph_id)
    if run_id is not None:
        query = query.where(FileRecord.run_id == run_id)
    return list(db.scalars(query.order_by(FileRecord.created_at.desc()).limit(max(1, min(limit, 500)))))


def delete_files_where(db: Session, *conditions) -> int:
    """Retention helper: remove bytes + metadata of matching files."""
    records = list(db.scalars(select(FileRecord).where(*conditions)))
    storage = get_storage()
    for record in records:
        storage.delete(record.storage_key)
        db.delete(record)
    return len(records)


def resolve_run_context(db: Session, workflow_id: str) -> tuple[int | None, int | None, int | None]:
    """(workspace_id, graph_id, run_id) of a run - files a run creates belong to its workspace."""
    run = db.scalar(select(RunRecord).where(RunRecord.workflow_id == workflow_id))
    if run is None:
        return None, None, None
    graph = db.get(GraphRecord, run.graph_id)
    return (graph.workspace_id if graph else None), run.graph_id, run.id

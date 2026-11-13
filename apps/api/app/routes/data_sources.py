"""Company databases: connect (owners), browse tables, try a query (editors), and the worker's query endpoint.

The database address (with password) never leaves the API: responses show only "postgres · host/db".
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Request, Response, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..deps import current_user_id, get_db, require_internal_token
from ..rate_limiter import rate_limit
from ..services import data_sources as svc
from ..services.audit import audit
from ..services.common import ServiceError

router = APIRouter(tags=["data-sources"])
internal_router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_token)],
                            include_in_schema=False)


def _audit(request: Request | None, db: Session, action: str, record, **details) -> None:
    audit(request, db, action, resource_type="data_source", resource_id=record.id, workspace_id=record.workspace_id,
          details={"name": record.name, **details})


@router.get("/workspaces/{workspace_id}/data-sources")
def list_sources(workspace_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return [svc.source_to_dict(r) for r in svc.list_sources(db, user_id, workspace_id)]


@router.post("/workspaces/{workspace_id}/data-sources", status_code=status.HTTP_201_CREATED)
async def create_source(workspace_id: int, request: Request, payload: dict[str, Any] = Body(...),
                        user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Body: {"name", "url", "description", "max_rows"}. Tests the connection first (owners only)."""
    record = await run_in_threadpool(
        svc.create_source, db, user_id, workspace_id, name=str(payload.get("name") or ""),
        url=str(payload.get("url") or ""), description=str(payload.get("description") or ""),
        max_rows=payload.get("max_rows", 200))
    _audit(request, db, "data_source.create", record, kind=record.kind)
    return svc.source_to_dict(record)


@router.patch("/data-sources/{source_id}")
async def update_source(source_id: int, request: Request, payload: dict[str, Any] = Body(...),
                        user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    record = await run_in_threadpool(svc.update_source, db, user_id, source_id, payload)
    _audit(request, db, "data_source.update", record, fields=sorted(k for k in payload if k != "url"),
           address_changed=bool(payload.get("url")))
    return svc.source_to_dict(record)


@router.delete("/data-sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, request: Request, user_id: int | None = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    record = svc.delete_source(db, user_id, source_id)
    _audit(request, db, "data_source.delete", record)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/data-sources/{source_id}/test")
async def test_source(source_id: int, request: Request, user_id: int | None = Depends(current_user_id),
                      db: Session = Depends(get_db)):
    """Re-reads the table list. A failure is reported in last_error (not as an HTTP error)."""
    record = svc.get_source(db, user_id, source_id, "editor")
    record = await run_in_threadpool(svc.refresh_schema, db, record)
    _audit(request, db, "data_source.test", record, ok=record.last_error is None)
    return svc.source_to_dict(record)


@router.get("/business-concepts")
def business_concepts():
    """The standard concepts a data dictionary maps (for the Company data page)."""
    from agentforge_core.business_concepts import describe_concepts

    return describe_concepts()


@router.get("/data-sources/{source_id}/schema")
def schema(source_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    record = svc.get_source(db, user_id, source_id, "viewer")
    return record.schema_cache or {"tables": []}


@router.post("/data-sources/{source_id}/query", dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))])
async def try_query(source_id: int, request: Request, payload: dict[str, Any] = Body(...),
                    user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """'Try a query' box on the Company data page (editors). Body: {"sql", "params"}."""
    record = svc.get_source(db, user_id, source_id, "editor")
    result = await run_in_threadpool(svc.run_query, record, str(payload.get("sql") or ""), payload.get("params"))
    _audit(request, db, "data_source.query", record, rows=result["row_count"], sql_chars=len(str(payload.get("sql"))))
    return result


# ------------------------------------------------------------------ worker


@internal_router.post("/data-sources/{source_id}/run")
async def run_for_workflow(source_id: int, payload: dict[str, Any] = Body(...), workflow_id: str = "",
                           db: Session = Depends(get_db)):
    """The 'Company database' block / agent tool. Body: {"action": "query"|"list_tables"|"describe_table",
    "sql", "params", "table", "max_rows"}. The run's workspace must own the database."""
    record = svc.check_source_for_run(db, source_id, workflow_id)
    action = payload.get("action") or "query"
    only = payload.get("tables")
    if only is not None and not (isinstance(only, list) and all(isinstance(t, str) for t in only)):
        raise ServiceError('tables must be a list of table names, e.g. ["receipts", "invoices"].', 422)
    concepts = payload.get("concepts")
    if concepts is not None and not (isinstance(concepts, list) and all(isinstance(c, str) for c in concepts)):
        raise ServiceError('concepts must be a list like ["customer_payment", "invoice"].', 422)
    if action == "list_tables":
        return svc.describe_tables(record, only=only, concepts=concepts)
    if action == "describe_table":
        return svc.describe_tables(record, str(payload.get("table") or ""), only=only, concepts=concepts)
    if action != "query":
        raise ServiceError(f"Unknown action '{action}'.", 422)
    params = payload.get("params")
    if params is not None and not isinstance(params, dict):
        raise ServiceError("params must be an object like {\"email\": \"ada@example.com\"}.", 422)
    max_rows = payload.get("max_rows")
    return await run_in_threadpool(svc.run_query, record, str(payload.get("sql") or ""), params,
                                   int(max_rows) if isinstance(max_rows, int) else None)

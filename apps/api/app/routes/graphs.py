from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from ..compiler import compile_graph
from ..deps import current_user_id, get_db
from ..schemas import (
    GraphCreate,
    GraphDuplicate,
    GraphPublish,
    GraphRead,
    GraphRename,
    GraphUpdate,
    GraphVersionRead,
    TriggerRead,
)
from ..services import graphs as svc
from ..services import triggers
from ..services.common import ServiceError

router = APIRouter(prefix="/graphs", tags=["graphs"])


def _trigger_read(trigger) -> TriggerRead:
    return TriggerRead(
        id=trigger.id,
        node_id=trigger.node_id,
        kind=trigger.kind,
        active=trigger.active,
        url=triggers.webhook_url(trigger) if trigger.kind in ("webhook", "chat") else None,
        config=trigger.config,
        schedule_id=trigger.schedule_id,
    )


@router.get("", response_model=list[GraphRead])
def list_graphs(workspace_id: int | None = None, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.list_graphs_for_workspace(db, user_id, workspace_id)


@router.post("", response_model=GraphRead, status_code=status.HTTP_201_CREATED)
def create_graph(payload: GraphCreate, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    definition = payload.definition.model_dump(mode="json") if payload.definition else None
    return svc.create_graph(db, user_id, payload.name, payload.description, definition, payload.workspace_id)


@router.get("/{graph_id}", response_model=GraphRead)
def get_graph(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.get_graph_by_id(db, user_id, graph_id)


@router.put("/{graph_id}", response_model=GraphRead)
def update_graph(graph_id: int, payload: GraphUpdate, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.update_graph(
        db, user_id, graph_id,
        name=payload.name, description=payload.description, definition=payload.definition.model_dump(mode="json"),
    )


@router.delete("/{graph_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_graph(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    graph = svc.delete_graph(db, user_id, graph_id)
    await triggers.deactivate_graph_triggers(db, graph)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{graph_id}/restore", response_model=GraphRead)
def restore_graph(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.restore_graph(db, user_id, graph_id)


@router.post("/{graph_id}/duplicate", response_model=GraphRead, status_code=status.HTTP_201_CREATED)
def duplicate_graph(graph_id: int, payload: GraphDuplicate, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.duplicate_graph(db, user_id, graph_id, payload.name)


@router.patch("/{graph_id}/name", response_model=GraphRead)
def rename_graph(graph_id: int, payload: GraphRename, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.rename_graph(db, user_id, graph_id, payload.name)


@router.get("/{graph_id}/versions", response_model=list[GraphVersionRead])
def list_versions(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.list_versions_of_graph(db, user_id, graph_id)


@router.post("/{graph_id}/versions/{version}/rollback", response_model=GraphRead)
def rollback(graph_id: int, version: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.rollback_to_version(db, user_id, graph_id, version)


@router.get("/{graph_id}/compiled")
def compiled(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """The executable IR (supersteps, hash) for the current draft."""
    return compile_graph(svc.get_graph_by_id(db, user_id, graph_id).definition)


@router.post("/{graph_id}/publish")
async def publish(graph_id: int, payload: GraphPublish, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    version = svc.publish_graph_version(db, user_id, graph_id, payload.version)
    graph = svc.get_graph_by_id(db, user_id, graph_id)
    try:
        synced = await triggers.sync_triggers_for_graph(db, graph, version.compiled_ir)
    except ServiceError:
        svc.unpublish_graph_version(db, user_id, graph_id)
        raise
    return {
        "graph_id": graph_id,
        "published_version": version.version,
        "hash": version.compiled_hash,
        "triggers": [_trigger_read(t) for t in synced],
    }


@router.post("/{graph_id}/unpublish", response_model=GraphRead)
async def unpublish(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    graph = svc.get_graph_by_id(db, user_id, graph_id)
    await triggers.deactivate_graph_triggers(db, graph)
    await triggers.delete_schedule_for_graph(db, graph)
    return svc.unpublish_graph_version(db, user_id, graph_id)


@router.get("/{graph_id}/triggers", response_model=list[TriggerRead])
def list_triggers(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    graph = svc.get_graph_by_id(db, user_id, graph_id)
    return [_trigger_read(t) for t in triggers.list_triggers_for_graph(db, graph)]


@router.post("/{graph_id}/activate", response_model=GraphRead)
async def activate(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    graph = svc.get_graph_by_id(db, user_id, graph_id)
    svc.require_edit(db, user_id, graph)
    await triggers.activate_graph_triggers(db, graph)
    return graph


@router.post("/{graph_id}/deactivate", response_model=GraphRead)
async def deactivate(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    graph = svc.get_graph_by_id(db, user_id, graph_id)
    svc.require_edit(db, user_id, graph)
    await triggers.deactivate_graph_triggers(db, graph)
    return graph

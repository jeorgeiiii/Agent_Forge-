"""Cluster 3 - Graph storage & CRUD with version history (Postgres)."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..compiler import GraphValidationError, compile_graph
from ..config import get_settings
from ..models import GraphRecord, GraphVersionRecord, MembershipRecord, WorkspaceRecord
from .authz import can_user_edit_graph, can_user_view_graph, default_workspace_for_user, require_role
from .common import Forbidden, NotFound, ServiceError, now_utc

DEFAULT_DEFINITION: dict[str, Any] = {
    "nodes": [
        {"id": "start", "type": "start", "label": "Start", "config": {}},
        {"id": "end", "type": "end", "label": "End", "config": {}},
    ],
    "edges": [{"source": "start", "target": "end"}],
}


def _validated(definition: dict[str, Any]) -> dict[str, Any]:
    """Compile to make sure the definition is runnable; returns the IR."""
    try:
        return compile_graph(definition)
    except GraphValidationError as error:
        raise ServiceError(str(error), 422) from error


def _snapshot(db: Session, graph: GraphRecord, user_id: int | None, ir: dict[str, Any] | None = None) -> GraphVersionRecord:
    version = GraphVersionRecord(
        graph_id=graph.id,
        version=graph.version,
        name=graph.name,
        description=graph.description,
        definition=copy.deepcopy(graph.definition),
        compiled_ir=ir,
        compiled_hash=ir["hash"] if ir else None,
        created_by=user_id,
    )
    db.add(version)
    return version


def _load(db: Session, graph_id: int) -> GraphRecord:
    graph = db.get(GraphRecord, graph_id)
    if graph is None or graph.deleted_at is not None:
        raise NotFound("Graph not found.")
    return graph


def require_edit(db: Session, user_id: int | None, graph: GraphRecord) -> None:
    if not can_user_edit_graph(db, user_id, graph):
        raise Forbidden("You cannot edit this graph.")


def create_graph(
    db: Session,
    user_id: int | None,
    name: str,
    description: str = "",
    definition: dict[str, Any] | None = None,
    workspace_id: int | None = None,
) -> GraphRecord:
    """Insert a graph (default Start -> End) owned by a workspace, plus its version 1 snapshot."""
    definition = copy.deepcopy(definition or DEFAULT_DEFINITION)
    ir = _validated(definition)
    if user_id is not None:
        workspace_id = workspace_id or default_workspace_for_user(db, user_id)
        if workspace_id is None:
            raise ServiceError("You have no workspace to create graphs in.", 409)
        require_role(db, user_id, workspace_id, "editor")
    elif workspace_id is not None or get_settings().auth_required:
        raise Forbidden("Sign in to create graphs in a workspace.")
    graph = GraphRecord(name=name, description=description, definition=definition, workspace_id=workspace_id, version=1)
    db.add(graph)
    db.flush()
    _snapshot(db, graph, user_id, ir)
    db.commit()
    db.refresh(graph)
    return graph


def get_graph_by_id(db: Session, user_id: int | None, graph_id: int) -> GraphRecord:
    graph = _load(db, graph_id)
    if not can_user_view_graph(db, user_id, graph):
        raise NotFound("Graph not found.")  # do not reveal existence
    return graph


def list_graphs_for_workspace(db: Session, user_id: int | None, workspace_id: int | None = None) -> list[GraphRecord]:
    """Graphs visible to the caller, newest first.

    With a workspace id -> that workspace; signed in without one -> all their
    workspaces (+ legacy graphs in single-user mode); anonymous -> legacy graphs.
    """
    query = select(GraphRecord).where(GraphRecord.deleted_at.is_(None)).order_by(GraphRecord.updated_at.desc())
    legacy_open = not get_settings().auth_required
    if workspace_id is not None:
        require_role(db, user_id, workspace_id, "viewer")
        return list(db.scalars(query.where(GraphRecord.workspace_id == workspace_id)))
    if user_id is not None:
        member_of = (select(MembershipRecord.workspace_id)
                     .join(WorkspaceRecord, WorkspaceRecord.id == MembershipRecord.workspace_id)
                     .where(MembershipRecord.user_id == user_id, WorkspaceRecord.deleted_at.is_(None)))
        condition = GraphRecord.workspace_id.in_(member_of)
        if legacy_open:
            condition = condition | GraphRecord.workspace_id.is_(None)
        return list(db.scalars(query.where(condition)))
    if legacy_open:
        return list(db.scalars(query.where(GraphRecord.workspace_id.is_(None))))
    raise Forbidden("Sign in to list graphs.")


def update_graph(
    db: Session,
    user_id: int | None,
    graph_id: int,
    *,
    name: str,
    description: str,
    definition: dict[str, Any],
) -> GraphRecord:
    """Save new JSON, bump the version number and snapshot it into graph_versions."""
    graph = _load(db, graph_id)
    require_edit(db, user_id, graph)
    ir = _validated(definition)
    get_latest_version_of_graph(db, graph)  # make sure legacy graphs have their v1 snapshot
    graph.name = name
    graph.description = description
    graph.definition = copy.deepcopy(definition)
    graph.version += 1
    _snapshot(db, graph, user_id, ir)
    db.commit()
    db.refresh(graph)
    return graph


def delete_graph(db: Session, user_id: int | None, graph_id: int) -> GraphRecord:
    """Soft-delete (sets deleted_at) so the graph can be restored."""
    graph = _load(db, graph_id)
    require_edit(db, user_id, graph)
    graph.deleted_at = now_utc()
    graph.is_active = False
    db.commit()
    return graph


def restore_graph(db: Session, user_id: int | None, graph_id: int) -> GraphRecord:
    graph = db.get(GraphRecord, graph_id)
    if graph is None or graph.deleted_at is None:
        raise NotFound("No deleted graph with that id.")
    require_edit(db, user_id, graph)
    graph.deleted_at = None
    db.commit()
    db.refresh(graph)
    return graph


def duplicate_graph(db: Session, user_id: int | None, graph_id: int, name: str | None = None) -> GraphRecord:
    """Copy a graph's latest definition into a new graph in the same workspace."""
    source = get_graph_by_id(db, user_id, graph_id)
    if user_id is not None and source.workspace_id is not None:
        require_role(db, user_id, source.workspace_id, "editor")
    graph = GraphRecord(
        name=(name or f"{source.name} (copy)")[:200],
        description=source.description,
        definition=copy.deepcopy(source.definition),
        workspace_id=source.workspace_id,
        version=1,
    )
    db.add(graph)
    db.flush()
    _snapshot(db, graph, user_id, _validated(graph.definition))
    db.commit()
    db.refresh(graph)
    return graph


def rename_graph(db: Session, user_id: int | None, graph_id: int, name: str) -> GraphRecord:
    graph = _load(db, graph_id)
    require_edit(db, user_id, graph)
    if not name.strip():
        raise ServiceError("Name cannot be empty.", 422)
    graph.name = name.strip()[:200]
    db.commit()
    db.refresh(graph)
    return graph


def list_versions_of_graph(db: Session, user_id: int | None, graph_id: int) -> list[GraphVersionRecord]:
    graph = get_graph_by_id(db, user_id, graph_id)
    get_latest_version_of_graph(db, graph)
    return list(db.scalars(
        select(GraphVersionRecord).where(GraphVersionRecord.graph_id == graph.id).order_by(GraphVersionRecord.version.desc())
    ))


def get_version(db: Session, graph: GraphRecord, version: int) -> GraphVersionRecord:
    record = db.scalar(
        select(GraphVersionRecord).where(GraphVersionRecord.graph_id == graph.id, GraphVersionRecord.version == version)
    )
    if record is None:
        raise NotFound(f"Version {version} not found.")
    return record


def get_latest_version_of_graph(db: Session, graph: GraphRecord) -> GraphVersionRecord:
    """Most recent version snapshot (creates one for graphs saved before versioning existed)."""
    record = db.scalar(
        select(GraphVersionRecord)
        .where(GraphVersionRecord.graph_id == graph.id)
        .order_by(GraphVersionRecord.version.desc())
        .limit(1)
    )
    if record is None:
        record = _snapshot(db, graph, None)
        db.commit()
        db.refresh(record)
    return record


def rollback_to_version(db: Session, user_id: int | None, graph_id: int, version: int) -> GraphRecord:
    """Create a *new* version copying an older one, so history is never rewritten."""
    graph = _load(db, graph_id)
    require_edit(db, user_id, graph)
    old = get_version(db, graph, version)
    return update_graph(db, user_id, graph_id, name=old.name, description=old.description, definition=old.definition)


def publish_graph_version(db: Session, user_id: int | None, graph_id: int, version: int | None = None) -> GraphVersionRecord:
    """Mark a version as live. Publishing freezes its compiled IR (and hash) on the version row."""
    graph = _load(db, graph_id)
    require_edit(db, user_id, graph)
    record = get_version(db, graph, version) if version else get_latest_version_of_graph(db, graph)
    ir = _validated(record.definition)
    record.compiled_ir = ir
    record.compiled_hash = ir["hash"]
    graph.published_version = record.version
    graph.is_active = True
    db.commit()
    db.refresh(record)
    return record


def unpublish_graph_version(db: Session, user_id: int | None, graph_id: int) -> GraphRecord:
    """Revert to draft-only: no live version, production triggers off."""
    graph = _load(db, graph_id)
    require_edit(db, user_id, graph)
    graph.published_version = None
    graph.is_active = False
    db.commit()
    db.refresh(graph)
    return graph


def get_published_version(db: Session, graph: GraphRecord) -> GraphVersionRecord | None:
    if graph.published_version is None:
        return None
    return get_version(db, graph, graph.published_version)

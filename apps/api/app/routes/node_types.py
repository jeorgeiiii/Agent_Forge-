"""Cluster 5/7 endpoints: node palette, schemas, config + expression validation."""

from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

import agentforge_core  # noqa: F401
from agentforge_core.expressions import list_available_references, validate_expression
from agentforge_core.registry import get_node_schema, get_node_type, list_node_types, validate_node_config

from ..compiler import GraphValidationError, compile_graph
from ..deps import current_user_id, get_db
from ..services.common import ServiceError
from ..services.graphs import get_graph_by_id

router = APIRouter(tags=["nodes"])


def _spec(node_type: str):
    try:
        return get_node_type(node_type)
    except KeyError as error:
        raise ServiceError(f"Unknown node type '{node_type}'.", 404) from error


@router.get("/node-types")
def node_types(category: str | None = None):
    return [spec.to_dict() for spec in list_node_types(category)]


@router.get("/node-types/{node_type}")
def node_type(node_type: str):
    return _spec(node_type).to_dict()


@router.get("/node-types/{node_type}/schema")
def node_schema(node_type: str):
    _spec(node_type)
    return get_node_schema(node_type)


@router.post("/node-types/{node_type}/validate")
def validate_config(node_type: str, config: dict[str, Any] = Body(default_factory=dict)):
    _spec(node_type)
    errors = validate_node_config(node_type, config)
    return {"valid": not errors, "errors": errors}


@router.post("/expressions/validate")
def validate(payload: dict[str, Any] = Body(...)):
    """Body: {"expression": "...", "known_nodes": ["HTTP", ...]}"""
    errors = validate_expression(str(payload.get("expression", "")), payload.get("known_nodes"))
    return {"valid": not errors, "errors": errors}


@router.get("/graphs/{graph_id}/nodes/{node_id}/references")
def references(graph_id: int, node_id: str, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Autocomplete: variables and upstream node outputs available at a node."""
    graph = get_graph_by_id(db, user_id, graph_id)
    try:
        ir = compile_graph(graph.definition)
    except GraphValidationError as error:
        raise ServiceError(str(error), 422) from error
    if node_id not in ir["nodes"]:
        raise ServiceError("Node not found.", 404)
    return list_available_references(ir, node_id)

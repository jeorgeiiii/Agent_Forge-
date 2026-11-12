"""Integrations catalog: the services a workflow can use (Slack, GitHub, ...).

The definitions live in agentforge_core/connector_catalog.py; each one is also a node type in the editor.
Connecting a service = saving its key with the normal Saved keys API (POST /credentials, type api_key).
"""

from fastapi import APIRouter

import agentforge_core  # noqa: F401 - registers the connectors
from agentforge_core.connectors import connector_to_dict, get_connector, list_connectors

from ..services.common import ServiceError

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("")
def catalog():
    """Every integration with its actions and where to find its key (no secrets in here)."""
    return [connector_to_dict(c) for c in list_connectors()]


@router.get("/{connector_id}")
def integration(connector_id: str):
    try:
        return connector_to_dict(get_connector(connector_id))
    except ValueError as error:
        raise ServiceError(str(error), 404) from error

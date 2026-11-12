"""SSO step 1 - owner endpoints for company login settings and domains, plus public login discovery.

PAUSED: kept for later, switched off by SSO_ENABLED=false (the default). While off, every endpoint here
answers 404 "not enabled" and nothing in this file runs. Turn on with SSO_ENABLED=true.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import current_user_id, get_db
from ..rate_limiter import rate_limit
from ..services import sso as svc
from ..services.audit import audit
from ..services.common import ServiceError


def require_sso_enabled() -> None:
    """Gate for every SSO endpoint: 404 while company login is switched off (SSO_ENABLED=false)."""
    if not get_settings().sso_enabled:
        raise ServiceError("Company login (SSO) is not enabled on this server.", 404)


router = APIRouter(tags=["sso"], dependencies=[Depends(require_sso_enabled)])


def _audit(request: Request, db: Session, action: str, workspace_id: int, resource_type: str, resource_id: Any, **details):
    audit(request, db, action, resource_type=resource_type, resource_id=resource_id, workspace_id=workspace_id,
          details=details)


# ------------------------------------------------------------------ settings (owners)

@router.get("/workspaces/{workspace_id}/sso")
def get_sso(workspace_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """The company login (secret hidden) and claimed domains with the DNS record each one needs."""
    return svc.get_sso_settings(db, user_id, workspace_id)


@router.put("/workspaces/{workspace_id}/sso")
def save_sso(workspace_id: int, request: Request, payload: dict[str, Any] = Body(...),
             user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Create/update. Body: protocol, display_name, issuer, client_id, client_secret (write-only), settings,
    default_role (viewer|editor), allow_jit, enabled, enforce_sso."""
    record = svc.save_connection(db, user_id, workspace_id, payload)
    _audit(request, db, "sso.update", workspace_id, "sso_connection", record.id, protocol=record.protocol,
           enabled=record.enabled, enforce_sso=record.enforce_sso,
           fields=sorted(k for k in payload if k != "client_secret"), secret_changed=bool(payload.get("client_secret")))
    return svc.connection_to_dict(record)


@router.delete("/workspaces/{workspace_id}/sso", status_code=status.HTTP_204_NO_CONTENT)
def delete_sso(workspace_id: int, request: Request, user_id: int | None = Depends(current_user_id),
               db: Session = Depends(get_db)):
    svc.delete_connection(db, user_id, workspace_id)
    _audit(request, db, "sso.delete", workspace_id, "sso_connection", workspace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workspaces/{workspace_id}/sso/domains", status_code=status.HTTP_201_CREATED)
def add_domain(workspace_id: int, request: Request, payload: dict[str, Any] = Body(...),
               user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Body: {"domain": "acme.com"}. Returns the TXT record to add to DNS."""
    record = svc.add_domain(db, user_id, workspace_id, str(payload.get("domain") or ""))
    _audit(request, db, "sso.domain_add", workspace_id, "sso_domain", record.id, domain=record.domain)
    return svc.domain_to_dict(record)


@router.post("/workspaces/{workspace_id}/sso/domains/{domain_id}/verify")
async def verify_domain(workspace_id: int, domain_id: int, request: Request,
                        user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Checks the DNS TXT record now."""
    record = await svc.verify_domain(db, user_id, workspace_id, domain_id)
    _audit(request, db, "sso.domain_verify", workspace_id, "sso_domain", record.id, domain=record.domain)
    return svc.domain_to_dict(record)


@router.delete("/workspaces/{workspace_id}/sso/domains/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_domain(workspace_id: int, domain_id: int, request: Request,
                  user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    record = svc.remove_domain(db, user_id, workspace_id, domain_id)
    _audit(request, db, "sso.domain_remove", workspace_id, "sso_domain", domain_id, domain=record.domain)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ public: which login should this email use?

@router.post("/auth/sso/discover", dependencies=[Depends(rate_limit(max_requests=20, window_seconds=60))])
def discover(payload: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Body: {"email": "..."} -> {"method": "password"} or {"method": "sso", "required", "connection_id", "display_name"}."""
    return svc.discover_login_method(db, str(payload.get("email") or ""))

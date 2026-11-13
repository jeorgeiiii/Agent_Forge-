import html
import json

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import current_user_id, get_db, require_internal_token
from ..schemas import CredentialCreate, CredentialRead, CredentialUpdate, OAuthStart
from ..services import credentials as svc
from ..services.audit import SYSTEM, audit, record_audit_event

router = APIRouter(prefix="/credentials", tags=["credentials"])


def _audit(request, db: Session, action: str, record, **details) -> None:
    audit(request, db, action, resource_type="credential", resource_id=record.id, workspace_id=record.workspace_id,
          details={"name": record.name, **details})


@router.get("", response_model=list[CredentialRead])
def list_credentials(workspace_id: int | None = None, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.list_credentials_for_workspace(db, user_id, workspace_id)


@router.post("", response_model=CredentialRead, status_code=status.HTTP_201_CREATED)
def create_credential(payload: CredentialCreate, request: Request, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    record = svc.create_credential(
        db, user_id, name=payload.name, type=payload.type, data=payload.data, workspace_id=payload.workspace_id
    )
    _audit(request, db, "credential.create", record, type=record.type)  # never the secret itself
    return record


@router.patch("/{credential_id}", response_model=CredentialRead)
def update_credential(credential_id: int, payload: CredentialUpdate, request: Request,
                      user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    record = svc.update_credential(db, user_id, credential_id, name=payload.name, data=payload.data)
    _audit(request, db, "credential.update", record, secret_changed=payload.data is not None)
    return record


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(credential_id: int, request: Request, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    record = svc.load_credential(db, credential_id)
    name, workspace_id = record.name, record.workspace_id
    svc.delete_credential(db, user_id, credential_id)
    audit(request, db, "credential.delete", resource_type="credential", resource_id=credential_id,
          workspace_id=workspace_id, details={"name": name})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{credential_id}/refresh", response_model=CredentialRead)
async def refresh(credential_id: int, request: Request, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    svc.check_access(db, user_id, svc.load_credential(db, credential_id).workspace_id, "editor")
    record = await svc.refresh_oauth_token(db, credential_id)
    _audit(request, db, "credential.refresh", record)
    return record


@router.post("/oauth/start")
def oauth_start(payload: OAuthStart, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    """Body: {"connector": "google_sheets", "credential_name", "workspace_id"} - provider and scopes come from the
    integration's definition (the browser can't widen them). Older form: {"provider", "scopes"}."""
    provider, scopes = payload.provider, payload.scopes
    if payload.connector:
        from agentforge_core.connectors import get_connector

        try:
            connector = get_connector(payload.connector)
        except ValueError as error:
            raise svc.ServiceError(str(error), 404) from error
        if not connector.oauth_provider:
            raise svc.ServiceError(f"{connector.name} uses a pasted key, not 'Sign in with'.", 422)
        provider, scopes = connector.oauth_provider, list(connector.oauth_scopes)
    if not provider:
        raise svc.ServiceError("Say which integration (connector) or provider to sign in with.", 422)
    url = svc.start_oauth_flow(
        db, user_id, provider=provider, credential_name=payload.credential_name,
        workspace_id=payload.workspace_id, scopes=scopes,
    )
    return {"authorize_url": url}


def _oauth_page(title: str, message: str, ok: bool) -> HTMLResponse:
    """Small page shown in the sign-in popup. Tells the AgentForge tab (only that origin) and closes itself."""
    origin = json.dumps(get_settings().public_app_url.rstrip("/"))
    status_ = json.dumps("done" if ok else "failed")
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>AgentForge</title>"
        "<body style='font-family:system-ui;background:#16181f;color:#e5e7eb;display:grid;place-items:center;"
        "min-height:90vh;margin:0'><div style='text-align:center;max-width:420px;padding:16px'>"
        f"<h2>{html.escape(title)}</h2><p>{html.escape(message)}</p><p style='color:#9ca3af'>You can close this window.</p>"
        "</div><script>"
        f"try{{window.opener&&window.opener.postMessage({{type:'agentforge-oauth-'+{status_}}},{origin})}}catch(e){{}}"
        + ("setTimeout(function(){window.close()},1500);" if ok else "")
        + "</script></body>",
        status_code=200 if ok else 400,
    )


@router.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)):
    if error or not code:  # e.g. the person pressed "Cancel" on Google's screen
        reason = "You cancelled the sign-in." if error == "access_denied" else f"Sign-in did not finish ({error or 'no code'})."
        return _oauth_page("Not connected", reason, ok=False)
    try:
        record = await svc.handle_oauth_callback(db, state=state, code=code)
    except svc.ServiceError as failure:
        return _oauth_page("Not connected", failure.message, ok=False)
    _audit(None, db, "credential.oauth_connect", record, provider=record.type)
    return _oauth_page("Connected", f"{record.name} is connected to AgentForge.", ok=True)


@router.post("/rotate-master-key", dependencies=[Depends(require_internal_token)])
def rotate_master_key(db: Session = Depends(get_db)):
    """Admin-only (internal token): re-encrypt every credential with the current master key."""
    count = svc.rotate_master_key(db)
    record_audit_event(db, SYSTEM, "credentials.rotate_master_key", resource_type="credential", details={"reencrypted": count})
    return {"reencrypted": count}

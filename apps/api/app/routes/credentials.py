from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..deps import current_user_id, get_db, require_internal_token
from ..schemas import CredentialCreate, CredentialRead, CredentialUpdate, OAuthStart
from ..services import credentials as svc

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.get("", response_model=list[CredentialRead])
def list_credentials(workspace_id: int | None = None, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.list_credentials_for_workspace(db, user_id, workspace_id)


@router.post("", response_model=CredentialRead, status_code=status.HTTP_201_CREATED)
def create_credential(payload: CredentialCreate, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.create_credential(
        db, user_id, name=payload.name, type=payload.type, data=payload.data, workspace_id=payload.workspace_id
    )


@router.patch("/{credential_id}", response_model=CredentialRead)
def update_credential(credential_id: int, payload: CredentialUpdate,
                      user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.update_credential(db, user_id, credential_id, name=payload.name, data=payload.data)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(credential_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    svc.delete_credential(db, user_id, credential_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{credential_id}/refresh", response_model=CredentialRead)
async def refresh(credential_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    svc.check_access(db, user_id, svc.load_credential(db, credential_id).workspace_id, "editor")
    return await svc.refresh_oauth_token(db, credential_id)


@router.post("/oauth/start")
def oauth_start(payload: OAuthStart, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    url = svc.start_oauth_flow(
        db, user_id, provider=payload.provider, credential_name=payload.credential_name,
        workspace_id=payload.workspace_id, scopes=payload.scopes,
    )
    return {"authorize_url": url}


@router.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(state: str, code: str, db: Session = Depends(get_db)):
    record = await svc.handle_oauth_callback(db, state=state, code=code)
    return HTMLResponse(
        f"<p>Connected <b>{record.name}</b>. You can close this window.</p>"
        "<script>window.opener && window.opener.postMessage({type:'agentforge-oauth-done'}, '*');</script>"
    )


@router.post("/rotate-master-key", dependencies=[Depends(require_internal_token)])
def rotate_master_key(db: Session = Depends(get_db)):
    """Admin-only (internal token): re-encrypt every credential with the current master key."""
    return {"reencrypted": svc.rotate_master_key(db)}

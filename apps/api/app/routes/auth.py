from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_current_user, get_db, session_token_from_request
from ..models import UserRecord
from starlette.concurrency import run_in_threadpool

from ..rate_limiter import client_ip, enforce_limit, rate_limit
from ..schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyRead, LoginRequest, LoginResponse, SignupRequest, UserRead
from ..services import auth
from ..services.audit import audit
from ..services.common import ServiceError

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    return client_ip(request)


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def _login_response(db: Session, request: Request, response: Response, user: UserRecord) -> LoginResponse:
    token, session = auth.create_session(db, user.id, request.headers.get("user-agent", ""), _client_ip(request))
    _set_cookie(response, token)
    return LoginResponse(user=UserRead.model_validate(user), token=token, expires_at=session.expires_at)


@router.post("/signup", response_model=LoginResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit(max_requests=5, window_seconds=60, strict=True))])
def signup(payload: SignupRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user = auth.create_user(db, payload.email, payload.password, payload.name)
    audit(request, db, "auth.signup", user=user, resource_type="user", resource_id=user.id)
    return _login_response(db, request, response, user)


@router.post("/login", response_model=LoginResponse,
             dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60, strict=True))])
async def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    # Per-account limit too: stops slow password guessing spread over many IPs.
    await enforce_limit(f"email:{payload.email.strip().lower()}", "login", max_requests=10, window_seconds=300, strict=True)
    result = await run_in_threadpool(auth.check_login, db, payload.email, payload.password)  # scrypt is CPU-bound
    if not result.success or result.user is None:
        # failed logins are security-relevant: record the attempted email (never the password)
        audit(request, db, "auth.login_failed", resource_type="user", outcome="denied",
              details={"email": payload.email.strip().lower()[:320]})
        raise ServiceError(result.reason, 401)
    audit(request, db, "auth.login", user=result.user, resource_type="user", resource_id=result.user.id)
    return _login_response(db, request, response, result.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, db: Session = Depends(get_db)):
    token = session_token_from_request(request)
    if token:
        user_id = auth.validate_session(db, token)
        if auth.revoke_session(db, token):
            audit(request, db, "auth.logout", user=auth.find_user_by_id(db, user_id) if user_id else None,
                  resource_type="user", resource_id=user_id or "")
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return response


@router.post("/logout-all")
def logout_all(request: Request, user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    revoked = auth.revoke_all_sessions_for_user(db, user.id)
    audit(request, db, "auth.logout_all", resource_type="user", resource_id=user.id, details={"sessions": revoked})
    return {"revoked": revoked}


@router.get("/me", response_model=UserRead)
def me(user: UserRecord = Depends(get_current_user)):
    return user


@router.get("/api-keys", response_model=list[ApiKeyRead])
def list_keys(user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    return auth.list_api_keys(db, user.id)


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_key(payload: ApiKeyCreate, request: Request, user: UserRecord = Depends(get_current_user),
               db: Session = Depends(get_db)):
    raw, record = auth.create_api_key(db, user.id, payload.label)
    audit(request, db, "api_key.create", resource_type="api_key", resource_id=record.id,
          details={"label": record.label, "prefix": record.prefix})
    return ApiKeyCreated(**ApiKeyRead.model_validate(record).model_dump(), key=raw)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(key_id: int, request: Request, user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    if not auth.revoke_api_key(db, user.id, key_id):
        raise ServiceError("API key not found.", 404)
    audit(request, db, "api_key.revoke", resource_type="api_key", resource_id=key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_current_user, get_db, session_token_from_request
from ..models import UserRecord
from starlette.concurrency import run_in_threadpool

from ..rate_limiter import client_ip, enforce_limit, rate_limit
from ..schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyRead, LoginRequest, LoginResponse, SignupRequest, UserRead
from ..services import auth
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
             dependencies=[Depends(rate_limit(max_requests=5, window_seconds=60))])
def signup(payload: SignupRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user = auth.create_user(db, payload.email, payload.password, payload.name)
    return _login_response(db, request, response, user)


@router.post("/login", response_model=LoginResponse,
             dependencies=[Depends(rate_limit(max_requests=10, window_seconds=60))])
async def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    # Per-account limit too: stops slow password guessing spread over many IPs.
    await enforce_limit(f"email:{payload.email.strip().lower()}", "login", max_requests=10, window_seconds=300)
    result = await run_in_threadpool(auth.check_login, db, payload.email, payload.password)  # scrypt is CPU-bound
    if not result.success or result.user is None:
        raise ServiceError(result.reason, 401)
    return _login_response(db, request, response, result.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, db: Session = Depends(get_db)):
    token = session_token_from_request(request)
    if token:
        auth.revoke_session(db, token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return response


@router.post("/logout-all")
def logout_all(user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"revoked": auth.revoke_all_sessions_for_user(db, user.id)}


@router.get("/me", response_model=UserRead)
def me(user: UserRecord = Depends(get_current_user)):
    return user


@router.get("/api-keys", response_model=list[ApiKeyRead])
def list_keys(user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    return auth.list_api_keys(db, user.id)


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_key(payload: ApiKeyCreate, user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    raw, record = auth.create_api_key(db, user.id, payload.label)
    return ApiKeyCreated(**ApiKeyRead.model_validate(record).model_dump(), key=raw)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(key_id: int, user: UserRecord = Depends(get_current_user), db: Session = Depends(get_db)):
    if not auth.revoke_api_key(db, user.id, key_id):
        raise ServiceError("API key not found.", 404)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

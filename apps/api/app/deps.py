import hmac
from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal
from .models import UserRecord
from .services import auth


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def session_token_from_request(request: Request) -> str | None:
    """Session token from the HttpOnly cookie or an ``Authorization: Bearer`` header."""
    cookie = request.cookies.get(get_settings().session_cookie_name)
    if cookie:
        return cookie
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if not token.startswith(auth.API_KEY_PREFIX):
            return token
    return None


def _api_key_from_request(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer ") and header[7:].strip().startswith(auth.API_KEY_PREFIX):
        return header[7:].strip()
    return None


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> UserRecord | None:
    """The signed-in user (session cookie/bearer or API key), or None."""
    user_id = auth.validate_session(db, session_token_from_request(request))
    if user_id is not None:
        user = auth.find_user_by_id(db, user_id)
        request.state.auth_method = "session"  # read by the audit log
        request.state.user = user if user and user.is_active else None
        return request.state.user
    raw_key = _api_key_from_request(request)
    user = auth.get_user_by_api_key(db, raw_key)
    if user is not None:
        request.state.auth_method = "api_key"
        request.state.api_key_prefix = (raw_key or "")[:10]
    request.state.user = user
    return user


def get_current_user(user: UserRecord | None = Depends(get_optional_user)) -> UserRecord:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")
    return user


def current_user_id(user: UserRecord | None = Depends(get_optional_user)) -> int | None:
    """User id for routes that also work in single-user mode (AUTH_REQUIRED=false)."""
    if user is None and get_settings().auth_required:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")
    return user.id if user else None


def require_internal_token(x_internal_token: str = Header(default="")) -> None:
    """Guards /internal/* endpoints that only the worker may call."""
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="INTERNAL_API_TOKEN is not configured.")
    if not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=403, detail="Invalid internal token.")


def current_user_and_id(user: UserRecord | None = Depends(get_optional_user)) -> tuple[UserRecord | None, int | None]:
    """Like current_user_id, but also returns the user object (needed to attribute audit events)."""
    if user is None and get_settings().auth_required:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")
    return user, (user.id if user else None)

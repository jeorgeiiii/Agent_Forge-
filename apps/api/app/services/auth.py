"""Cluster 1 - Authentication & login (Postgres).

Passwords are hashed with scrypt (stdlib, memory-hard). Session tokens and API
keys are random secrets; only their SHA-256 hashes are stored, so a database
leak does not leak usable tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import ApiKeyRecord, SessionRecord, UserRecord
from .common import Conflict, ServiceError, as_utc, now_utc

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_LEN = 2**14, 8, 1, 64
API_KEY_PREFIX = "af_"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalise_email(email: str) -> str:
    return email.strip().lower()


# ----------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    """One-way scrypt hash: ``scrypt$n$r$p$salt$hash``. The plain password is never stored."""
    if len(password) < 8:
        raise ServiceError("Password must be at least 8 characters.", 422)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_LEN)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, password_hash: str) -> bool:
    """Re-hash the typed password with the stored salt/params and compare in constant time."""
    try:
        scheme, n, r, p, salt, expected = password_hash.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(_unb64(expected))
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, _unb64(expected))


# A real hash used to keep login timing constant when the email does not exist.
_DUMMY_HASH = hash_password("timing-equaliser-password")


# --------------------------------------------------------------------- users


def find_user_by_email(db: Session, email: str) -> UserRecord | None:
    return db.scalar(select(UserRecord).where(func.lower(UserRecord.email) == _normalise_email(email)))


def find_user_by_id(db: Session, user_id: int) -> UserRecord | None:
    return db.get(UserRecord, user_id)


def create_user(db: Session, email: str, password: str, name: str = "") -> UserRecord:
    """Insert a user with a hashed password. Fails if the email already exists.

    Also creates a personal workspace so the user can start building immediately.
    """
    email = _normalise_email(email)
    if "@" not in email or len(email) > 320:
        raise ServiceError("A valid email address is required.", 422)
    if find_user_by_email(db, email):
        raise Conflict("An account with this email already exists.")
    user = UserRecord(email=email, password_hash=hash_password(password), name=name.strip())
    db.add(user)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise Conflict("An account with this email already exists.") from error

    from .authz import create_workspace  # local import avoids a cycle

    create_workspace(db, owner=user, name=f"{name.strip() or email.split('@')[0]}'s workspace", commit=False)
    db.commit()
    db.refresh(user)
    return user


@dataclass
class LoginResult:
    success: bool
    user: UserRecord | None = None
    reason: str = ""


def check_login(db: Session, email: str, password: str) -> LoginResult:
    """Main login check: look the user up, verify the password, return success/fail.

    The failure reason shown to clients is deliberately generic.
    """
    user = find_user_by_email(db, email)
    if user is None:
        verify_password(password, _DUMMY_HASH)  # equalise timing
        return LoginResult(False, reason="Invalid email or password.")
    if not verify_password(password, user.password_hash):
        return LoginResult(False, reason="Invalid email or password.")
    if not user.is_active:
        return LoginResult(False, reason="This account is disabled.")
    return LoginResult(True, user=user)


# ------------------------------------------------------------------ sessions


def create_session(db: Session, user_id: int, user_agent: str = "", ip_address: str = "") -> tuple[str, SessionRecord]:
    """Generate a random session token, store its hash with an expiry, return the raw token."""
    token = secrets.token_urlsafe(32)
    record = SessionRecord(
        user_id=user_id,
        token_hash=_sha256(token),
        expires_at=now_utc() + timedelta(hours=get_settings().session_ttl_hours),
        user_agent=user_agent[:300],
        ip_address=ip_address[:64],
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return token, record


def validate_session(db: Session, token: str | None) -> int | None:
    """Return the user id for a live (existing, unexpired, unrevoked) session token."""
    if not token:
        return None
    record = db.scalar(select(SessionRecord).where(SessionRecord.token_hash == _sha256(token)))
    if record is None or record.revoked_at is not None or as_utc(record.expires_at) <= now_utc():
        return None
    return record.user_id


def revoke_session(db: Session, token: str) -> bool:
    """Logout: mark this session token as dead."""
    result = db.execute(
        update(SessionRecord)
        .where(SessionRecord.token_hash == _sha256(token), SessionRecord.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    db.commit()
    return result.rowcount > 0


def revoke_all_sessions_for_user(db: Session, user_id: int) -> int:
    """Kill every active session (password change / compromised account)."""
    result = db.execute(
        update(SessionRecord)
        .where(SessionRecord.user_id == user_id, SessionRecord.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    db.commit()
    return result.rowcount


def cleanup_expired_sessions(db: Session) -> int:
    """Background job: delete expired or revoked session rows."""
    result = db.execute(
        delete(SessionRecord).where((SessionRecord.expires_at < now_utc()) | SessionRecord.revoked_at.is_not(None))
    )
    db.commit()
    return result.rowcount


# ------------------------------------------------------------------ API keys


def create_api_key(db: Session, user_id: int, label: str = "") -> tuple[str, ApiKeyRecord]:
    """Generate an API key; store only its hash. The raw key is returned once."""
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    record = ApiKeyRecord(user_id=user_id, label=label[:200], prefix=raw[:10], key_hash=_sha256(raw))
    db.add(record)
    db.commit()
    db.refresh(record)
    return raw, record


def validate_api_key(db: Session, raw_key: str | None) -> int | None:
    """Hash the presented key and return the owning user id if the key is active."""
    if not raw_key or not raw_key.startswith(API_KEY_PREFIX):
        return None
    record = db.scalar(select(ApiKeyRecord).where(ApiKeyRecord.key_hash == _sha256(raw_key)))
    if record is None or record.revoked_at is not None:
        return None
    record.last_used_at = now_utc()
    db.commit()
    return record.user_id


def revoke_api_key(db: Session, user_id: int, key_id: int) -> bool:
    record = db.get(ApiKeyRecord, key_id)
    if record is None or record.user_id != user_id:
        return False
    record.revoked_at = record.revoked_at or now_utc()
    db.commit()
    return True


def list_api_keys(db: Session, user_id: int) -> list[ApiKeyRecord]:
    return list(db.scalars(select(ApiKeyRecord).where(ApiKeyRecord.user_id == user_id).order_by(ApiKeyRecord.id)))


def get_user_by_api_key(db: Session, raw_key: str | None) -> UserRecord | None:
    user_id = validate_api_key(db, raw_key)
    user = find_user_by_id(db, user_id) if user_id else None
    return user if user and user.is_active else None

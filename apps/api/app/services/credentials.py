"""Cluster 9 - Credentials & secrets vault.

Secret payloads are JSON-encoded and encrypted with AES-256-GCM. Ciphertexts
carry a key id (``v1:<kid>:<nonce>:<ciphertext>``) so the master key can be
rotated: put the old key in CREDENTIALS_PREVIOUS_KEYS, set the new one as
CREDENTIALS_MASTER_KEY, then call ``rotate_master_key``.
Decrypted values are only ever returned to the worker (internal endpoint),
never to the browser.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import CredentialRecord, OAuthStateRecord
from .authz import require_role
from .common import Forbidden, NotFound, ServiceError, as_utc, now_utc

CREDENTIAL_TYPES = ("api_key", "bearer", "basic", "header", "oauth2", "custom")


# ----------------------------------------------------------- low-level crypto


def _decode_key(raw: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError) as error:
        raise ServiceError("Credentials master key is not valid base64.", 500) from error
    if len(key) != 32:
        raise ServiceError("Credentials master key must be 32 bytes (urlsafe base64).", 500)
    return key


def _keyring() -> tuple[str, dict[str, bytes]]:
    settings = get_settings()
    if not settings.credentials_master_key:
        raise ServiceError(
            "Credential vault is not configured. Set CREDENTIALS_MASTER_KEY (32 random bytes, urlsafe base64).", 503
        )
    keys = {settings.credentials_master_key_id: _decode_key(settings.credentials_master_key)}
    for entry in filter(None, (e.strip() for e in settings.credentials_previous_keys.split(","))):
        kid, _, raw = entry.partition(":")
        keys.setdefault(kid, _decode_key(raw))
    return settings.credentials_master_key_id, keys


def encrypt_value(plaintext: str, *, key: bytes | None = None, key_id: str | None = None) -> str:
    """AES-GCM encrypt with the current master key (or an explicit one)."""
    if key is None:
        key_id, keys = _keyring()
        key = keys[key_id]
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), key_id.encode())
    b64 = lambda b: base64.urlsafe_b64encode(b).decode()  # noqa: E731
    return f"v1:{key_id}:{b64(nonce)}:{b64(ciphertext)}"


def decrypt_value(token: str) -> str:
    try:
        version, key_id, nonce, ciphertext = token.split(":")
    except ValueError as error:
        raise ServiceError("Corrupt encrypted value.", 500) from error
    if version != "v1":
        raise ServiceError("Unsupported encryption version.", 500)
    _, keys = _keyring()
    if key_id not in keys:
        raise ServiceError(f"Encryption key '{key_id}' is not available; add it to CREDENTIALS_PREVIOUS_KEYS.", 500)
    plaintext = AESGCM(keys[key_id]).decrypt(
        base64.urlsafe_b64decode(nonce), base64.urlsafe_b64decode(ciphertext), key_id.encode()
    )
    return plaintext.decode("utf-8")


def rotate_master_key(db: Session, new_key: str | None = None, new_key_id: str | None = None) -> int:
    """Re-encrypt every credential (and pending OAuth state) with the current or a supplied key."""
    if new_key is not None:
        if not new_key_id:
            raise ServiceError("new_key_id is required with new_key.", 422)
        key, key_id = _decode_key(new_key), new_key_id
    else:
        key_id, keys = _keyring()
        key = keys[key_id]
    count = 0
    for record in db.scalars(select(CredentialRecord)):
        record.encrypted_data = encrypt_value(decrypt_value(record.encrypted_data), key=key, key_id=key_id)
        count += 1
    for state in db.scalars(select(OAuthStateRecord)):
        state.encrypted_verifier = encrypt_value(decrypt_value(state.encrypted_verifier), key=key, key_id=key_id)
    db.commit()
    return count


# ------------------------------------------------------------- access checks


def check_access(db: Session, user_id: int | None, workspace_id: int | None, required: str) -> None:
    if workspace_id is None:
        if get_settings().auth_required:
            raise Forbidden("Credentials must belong to a workspace.")
        return
    require_role(db, user_id, workspace_id, required)


def load_credential(db: Session, credential_id: int) -> CredentialRecord:
    record = db.get(CredentialRecord, credential_id)
    if record is None:
        raise NotFound("Credential not found.")
    return record


# ------------------------------------------------------------------- CRUD


def create_credential(
    db: Session, user_id: int | None, *, name: str, type: str, data: dict[str, Any], workspace_id: int | None = None
) -> CredentialRecord:
    """Encrypt the secret payload and store it, linked to a workspace."""
    if type not in CREDENTIAL_TYPES:
        raise ServiceError(f"type must be one of {list(CREDENTIAL_TYPES)}.", 422)
    check_access(db, user_id, workspace_id, "editor")
    record = CredentialRecord(
        workspace_id=workspace_id, name=name.strip()[:200], type=type, encrypted_data=encrypt_value(json.dumps(data))
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_credential(db: Session, credential_id: int) -> dict[str, Any]:
    """Fetch and DECRYPT a credential. Only for the runtime (internal endpoint), never the UI."""
    record = load_credential(db, credential_id)
    return {"id": record.id, "name": record.name, "type": record.type, "data": json.loads(decrypt_value(record.encrypted_data))}


def update_credential(
    db: Session, user_id: int | None, credential_id: int, *, name: str | None = None, data: dict[str, Any] | None = None
) -> CredentialRecord:
    record = load_credential(db, credential_id)
    check_access(db, user_id, record.workspace_id, "editor")
    if name is not None:
        record.name = name.strip()[:200]
    if data is not None:
        record.encrypted_data = encrypt_value(json.dumps(data))
    db.commit()
    db.refresh(record)
    return record


def delete_credential(db: Session, user_id: int | None, credential_id: int) -> None:
    """Remove a credential. Nodes still referencing its id will fail until updated."""
    record = load_credential(db, credential_id)
    check_access(db, user_id, record.workspace_id, "editor")
    db.delete(record)
    db.commit()


def list_credentials_for_workspace(db: Session, user_id: int | None, workspace_id: int | None) -> list[CredentialRecord]:
    """Names/types only - secret values are never returned to the UI."""
    check_access(db, user_id, workspace_id, "viewer")
    condition = (
        CredentialRecord.workspace_id.is_(None) if workspace_id is None else CredentialRecord.workspace_id == workspace_id
    )
    return list(db.scalars(select(CredentialRecord).where(condition).order_by(CredentialRecord.name)))


# ------------------------------------------------------------------- OAuth2

OAUTH_PROVIDERS: dict[str, dict[str, Any]] = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "default_scopes": ["openid", "email"],
        "extra": {"access_type": "offline", "prompt": "consent"},
    },
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "default_scopes": ["read:user"],
        "extra": {},
    },
    "slack": {
        "authorize_url": "https://slack.com/oauth/v2/authorize",
        "token_url": "https://slack.com/api/oauth.v2.access",
        "default_scopes": ["chat:write"],
        "extra": {},
    },
}


def _client(provider: str) -> tuple[dict[str, Any], str, str]:
    if provider not in OAUTH_PROVIDERS:
        raise ServiceError(f"Unknown OAuth provider '{provider}'.", 422)
    settings = get_settings()
    client_id = getattr(settings, f"oauth_{provider}_client_id")
    client_secret = getattr(settings, f"oauth_{provider}_client_secret")
    if not client_id or not client_secret:
        raise ServiceError(f"OAuth for {provider} is not configured (set OAUTH_{provider.upper()}_CLIENT_ID/SECRET).", 503)
    return OAUTH_PROVIDERS[provider], client_id, client_secret


def _redirect_uri() -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/credentials/oauth/callback"


def start_oauth_flow(
    db: Session,
    user_id: int | None,
    *,
    provider: str,
    credential_name: str,
    workspace_id: int | None = None,
    scopes: list[str] | None = None,
) -> str:
    """Build the provider's authorize URL with a random state and a PKCE (S256) challenge."""
    check_access(db, user_id, workspace_id, "editor")
    config, client_id, _ = _client(provider)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    db.add(OAuthStateRecord(
        state=state,
        provider=provider,
        credential_name=credential_name,
        workspace_id=workspace_id,
        user_id=user_id,
        encrypted_verifier=encrypt_value(verifier),
        redirect_uri=_redirect_uri(),
        expires_at=now_utc() + timedelta(minutes=10),
    ))
    db.commit()
    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(scopes or config["default_scopes"]),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **config["extra"],
    }
    return f"{config['authorize_url']}?{urlencode(params)}"


async def _token_request(provider: str, form: dict[str, str]) -> dict[str, Any]:
    config, client_id, client_secret = _client(provider)
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            config["token_url"],
            data={**form, "client_id": client_id, "client_secret": client_secret},
            headers={"Accept": "application/json"},
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or "error" in payload or payload.get("ok") is False:
        raise ServiceError(f"{provider} token exchange failed: {payload.get('error', response.status_code)}", 502)
    return payload


def _token_data(provider: str, payload: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(previous or {})
    data.update({
        "provider": provider,
        "access_token": payload.get("access_token") or (payload.get("authed_user") or {}).get("access_token"),
        "token_type": payload.get("token_type", "bearer"),
        "scope": payload.get("scope", data.get("scope", "")),
    })
    if payload.get("refresh_token"):
        data["refresh_token"] = payload["refresh_token"]
    if payload.get("expires_in"):
        data["expires_at"] = (now_utc() + timedelta(seconds=int(payload["expires_in"]))).isoformat()
    return data


async def handle_oauth_callback(db: Session, *, state: str, code: str) -> CredentialRecord:
    """Exchange the authorization code for tokens and store them as an oauth2 credential."""
    pending = db.scalar(select(OAuthStateRecord).where(OAuthStateRecord.state == state))
    if pending is None or as_utc(pending.expires_at) < now_utc():
        raise ServiceError("OAuth state is invalid or expired. Start the connection again.", 400)
    verifier = decrypt_value(pending.encrypted_verifier)
    payload = await _token_request(pending.provider, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending.redirect_uri,
        "code_verifier": verifier,
    })
    record = CredentialRecord(
        workspace_id=pending.workspace_id,
        name=pending.credential_name,
        type="oauth2",
        encrypted_data=encrypt_value(json.dumps(_token_data(pending.provider, payload))),
    )
    db.add(record)
    db.delete(pending)
    db.commit()
    db.refresh(record)
    return record


async def refresh_oauth_token(db: Session, credential_id: int) -> CredentialRecord:
    """Use the stored refresh token to get a new access token and update the credential."""
    record = load_credential(db, credential_id)
    if record.type != "oauth2":
        raise ServiceError("Only oauth2 credentials can be refreshed.", 422)
    data = json.loads(decrypt_value(record.encrypted_data))
    if not data.get("refresh_token"):
        raise ServiceError("This credential has no refresh token.", 422)
    payload = await _token_request(data["provider"], {"grant_type": "refresh_token", "refresh_token": data["refresh_token"]})
    record.encrypted_data = encrypt_value(json.dumps(_token_data(data["provider"], payload, data)))
    db.commit()
    db.refresh(record)
    return record


def oauth_token_needs_refresh(data: dict[str, Any], leeway_seconds: int = 60) -> bool:
    expires_at = data.get("expires_at")
    if not expires_at or not data.get("refresh_token"):
        return False
    from datetime import datetime

    return datetime.fromisoformat(expires_at) <= now_utc() + timedelta(seconds=leeway_seconds)

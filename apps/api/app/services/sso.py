"""SSO step 1 - company login settings and company email domains.

How it fits together:
  1. An owner adds their company's email domain (acme.com) to the workspace.
  2. They prove they own it by adding a DNS TXT record; `verify_domain` checks it.
  3. They save the company login details (`save_connection`); the secret is encrypted.
  4. At login, `discover_login_method(email)` looks at the email's domain and says
     "use company login" (verified domain + enabled connection) or "use a password".

The login redirect itself (OIDC / SAML) comes in later steps.

STATUS: PAUSED - switched off by SSO_ENABLED=false (default). The code is kept for later; while off,
routes/sso.py answers 404 and `password_login_blocked` never blocks anyone.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import MembershipRecord, SsoConnectionRecord, SsoDomainRecord, WorkspaceRecord
from .auth import find_user_by_email
from .authz import require_role
from .common import Conflict, NotFound, ServiceError, now_utc
from .credentials import encrypt_value

PROTOCOLS = ("oidc", "saml")
ROLES = ("viewer", "editor")  # new SSO users never become owners automatically
CHALLENGE_PREFIX = "_agentforge-challenge"
TXT_PREFIX = "agentforge-verify="

# Shared mailbox providers: nobody can "own" these for a company.
PUBLIC_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "ymail.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de", "mail.com",
    "yandex.com", "yandex.ru", "zoho.com", "qq.com", "163.com", "126.com", "rediffmail.com", "fastmail.com",
})
_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?!-)([a-z0-9-]{1,63}(?<!-)\.)+[a-z]{2,63}$")
# Settings an owner may store per protocol (anything else is rejected, keeps the JSON tidy).
ALLOWED_SETTINGS = {
    "oidc": {"authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint", "scopes"},
    "saml": {"sso_url", "certificate", "name_id_format"},
}


# --------------------------------------------------------------------- helpers


def normalize_domain(value: str) -> str:
    """'  @ACME.com. ' -> 'acme.com'. Raises a readable error for anything that is not a company domain."""
    domain = (value or "").strip().lower().lstrip("@").rstrip(".")
    if "@" in domain:
        domain = domain.rsplit("@", 1)[1]
    try:
        domain = domain.encode("idna").decode("ascii")  # bücher.de -> xn--bcher-kva.de
    except UnicodeError as error:
        raise ServiceError("That doesn't look like a valid domain.", 422) from error
    if not _DOMAIN_RE.match(domain):
        raise ServiceError("That doesn't look like a valid domain, e.g. acme.com.", 422)
    if domain in PUBLIC_EMAIL_DOMAINS:
        raise ServiceError(f"{domain} is a public email provider and can't be used for company login.", 422)
    return domain


def email_domain(email: str) -> str | None:
    if "@" not in (email or ""):
        return None
    return email.strip().lower().rsplit("@", 1)[1].rstrip(".") or None


def txt_record_for(record: SsoDomainRecord) -> dict[str, str]:
    """What the owner must add to their DNS."""
    return {"type": "TXT", "name": f"{CHALLENGE_PREFIX}.{record.domain}", "value": f"{TXT_PREFIX}{record.verification_token}"}


def domain_to_dict(record: SsoDomainRecord) -> dict[str, Any]:
    return {"id": record.id, "domain": record.domain, "verified": record.verified_at is not None,
            "verified_at": record.verified_at, "last_checked_at": record.last_checked_at,
            "created_at": record.created_at, "dns_record": txt_record_for(record)}


def connection_to_dict(record: SsoConnectionRecord | None) -> dict[str, Any] | None:
    """Safe view of a connection: the client secret is reported only as present / absent."""
    if record is None:
        return None
    return {"id": record.id, "protocol": record.protocol, "display_name": record.display_name, "issuer": record.issuer,
            "client_id": record.client_id, "has_client_secret": bool(record.encrypted_client_secret),
            "settings": record.settings or {}, "enabled": record.enabled, "enforce_sso": record.enforce_sso,
            "allow_jit": record.allow_jit, "default_role": record.default_role,
            "created_at": record.created_at, "updated_at": record.updated_at}


def _connection(db: Session, workspace_id: int) -> SsoConnectionRecord | None:
    return db.scalar(select(SsoConnectionRecord).where(SsoConnectionRecord.workspace_id == workspace_id))


def _domains(db: Session, workspace_id: int) -> list[SsoDomainRecord]:
    return list(db.scalars(select(SsoDomainRecord).where(SsoDomainRecord.workspace_id == workspace_id)
                           .order_by(SsoDomainRecord.domain)))


def _has_verified_domain(db: Session, workspace_id: int) -> bool:
    return any(d.verified_at for d in _domains(db, workspace_id))


# --------------------------------------------------------------------- read


def get_sso_settings(db: Session, user_id: int | None, workspace_id: int) -> dict[str, Any]:
    """Owners: the connection (without its secret) and every claimed domain with its DNS instructions."""
    require_role(db, user_id, workspace_id, "owner")
    return {"workspace_id": workspace_id, "connection": connection_to_dict(_connection(db, workspace_id)),
            "domains": [domain_to_dict(d) for d in _domains(db, workspace_id)]}


# --------------------------------------------------------------------- connection


def save_connection(db: Session, user_id: int | None, workspace_id: int, data: dict[str, Any]) -> SsoConnectionRecord:
    """Owners create or update the company login. Omit `client_secret` to keep the stored one."""
    require_role(db, user_id, workspace_id, "owner")
    record = _connection(db, workspace_id)
    protocol = str(data.get("protocol") or (record.protocol if record else "oidc")).lower()
    if protocol not in PROTOCOLS:
        raise ServiceError(f"protocol must be one of {list(PROTOCOLS)}.", 422)
    if record is None:
        record = SsoConnectionRecord(workspace_id=workspace_id, protocol=protocol, display_name="Company login")
        db.add(record)
    record.protocol = protocol

    if "display_name" in data:
        name = str(data["display_name"] or "").strip()
        if not 1 <= len(name) <= 100:
            raise ServiceError("display_name must be 1-100 characters, e.g. \"Acme Okta\".", 422)
        record.display_name = name
    if "issuer" in data:
        issuer = str(data["issuer"] or "").strip()
        if issuer and protocol == "oidc" and not issuer.startswith("https://"):
            raise ServiceError("The OIDC issuer must start with https://", 422)
        record.issuer = issuer[:500]
    if "client_id" in data:
        record.client_id = str(data["client_id"] or "").strip()[:300]
    if data.get("client_secret"):
        record.encrypted_client_secret = encrypt_value(str(data["client_secret"]))
    if "settings" in data:
        settings = dict(data["settings"] or {})
        unknown = set(settings) - ALLOWED_SETTINGS[protocol]
        if unknown:
            raise ServiceError(f"Unknown {protocol} settings: {sorted(unknown)}.", 422)
        record.settings = settings or None
    if "default_role" in data:
        if data["default_role"] not in ROLES:
            raise ServiceError(f"default_role must be one of {list(ROLES)}.", 422)
        record.default_role = data["default_role"]
    if "allow_jit" in data:
        record.allow_jit = bool(data["allow_jit"])
    if "enabled" in data:
        record.enabled = bool(data["enabled"])
    if "enforce_sso" in data:
        record.enforce_sso = bool(data["enforce_sso"])

    # Turning things on needs the basics in place.
    if record.enabled and (not record.issuer or (protocol == "oidc" and not record.client_id)):
        raise ServiceError("Fill in the issuer and client ID before turning company login on.", 422)
    if record.enforce_sso and not (record.enabled and _has_verified_domain(db, workspace_id)):
        raise ServiceError("To require company login, turn it on and verify at least one domain first.", 422)
    db.commit()
    db.refresh(record)
    return record


def delete_connection(db: Session, user_id: int | None, workspace_id: int) -> None:
    require_role(db, user_id, workspace_id, "owner")
    record = _connection(db, workspace_id)
    if record is None:
        raise NotFound("This workspace has no company login.")
    db.delete(record)
    db.commit()


# --------------------------------------------------------------------- domains


def add_domain(db: Session, user_id: int | None, workspace_id: int, value: str) -> SsoDomainRecord:
    """Owners claim a domain. It stays unverified (and unused) until the DNS check passes."""
    require_role(db, user_id, workspace_id, "owner")
    domain = normalize_domain(value)
    if db.scalar(select(SsoDomainRecord).where(SsoDomainRecord.workspace_id == workspace_id,
                                               SsoDomainRecord.domain == domain)):
        raise Conflict(f"{domain} is already added to this workspace.")
    if _verified_elsewhere(db, domain, workspace_id):
        raise Conflict(f"{domain} is already verified by another workspace.")
    record = SsoDomainRecord(workspace_id=workspace_id, domain=domain, verification_token=secrets.token_urlsafe(24))
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _domain(db: Session, workspace_id: int, domain_id: int) -> SsoDomainRecord:
    record = db.get(SsoDomainRecord, domain_id)
    if record is None or record.workspace_id != workspace_id:
        raise NotFound("Domain not found.")
    return record


def _verified_elsewhere(db: Session, domain: str, workspace_id: int) -> bool:
    return db.scalar(select(SsoDomainRecord.id).where(
        SsoDomainRecord.domain == domain, SsoDomainRecord.workspace_id != workspace_id,
        SsoDomainRecord.verified_at.is_not(None))) is not None


async def lookup_txt(name: str) -> list[str]:
    """TXT records for `name`, via DNS-over-HTTPS (JSON API). Returns [] when there are none."""
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(get_settings().sso_dns_resolver_url, params={"name": name, "type": "TXT"},
                                    headers={"accept": "application/dns-json"})
    response.raise_for_status()
    answers = response.json().get("Answer") or []
    # TXT data arrives quoted and may be split into several "chunks"
    return ["".join(re.findall(r'"([^"]*)"', a.get("data", ""))) or a.get("data", "") for a in answers if a.get("type") == 16]


async def verify_domain(db: Session, user_id: int | None, workspace_id: int, domain_id: int) -> SsoDomainRecord:
    """Owners: check the DNS TXT record now. Marks the domain verified when the expected value is found."""
    require_role(db, user_id, workspace_id, "owner")
    record = _domain(db, workspace_id, domain_id)
    if record.verified_at:
        return record
    if _verified_elsewhere(db, record.domain, workspace_id):
        raise Conflict(f"{record.domain} is already verified by another workspace.")
    expected = txt_record_for(record)
    name, value, domain = expected["name"], expected["value"], record.domain
    db.commit()  # release the connection while we wait on the network
    try:
        values = await lookup_txt(name)
    except (httpx.HTTPError, ValueError) as error:
        raise ServiceError(f"Couldn't check DNS right now ({type(error).__name__}). Try again in a minute.", 503) from error
    record = _domain(db, workspace_id, domain_id)
    record.last_checked_at = now_utc()
    if value in values:
        record.verified_at = now_utc()
    db.commit()
    db.refresh(record)
    if record.verified_at is None:
        raise ServiceError(f"The TXT record for {domain} wasn't found yet. DNS changes can take up to an hour - "
                           f"add {name} = {value} and try again.", 409)
    return record


def remove_domain(db: Session, user_id: int | None, workspace_id: int, domain_id: int) -> SsoDomainRecord:
    """Owners remove a domain. Refused if it would leave 'require company login' on with no verified domain."""
    require_role(db, user_id, workspace_id, "owner")
    record = _domain(db, workspace_id, domain_id)
    connection = _connection(db, workspace_id)
    others_verified = any(d.verified_at for d in _domains(db, workspace_id) if d.id != record.id)
    if connection and connection.enforce_sso and record.verified_at and not others_verified:
        raise ServiceError("Turn off \"require company login\" before removing the last verified domain.", 409)
    db.delete(record)
    db.commit()
    return record


# --------------------------------------------------------------------- login discovery


def discover_login_method(db: Session, email: str) -> dict[str, Any]:
    """Public: which way should this email log in? Never reveals the workspace, only the button label.

    Returns {"method": "password"} or {"method": "sso", "required": bool, "connection_id": id, "display_name": str}.
    """
    domain = email_domain(email)
    if not domain or domain in PUBLIC_EMAIL_DOMAINS:
        return {"method": "password"}
    row = db.execute(
        select(SsoConnectionRecord)
        .join(SsoDomainRecord, SsoDomainRecord.workspace_id == SsoConnectionRecord.workspace_id)
        .join(WorkspaceRecord, WorkspaceRecord.id == SsoConnectionRecord.workspace_id)
        .where(SsoDomainRecord.domain == domain, SsoDomainRecord.verified_at.is_not(None),
               SsoConnectionRecord.enabled.is_(True), WorkspaceRecord.deleted_at.is_(None))
    ).scalars().first()
    if row is None:
        return {"method": "password"}
    return {"method": "sso", "required": row.enforce_sso, "connection_id": row.id, "display_name": row.display_name}


def password_login_blocked(db: Session, email: str) -> str | None:
    """SSO step 2: returns the company login name if this email must use SSO instead of a password, else None.

    Always None while SSO is switched off (SSO_ENABLED=false), so passwords work for everyone.

    Break-glass: owners of the workspace that set up SSO may still use their password, so a broken
    company login can never lock the people who fix it out of their own workspace.
    """
    if not get_settings().sso_enabled:
        return None
    method = discover_login_method(db, email)
    if method.get("method") != "sso" or not method.get("required"):
        return None
    connection = db.get(SsoConnectionRecord, method["connection_id"])
    user = find_user_by_email(db, email)
    if connection is not None and user is not None:
        owner = db.execute(select(MembershipRecord).where(
            MembershipRecord.workspace_id == connection.workspace_id, MembershipRecord.user_id == user.id,
            MembershipRecord.role == "owner")).scalars().first()
        if owner is not None:
            return None
    return method["display_name"]

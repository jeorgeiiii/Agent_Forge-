"""Production safety checks: refuse to start with settings that are only OK on a laptop.

APP_ENV=production turns these on. At startup `check_production_settings()`:
  - collects BLOCKERS (unsafe: no login required, placeholder secrets, SQLite, http links, ...) and
    raises one error listing all of them, so the API never runs half-secured;
  - logs WARNINGS (works, but you probably want to change it: console email, local file storage, ...).

APP_ENV=development (the default) skips the blockers, so local work keeps running as before.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..config import Settings

log = logging.getLogger("agentforge.production")

PLACEHOLDER_SECRETS = {"", "dev-internal-token-change-me", "change-me", "changeme", "secret", "test"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


@dataclass
class Report:
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _is_local_or_plain_http(url: str) -> bool:
    """True for http:// links and links to this machine - they don't work for real users."""
    parsed = urlparse(url.strip())
    return parsed.scheme != "https" or (parsed.hostname or "") in LOCAL_HOSTS


def review_settings(s: Settings) -> Report:
    """Checks the settings without side effects (easy to test). Messages name the env var to change."""
    r = Report()
    # --- who can get in
    if not s.auth_required:
        r.blockers.append("AUTH_REQUIRED must be true (otherwise some requests work without signing in).")
    if not s.session_cookie_secure:
        r.blockers.append("SESSION_COOKIE_SECURE must be true (login cookies must only travel over HTTPS).")
    # --- secrets
    if s.internal_api_token.strip().lower() in PLACEHOLDER_SECRETS or len(s.internal_api_token) < 32:
        r.blockers.append("INTERNAL_API_TOKEN must be a random value of at least 32 characters "
                          "(python -c \"import secrets;print(secrets.token_urlsafe(48))\").")
    if not s.credentials_master_key:
        r.blockers.append("CREDENTIALS_MASTER_KEY is empty: saved keys and company-login secrets can't be stored.")
    if s.file_signing_key and len(s.file_signing_key) < 32:
        r.blockers.append("FILE_SIGNING_KEY is too short (use at least 32 random characters, or leave it empty).")
    # --- data
    if s.database_url.startswith("sqlite"):
        r.blockers.append("DATABASE_URL points to SQLite; use PostgreSQL in production (backups, many users at once).")
    # --- links users and browsers see
    origins = s.cors_origin_list
    if not origins or "*" in origins:
        r.blockers.append("CORS_ORIGINS must list your web app's address(es), not '*'.")
    elif any(_is_local_or_plain_http(o) for o in origins):
        r.blockers.append(f"CORS_ORIGINS has a local or http:// address: {', '.join(origins)}.")
    for name, value in (("PUBLIC_APP_URL", s.public_app_url), ("PUBLIC_BASE_URL", s.public_base_url)):
        if _is_local_or_plain_http(value):
            r.blockers.append(f"{name} must be your public https:// address (now: {value}).")
    # --- worth a look, not fatal
    if s.mail_backend == "console":
        r.warnings.append("MAIL_BACKEND=console: invite emails are only written to the log. Set up SMTP.")
    if s.storage_backend == "local":
        r.warnings.append("STORAGE_BACKEND=local: uploaded files live on this server's disk. "
                          "Use s3 if you run more than one API server.")
    if not s.rate_limit_fail_closed:
        r.warnings.append("RATE_LIMIT_FAIL_CLOSED=false: if Redis goes down, login rate limits fall back "
                          "to per-server memory.")
    if s.log_format != "json":
        r.warnings.append("LOG_FORMAT=text: use json so your log service can search the fields.")
    return r


def check_production_settings(s: Settings) -> Report:
    """Called at startup. In production, raises RuntimeError listing every blocker."""
    if s.app_env != "production":
        return Report()
    report = review_settings(s)
    for warning in report.warnings:
        log.warning("Production check: %s", warning)
    if report.blockers:
        lines = "\n".join(f"  - {b}" for b in report.blockers)
        raise RuntimeError(f"APP_ENV=production but these settings are unsafe:\n{lines}\n"
                           "Fix them (or set APP_ENV=development for local work).")
    log.info("Production checks passed.")
    return report

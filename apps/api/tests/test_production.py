"""Production readiness: startup safety checks, security headers, health/readiness probes."""

import pytest

from app.config import Settings
from app.services.production import check_production_settings, review_settings

SAFE = dict(
    app_env="production", auth_required=True, session_cookie_secure=True,
    internal_api_token="x" * 48, credentials_master_key="k" * 44,
    database_url="postgresql+psycopg://u:p@db:5432/agentforge",
    cors_origins="https://app.example.com", public_app_url="https://app.example.com",
    public_base_url="https://api.example.com", mail_backend="smtp", storage_backend="s3",
    rate_limit_fail_closed=True, log_format="json",
)


def _settings(**overrides):
    return Settings(_env_file=None, **{**SAFE, **overrides})


def test_safe_production_settings_pass_without_warnings():
    report = check_production_settings(_settings())
    assert report.blockers == [] and report.warnings == []


@pytest.mark.parametrize("override, env_var", [
    ({"auth_required": False}, "AUTH_REQUIRED"),
    ({"session_cookie_secure": False}, "SESSION_COOKIE_SECURE"),
    ({"internal_api_token": "dev-internal-token-change-me"}, "INTERNAL_API_TOKEN"),
    ({"internal_api_token": "short"}, "INTERNAL_API_TOKEN"),
    ({"credentials_master_key": ""}, "CREDENTIALS_MASTER_KEY"),
    ({"file_signing_key": "tiny"}, "FILE_SIGNING_KEY"),
    ({"database_url": "sqlite:///./agentforge.db"}, "DATABASE_URL"),
    ({"cors_origins": "*"}, "CORS_ORIGINS"),
    ({"cors_origins": "https://app.example.com,http://localhost:3000"}, "CORS_ORIGINS"),
    ({"public_app_url": "http://app.example.com"}, "PUBLIC_APP_URL"),
    ({"public_base_url": "https://localhost:8000"}, "PUBLIC_BASE_URL"),
])
def test_each_unsafe_setting_blocks_startup(override, env_var):
    with pytest.raises(RuntimeError) as error:
        check_production_settings(_settings(**override))
    assert env_var in str(error.value)


def test_all_blockers_are_listed_at_once_and_warnings_are_soft():
    report = review_settings(_settings(auth_required=False, database_url="sqlite://", mail_backend="console",
                                       storage_backend="local", rate_limit_fail_closed=False, log_format="text"))
    assert len(report.blockers) == 2
    assert {w.split("=")[0] for w in report.warnings} == {"MAIL_BACKEND", "STORAGE_BACKEND",
                                                          "RATE_LIMIT_FAIL_CLOSED", "LOG_FORMAT"}


def test_development_never_blocks():
    assert check_production_settings(_settings(app_env="development", auth_required=False,
                                               database_url="sqlite://")).blockers == []


def test_security_headers_and_probes(client):
    response = client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in response.headers and "X-Request-ID" in response.headers
    assert "Strict-Transport-Security" not in response.headers  # only in production

    ready = client.get("/health/ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["checks"]["database"] == "ok"


def test_readiness_fails_when_database_is_down(client, monkeypatch):
    import app.main as main

    class Broken:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(main, "SessionLocal", lambda: Broken())
    ready = client.get("/health/ready")
    assert ready.status_code == 503 and ready.json()["checks"]["database"] == "unavailable"


def test_route_specific_headers_are_not_overwritten(client):
    """files.py sends CSP 'sandbox' on downloads; the global defaults must not replace route values."""
    from fastapi import Response

    import app.main as main

    @main.app.get("/__header_probe")
    def probe():
        return Response("x", headers={"X-Frame-Options": "SAMEORIGIN"})

    assert client.get("/__header_probe").headers["X-Frame-Options"] == "SAMEORIGIN"

"""'Sign in with Google' for integrations: scopes come from the integration, the popup page is safe."""

from urllib.parse import parse_qs, urlparse

from app.config import get_settings
from app.services import credentials as svc
from tests.test_api import _headers, _signup


def _google_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "oauth_google_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "oauth_google_client_secret", "test-secret")


def test_start_uses_the_integrations_own_scopes(client, monkeypatch):
    _google_configured(monkeypatch)
    token = _signup(client, "sheets-owner@oauth.test")
    ws = client.get("/workspaces", headers=_headers(token)).json()[0]["id"]
    started = client.post("/credentials/oauth/start", headers=_headers(token), json={
        "connector": "google_sheets", "credential_name": "Google Sheets - main", "workspace_id": ws,
        "scopes": ["https://www.googleapis.com/auth/drive"]})  # ignored: the integration decides
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["authorize_url"]).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/spreadsheets"]
    assert query["access_type"] == ["offline"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"][0].endswith("/credentials/oauth/callback")

    assert client.post("/credentials/oauth/start", headers=_headers(token), json={
        "connector": "slack", "credential_name": "x", "workspace_id": ws}).status_code == 422  # pasted-key service
    assert client.post("/credentials/oauth/start", headers=_headers(token), json={
        "connector": "nope", "credential_name": "x", "workspace_id": ws}).status_code == 404


def test_callback_cancel_and_bad_state_show_a_friendly_page(client):
    cancelled = client.get("/credentials/oauth/callback?error=access_denied&state=abc")
    assert cancelled.status_code == 400 and "You cancelled the sign-in." in cancelled.text
    expired = client.get("/credentials/oauth/callback?state=nope&code=xyz")
    assert expired.status_code == 400 and "invalid or expired" in expired.text


def test_callback_page_escapes_names_and_only_messages_the_app(client, monkeypatch):
    class Record:
        id, name, type, workspace_id = 1, "<script>alert(1)</script>", "oauth2", None

    async def fake_callback(db, *, state, code):
        return Record()

    monkeypatch.setattr(svc, "handle_oauth_callback", fake_callback)
    page = client.get("/credentials/oauth/callback?state=s&code=c")
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text and "&lt;script&gt;" in page.text
    assert f'"{get_settings().public_app_url.rstrip("/")}"' in page.text and "'*'" not in page.text

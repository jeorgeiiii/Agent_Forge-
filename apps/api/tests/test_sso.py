"""SSO step 1: company login settings, domain claims + DNS verification, login discovery."""

import httpx
import pytest

from app.db import SessionLocal
from app.models import AuditEventRecord, SsoConnectionRecord
from app.services import sso
from app.services.credentials import decrypt_value
from tests.test_api import _headers, _signup


def _ws(client, token):
    return client.get("/workspaces", headers=_headers(token)).json()[0]["id"]


@pytest.fixture(autouse=True)
def sso_on(monkeypatch):
    """SSO is paused by default (SSO_ENABLED=false); these tests switch it on."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "sso_enabled", True)


@pytest.fixture
def dns(monkeypatch):
    """Fake DNS: tests put TXT values into this dict, keyed by record name."""
    records: dict[str, list[str]] = {}

    async def fake_lookup(name):
        if records.get("__fail__"):
            raise httpx.ConnectError("dns down")
        return records.get(name, [])

    monkeypatch.setattr(sso, "lookup_txt", fake_lookup)
    return records


def _add_and_verify(client, token, ws, domain, dns):
    added = client.post(f"/workspaces/{ws}/sso/domains", json={"domain": domain}, headers=_headers(token))
    assert added.status_code == 201, added.text
    record = added.json()["dns_record"]
    dns[record["name"]] = ["some-other-record", record["value"]]
    verified = client.post(f"/workspaces/{ws}/sso/domains/{added.json()['id']}/verify", headers=_headers(token))
    assert verified.status_code == 200, verified.text
    return verified.json()


def test_normalize_domain_rules():
    assert sso.normalize_domain("  @ACME.com. ") == "acme.com"
    assert sso.normalize_domain("priya@Sub.Acme.co.uk") == "sub.acme.co.uk"
    assert sso.normalize_domain("bücher.de") == "xn--bcher-kva.de"
    for bad in ["", "acme", "-acme.com", "acme..com", "http://acme.com", "gmail.com", "Outlook.com"]:
        with pytest.raises(sso.ServiceError):
            sso.normalize_domain(bad)


def test_domain_claim_verify_and_dns_instructions(client, dns):
    owner = _signup(client, "sso-owner@acme-sso.test")
    ws = _ws(client, owner)
    added = client.post(f"/workspaces/{ws}/sso/domains", json={"domain": "Acme-SSO.test"}, headers=_headers(owner))
    assert added.status_code == 201, added.text
    body = added.json()
    assert body["domain"] == "acme-sso.test" and body["verified"] is False
    assert body["dns_record"]["name"] == "_agentforge-challenge.acme-sso.test"
    assert body["dns_record"]["value"].startswith("agentforge-verify=")

    # no record yet -> clear 409 with the instructions; last_checked_at recorded
    missing = client.post(f"/workspaces/{ws}/sso/domains/{body['id']}/verify", headers=_headers(owner))
    assert missing.status_code == 409 and "_agentforge-challenge.acme-sso.test" in missing.json()["detail"]
    # DNS provider down -> 503, nothing verified
    dns["__fail__"] = ["1"]
    assert client.post(f"/workspaces/{ws}/sso/domains/{body['id']}/verify", headers=_headers(owner)).status_code == 503
    del dns["__fail__"]
    # a wrong value doesn't count
    dns[body["dns_record"]["name"]] = ["agentforge-verify=not-the-token"]
    assert client.post(f"/workspaces/{ws}/sso/domains/{body['id']}/verify", headers=_headers(owner)).status_code == 409
    # the right value verifies
    dns[body["dns_record"]["name"]] = [body["dns_record"]["value"]]
    ok = client.post(f"/workspaces/{ws}/sso/domains/{body['id']}/verify", headers=_headers(owner))
    assert ok.status_code == 200 and ok.json()["verified"] is True and ok.json()["last_checked_at"]

    # duplicates and public providers are refused
    assert client.post(f"/workspaces/{ws}/sso/domains", json={"domain": "acme-sso.test"}, headers=_headers(owner)).status_code == 409
    assert client.post(f"/workspaces/{ws}/sso/domains", json={"domain": "gmail.com"}, headers=_headers(owner)).status_code == 422

    # another workspace can't claim a domain that is already verified elsewhere
    rival = _signup(client, "rival@rival-sso.test")
    rival_ws = _ws(client, rival)
    taken = client.post(f"/workspaces/{rival_ws}/sso/domains", json={"domain": "acme-sso.test"}, headers=_headers(rival))
    assert taken.status_code == 409 and "another workspace" in taken.json()["detail"]


def test_only_owners_manage_sso(client, dns):
    owner = _signup(client, "boss@owners-sso.test")
    ws = _ws(client, owner)
    member = _signup(client, "staff@owners-sso.test")
    member_id = client.get("/auth/me", headers=_headers(member)).json()["id"]
    assert client.post(f"/workspaces/{ws}/members", json={"user_id": member_id, "role": "editor"},
                       headers=_headers(owner)).status_code == 201
    for method, path, body in [
        ("get", f"/workspaces/{ws}/sso", None),
        ("put", f"/workspaces/{ws}/sso", {"display_name": "x"}),
        ("post", f"/workspaces/{ws}/sso/domains", {"domain": "owners-sso.test"}),
    ]:
        response = client.request(method.upper(), path, json=body, headers=_headers(member))
        assert response.status_code == 403, (path, response.text)


def test_connection_save_secret_hidden_and_rules(client, dns):
    owner = _signup(client, "it@globex-sso.test")
    ws = _ws(client, owner)
    assert client.get(f"/workspaces/{ws}/sso", headers=_headers(owner)).json() == {"workspace_id": ws, "connection": None, "domains": []}

    # validation: bad protocol, http issuer, unknown setting, owner as default role
    for bad in [{"protocol": "ldap"}, {"issuer": "http://idp.test"}, {"settings": {"evil": 1}}, {"default_role": "owner"}]:
        assert client.put(f"/workspaces/{ws}/sso", json=bad, headers=_headers(owner)).status_code == 422, bad
    # can't turn on without issuer + client id
    assert client.put(f"/workspaces/{ws}/sso", json={"enabled": True}, headers=_headers(owner)).status_code == 422

    saved = client.put(f"/workspaces/{ws}/sso", json={
        "protocol": "oidc", "display_name": "Globex Okta", "issuer": "https://globex.okta.test",
        "client_id": "abc123", "client_secret": "super-secret-value", "settings": {"scopes": "openid email profile"},
        "default_role": "editor", "enabled": True}, headers=_headers(owner))
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert data["has_client_secret"] is True and data["enabled"] is True and data["default_role"] == "editor"
    assert "super-secret-value" not in saved.text and "client_secret" not in data
    with SessionLocal() as db:
        record = db.query(SsoConnectionRecord).filter_by(workspace_id=ws).one()
        assert record.encrypted_client_secret != "super-secret-value"
        assert decrypt_value(record.encrypted_client_secret) == "super-secret-value"
        events = db.query(AuditEventRecord).filter_by(workspace_id=ws, action="sso.update").all()
        assert events and all("super-secret-value" not in str(e.details) for e in events)

    # updating without a secret keeps the old one
    kept = client.put(f"/workspaces/{ws}/sso", json={"display_name": "Globex SSO"}, headers=_headers(owner)).json()
    assert kept["has_client_secret"] is True and kept["display_name"] == "Globex SSO"

    # "require company login" needs a verified domain
    refused = client.put(f"/workspaces/{ws}/sso", json={"enforce_sso": True}, headers=_headers(owner))
    assert refused.status_code == 422 and "verify" in refused.json()["detail"]
    domain = _add_and_verify(client, owner, ws, "globex-sso.test", dns)
    assert client.put(f"/workspaces/{ws}/sso", json={"enforce_sso": True}, headers=_headers(owner)).json()["enforce_sso"] is True
    # ...and the last verified domain can't be removed while it is required
    blocked = client.delete(f"/workspaces/{ws}/sso/domains/{domain['id']}", headers=_headers(owner))
    assert blocked.status_code == 409
    client.put(f"/workspaces/{ws}/sso", json={"enforce_sso": False}, headers=_headers(owner))
    assert client.delete(f"/workspaces/{ws}/sso/domains/{domain['id']}", headers=_headers(owner)).status_code == 204

    assert client.delete(f"/workspaces/{ws}/sso", headers=_headers(owner)).status_code == 204
    assert client.get(f"/workspaces/{ws}/sso", headers=_headers(owner)).json()["connection"] is None


def test_login_discovery(client, dns):
    owner = _signup(client, "admin@initech-sso.test")
    ws = _ws(client, owner)

    def discover(email):
        return client.post("/auth/sso/discover", json={"email": email}, headers=_headers()).json()

    # nothing set up -> password; public providers and junk -> password
    assert discover("peter@initech-sso.test") == {"method": "password"}
    assert discover("someone@gmail.com") == {"method": "password"}
    assert discover("not-an-email") == {"method": "password"}

    client.put(f"/workspaces/{ws}/sso", json={"display_name": "Initech Login", "issuer": "https://login.initech.test",
                                               "client_id": "cid", "enabled": True}, headers=_headers(owner))
    # connection on but domain not verified -> still password (an unverified claim must never redirect anyone)
    client.post(f"/workspaces/{ws}/sso/domains", json={"domain": "initech-sso.test"}, headers=_headers(owner))
    assert discover("peter@initech-sso.test") == {"method": "password"}

    domain_id = client.get(f"/workspaces/{ws}/sso", headers=_headers(owner)).json()["domains"][0]["id"]
    record = client.get(f"/workspaces/{ws}/sso", headers=_headers(owner)).json()["domains"][0]["dns_record"]
    dns[record["name"]] = [record["value"]]
    client.post(f"/workspaces/{ws}/sso/domains/{domain_id}/verify", headers=_headers(owner))

    found = discover("Peter@INITECH-SSO.test")
    assert found["method"] == "sso" and found["display_name"] == "Initech Login" and found["required"] is False
    assert "workspace" not in str(found).lower()  # never reveals which workspace
    assert discover("peter@other.initech-sso.test") == {"method": "password"}  # subdomains need their own claim

    client.put(f"/workspaces/{ws}/sso", json={"enforce_sso": True}, headers=_headers(owner))
    assert discover("peter@initech-sso.test")["required"] is True
    client.put(f"/workspaces/{ws}/sso", json={"enforce_sso": False, "enabled": False}, headers=_headers(owner))
    assert discover("peter@initech-sso.test") == {"method": "password"}  # turned off -> back to password


def test_txt_lookup_parses_doh_json(monkeypatch):
    """The real resolver: Cloudflare/Google JSON answers, quoted + chunked TXT data, non-TXT answers ignored."""
    payload = {"Answer": [
        {"name": "_agentforge-challenge.acme.test.", "type": 16, "data": "\"agentforge-verify=abc\""},
        {"name": "_agentforge-challenge.acme.test.", "type": 16, "data": "\"part-one\" \"part-two\""},
        {"name": "x", "type": 5, "data": "cname.example."},
    ]}

    def handler(request):
        assert request.url.params["type"] == "TXT" and request.headers["accept"] == "application/dns-json"
        return httpx.Response(200, json=payload)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    import asyncio
    assert asyncio.run(sso.lookup_txt("_agentforge-challenge.acme.test")) == ["agentforge-verify=abc", "part-onepart-two"]


def test_required_company_login_blocks_passwords_except_owners(client, dns):
    """SSO step 2: with "require company login" on, staff can't use passwords; owners keep a break-glass login."""
    owner = _signup(client, "root@umbrella-sso.test")
    ws = _ws(client, owner)
    _signup(client, "alice@umbrella-sso.test")  # signed up before SSO was required
    client.put(f"/workspaces/{ws}/sso", json={"display_name": "Umbrella Okta", "issuer": "https://umbrella.okta.test",
                                               "client_id": "cid", "enabled": True}, headers=_headers(owner))
    _add_and_verify(client, owner, ws, "umbrella-sso.test", dns)

    def password_login(email, password="correct-horse"):
        response = client.post("/auth/login", json={"email": email, "password": password}, headers=_headers())
        client.cookies.clear()  # keep using explicit bearer tokens
        return response

    # SSO on but optional: passwords still fine
    assert password_login("alice@umbrella-sso.test").status_code == 200

    enforced = client.put(f"/workspaces/{ws}/sso", json={"enforce_sso": True}, headers=_headers(owner))
    assert enforced.status_code == 200, enforced.text
    blocked = password_login("Alice@Umbrella-SSO.test")
    assert blocked.status_code == 403 and "Umbrella Okta" in blocked.json()["detail"]
    # new password accounts on that domain are refused too
    signup = client.post("/auth/signup", json={"email": "bob@umbrella-sso.test", "password": "correct-horse-1"},
                         headers=_headers())
    assert signup.status_code == 403
    # the workspace owner can still sign in with a password (break-glass)
    assert password_login("root@umbrella-sso.test").status_code == 200
    # other domains are unaffected
    assert client.post("/auth/signup", json={"email": "carol@elsewhere-sso.test", "password": "correct-horse-1"},
                       headers=_headers()).status_code == 201


def test_sso_switched_off_means_plain_passwords(client, dns, monkeypatch):
    """SSO_ENABLED=false (the default): all SSO endpoints 404, and a "required" company login blocks no one."""
    from app.config import get_settings

    owner = _signup(client, "boss@paused-sso.test")
    ws = _ws(client, owner)
    _signup(client, "worker@paused-sso.test")
    client.put(f"/workspaces/{ws}/sso", json={"display_name": "Paused Okta", "issuer": "https://paused.okta.test",
                                               "client_id": "cid", "enabled": True}, headers=_headers(owner))
    _add_and_verify(client, owner, ws, "paused-sso.test", dns)
    client.put(f"/workspaces/{ws}/sso", json={"enforce_sso": True}, headers=_headers(owner))

    monkeypatch.setattr(get_settings(), "sso_enabled", False)
    assert client.get(f"/workspaces/{ws}/sso", headers=_headers(owner)).status_code == 404
    assert client.post("/auth/sso/discover", json={"email": "worker@paused-sso.test"}).status_code == 404
    login = client.post("/auth/login", json={"email": "worker@paused-sso.test", "password": "correct-horse"})
    client.cookies.clear()
    assert login.status_code == 200
    assert client.post("/auth/signup", json={"email": "new@paused-sso.test", "password": "correct-horse"}).status_code == 201
    client.cookies.clear()

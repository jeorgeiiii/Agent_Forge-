"""Cluster 16: invitations, workspace settings, ownership transfer, delete/restore."""

import asyncio
from datetime import timedelta

from app.db import SessionLocal
from app.models import InvitationRecord, WorkspaceRecord
from app.services.common import now_utc
from app.services.mailer import OUTBOX
from tests.test_api import _headers, _signup


def _ws(client, token):
    return client.get("/workspaces", headers=_headers(token)).json()[0]["id"]


def _token_from(url):
    return url.split("token=")[1]


def test_invite_email_accept_flow(client):
    owner = _signup(client, "team-owner@example.com")
    ws = _ws(client, owner)
    invite = client.post(f"/workspaces/{ws}/invitations", json={"email": "New.Person@Example.com", "role": "editor"},
                         headers=_headers(owner))
    assert invite.status_code == 201, invite.text
    data = invite.json()
    assert data["email"] == "new.person@example.com" and data["status"] == "pending" and data["email_sent"] is False
    mail = OUTBOX[-1]
    assert mail["to"] == "new.person@example.com" and data["accept_url"] in mail["text"]  # the link is in the email
    token = _token_from(data["accept_url"])
    with SessionLocal() as db:
        stored = db.get(InvitationRecord, data["id"])
        assert stored.token_hash != token and token not in str(stored.__dict__)  # only the hash is stored

    preview = client.get(f"/invitations/preview?token={token}").json()
    assert preview["role"] == "editor" and preview["invited_by"] and preview["status"] == "pending"

    newcomer = _signup(client, "new.person@example.com")
    joined = client.post("/invitations/accept", json={"token": token}, headers=_headers(newcomer)).json()
    assert joined == {"workspace_id": ws, "role": "editor"}
    roles = {m["email"]: m["role"] for m in client.get(f"/workspaces/{ws}/members", headers=_headers(owner)).json()}
    assert roles["new.person@example.com"] == "editor"
    again = client.post("/invitations/accept", json={"token": token}, headers=_headers(newcomer))
    assert again.status_code == 410 and "accepted" in again.json()["detail"]  # single use
    actions = [e["action"] for e in client.get(f"/workspaces/{ws}/audit?action=invitation.", headers=_headers(owner)).json()]
    assert actions == ["invitation.accept", "invitation.create"]


def test_invite_is_bound_to_the_invited_email(client):
    owner = _signup(client, "team-bind@example.com")
    ws = _ws(client, owner)
    url = client.post(f"/workspaces/{ws}/invitations", json={"email": "alice-bind@example.com", "role": "viewer"},
                      headers=_headers(owner)).json()["accept_url"]
    thief = _signup(client, "mallory-bind@example.com")
    response = client.post("/invitations/accept", json={"token": _token_from(url)}, headers=_headers(thief))
    assert response.status_code == 403  # a forwarded/stolen link cannot be used by another account
    assert client.post("/invitations/accept", json={"token": "made-up"}, headers=_headers(thief)).status_code == 404


def test_expired_revoked_and_reissued_invitations(client):
    owner = _signup(client, "team-exp@example.com")
    ws = _ws(client, owner)
    first = client.post(f"/workspaces/{ws}/invitations", json={"email": "bob-exp@example.com", "role": "viewer"},
                        headers=_headers(owner)).json()
    second = client.post(f"/workspaces/{ws}/invitations", json={"email": "bob-exp@example.com", "role": "editor"},
                         headers=_headers(owner)).json()
    statuses = {i["id"]: i["status"] for i in client.get(f"/workspaces/{ws}/invitations", headers=_headers(owner)).json()}
    assert statuses == {first["id"]: "revoked", second["id"]: "pending"}  # re-inviting replaces the old link
    bob = _signup(client, "bob-exp@example.com")
    assert client.post("/invitations/accept", json={"token": _token_from(first["accept_url"])},
                       headers=_headers(bob)).status_code == 410
    with SessionLocal() as db:
        db.get(InvitationRecord, second["id"]).expires_at = now_utc() - timedelta(minutes=1)
        db.commit()
    expired = client.post("/invitations/accept", json={"token": _token_from(second["accept_url"])}, headers=_headers(bob))
    assert expired.status_code == 410 and "expired" in expired.json()["detail"]

    third = client.post(f"/workspaces/{ws}/invitations", json={"email": "bob-exp@example.com", "role": "editor"},
                        headers=_headers(owner)).json()
    client.delete(f"/workspaces/{ws}/invitations/{third['id']}", headers=_headers(owner))
    assert client.post("/invitations/accept", json={"token": _token_from(third["accept_url"])},
                       headers=_headers(bob)).status_code == 410


def test_only_owners_invite_and_members_cannot_be_reinvited(client):
    owner = _signup(client, "team-perm@example.com")
    ws = _ws(client, owner)
    editor = _signup(client, "team-perm-ed@example.com")
    client.post(f"/workspaces/{ws}/members", json={"email": "team-perm-ed@example.com", "role": "editor"}, headers=_headers(owner))
    assert client.post(f"/workspaces/{ws}/invitations", json={"email": "x@example.com"},
                       headers=_headers(editor)).status_code == 403
    assert client.post(f"/workspaces/{ws}/invitations", json={"email": "team-perm-ed@example.com"},
                       headers=_headers(owner)).status_code == 409
    assert client.post(f"/workspaces/{ws}/invitations", json={"email": "x@example.com", "role": "god"},
                       headers=_headers(owner)).status_code == 422


def test_rename_and_transfer_ownership(client):
    owner = _signup(client, "team-xfer@example.com")
    ws = _ws(client, owner)
    assert client.patch(f"/workspaces/{ws}", json={"name": "Acme AI"}, headers=_headers(owner)).json()["name"] == "Acme AI"
    member = _signup(client, "team-xfer-m@example.com")
    member_id = client.get("/auth/me", headers=_headers(member)).json()["id"]
    client.post(f"/workspaces/{ws}/members", json={"user_id": member_id, "role": "viewer"}, headers=_headers(owner))
    assert client.post(f"/workspaces/{ws}/transfer-ownership", json={"user_id": 999999},
                       headers=_headers(owner)).status_code == 404  # must already be a member
    result = client.post(f"/workspaces/{ws}/transfer-ownership", json={"user_id": member_id}, headers=_headers(owner)).json()
    assert result["owner"] == member_id
    roles = {m["email"]: m["role"] for m in client.get(f"/workspaces/{ws}/members", headers=_headers(member)).json()}
    assert roles == {"team-xfer@example.com": "editor", "team-xfer-m@example.com": "owner"}
    assert client.patch(f"/workspaces/{ws}", json={"name": "nope"}, headers=_headers(owner)).status_code == 403


def test_delete_and_restore_workspace(client):
    owner = _signup(client, "team-del@example.com")
    personal = _ws(client, owner)
    extra = client.post("/workspaces", json={"name": "Side project"}, headers=_headers(owner)).json()["id"]
    gid = client.post("/graphs", json={"name": "In side", "workspace_id": extra}, headers=_headers(owner)).json()["id"]
    assert client.request("DELETE", f"/workspaces/{personal}", json={"confirm_name": "wrong"},
                          headers=_headers(owner)).status_code == 422
    assert client.request("DELETE", f"/workspaces/{extra}", json={"confirm_name": "Side project"},
                          headers=_headers(owner)).status_code == 200
    # gone everywhere: not listed, graphs not reachable
    assert extra not in [w["id"] for w in client.get("/workspaces", headers=_headers(owner)).json()]
    assert client.get(f"/graphs/{gid}", headers=_headers(owner)).status_code == 404
    assert gid not in [g["id"] for g in client.get("/graphs", headers=_headers(owner)).json()]
    restored = client.post(f"/workspaces/{extra}/restore", headers=_headers(owner))
    assert restored.status_code == 200 and client.get(f"/graphs/{gid}", headers=_headers(owner)).status_code == 200
    # cannot delete your only workspace
    solo = _signup(client, "team-solo@example.com")
    solo_ws = _ws(client, solo)
    name = client.get("/workspaces", headers=_headers(solo)).json()[0]["name"]
    assert client.request("DELETE", f"/workspaces/{solo_ws}", json={"confirm_name": name},
                          headers=_headers(solo)).status_code == 409


def test_deleted_workspace_is_purged_by_retention(client):
    from app.services.retention import run_retention

    owner = _signup(client, "team-purge@example.com")
    extra = client.post("/workspaces", json={"name": "Temp"}, headers=_headers(owner)).json()["id"]
    client.post("/graphs", json={"name": "t", "workspace_id": extra}, headers=_headers(owner))
    client.request("DELETE", f"/workspaces/{extra}", json={"confirm_name": "Temp"}, headers=_headers(owner))
    with SessionLocal() as db:
        report = asyncio.run(run_retention(db, now=now_utc() + timedelta(days=31)))
        assert report["workspaces_purged"] >= 1 and db.get(WorkspaceRecord, extra) is None

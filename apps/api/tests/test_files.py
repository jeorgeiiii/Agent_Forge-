"""Cluster 19: files & binary data - upload, download, signed links, validation, S3, retention."""

import asyncio
import time
from datetime import timedelta

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import FileRecord, RunRecord
from app.services import storage as storage_module
from app.services.common import now_utc
from tests.test_api import _headers, _signup

INTERNAL = {"X-Internal-Token": "test-internal-token"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _ws(client, token):
    return client.get("/workspaces", headers=_headers(token)).json()[0]["id"]


def _upload(client, token, ws, name="photo.png", data=PNG, mime="image/png"):
    return client.post("/files", headers=_headers(token), data={"workspace_id": str(ws)}, files={"file": (name, data, mime)})


def test_upload_list_download_and_delete(client):
    owner = _signup(client, "files-owner@example.com")
    ws = _ws(client, owner)
    response = _upload(client, owner, ws)
    assert response.status_code == 201, response.text
    meta = response.json()
    assert meta["size"] == len(PNG) and meta["mime"] == "image/png" and len(meta["sha256"]) == 64
    assert meta["ref"] == {"$file": {"id": meta["id"], "name": "photo.png", "mime": "image/png", "size": len(PNG)}}
    assert [f["id"] for f in client.get(f"/files?workspace_id={ws}", headers=_headers(owner)).json()] == [meta["id"]]

    content = client.get(f"/files/{meta['id']}/content", headers=_headers(owner))
    assert content.content == PNG and content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["content-disposition"].startswith("inline")  # safe images render inline
    html = _upload(client, owner, ws, "page.html", b"<script>alert(1)</script>", "text/html").json()
    html_dl = client.get(f"/files/{html['id']}/content", headers=_headers(owner))
    assert html_dl.headers["content-disposition"].startswith("attachment")  # HTML/SVG never render on our origin
    assert html_dl.headers["content-security-policy"] == "sandbox"

    assert client.delete(f"/files/{meta['id']}", headers=_headers(owner)).status_code == 204
    assert client.get(f"/files/{meta['id']}", headers=_headers(owner)).status_code == 404
    events = [e["action"] for e in client.get(f"/workspaces/{ws}/audit?action=file.", headers=_headers(owner)).json()]
    assert events == ["file.delete", "file.upload", "file.upload"]


@pytest.mark.parametrize("name,data,mime,code", [
    ("tool.exe", b"hello", "application/octet-stream", 415),         # blocked extension
    ("innocent.png", b"MZ\x90\x00binary", "image/png", 415),          # Windows executable in disguise
    ("run.txt", b"#!/bin/sh\nrm -rf /", "text/plain", 415),          # script in disguise
    ("app.bin", b"\x7fELF\x02\x01", "application/octet-stream", 415),  # Linux executable
    ("x.wasm", b"\x00asm", "application/wasm", 415),                  # type not on the allow list
    ("empty.txt", b"", "text/plain", 422),
])
def test_dangerous_or_invalid_uploads_are_rejected(client, name, data, mime, code):
    owner = _signup(client, f"files-bad-{code}-{name}@example.com")
    assert _upload(client, owner, _ws(client, owner), name, data, mime).status_code == code


def test_size_limit(client, monkeypatch):
    owner = _signup(client, "files-size@example.com")
    monkeypatch.setattr(get_settings(), "max_file_bytes", 1000)
    assert _upload(client, owner, _ws(client, owner), "big.txt", b"x" * 1001, "text/plain").status_code == 413


def test_permissions(client):
    owner = _signup(client, "files-perm@example.com")
    ws = _ws(client, owner)
    file_id = _upload(client, owner, ws).json()["id"]
    stranger = _signup(client, "files-stranger@example.com")
    assert client.get(f"/files/{file_id}", headers=_headers(stranger)).status_code == 404
    assert client.get(f"/files/{file_id}/content", headers=_headers(stranger)).status_code == 404
    viewer = _signup(client, "files-viewer@example.com")
    client.post(f"/workspaces/{ws}/members", json={"email": "files-viewer@example.com", "role": "viewer"}, headers=_headers(owner))
    assert client.get(f"/files/{file_id}/content", headers=_headers(viewer)).status_code == 200  # viewers can read
    assert _upload(client, viewer, ws).status_code == 403                                      # ...not write
    assert client.delete(f"/files/{file_id}", headers=_headers(viewer)).status_code == 403


def test_signed_download_links_expire_and_cannot_be_forged(client, monkeypatch):
    owner = _signup(client, "files-signed@example.com")
    file_id = _upload(client, owner, _ws(client, owner)).json()["id"]
    link = client.post(f"/files/{file_id}/url", headers=_headers(owner)).json()
    path = link["url"].split("8000", 1)[1]
    client.cookies.clear()
    assert client.get(path).content == PNG  # works with no login: the signature is the permission
    token = path.rsplit("/", 1)[1]
    assert client.get(path[:-1] + ("0" if token[-1] != "0" else "1")).status_code == 403      # tampered signature
    assert client.put(path, content=b"overwrite").status_code == 403                         # a GET link cannot upload
    future = time.time() + 10_000
    monkeypatch.setattr(storage_module.time, "time", lambda: future)
    assert client.get(path).status_code == 410  # expired


def test_direct_upload_flow_local_backend(client):
    owner = _signup(client, "files-direct@example.com")
    ws = _ws(client, owner)
    start = client.post("/files/uploads", headers=_headers(owner),
                        json={"name": "notes.txt", "mime": "text/plain", "size": 11, "workspace_id": ws}).json()
    assert client.get(f"/files/{start['file_id']}/content", headers=_headers(owner)).status_code == 409  # not uploaded yet
    put_path = start["upload_url"].split("8000", 1)[1]
    assert client.put(put_path, content=b"hello world", headers=start["headers"]).status_code == 200
    done = client.post(f"/files/{start['file_id']}/complete", headers=_headers(owner)).json()
    assert done["status"] == "ready" and done["size"] == 11
    assert client.get(f"/files/{start['file_id']}/content", headers=_headers(owner)).content == b"hello world"
    assert client.put(put_path, content=b"again").status_code == 410  # upload links are single-use


def test_worker_internal_endpoints_scope_files_to_the_run_workspace(client):
    owner = _signup(client, "files-worker@example.com")
    ws = _ws(client, owner)
    gid = client.post("/graphs", json={"name": "Files"}, headers=_headers(owner)).json()["id"]
    with SessionLocal() as db:
        db.add(RunRecord(graph_id=gid, workflow_id="files-run", status="running", input={}))
        db.commit()
    ref = client.post("/internal/files", headers={**INTERNAL, "X-File-Name": "out.csv", "X-File-Mime": "text/csv",
                                                  "X-Workflow-Id": "files-run"}, content=b"a,b\n1,2\n").json()
    meta = client.get(f"/files/{ref['$file']['id']}", headers=_headers(owner)).json()
    assert meta["workspace_id"] == ws and meta["graph_id"] == gid and meta["run_id"]  # owned by the run's workspace
    assert client.get(f"/internal/files/{ref['$file']['id']}?workflow_id=files-run", headers=INTERNAL).content == b"a,b\n1,2\n"
    other = _signup(client, "files-other@example.com")
    foreign = _upload(client, other, _ws(client, other)).json()["id"]
    assert client.get(f"/internal/files/{foreign}?workflow_id=files-run", headers=INTERNAL).status_code == 404
    assert client.post("/internal/files", content=b"x").status_code == 403


def test_run_files_are_removed_with_the_run_data(client):
    from app.services.retention import run_retention

    owner = _signup(client, "files-ret@example.com")
    gid = client.post("/graphs", json={"name": "Ret"}, headers=_headers(owner)).json()["id"]
    with SessionLocal() as db:
        db.add(RunRecord(graph_id=gid, workflow_id="files-ret-run", status="success", input={}, finished_at=now_utc()))
        db.commit()
    ref = client.post("/internal/files", headers={**INTERNAL, "X-File-Name": "r.txt", "X-File-Mime": "text/plain",
                                                  "X-Workflow-Id": "files-ret-run"}, content=b"data").json()
    file_id = ref["$file"]["id"]
    with SessionLocal() as db:
        key = db.get(FileRecord, file_id).storage_key
        asyncio.run(run_retention(db, now=now_utc() + timedelta(days=31)))
        assert db.get(FileRecord, file_id) is None
    assert not storage_module.get_storage().exists(key)  # the bytes are gone too, not just the row


def test_s3_backend(client, monkeypatch):
    import boto3
    from moto import mock_aws

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="af-test")
        settings = get_settings()
        monkeypatch.setattr(settings, "storage_backend", "s3")
        monkeypatch.setattr(settings, "s3_bucket", "af-test")
        storage_module.get_storage.cache_clear()
        try:
            owner = _signup(client, "files-s3@example.com")
            ws = _ws(client, owner)
            meta = _upload(client, owner, ws, "report.pdf", b"%PDF-1.7 test", "application/pdf").json()
            assert client.get(f"/files/{meta['id']}/content", headers=_headers(owner)).content == b"%PDF-1.7 test"
            link = client.post(f"/files/{meta['id']}/url", headers=_headers(owner)).json()["url"]
            assert "af-test" in link and "Signature" in link and "response-content-disposition=inline" in link
            start = client.post("/files/uploads", headers=_headers(owner),
                                json={"name": "big.txt", "mime": "text/plain", "size": 5, "workspace_id": ws}).json()
            assert "X-Amz-Signature" in start["upload_url"] or "Signature" in start["upload_url"]
            with SessionLocal() as db:
                key = db.get(FileRecord, start["file_id"]).storage_key
            boto3.client("s3", region_name="us-east-1").put_object(Bucket="af-test", Key=key, Body=b"hello")  # = browser PUT
            done = client.post(f"/files/{start['file_id']}/complete", headers=_headers(owner)).json()
            assert done["status"] == "ready" and done["size"] == 5
            client.delete(f"/files/{meta['id']}", headers=_headers(owner))
            assert "Contents" not in boto3.client("s3", region_name="us-east-1").list_objects_v2(
                Bucket="af-test", Prefix=f"ws-{ws}/{meta['id'][:2]}/{meta['id']}")
        finally:
            storage_module.get_storage.cache_clear()

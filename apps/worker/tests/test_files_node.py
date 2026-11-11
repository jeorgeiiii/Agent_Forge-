"""Cluster 19 in the worker: HTTP node saves a download as a file, the next node uploads that file."""

import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

import activities  # noqa: E402
from activities import checkpoint_state, emit_events, execute_node, llm_chat  # noqa: E402
from agentforge_core.compiler import compile_graph  # noqa: E402
from workflow import AgentWorkflow, RunGraphWorkflow  # noqa: E402

pytestmark = pytest.mark.asyncio
PDF = b"%PDF-1.7 " + b"x" * 2_000_000  # 2 MB: bigger than the 1 MB JSON limit, fine as a file


@pytest.fixture
def world(monkeypatch):
    stored: dict[str, dict] = {}
    received: list[httpx.Request] = []

    def internet(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf",
                                                             "content-disposition": 'attachment; filename="invoice.pdf"'})
        received.append(request)
        return httpx.Response(200, json={"uploaded": len(request.content)})

    async def call_raw(method, path, content=None, headers=None, timeout=60):
        if method == "POST" and path == "/internal/files":
            file_id = str(uuid.uuid4())
            stored[file_id] = {"data": content, "mime": headers["X-File-Mime"], "name": headers["X-File-Name"],
                               "workflow": headers["X-Workflow-Id"]}
            return httpx.Response(200, json={"$file": {"id": file_id, "name": headers["X-File-Name"],
                                                       "mime": headers["X-File-Mime"], "size": len(content)}})
        file_id = path.split("/internal/files/")[1].split("?")[0]
        item = stored[file_id]
        return httpx.Response(200, content=item["data"], headers={"content-type": item["mime"]})

    async def call(method, path, json=None, timeout=10):
        return None  # checkpoints / events

    async def public_ok(url):
        return None

    real_client = httpx.AsyncClient
    monkeypatch.setattr(activities.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(internet), **{k: v for k, v in kw.items()
                                                                                              if k != "trust_env"}))
    monkeypatch.setattr(activities.api_client, "call_raw", call_raw)
    monkeypatch.setattr(activities.api_client, "call", call)
    monkeypatch.setattr(activities.api_client, "is_configured", lambda: True)
    monkeypatch.setattr(activities, "validate_public_http_url", public_ok)
    return stored, received


async def test_download_as_file_then_upload_that_file(world):
    stored, received = world
    graph = {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "fetch", "type": "http", "label": "Fetch", "config": {
                "url": "https://files.example.com/invoice", "response_format": "file"}},
            {"id": "send", "type": "http", "label": "Send", "config": {
                "url": "https://archive.example.com/upload", "method": "POST", "body_file": "{{ $json.file }}"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"source": "start", "target": "fetch"}, {"source": "fetch", "target": "send"},
                  {"source": "send", "target": "end"}],
    }
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = f"test-{uuid.uuid4()}"
        async with Worker(env.client, task_queue=queue, workflows=[RunGraphWorkflow, AgentWorkflow],
                          activities=[execute_node, llm_chat, emit_events, checkpoint_state]):
            result = await env.client.execute_workflow(
                RunGraphWorkflow.execute_graph_workflow,
                {"ir": compile_graph(graph), "input": {}, "run": {"workflow_id": "wf-files"}},
                id=f"wf-{uuid.uuid4()}", task_queue=queue)

    (file_id, item), = stored.items()
    assert item["name"] == "invoice.pdf" and item["mime"] == "application/pdf" and item["workflow"] == "wf-files"
    assert len(item["data"]) == len(PDF)
    fetch_out = result["nodes"]["fetch"]
    assert fetch_out["file"]["$file"]["id"] == file_id  # only the small reference travelled through Temporal
    assert len(str(fetch_out)) < 300
    upload = received[0]
    assert upload.content == PDF and upload.headers["content-type"] == "application/pdf"
    assert result["output"]["body"] == {"uploaded": len(PDF)}


async def test_response_format_is_validated():
    from agentforge_core.registry import validate_node_config

    assert validate_node_config("http", {"url": "https://x", "response_format": "file"}) == []
    assert validate_node_config("http", {"url": "https://x", "response_format": "binary"})  # not a valid choice


def test_downloaded_files_get_a_proper_name():
    from activities import _filename_from

    png = httpx.Response(200, headers={"content-type": "image/png"})
    assert _filename_from(png, "https://httpbin.org/image/png") == "png.png"
    named = httpx.Response(200, headers={"content-type": "application/pdf", "content-disposition": 'attachment; filename="q3.pdf"'})
    assert _filename_from(named, "https://x/download?id=1") == "q3.pdf"
    assert _filename_from(httpx.Response(200, headers={"content-type": "text/csv"}), "https://x/") .endswith(".csv")

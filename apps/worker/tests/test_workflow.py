"""Runs RunGraphWorkflow end-to-end on Temporal's time-skipping test server."""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from temporalio.client import WorkflowFailureError  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

import agentforge_core  # noqa: E402,F401
from agentforge_core.compiler import compile_graph  # noqa: E402
from activities import checkpoint_state, execute_node  # noqa: E402
from workflow import RunGraphWorkflow  # noqa: E402

pytestmark = pytest.mark.asyncio


def n(id, type, label=None, **config):
    return {"id": id, "type": type, "label": label or id, "config": config}


GRAPH = {
    "nodes": [
        n("start", "start"),
        n("check", "if", condition="{{ $json.total > 100 }}"),
        n("vip", "set", "VIP", fields={"tier": "vip", "who": "{{ $trigger.name }}"}),
        n("normal", "set", "Normal", fields={"tier": "normal"}),
        n("merge", "merge", mode="first"),
        n("greet", "template", template="{{ $json.who }} is {{ $json.tier }}"),
        n("end", "end"),
    ],
    "edges": [
        {"source": "start", "target": "check"},
        {"source": "check", "target": "vip", "sourceHandle": "true"},
        {"source": "check", "target": "normal", "sourceHandle": "false"},
        {"source": "vip", "target": "merge"},
        {"source": "normal", "target": "merge"},
        {"source": "merge", "target": "greet"},
        {"source": "greet", "target": "end"},
    ],
}


async def _run(env, graph, run_input):
    queue = f"test-{uuid.uuid4()}"
    async with Worker(env.client, task_queue=queue, workflows=[RunGraphWorkflow], activities=[execute_node, checkpoint_state]):
        handle = await env.client.start_workflow(
            RunGraphWorkflow.execute_graph_workflow,
            {"ir": compile_graph(graph), "input": run_input, "run": {}},
            id=f"wf-{uuid.uuid4()}",
            task_queue=queue,
        )
        result = await handle.result()
        return await handle.query("state"), result


async def test_branching_graph_runs_end_to_end():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        state, result = await _run(env, GRAPH, {"total": 150, "name": "Ada"})
        assert result["status"] == "success"
        assert result["output"] == "Ada is vip"
        assert state["nodes"]["normal"]["status"] == "skipped"
        assert state["nodes"]["vip"]["status"] == "success"


async def test_failing_node_fails_the_run_with_a_clear_error():
    bad = {
        "nodes": [n("start", "start"), n("t", "template", "Broken", template="{{ $json.missing.key }}"), n("end", "end")],
        "edges": [{"source": "start", "target": "t"}, {"source": "t", "target": "end"}],
    }
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with pytest.raises(WorkflowFailureError) as info:
            await _run(env, bad, {})
        assert "Broken" in str(info.value.cause)

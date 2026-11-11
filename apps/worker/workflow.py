"""Cluster 6 - Execution engine (Temporal workflows).

``RunGraphWorkflow`` executes a compiled IR superstep by superstep: nodes in the
same superstep run in parallel, IF/Switch outputs decide which downstream nodes
are active, and the run state is checkpointed to Postgres after every step.
"""

import asyncio
import json
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from activities import checkpoint_state, execute_node, fire_schedule_trigger, llm_chat
    from agentforge_core.builtin_nodes import MAX_AGENT_DEPTH  # supervisor -> worker -> sub-worker
    from agentforge_core.expressions import build_expression_context, render_parameter
    from agentforge_core.runtime import (
        final_output,
        new_run_state,
        node_outputs_for_context,
        primary_output,
        resolve_inputs_for_node,
    )

NODE_TIMEOUT = timedelta(seconds=45)
DEFAULT_AGENT_STEPS = 8
MAX_TOOL_RESULT_CHARS = 20_000
CHECKPOINT_TIMEOUT = timedelta(seconds=5)
CHECKPOINT_INTERVAL = timedelta(seconds=1)  # max one progress save per second per run
MAX_PARALLEL = 10


def _error_message(error: BaseException) -> str:
    cause = error
    while isinstance(cause, (ActivityError, ChildWorkflowError)) and cause.cause:  # unwrap to the real error
        cause = cause.cause
    return getattr(cause, "message", None) or str(cause)


@workflow.defn(name="RunGraphWorkflow")
class RunGraphWorkflow:
    def __init__(self) -> None:
        self._state: dict[str, Any] = new_run_state()
        self._approvals: dict[str, dict[str, Any]] = {}
        self._workflow_id = ""
        self._last_checkpoint = None

    # ----- signals & queries ---------------------------------------------
    @workflow.signal(name="approval")
    def resume_run_with_signal(self, decision: dict[str, Any]) -> None:
        """Human-in-the-loop decision: {"node_id", "approved": bool, "comment", "user_id"}."""
        self._approvals[str(decision.get("node_id"))] = decision

    @workflow.query(name="state")
    def get_state(self) -> dict[str, Any]:
        return self._state

    # ----- main loop -------------------------------------------------------
    @workflow.run
    async def execute_graph_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        ir: dict[str, Any] = payload["ir"]
        run_input = payload.get("input", {})
        meta = payload.get("run", {})
        self._workflow_id = workflow.info().workflow_id
        if payload.get("checkpoint"):  # resuming / retrying from a saved state
            self._state = payload["checkpoint"]
            self._state["status"] = "running"
            # failed nodes are retried
            for info in self._state.get("nodes", {}).values():
                if info.get("status") == "failed":
                    info["status"] = "pending"

        try:
            # No save at start: the API already marked the run 'running'.
            self._last_checkpoint = workflow.now()
            for index in range(self._state.get("superstep", 0), len(ir["supersteps"])):
                await self.execute_superstep(ir, ir["supersteps"][index], run_input, meta)
                self._state["superstep"] = index + 1
                await self._checkpoint()  # throttled
            self._state["status"] = "success"
            self._state["output"] = final_output(ir, self._state)
        except asyncio.CancelledError:
            self._state["status"] = "cancelled"
            await self._checkpoint(final=True)
            raise
        except _NodeFailed as failure:
            self._state["status"] = "failed"
            self._state["error"] = str(failure)
            await self._checkpoint(final=True)
            raise ApplicationError(str(failure), type="NodeFailed", non_retryable=True) from None
        await self._checkpoint(final=True)
        return {
            "status": "success",
            "output": self._state["output"],
            "nodes": node_outputs_for_context(self._state),
            "usage": self._state.get("usage"),
        }

    async def execute_superstep(self, ir: dict[str, Any], layer: list[str], run_input: Any, meta: dict[str, Any]) -> None:
        """Run every node of one layer; independent nodes execute concurrently."""
        for start in range(0, len(layer), MAX_PARALLEL):
            batch = layer[start:start + MAX_PARALLEL]
            await asyncio.gather(*(self._run_node(ir, node_id, run_input, meta) for node_id in batch))

    async def _run_node(self, ir: dict[str, Any], node_id: str, run_input: Any, meta: dict[str, Any]) -> None:
        nodes_state = self._state.setdefault("nodes", {})
        if nodes_state.get(node_id, {}).get("status") in ("success", "skipped"):
            return  # already done in a previous attempt
        node = ir["nodes"][node_id]
        resolved = resolve_inputs_for_node(ir, node_id, self._state, run_input)
        if not resolved["active"]:
            nodes_state[node_id] = {"status": "skipped", "outputs": {}}
            return
        info: dict[str, Any] = {"status": "running", "started_at": workflow.now().isoformat()}
        nodes_state[node_id] = info
        try:
            if node["type"] == "wait":
                info["outputs"] = await self._wait(node, resolved["input"])
            elif node["type"] == "approval":
                info["outputs"] = await self.pause_for_approval(node, resolved["input"])
            elif node["type"] == "agent":
                info["outputs"] = await self.run_agent(ir, node, resolved, run_input, meta, info)
            else:
                info.update(await self._execute_activity(ir, node, resolved, run_input, meta))
                self._add_usage(info.get("usage"))
            info["status"] = "success"
        except (ActivityError, ApplicationError) as error:
            info["error"] = _error_message(error)
            if node["config"].get("continue_on_fail"):
                info["status"] = "success"
                info["outputs"] = {node["outputs"][0]: {"error": info["error"]}}
            else:
                info["status"] = "failed"
                raise _NodeFailed(f"Node '{node.get('label') or node_id}' failed: {info['error']}") from None
        finally:
            info["finished_at"] = workflow.now().isoformat()

    # ----- agent node --------------------------------------------------------
    async def run_agent(self, ir, node, resolved, run_input, meta, info) -> dict[str, Any]:
        """Agent node in the graph: runs the shared AgentLoop at depth 1."""
        labels = {i: n.get("label") for i, n in ir["nodes"].items() if n.get("label")}
        loop = AgentLoop(
            node["config"],
            depth=1,
            current_input=resolved["input"],
            run_input=run_input,
            node_outputs=node_outputs_for_context(self._state),
            labels=labels,
            meta=meta,
            id_prefix=f"{self._workflow_id}/{node['id']}",
            on_usage=self._add_usage,
            on_step=self._checkpoint,  # progress is visible in the run while the agent works
        )
        info["agent"] = {"steps": loop.trace, "usage": loop.usage}
        return {"main": await loop.run()}

    def _add_usage(self, usage: dict[str, int] | None) -> None:
        """Run-level token totals across every LLM call (shown in the run status)."""
        if not usage:
            return
        totals = self._state.setdefault("usage", {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0})
        for key in totals:
            totals[key] += int(usage.get(key, 0))

    async def _execute_activity(self, ir, node, resolved, run_input, meta) -> dict[str, Any]:
        # LLM providers rate-limit often, so LLM nodes retry transient errors twice by default.
        retries = int(node["config"].get("retries", 2 if node["type"] == "llm" else 0) or 0)
        request = {
            "node": node,
            "input": resolved["input"],
            "inputs": resolved["inputs"],
            "run_input": run_input,
            "node_outputs": node_outputs_for_context(self._state),
            "labels": {i: n.get("label") for i, n in ir["nodes"].items() if n.get("label")},
            "order": ir["order"],
            "run": meta,
            "variables": meta.get("variables", {}),
        }
        return await workflow.execute_activity(
            execute_node,
            request,
            start_to_close_timeout=timedelta(seconds=float(node["config"].get("timeout_seconds", NODE_TIMEOUT.seconds))),
            retry_policy=RetryPolicy(
                maximum_attempts=retries + 1,
                initial_interval=timedelta(seconds=1),
                backoff_coefficient=2.0,
            ),
        )

    async def _wait(self, node: dict[str, Any], value: Any) -> dict[str, Any]:
        await asyncio.sleep(float(node["config"].get("seconds", 0)))  # durable Temporal timer
        return {"main": value}

    async def pause_for_approval(self, node: dict[str, Any], value: Any) -> dict[str, Any]:
        """Mark the run as paused and wait for an 'approval' signal for this node."""
        node_id = node["id"]
        self._state["status"] = "paused"
        self._state["nodes"][node_id]["status"] = "waiting"
        self._state["waiting_for"] = {"node_id": node_id, "message": node["config"].get("message", "")}
        await self._checkpoint(force=True)  # the UI must see 'paused' right away
        timeout = node["config"].get("timeout_seconds")
        try:
            await workflow.wait_condition(
                lambda: node_id in self._approvals,
                timeout=timedelta(seconds=float(timeout)) if timeout else None,
            )
            decision = self._approvals[node_id]
        except asyncio.TimeoutError:
            decision = {"approved": False, "comment": "Approval timed out."}
        self._state["status"] = "running"
        self._state.pop("waiting_for", None)
        payload = {"input": value, "decision": decision}
        return {"approved": payload} if decision.get("approved") else {"rejected": payload}

    async def _checkpoint(self, *, force: bool = False, final: bool = False) -> None:
        """Persist run state to the API.

        Progress saves are throttled (at most one per CHECKPOINT_INTERVAL) and
        give up fast; pause and final saves always happen, and the final one
        retries with backoff until the API accepts it, so the runs table never
        stays 'running' for a finished run. ``seq`` lets the API drop stale saves.
        """
        now = workflow.now()
        if not (force or final) and self._last_checkpoint and now - self._last_checkpoint < CHECKPOINT_INTERVAL:
            return
        self._last_checkpoint = now
        self._state["seq"] = self._state.get("seq", 0) + 1
        policy = (
            RetryPolicy(maximum_attempts=20, initial_interval=timedelta(seconds=1),
                        backoff_coefficient=2.0, maximum_interval=timedelta(seconds=30))
            if final else RetryPolicy(maximum_attempts=2, initial_interval=timedelta(milliseconds=500))
        )
        try:
            await workflow.execute_activity(
                checkpoint_state,
                args=[self._workflow_id, self._state],
                start_to_close_timeout=CHECKPOINT_TIMEOUT,
                retry_policy=policy,
            )
        except ActivityError as error:
            workflow.logger.warning("Checkpoint failed: %s", _error_message(error))


class AgentLoop:
    """The LLM <-> tools loop, shared by the agent node and by sub-agents.

    Every LLM call and tool call is its own activity, so the loop is durable:
    after a crash Temporal replays it without calling the model again. A tool
    whose node type is ``agent`` runs as a child ``AgentWorkflow`` one level
    deeper - that is how a supervisor agent delegates to worker agents.
    """

    def __init__(self, config, *, depth, current_input, run_input, node_outputs, labels, meta,
                 id_prefix, on_usage=None, on_step=None):
        self.config = config
        self.depth = depth
        self.run_input = run_input
        self.node_outputs = node_outputs
        self.labels = labels
        self.meta = meta
        self.id_prefix = id_prefix
        self.on_usage = on_usage
        self.on_step = on_step
        self.usage = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}
        self.trace: list[dict[str, Any]] = []
        self.context = build_expression_context(
            run_input=run_input, node_outputs=node_outputs, labels=labels, current_input=current_input,
            run_meta=meta, now=workflow.now().isoformat(),
            tool_args=current_input if isinstance(current_input, dict) else None,
        )

    def _charge(self, usage: dict[str, int] | None) -> None:
        if not usage:
            return
        for key in self.usage:
            self.usage[key] += int(usage.get(key, 0))
        if self.on_usage:
            self.on_usage(usage)

    async def run(self) -> dict[str, Any]:
        config = self.config
        messages: list[dict[str, Any]] = []
        if config.get("system"):
            messages.append({"role": "system", "content": str(render_parameter(config["system"], self.context))})
        messages.append({"role": "user", "content": str(render_parameter(config["prompt"], self.context))})
        tools = {t["name"]: t for t in config.get("tools") or []}
        tool_defs = [
            {"type": "function", "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("parameters") or _default_tool_parameters(t),
            }}
            for t in tools.values()
        ]
        max_steps = int(config.get("max_steps", DEFAULT_AGENT_STEPS))
        token_budget = config.get("max_total_tokens")
        text, stopped = "", "max_steps"

        for step in range(1, max_steps + 1):
            reply = await workflow.execute_activity(
                llm_chat,
                {
                    "credential_id": config.get("credential_id"),
                    "base_url": config.get("base_url"),
                    "model": config["model"],
                    "messages": messages,
                    "tools": tool_defs,
                    "temperature": config.get("temperature"),
                    "max_tokens": config.get("max_tokens"),
                    "json_output": config.get("json_output") and not tool_defs,
                },
                start_to_close_timeout=timedelta(seconds=90),
                retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2), backoff_coefficient=2.0),
            )
            self._charge(reply["usage"])
            message = reply["message"]
            messages.append(message)
            text = message.get("content") or ""
            calls = message.get("tool_calls") or []
            self.trace.append({"step": step, "tool_calls": [c.get("function", {}).get("name") for c in calls]})
            if not calls:
                stopped = "final"
                break
            if token_budget and self.usage["input_tokens"] + self.usage["output_tokens"] >= int(token_budget):
                stopped = "token_budget"
                break
            results = await asyncio.gather(*(self._run_tool(tools, call, step) for call in calls))
            messages.extend(results)
            if self.on_step:
                await self.on_step()

        output: dict[str, Any] = {"text": text, "stopped_reason": stopped, "steps": len(self.trace), "usage": self.usage}
        if config.get("json_output") and stopped == "final":
            try:
                output["json"] = json.loads(text)
            except json.JSONDecodeError:
                output["json_error"] = "The agent's final answer was not valid JSON."
        return output

    async def _run_tool(self, tools, call, step) -> dict[str, Any]:
        """Execute one tool call; errors are reported back to the model instead of failing the run."""
        call_id = call.get("id", "") or f"s{step}"
        name = call.get("function", {}).get("name", "")
        tool = tools.get(name)
        try:
            if tool is None:
                raise ValueError(f"Unknown tool '{name}'.")
            arguments = json.loads(call.get("function", {}).get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object.")
            node_type = tool["node"]["type"]
            node_config = tool["node"].get("config") or {}
            if node_type == "agent":
                content = await self._run_sub_agent(name, call_id, node_config, arguments)
            else:
                result = await workflow.execute_activity(
                    execute_node,
                    {
                        "node": {"id": f"tool:{name}", "type": node_type, "label": name, "config": node_config},
                        "input": arguments,
                        "tool_args": arguments,
                        "run_input": self.run_input,
                        "node_outputs": self.node_outputs,
                        "run": self.meta,
                    },
                    start_to_close_timeout=NODE_TIMEOUT,
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                self._charge(result.get("usage"))
                content = json.dumps(primary_output(result["outputs"]), default=str)
        except (ActivityError, ChildWorkflowError, ApplicationError, ValueError, json.JSONDecodeError) as error:
            content = json.dumps({"error": _error_message(error)})
        return {"role": "tool", "tool_call_id": call_id, "content": content[:MAX_TOOL_RESULT_CHARS]}

    async def _run_sub_agent(self, name, call_id, config, arguments) -> str:
        """Delegate to another agent as a child workflow, one level deeper."""
        if self.depth >= MAX_AGENT_DEPTH:
            raise ValueError(f"Sub-agent '{name}' not started: maximum agent nesting depth ({MAX_AGENT_DEPTH}) reached.")
        result = await workflow.execute_child_workflow(
            AgentWorkflow.run,
            {
                "config": config,
                "depth": self.depth + 1,
                "input": arguments,
                "run_input": self.run_input,
                "node_outputs": self.node_outputs,
                "labels": self.labels,
                "run": self.meta,
            },
            id=f"{self.id_prefix}/{name}-{call_id}",
            execution_timeout=timedelta(minutes=15),  # never outlive the step waiting for it
        )
        self._charge(result.get("usage"))
        self.trace[-1].setdefault("sub_agents", []).append(
            {"tool": name, "steps": result.get("steps"), "stopped_reason": result.get("stopped_reason"),
             "usage": result.get("usage")}
        )
        answer = {"text": result.get("text", "")}
        if "json" in result:
            answer["json"] = result["json"]
        if result.get("stopped_reason") != "final":
            answer["note"] = f"sub-agent stopped early ({result.get('stopped_reason')})"
        return json.dumps(answer, default=str)


def _default_tool_parameters(tool: dict[str, Any]) -> dict[str, Any]:
    if tool.get("node", {}).get("type") == "agent":
        # sub-agents receive a task description by default (use {{ $args.task }} in their prompt)
        return {"type": "object", "required": ["task"],
                "properties": {"task": {"type": "string", "description": "What the sub-agent should do."}}}
    return {"type": "object", "properties": {}}


@workflow.defn(name="AgentWorkflow")
class AgentWorkflow:
    """A sub-agent, run as a child workflow of the agent that delegated to it."""

    def __init__(self) -> None:
        self._loop: AgentLoop | None = None

    @workflow.query(name="trace")
    def get_trace(self) -> dict[str, Any]:
        return {"steps": self._loop.trace, "usage": self._loop.usage} if self._loop else {}

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._loop = AgentLoop(
            payload["config"],
            depth=int(payload["depth"]),
            current_input=payload.get("input"),
            run_input=payload.get("run_input"),
            node_outputs=payload.get("node_outputs") or {},
            labels=payload.get("labels") or {},
            meta=payload.get("run") or {},
            id_prefix=workflow.info().workflow_id,
        )
        output = await self._loop.run()
        output["trace"] = self._loop.trace
        return output


class _NodeFailed(Exception):
    pass


@workflow.defn(name="ScheduleFireWorkflow")
class ScheduleFireWorkflow:
    """Started by a Temporal Schedule; asks the API to start a run of the graph."""

    @workflow.run
    async def run(self, trigger_id: int) -> dict[str, Any]:
        return await workflow.execute_activity(
            fire_schedule_trigger,
            trigger_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

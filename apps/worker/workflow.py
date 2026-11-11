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
    from activities import (checkpoint_state, emit_events, execute_node, fire_schedule_trigger, llm_chat,
                            load_memory, save_memory, save_summary)
    from agentforge_core.memory import (DEFAULT_KEEP_RECENT, DEFAULT_MAX_TOKENS, estimate_tokens, memory_key,
                                        redact_pii, summary_request, trim_to_token_budget)
    from agentforge_core.builtin_nodes import MAX_AGENT_DEPTH  # supervisor -> worker -> sub-worker
    from agentforge_core.expressions import ExpressionError, build_expression_context, render_parameter
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
DEFAULT_MEMORY_WINDOW = 10  # messages loaded for memory type "window"
MAX_MEMORY_BUFFER = 200     # cap for memory type "buffer"
CHECKPOINT_TIMEOUT = timedelta(seconds=5)
CHECKPOINT_INTERVAL = timedelta(seconds=1)  # max one progress save per second per run
EVENT_FLUSH_INTERVAL = timedelta(milliseconds=250)  # live events: at most 4 sends per second per producer
MAX_PARALLEL = 10


def _error_message(error: BaseException) -> str:
    cause = error
    while isinstance(cause, (ActivityError, ChildWorkflowError)) and cause.cause:  # unwrap to the real error
        cause = cause.cause
    return getattr(cause, "message", None) or str(cause)


class EventOutbox:
    """Buffers run events and delivers them to the API.

    ``source`` is "" for the run itself and the child workflow id for a sub-agent,
    so every producer has its own seq space; the API de-duplicates on
    (run, source, seq), which makes redelivery after a failure safe.
    """

    def __init__(self, run_workflow_id: str, source: str = "") -> None:
        self.run_workflow_id = run_workflow_id
        self.source = source
        self.pending: list[dict[str, Any]] = []
        self.seq = 0
        self._last_flush = None

    def add(self, type_: str, node_id: str | None = None, at: str | None = None, **data: Any) -> None:
        self.seq += 1
        self.pending.append({"seq": self.seq, "type": type_, "node_id": node_id,
                             "at": at or workflow.now().isoformat(), "data": data})

    def mark_delivered(self, sent: list[dict[str, Any]]) -> None:
        done = {e["seq"] for e in sent}
        self.pending = [e for e in self.pending if e["seq"] not in done]

    async def flush(self, *, force: bool = False) -> bool:
        """Send pending events now (throttled unless forced). Returns True if nothing is left."""
        if not self.pending:
            return True
        now = workflow.now()
        if not force and self._last_flush and now - self._last_flush < EVENT_FLUSH_INTERVAL:
            return False
        self._last_flush = now
        sending = list(self.pending)
        try:
            await workflow.execute_activity(
                emit_events, args=[self.run_workflow_id, self.source, sending],
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=RetryPolicy(maximum_attempts=3 if force else 1),
            )
        except ActivityError as error:
            workflow.logger.warning("Event flush failed (will retry later): %s", _error_message(error))
            return False
        self.mark_delivered(sending)
        return not self.pending


@workflow.defn(name="RunGraphWorkflow")
class RunGraphWorkflow:
    def __init__(self) -> None:
        self._state: dict[str, Any] = new_run_state()
        self._approvals: dict[str, dict[str, Any]] = {}
        self._workflow_id = ""
        self._last_checkpoint = None
        self._outbox: EventOutbox | None = None

    def _emit(self, type_: str, node_id: str | None = None, at: str | None = None, **data: Any) -> None:
        """Record a run event; delivered with the next checkpoint or live flush."""
        self._outbox.add(type_, node_id, at, **data)

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
        self._outbox = EventOutbox(self._workflow_id)
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
            if "seq" in self._state:  # resumed run: keep event numbering monotonic
                self._outbox.seq = int(self._state.get("event_seq", 0))
            self._emit("run.started", nodes=len(ir["order"]), resumed=bool(payload.get("checkpoint")))
            for index in range(self._state.get("superstep", 0), len(ir["supersteps"])):
                await self.execute_superstep(ir, ir["supersteps"][index], run_input, meta)
                self._state["superstep"] = index + 1
                await self._checkpoint()  # throttled
            self._state["status"] = "success"
            self._state["output"] = final_output(ir, self._state)
        except asyncio.CancelledError:
            self._state["status"] = "cancelled"
            self._emit("run.finished", status="cancelled", usage=self._state.get("usage"))
            await self._checkpoint(final=True)
            raise
        except _NodeFailed as failure:
            self._state["status"] = "failed"
            self._state["error"] = str(failure)
            self._emit("run.finished", status="failed", error=str(failure), usage=self._state.get("usage"))
            await self._checkpoint(final=True)
            raise ApplicationError(str(failure), type="NodeFailed", non_retryable=True) from None
        self._emit("run.finished", status="success", usage=self._state.get("usage"))
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
            self._emit("node.skipped", node_id, label=node.get("label"))
            return
        started = workflow.now()
        info: dict[str, Any] = {"status": "running", "started_at": started.isoformat()}
        nodes_state[node_id] = info
        self._emit("node.started", node_id, node_type=node["type"], label=node.get("label"))
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
            finished = workflow.now()
            info["finished_at"] = finished.isoformat()
            self._emit("node.finished", node_id, status=info["status"], error=info.get("error"),
                       duration_ms=round((finished - started).total_seconds() * 1000), usage=info.get("usage"))

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
            memory_scope=node["id"],
            on_usage=self._add_usage,
            on_step=self._checkpoint,  # progress is visible in the run while the agent works
            on_event=self._emit,
            on_flush=self._outbox.flush,  # live view: deliver events right after each LLM call
            run_workflow_id=self._workflow_id,
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
        self._emit("run.paused", node_id, message=node["config"].get("message", ""))
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
        self._emit("run.resumed", node_id, approved=bool(decision.get("approved")), comment=decision.get("comment"))
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
        self._state["event_seq"] = self._outbox.seq
        sending = list(self._outbox.pending)
        policy = (
            RetryPolicy(maximum_attempts=20, initial_interval=timedelta(seconds=1),
                        backoff_coefficient=2.0, maximum_interval=timedelta(seconds=30))
            if final else RetryPolicy(maximum_attempts=2, initial_interval=timedelta(milliseconds=500))
        )
        try:
            await workflow.execute_activity(
                checkpoint_state,
                args=[self._workflow_id, {**self._state, "events": sending}],
                start_to_close_timeout=CHECKPOINT_TIMEOUT,
                retry_policy=policy,
            )
            self._outbox.mark_delivered(sending)
        except ActivityError as error:  # events stay queued for the next checkpoint
            workflow.logger.warning("Checkpoint failed: %s", _error_message(error))


class AgentLoop:
    """The LLM <-> tools loop, shared by the agent node and by sub-agents.

    Every LLM call and tool call is its own activity, so the loop is durable:
    after a crash Temporal replays it without calling the model again. A tool
    whose node type is ``agent`` runs as a child ``AgentWorkflow`` one level
    deeper - that is how a supervisor agent delegates to worker agents.
    """

    def __init__(self, config, *, depth, current_input, run_input, node_outputs, labels, meta,
                 id_prefix, memory_scope="agent", on_usage=None, on_step=None, on_event=None, on_flush=None,
                 run_workflow_id=""):
        self.config = config
        self.on_event = on_event
        self.on_flush = on_flush
        self.run_workflow_id = run_workflow_id  # the RunGraphWorkflow whose event log everything goes to
        self.memory_scope = memory_scope  # stable path like "boss/researcher" (not the per-run workflow id)
        self.memory_note: str | None = None
        self.memory_session = ""
        self.memory_trimmed = 0
        self.memory_summarized = 0
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

    def _event(self, type_: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(type_, self.memory_scope, **data)

    def _charge(self, usage: dict[str, int] | None) -> None:
        if not usage:
            return
        for key in self.usage:
            self.usage[key] += int(usage.get(key, 0))
        if self.on_usage:
            self.on_usage(usage)

    # ----- memory (cluster 12) ------------------------------------------------
    def _memory_settings(self) -> dict[str, Any]:
        return self.config.get("memory") or {}

    def _memory_key(self) -> str | None:
        """Opaque key for this agent's conversation, or None when memory is off.

        Scoped by graph + agent path + session (agentforge_core.memory.memory_key),
        so different graphs/agents never share a conversation.
        """
        memory = self._memory_settings()
        if memory.get("type", "none") == "none":
            return None
        try:
            session = render_parameter(memory.get("session_id") or "{{ $trigger.session_id }}", self.context)
        except ExpressionError:
            session = None
        if session in (None, ""):
            self.memory_note = "memory skipped: no session_id for this run"
            return None
        self.memory_session = str(session)
        return memory_key(self.meta.get("graph_id"), self.memory_scope, self.memory_session)

    def _memory_meta(self) -> dict[str, Any]:
        return {"graph_id": self.meta.get("graph_id"), "scope": self.memory_scope, "session_id": self.memory_session}

    async def _load_history(self, key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """History to put in front of the prompt + the raw load (for summarising later)."""
        memory = self._memory_settings()
        kind = memory["type"]
        limit = int(memory.get("window", DEFAULT_MEMORY_WINDOW)) if kind == "window" else MAX_MEMORY_BUFFER
        try:
            loaded = await workflow.execute_activity(
                load_memory, args=[key, limit], start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        except ActivityError as error:  # memory is best-effort: answer without it rather than fail
            self.memory_note = f"memory unavailable: {_error_message(error)}"
            return [], {"summary": None, "messages": []}
        budget = int(memory.get("max_tokens", DEFAULT_MAX_TOKENS))
        raw = [{"role": m["role"], "content": m["content"]} for m in loaded["messages"]]
        history: list[dict[str, Any]] = []
        if kind == "summary" and loaded.get("summary"):
            summary_msg = {"role": "system", "content": "Summary of the earlier conversation:\n" + loaded["summary"]}
            budget -= estimate_tokens(summary_msg["content"])
            history.append(summary_msg)
        trimmed = trim_to_token_budget(raw, max(budget, 1))
        if len(trimmed) < len(raw):
            self.memory_trimmed = len(raw) - len(trimmed)
        return history + trimmed, loaded

    async def _save_exchange(self, key: str, user_text: str, answer: str) -> None:
        if self._memory_settings().get("redact_pii"):
            user_text, answer = redact_pii(user_text), redact_pii(answer)
        try:
            await workflow.execute_activity(
                save_memory,
                args=[key, [{"role": "user", "content": user_text}, {"role": "assistant", "content": answer}],
                      self._memory_meta()],
                start_to_close_timeout=timedelta(seconds=10), retry_policy=RetryPolicy(maximum_attempts=5),
            )
        except ActivityError as error:
            self.memory_note = f"memory not saved: {_error_message(error)}"

    async def _maybe_summarize(self, key: str, loaded: dict[str, Any], user_text: str, answer: str) -> None:
        """'summary' memory: when the verbatim tail outgrows the budget, fold the older part into the summary.

        Only messages that were LOADED (and so have ids) are folded; the newest
        `keep_recent` messages always stay verbatim.
        """
        memory = self._memory_settings()
        budget = int(memory.get("max_tokens", DEFAULT_MAX_TOKENS))
        keep_recent = int(memory.get("keep_recent", DEFAULT_KEEP_RECENT))
        stored = loaded.get("messages") or []
        tail_tokens = sum(estimate_tokens(m["content"]) for m in stored) + estimate_tokens(user_text) + estimate_tokens(answer)
        if tail_tokens <= budget:
            return
        foldable = stored[: max(0, len(stored) + 2 - keep_recent)]  # +2 = this run's exchange (not loaded, no ids)
        if not foldable:
            return
        try:
            reply = await workflow.execute_activity(
                llm_chat,
                {"credential_id": self.config.get("credential_id"), "base_url": self.config.get("base_url"),
                 "model": memory.get("summary_model") or self.config["model"],
                 "messages": summary_request(loaded.get("summary"), foldable), "max_tokens": 400},
                start_to_close_timeout=timedelta(seconds=90), retry_policy=RetryPolicy(maximum_attempts=2),
            )
            self._charge(reply["usage"])
            summary = (reply["message"].get("content") or "").strip()
            if memory.get("redact_pii"):
                summary = redact_pii(summary)
            if summary:
                await workflow.execute_activity(
                    save_summary, args=[key, summary, int(foldable[-1]["id"]), self._memory_meta()],
                    start_to_close_timeout=timedelta(seconds=10), retry_policy=RetryPolicy(maximum_attempts=5),
                )
                self.memory_summarized = len(foldable)
                self._event("memory.summarized", messages=len(foldable), tokens_before=tail_tokens)
        except ActivityError as error:  # a failed summary only means the history stays longer for now
            self.memory_note = f"summary not updated: {_error_message(error)}"

    async def run(self) -> dict[str, Any]:
        config = self.config
        messages: list[dict[str, Any]] = []
        if config.get("system"):
            messages.append({"role": "system", "content": str(render_parameter(config["system"], self.context))})
        user_text = str(render_parameter(config["prompt"], self.context))
        memory_key_ = self._memory_key()
        history, loaded = await self._load_history(memory_key_) if memory_key_ else ([], {})
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})
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
            asked_at = workflow.now()
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
            self._event(
                "llm.call", step=step, depth=self.depth, model=reply.get("model") or config["model"],
                input_tokens=reply["usage"].get("input_tokens", 0), output_tokens=reply["usage"].get("output_tokens", 0),
                latency_ms=round((workflow.now() - asked_at).total_seconds() * 1000),
                finish_reason=reply.get("finish_reason"),
                tool_calls=[c.get("function", {}).get("name") for c in calls],
                prompt_preview=_preview(messages[-2].get("content") if len(messages) > 1 else ""),
                response_preview=_preview(text),
            )
            if self.on_flush:  # show this step before (possibly slow) tool calls start
                await self.on_flush()
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
        if memory_key_ and text:
            await self._save_exchange(memory_key_, user_text, text)
            if self._memory_settings().get("type") == "summary":
                await self._maybe_summarize(memory_key_, loaded, user_text, text)
        if memory_key_ or self.memory_note:
            info = {"loaded_messages": len(history)}
            if self.memory_trimmed:
                info["trimmed_messages"] = self.memory_trimmed  # dropped to fit max_tokens
            if self.memory_summarized:
                info["summarized_messages"] = self.memory_summarized
            if self.memory_note:
                info["note"] = self.memory_note
            output["memory"] = info
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
        called_at = workflow.now()
        ok, error_text = True, None
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
            ok, error_text = False, _error_message(error)
            content = json.dumps({"error": error_text})
        self._event("tool.call", tool=name, step=step, ok=ok, error=error_text,
                    kind=(tool or {}).get("node", {}).get("type"),
                    duration_ms=round((workflow.now() - called_at).total_seconds() * 1000),
                    result_preview=_preview(content))
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
                "memory_scope": f"{self.memory_scope}/{name}",
                "run_workflow_id": self.run_workflow_id,
            },
            id=f"{self.id_prefix}/{name}-{call_id}",
            execution_timeout=timedelta(minutes=15),  # never outlive the step waiting for it
        )
        self._charge(result.get("usage"))
        undelivered = result.get("undelivered_events") or []
        if undelivered and self.run_workflow_id:  # the sub-agent could not deliver them itself: try once more
            try:
                await workflow.execute_activity(
                    emit_events, args=[self.run_workflow_id, result.get("event_source", ""), undelivered],
                    start_to_close_timeout=timedelta(seconds=5), retry_policy=RetryPolicy(maximum_attempts=3))
            except ActivityError as error:
                workflow.logger.warning("Sub-agent events lost: %s", _error_message(error))
        self._event("subagent.finished", tool=name, steps=result.get("steps"),
                    stopped_reason=result.get("stopped_reason"), usage=result.get("usage"))
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


def _preview(value: Any, limit: int = 500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


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
        self._outbox: EventOutbox | None = None

    @workflow.query(name="trace")
    def get_trace(self) -> dict[str, Any]:
        return {"steps": self._loop.trace, "usage": self._loop.usage} if self._loop else {}

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_workflow_id = payload.get("run_workflow_id") or ""
        self._outbox = EventOutbox(run_workflow_id, source=workflow.info().workflow_id)
        self._loop = AgentLoop(
            payload["config"],
            depth=int(payload["depth"]),
            current_input=payload.get("input"),
            run_input=payload.get("run_input"),
            node_outputs=payload.get("node_outputs") or {},
            labels=payload.get("labels") or {},
            meta=payload.get("run") or {},
            id_prefix=workflow.info().workflow_id,
            memory_scope=payload.get("memory_scope", "sub-agent"),
            on_event=self._outbox.add,
            on_flush=self._outbox.flush if run_workflow_id else None,
            run_workflow_id=run_workflow_id,
        )
        output = await self._loop.run()
        output["trace"] = self._loop.trace
        if run_workflow_id:
            await self._outbox.flush(force=True)
        if self._outbox.pending:  # could not deliver: hand them to the parent as a fallback
            output["undelivered_events"] = self._outbox.pending
            output["event_source"] = self._outbox.source
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

"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelRun,
  decideApproval,
  getRunDetails,
  getRunEvents,
  retryRun,
  runEventsStreamUrl,
  type RunDetails,
  type RunEvent,
} from "@/lib/api";
import StatusBadge from "../status-badge";

const TERMINAL = new Set(["success", "failed", "cancelled"]);
const EVENT_TYPES = ["run.started", "run.paused", "run.resumed", "run.finished", "node.started", "node.finished",
  "node.skipped", "llm.call", "tool.call", "subagent.finished", "memory.summarized"];

function ms(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return n < 1000 ? `${Math.round(n)} ms` : `${(n / 1000).toFixed(2)} s`;
}

function usd(value: unknown): string {
  return value === null || value === undefined ? "unpriced" : `$${Number(value).toFixed(6)}`;
}

function json(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function describe(event: RunEvent): string {
  const d = event.data;
  switch (event.type) {
    case "node.finished": return `${d.status}${d.duration_ms !== undefined ? ` in ${ms(d.duration_ms)}` : ""}${d.error ? ` - ${d.error}` : ""}`;
    case "llm.call": return `${d.model} · ${d.input_tokens}+${d.output_tokens} tokens · ${ms(d.latency_ms)} · ${usd(d.cost_usd)}`
      + (Array.isArray(d.tool_calls) && d.tool_calls.length ? ` · asked for ${(d.tool_calls as string[]).join(", ")}` : "");
    case "tool.call": return `${d.tool} ${d.ok ? "ok" : "failed"} in ${ms(d.duration_ms)}${d.error ? ` - ${d.error}` : ""}`;
    case "subagent.finished": return `${d.tool}: ${d.steps} steps, ${d.stopped_reason}`;
    case "run.finished": return String(d.status ?? "");
    case "run.paused": return `waiting for approval${d.message ? `: ${d.message}` : ""}`;
    case "run.resumed": return d.approved ? "approved" : "rejected";
    case "memory.summarized": return `${d.messages} messages folded into the summary`;
    default: return "";
  }
}

export default function RunInspectorPage() {
  const params = useParams<{ gid: string; rid: string }>();
  const graphId = Number(params.gid);
  const runId = Number(params.rid);
  const router = useRouter();
  const [details, setDetails] = useState<RunDetails | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [live, setLive] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [comment, setComment] = useState("");
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDetails(await getRunDetails(graphId, runId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load the run.");
    }
  }, [graphId, runId]);

  /** Details are re-fetched at most once per second while events stream in. */
  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      void refresh();
    }, 1000);
  }, [refresh]);

  useEffect(() => { void refresh(); }, [refresh]);

  // Finished runs: load the event log once. Active runs: stream it live (SSE resumes by itself via Last-Event-ID).
  const status = details?.run.status;
  useEffect(() => {
    if (!status) return;
    if (TERMINAL.has(status)) {
      if (!live) getRunEvents(graphId, runId).then(setEvents).catch(() => undefined);
      return;
    }
    const source = new EventSource(runEventsStreamUrl(graphId, runId), { withCredentials: true });
    setLive(true);
    const onEvent = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent;
      setEvents((current) => (current.some((e) => e.id === event.id) ? current : [...current, event]));
      scheduleRefresh();
    };
    for (const type of EVENT_TYPES) source.addEventListener(type, onEvent as EventListener);
    source.addEventListener("end", () => {
      source.close();
      setLive(false);
      void refresh();
    });
    return () => source.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status === undefined ? undefined : TERMINAL.has(status), graphId, runId]);

  async function act(action: () => Promise<unknown>, after?: (result: unknown) => void) {
    setBusy(true);
    setError("");
    try {
      const result = await action();
      after?.(result);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  const llmCalls = useMemo(() => events.filter((e) => e.type === "llm.call"), [events]);

  if (!details) {
    return <main className="container"><p className="muted">{error || "Loading run…"}</p></main>;
  }
  const { run, nodes, cost } = details;
  const waiting = run.waiting_for;

  return (
    <main className="container run-inspector">
      <div className="page-head">
        <div>
          <h1 style={{ margin: 0 }}>Run #{run.id} <StatusBadge status={run.status} /> {live && <span className="badge wait">live</span>}</h1>
          <p className="muted">
            {run.trigger} · graph v{run.graph_version ?? "?"} · started {new Date(run.created_at).toLocaleString()}
            {run.duration_ms !== null && ` · took ${ms(run.duration_ms)}`}
            {run.retry_of_run_id && <> · retry of <Link href={`/graphs/${graphId}/runs/${run.retry_of_run_id}`}>#{run.retry_of_run_id}</Link></>}
          </p>
        </div>
        <div className="header-right">
          <Link className="btn" href={`/graphs/${graphId}/runs`}>All runs</Link>
          <Link className="btn" href={`/editor/${graphId}`}>Editor</Link>
          {!TERMINAL.has(run.status) && (
            <button className="btn" disabled={busy} onClick={() => void act(() => cancelRun(graphId, runId))}>Cancel run</button>
          )}
          {details.can_retry && (
            <button className="btn primary" disabled={busy}
                    onClick={() => void act(() => retryRun(graphId, runId),
                      (r) => router.push(`/graphs/${graphId}/runs/${(r as { id: number }).id}`))}>
              Retry from failed node
            </button>
          )}
        </div>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}

      {waiting && run.status === "paused" && (
        <section className="panel-box approval-box">
          <h2>Waiting for approval</h2>
          <p>Node <code>{waiting.node_id}</code>{waiting.message ? `: ${waiting.message}` : ""}</p>
          <input className="input-field" placeholder="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="header-right">
            <button className="btn primary" disabled={busy} onClick={() => void act(() => decideApproval(graphId, runId, waiting.node_id, true, comment))}>Approve</button>
            <button className="btn" disabled={busy} onClick={() => void act(() => decideApproval(graphId, runId, waiting.node_id, false, comment))}>Reject</button>
          </div>
        </section>
      )}

      <section className="stat-row">
        <div className="stat"><span className="muted">LLM calls</span><strong>{cost.llm_calls}</strong></div>
        <div className="stat"><span className="muted">Tokens (in + out)</span><strong>{cost.input_tokens} + {cost.output_tokens}</strong></div>
        <div className="stat"><span className="muted">Cost</span><strong>{usd(cost.total_cost_usd)}</strong></div>
        <div className="stat"><span className="muted">Events</span><strong>{events.length}</strong></div>
      </section>
      {cost.unpriced_models.length > 0 && (
        <p className="muted">No price set for {cost.unpriced_models.join(", ")} - add it to MODEL_PRICES to see its cost.</p>
      )}

      {run.error && <section className="panel-box"><h2>Error</h2><pre className="code-block err-text">{run.error}</pre></section>}
      {run.status === "success" && (
        <section className="panel-box"><h2>Output</h2><pre className="code-block">{json(run.output)}</pre></section>
      )}

      <section className="panel-box">
        <h2>Steps</h2>
        <table className="run-table">
          <thead><tr><th>Node</th><th>Status</th><th>Time</th><th>Output / error</th></tr></thead>
          <tbody>
            {nodes.map((node) => (
              <tr key={node.node_id}>
                <td><code>{node.node_id}</code></td>
                <td><StatusBadge status={node.status} /></td>
                <td>{ms(node.duration_ms)}</td>
                <td>
                  {node.error ? <span className="err-text">{node.error}</span> : node.output !== null && node.output !== undefined ? (
                    <details><summary className="muted">show</summary><pre className="code-block">{json(node.output)}</pre></details>
                  ) : <span className="muted">-</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {llmCalls.length > 0 && (
        <section className="panel-box">
          <h2>LLM calls</h2>
          {llmCalls.map((call) => (
            <details key={call.id} className="llm-call">
              <summary>
                <code>{call.node_id}</code> step {String(call.data.step)} · {describe(call)}
              </summary>
              <div className="llm-previews">
                <div><span className="muted">Prompt (preview)</span><pre className="code-block">{String(call.data.prompt_preview ?? "")}</pre></div>
                <div><span className="muted">Reply (preview)</span><pre className="code-block">{String(call.data.response_preview ?? "")}</pre></div>
              </div>
            </details>
          ))}
        </section>
      )}

      <section className="panel-box">
        <h2>Timeline {live && <span className="muted">(updating live)</span>}</h2>
        <ol className="timeline">
          {events.map((event) => (
            <li key={event.id}>
              <span className="muted tl-time">{event.at ? new Date(event.at).toLocaleTimeString() : ""}</span>
              <span className={`tl-type tl-${event.type.replace(".", "-")}`}>{event.type}</span>
              {event.node_id && <code>{event.node_id}</code>}
              <span className="muted">{describe(event)}</span>
            </li>
          ))}
          {events.length === 0 && <li className="muted">No events recorded{TERMINAL.has(run.status) ? " (older runs may have been trimmed by retention)" : " yet"}.</li>}
        </ol>
      </section>

      <section className="panel-box">
        <details><summary>Run input</summary><pre className="code-block">{json(run.input)}</pre></details>
      </section>
    </main>
  );
}

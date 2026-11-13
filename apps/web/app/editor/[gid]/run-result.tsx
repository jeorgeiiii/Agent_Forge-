"use client";
/**
 * Run result panel - opens over the canvas right after ▶ Run, so you never leave the editor.
 * Checks the run every second until it finishes, then shows:
 *   - the answer in plain text when the output has one (Wikipedia extract, AI text, message ...),
 *   - each block's status (the canvas blocks also turn green / red via onNodeStatus),
 *   - the full output as JSON (folded) and a link to the full run page.
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { getRunDetails, type RunDetails } from "@/lib/api";
import { ResultView } from "../../run-view";

const ACTIVE = new Set(["queued", "running", "waiting", "pending"]);
export default function RunResult({ graphId, runId, onClose, onNodeStatus }: {
  graphId: number; runId: number; onClose: () => void; onNodeStatus: (statuses: Record<string, string>) => void;
}) {
  const [details, setDetails] = useState<RunDetails | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function check() {
      try {
        const next = await getRunDetails(graphId, runId);
        if (stopped) return;
        setDetails(next);
        onNodeStatus(Object.fromEntries(next.nodes.map((n) => [n.node_id, n.status])));
        if (ACTIVE.has(next.run.status)) timer = setTimeout(check, 1000);
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : "Could not load the run.");
      }
    }
    void check();
    return () => { stopped = true; clearTimeout(timer); };
  }, [graphId, runId, onNodeStatus]);

  const status = details?.run.status ?? "queued";
  const running = ACTIVE.has(status);

  return createPortal(
    <aside className="rr-panel" aria-live="polite" aria-label={`Run #${runId} result`}>
      <header className="rr-head">
        <strong>Run #{runId}</strong>
        <span className={`badge ${status === "success" ? "ok" : running ? "wait" : "err"}`}>
          {running ? (status === "queued" ? "waiting to start…" : "running…") : status}
        </span>
        {details?.run.duration_ms != null && <span className="muted help-small">{(details.run.duration_ms / 1000).toFixed(1)} s</span>}
        <button type="button" className="rr-close" onClick={onClose} aria-label="Close">×</button>
      </header>

      {error && <p className="form-error">{error}</p>}
      {status === "queued" && details && (
        <p className="muted help-small">Stuck here? The worker or Temporal is not running (see the setup steps).</p>
      )}
      {details?.run.error && <p className="form-error">{details.run.error}</p>}

      {details && !running && !details.run.error && <ResultView output={details.run.output} />}

      {details && details.nodes.length > 0 && (
        <ul className="rr-steps">
          {details.nodes.map((n) => (
            <li key={n.node_id} className={`rr-step ${n.status}`}>
              <span>{n.status === "success" ? "✓" : ACTIVE.has(n.status) ? "…" : "✕"}</span>
              <span>{n.node_id}</span>
              <span className="muted">{n.duration_ms != null ? `${n.duration_ms} ms` : ""}</span>
              {n.error && <small className="form-error">{n.error}</small>}
            </li>
          ))}
        </ul>
      )}

      {!running && details && (
        <Link className="btn" href={`/graphs/${graphId}/runs/${runId}`}>Full details (timeline, cost) →</Link>
      )}
    </aside>,
    document.body,
  );
}

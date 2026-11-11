"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { listRunsPage, type RunSummary } from "@/lib/api";
import StatusBadge from "./status-badge";

const FILTERS = [
  { label: "All", value: "" },
  { label: "Running", value: "queued,running,paused" },
  { label: "Succeeded", value: "success" },
  { label: "Failed", value: "failed,cancelled" },
];

function duration(run: RunSummary): string {
  if (!run.finished_at) return "-";
  const ms = new Date(run.finished_at).getTime() - new Date(run.created_at).getTime();
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export default function RunHistoryPage() {
  const graphId = Number(useParams<{ gid: string }>().gid);
  const [filter, setFilter] = useState("");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (status: string, beforeId: number | null) => {
    setLoading(true);
    setError("");
    try {
      const page = await listRunsPage(graphId, { status, beforeId });
      setRuns((current) => (beforeId ? [...current, ...page.runs] : page.runs));
      setCursor(page.nextCursor);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load runs.");
    } finally {
      setLoading(false);
    }
  }, [graphId]);

  useEffect(() => { void load(filter, null); }, [filter, load]);

  return (
    <main className="container">
      <div className="page-head">
        <div>
          <h1 style={{ margin: 0 }}>Run history</h1>
          <p className="muted">Every time workflow #{graphId} ran: when, whether it worked, and how long it took. Click a run to see its steps.</p>
        </div>
        <Link className="btn" href={`/editor/${graphId}`}>Back to editor</Link>
      </div>
      <div className="filter-row" role="tablist" aria-label="Filter runs">
        {FILTERS.map((f) => (
          <button key={f.label} role="tab" aria-selected={filter === f.value}
                  className={`btn ${filter === f.value ? "primary" : ""}`} onClick={() => setFilter(f.value)}>
            {f.label}
          </button>
        ))}
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="list">
        {!loading && runs.length === 0 && !error && <p className="muted">No runs yet. Use “Run” in the editor.</p>}
        {runs.map((run) => (
          <Link key={run.id} href={`/graphs/${graphId}/runs/${run.id}`} className="card">
            <div>
              <strong>Run #{run.id}</strong> <StatusBadge status={run.status} />
              <div className="muted">
                {run.trigger_kind}{run.retry_of_run_id ? ` · retry of #${run.retry_of_run_id}` : ""} · v{run.graph_version ?? "?"}
              </div>
              {run.error && <div className="form-error run-error-line">{run.error}</div>}
            </div>
            <div className="muted" style={{ textAlign: "right" }}>
              <div>{new Date(run.created_at).toLocaleString()}</div>
              <div>{duration(run)}</div>
            </div>
          </Link>
        ))}
      </div>
      {cursor && (
        <button className="btn wide" style={{ marginTop: 12 }} disabled={loading} onClick={() => void load(filter, cursor)}>
          {loading ? "Loading…" : "Load older runs"}
        </button>
      )}
    </main>
  );
}

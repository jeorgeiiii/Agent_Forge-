"use client";
/**
 * Simple "Use" page - /use/<workflow id>
 * For people who just want the answer, not the editor: fill in the boxes, press one button,
 * read the result (plain text or a table). No canvas, no JSON, no run IDs.
 *
 * Flow: load the workflow -> one box per {{ $input.x }} it uses -> start a run -> check it every second
 * ("Working… step 2 of 3") -> show the result with <ResultView>. "Run again" keeps the inputs.
 */
import Link from "next/link";
import { use, useEffect, useRef, useState } from "react";
import { getGraph, getRunDetails, startRun, type Graph, type RunDetails } from "@/lib/api";
import { DatabaseTables, findInputNames, guessValue, ResultView } from "../../run-view";

const ACTIVE = new Set(["queued", "running", "waiting", "pending"]);

/** Plain-words progress line from the run details. */
function progress(details: RunDetails | null, total: number): string {
  if (!details || details.run.status === "queued") return "Getting ready…";
  if (details.run.status === "waiting") return details.run.waiting_for?.message || "Waiting for someone to approve…";
  const done = details.nodes.filter((n) => n.status === "success").length;
  const current = details.nodes.find((n) => ACTIVE.has(n.status));
  return `Working… step ${Math.min(done + 1, total)} of ${total}${current ? ` (${current.node_id})` : ""}`;
}

export default function UsePage({ params }: { params: Promise<{ gid: string }> }) {
  const { gid } = use(params);
  const graphId = Number(gid);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [details, setDetails] = useState<RunDetails | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    getGraph(graphId).then(setGraph).catch((e) => setError(e instanceof Error ? e.message : "Could not open this workflow."));
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [graphId]);

  const labels = Object.fromEntries((graph?.definition.nodes ?? []).map((n) => [n.id, n.label || n.id]));
  const inputs = graph ? findInputNames(graph.definition.nodes.map((n) => n.config)) : [];
  const steps = graph?.definition.nodes.length ?? 0;

  /** Starts the run, then checks it every second until it is finished. */
  async function run(event: React.FormEvent) {
    event.preventDefault();
    const missing = inputs.filter((n) => !(values[n] ?? "").trim());
    if (missing.length) return setError(`Please fill in: ${missing.map((m) => m.replace(/_/g, " ")).join(", ")}.`);
    setError(""); setDetails(null); setRunning(true);
    try {
      const started = await startRun(graphId, Object.fromEntries(inputs.map((n) => [n, guessValue(values[n] ?? "")])));
      const check = async () => {
        try {
          const next = await getRunDetails(graphId, started.id);
          setDetails(next);
          if (ACTIVE.has(next.run.status)) timer.current = setTimeout(check, 1000);
          else setRunning(false);
        } catch (e) {
          setError(e instanceof Error ? e.message : "Lost track of the run."); setRunning(false);
        }
      };
      await check();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start."); setRunning(false);
    }
  }

  const status = details?.run.status;
  const finished = details && !ACTIVE.has(details.run.status);
  const stuck = running && status === "queued" && details && Date.now() - new Date(details.run.created_at).getTime() > 15000;

  return (
    <main className="container use-page">
      {!graph && !error && <p className="muted">Loading…</p>}
      {graph && (
        <>
          <h1>{graph.name}</h1>
          {graph.description && <p className="muted">{graph.description}</p>}

          <form className="panel-box stack use-form" onSubmit={(e) => void run(e)}>
            {inputs.length === 0 && <p className="muted">No information needed - just press the button.</p>}
            {inputs.map((name, index) => (
              <label key={name} className="field">
                <span>{name.replace(/_/g, " ")}</span>
                <input className="input-field" value={values[name] ?? ""} autoFocus={index === 0} disabled={running}
                       onChange={(e) => setValues({ ...values, [name]: e.target.value })} />
              </label>
            ))}
            <button className="btn primary use-go" type="submit" disabled={running}>
              {running ? "Working…" : finished ? "▶ Run again" : "▶ Run"}
            </button>
          </form>
        </>
      )}

      {error && <p className="form-error" role="alert">{error}</p>}

      {running && (
        <div className="panel-box use-progress" role="status">
          <span className="use-spinner" aria-hidden="true" /> {progress(details, steps)}
          {stuck && <p className="muted help-small">This is taking long to start. Ask your admin to check that AgentForge&apos;s worker is running.</p>}
        </div>
      )}

      {finished && status === "success" && (
        <section className="panel-box use-result" aria-live="polite">
          <h2>Result</h2>
          {/* the AI's / last step's answer - unless it is itself a database table (shown exactly below) */}
          {!(details!.run.output && typeof details!.run.output === "object" && "columns" in (details!.run.output as object)) && (
            <ResultView output={details!.run.output} />
          )}
          <DatabaseTables steps={details!.nodes} labels={labels} />
        </section>
      )}

      {finished && status !== "success" && (
        <section className="panel-box use-result danger-zone" aria-live="polite">
          <h2>It didn&apos;t work</h2>
          <p>{details!.run.error || details!.nodes.find((n) => n.error)?.error || `The run ended as “${status}”.`}</p>
          <p className="muted help-small">Check the information you typed and try again. If it keeps failing, ask the person who built this workflow.</p>
        </section>
      )}

      {graph && <p className="muted help-small use-footer">Built this workflow? <Link href={`/editor/${graphId}`}>Open it in the editor</Link></p>}
    </main>
  );
}

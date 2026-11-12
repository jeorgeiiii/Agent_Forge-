"use client";
/**
 * Activity log tab - /settings/activity   (owners only)
 * Who did what and when, in plain words, with filters, "load more", a tamper check
 * (re-checks the fingerprint chain) and a spreadsheet (CSV) download.
 */
import { useCallback, useEffect, useState } from "react";
import { ApiError, auditExportUrl, listAudit, verifyAudit, type AuditEvent } from "@/lib/api";
import { PAGE_HELP, actionLabel } from "@/lib/page-help";
import { useSession } from "@/lib/session";
import RoleHelpBox from "../../role-help-box";

const AREAS = [
  { id: "", label: "Everything" },
  { id: "auth.", label: "Sign-ins" },
  { id: "graph.", label: "Workflows" },
  { id: "run.", label: "Runs" },
  { id: "credential.", label: "Saved keys" },
  { id: "file.", label: "Files" },
  { id: "member.", label: "People" },
  { id: "workspace.", label: "Workspace" },
];

export default function ActivityPage() {
  const { workspace } = useSession();
  const ws = workspace!.id;
  const [area, setArea] = useState("");
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [check, setCheck] = useState<{ ok: boolean; text: string } | null>(null);

  /** Loads the first page (reset) or the next page (append). */
  const load = useCallback(async (beforeId: number | null) => {
    setError("");
    try {
      const page = await listAudit(ws, { action: area || undefined, outcome: onlyProblems ? "denied" : undefined, beforeId });
      setEvents((prev) => (beforeId && prev ? [...prev, ...page.events] : page.events));
      setNext(page.next);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 403 ? "Only workspace owners can see the activity log." : e instanceof Error ? e.message : "Could not load.");
      setEvents([]);
    }
  }, [ws, area, onlyProblems]);
  useEffect(() => { setEvents(null); void load(null); }, [load]);

  async function runCheck() {
    try {
      const r = await verifyAudit(ws);
      setCheck(r.valid
        ? { ok: true, text: `✔ All ${r.checked} entries checked - nothing was changed or removed.` }
        : { ok: false, text: `✖ Problem found${r.broken_at_id ? ` at entry #${r.broken_at_id}` : ""}: ${r.reason ?? "the chain is broken"}.` });
    } catch (e) { setCheck({ ok: false, text: e instanceof Error ? e.message : "Check failed." }); }
  }

  return (
    <>
      <RoleHelpBox help={PAGE_HELP.audit} />
      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="page-head" style={{ marginTop: 12 }}>
        <div className="filter-row" style={{ margin: 0 }}>
          {AREAS.map((a) => (
            <button key={a.id} type="button" className={`ge-role-chip${area === a.id ? " active" : ""}`} onClick={() => setArea(a.id)}>{a.label}</button>
          ))}
          <label className="ge-role-chip"><input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} /> Only blocked attempts</label>
        </div>
        <div className="row-actions">
          <button className="btn" type="button" onClick={() => void runCheck()} title="Proves no entry was edited or deleted">🛡 Check for tampering</button>
          <a className="btn" href={auditExportUrl(ws)} download>⬇ Download (CSV)</a>
        </div>
      </div>
      {check && <p className={check.ok ? "ge-ok" : "form-error"} role="status">{check.text}</p>}

      <section className="panel-box">
        {events === null && <p className="muted">Loading…</p>}
        {events?.length === 0 && !error && <p className="muted">Nothing recorded for this filter.</p>}
        {events && events.length > 0 && (
          <table className="run-table">
            <thead><tr><th>When</th><th>Who</th><th>What</th><th>Details</th></tr></thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td className="muted nowrap">{new Date(e.at).toLocaleString()}</td>
                  <td>{e.actor.label || e.actor.type}</td>
                  <td>
                    {actionLabel(e.action)}
                    {e.outcome !== "success" && <span className="badge err" style={{ marginLeft: 6 }}>{e.outcome}</span>}
                  </td>
                  <td className="muted">{describe(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {next && <button className="btn wide" style={{ marginTop: 12 }} type="button" onClick={() => void load(next)}>Load older entries</button>}
      </section>
    </>
  );
}

/** A short, readable line from the event details (names first, ids as a fallback). */
function describe(e: AuditEvent): string {
  const d = e.details ?? {};
  const name = (d.name ?? d.email ?? d.role) as string | undefined;
  const parts = [name ? String(name) : "", e.resource.type && e.resource.id ? `${e.resource.type} #${e.resource.id}` : ""];
  if (e.ip) parts.push(`from ${e.ip}`);
  return parts.filter(Boolean).join(" · ");
}

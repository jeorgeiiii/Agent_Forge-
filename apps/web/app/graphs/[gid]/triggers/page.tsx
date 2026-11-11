"use client";
/**
 * Triggers page - /graphs/[gid]/triggers
 * Shows how this workflow starts by itself (web address, timetable, chat), and lets you
 * Publish (make the latest saved version live), turn triggers on/off, or Unpublish.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useState } from "react";
import {
  ApiError, getGraphStatus, listTriggers, publishGraph, setGraphActive, unpublishGraph,
  type GraphStatus, type Trigger,
} from "@/lib/api";
import { nodeHelp } from "@/lib/help";
import { PAGE_HELP, TRIGGER_KINDS } from "@/lib/page-help";
import { roleHelp, useRole } from "@/lib/roles";
import { useSession } from "@/lib/session";
import RoleHelpBox from "../../../role-help-box";

export default function TriggersPage({ params }: { params: Promise<{ gid: string }> }) {
  const graphId = Number(use(params).gid);
  const router = useRouter();
  const { status, workspace } = useSession();
  const canEdit = status !== "signed-in" || workspace?.role !== "viewer";

  const [graph, setGraph] = useState<GraphStatus | null>(null);
  const [triggers, setTriggers] = useState<Trigger[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  /** Loads the workflow's live status and its triggers. */
  const load = useCallback(() => {
    Promise.all([getGraphStatus(graphId), listTriggers(graphId)])
      .then(([g, t]) => { setGraph(g); setTriggers(t); })
      .catch((reason) => {
        if (reason instanceof ApiError && reason.status === 401) return router.replace(`/login?next=/graphs/${graphId}/triggers`);
        setError(reason instanceof Error ? reason.message : "Could not load triggers.");
        setTriggers([]);
      });
  }, [graphId, router]);

  useEffect(() => { if (status !== "loading") load(); }, [status, load]);

  /** Runs one action (publish / on / off / unpublish), then shows the result and reloads. */
  async function act(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError("");
    try { await action(); setNotice(done); load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "That didn't work."); }
    finally { setBusy(false); }
  }

  const published = graph?.published_version != null;

  return (
    <main className="container">
      <div className="page-head">
        <div>
          <h1 style={{ margin: 0 }}>Triggers</h1>
          <p className="muted">{graph ? graph.name : "Loading…"} · <Link href={`/editor/${graphId}`}>back to editor</Link></p>
        </div>
      </div>
      <RoleHelpBox help={PAGE_HELP.triggers} />

      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="ge-ok" role="status">{notice}</p>}

      <section className="panel-box stack">
        <h2>Is it live?</h2>
        <p>
          {published
            ? <>Version <strong>{graph?.published_version}</strong> is published. Triggers are <strong className={graph?.is_active ? "ge-ok" : "err-text"}>{graph?.is_active ? "on" : "off"}</strong>.</>
            : <>Not published yet - triggers do nothing until you press <strong>Publish</strong>.</>}
        </p>
        {canEdit && (
          <div className="row-actions">
            <button className="btn primary" type="button" disabled={busy} onClick={() => void act(() => publishGraph(graphId), "Published - the latest saved version is now live.")}
                    title="Make the latest saved version live and create its web addresses / timetables">
              {published ? "Publish latest version" : "Publish"}
            </button>
            {published && (graph?.is_active
              ? <button className="btn" type="button" disabled={busy} onClick={() => void act(() => setGraphActive(graphId, false), "Triggers paused.")} title="Pause: nothing starts by itself until you turn it on again">Pause triggers</button>
              : <button className="btn" type="button" disabled={busy} onClick={() => void act(() => setGraphActive(graphId, true), "Triggers turned on.")}>Turn triggers on</button>)}
            {published && <button className="btn danger" type="button" disabled={busy} onClick={() => void act(() => unpublishGraph(graphId), "Unpublished - the workflow is no longer live.")} title="Stop being live and remove its web addresses and timetables">Unpublish</button>}
          </div>
        )}
        <small className="muted">Saved a change in the editor? Press Publish again so the live version gets it.</small>
      </section>

      <div className="list">
        {triggers === null && <p className="muted">Loading…</p>}
        {triggers?.length === 0 && (
          <p className="muted">
            No triggers yet. In the editor, start the workflow with a <strong>Webhook</strong>, <strong>Schedule</strong> or <strong>Chat</strong> block, save, then Publish here.
          </p>
        )}
        {triggers?.map((t) => <TriggerCard key={t.id} trigger={t} onCopied={setNotice} />)}
      </div>
    </main>
  );
}

/** One trigger: what kind it is, whether it is on, its address or timetable, and a role-based example. */
function TriggerCard({ trigger, onCopied }: { trigger: Trigger; onCopied: (text: string) => void }) {
  const [role] = useRole();
  const kind = TRIGGER_KINDS[trigger.kind] ?? { label: trigger.kind, icon: "⚡", helpType: "" };
  const help = kind.helpType ? roleHelp(nodeHelp(kind.helpType), kind.helpType, role) : null;
  const timetable = trigger.config.cron ? `cron ${String(trigger.config.cron)}`
    : trigger.config.interval_seconds ? `every ${String(trigger.config.interval_seconds)} seconds` : null;

  async function copy() {
    try { await navigator.clipboard.writeText(trigger.url ?? ""); onCopied("Address copied - paste it into the other app's settings."); }
    catch { onCopied("Could not copy - select the address and copy it by hand."); }
  }

  return (
    <div className="panel-box stack">
      <div className="page-head">
        <strong>{kind.icon} {kind.label} <span className="muted">· block “{trigger.node_id}”</span></strong>
        <span className={`badge ${trigger.active ? "ok" : "wait"}`}>{trigger.active ? "on" : "off"}</span>
      </div>
      {trigger.url && (
        <div className="copy-row">
          <code className="code-inline">{trigger.url}</code>
          <button className="btn" type="button" onClick={() => void copy()}>Copy address</button>
        </div>
      )}
      {timetable && <p>Runs: <strong>{timetable}</strong></p>}
      {help && (
        <div className="ge-help-card">
          <p><strong>Example:</strong> {help.example}</p>
          <p><strong>How to set it up:</strong> {help.where}</p>
        </div>
      )}
    </div>
  );
}

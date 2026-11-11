"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, createGraph, listGraphs, type Graph } from "@/lib/api";
import { BUTTON_HELP } from "@/lib/help";
import { ROLES, useRole } from "@/lib/roles";
import { useSession } from "@/lib/session";

const defaultGraphDefinition = {
  nodes: [
    { id: "start", type: "start", config: {} },
    { id: "message", type: "template", config: { template: "Hello {{ input.name }}!" } },
    { id: "end", type: "end", config: {} },
  ],
  edges: [
    { source: "start", target: "message" },
    { source: "message", target: "end" },
  ],
};

export default function Home() {
  const router = useRouter();
  const { status, workspace } = useSession();
  const [api, setApi] = useState<"checking" | "ok" | "down">("checking");
  const [graphs, setGraphs] = useState<Graph[] | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [role, setRole] = useRole();

  useEffect(() => {
    fetch("/api/health")
      .then((r) => setApi(r.ok ? "ok" : "down"))
      .catch(() => setApi("down"));
  }, []);

  useEffect(() => {
    if (status === "loading") return;
    setGraphs(null);
    setError("");
    // signed in -> the selected workspace; signed out -> single-user mode (graphs without a workspace)
    listGraphs(status === "signed-in" ? workspace?.id : undefined)
      .then(setGraphs)
      .catch((reason) => {
        if (reason instanceof ApiError && reason.status === 401) {
          router.replace("/login?next=/");  // AUTH_REQUIRED=true on the API
          return;
        }
        setError(reason instanceof Error ? reason.message : "Could not load graphs.");
        setGraphs([]);
      });
  }, [status, workspace?.id, router]);

  const canEdit = status !== "signed-in" || workspace?.role !== "viewer";

  async function create() {
    setCreating(true);
    setError("");
    try {
      const created = await createGraph({
        name: "New Agent Workflow",
        description: "Created in AgentForge",
        definition: defaultGraphDefinition,
        workspace_id: status === "signed-in" ? workspace?.id ?? null : null,
      });
      router.push(`/editor/${created.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create the graph.");
      setCreating(false);
    }
  }

  return (
    <main className="container">
      <div className="page-head">
        <div>
          <h1 style={{ margin: 0 }}>{workspace ? workspace.name : "Your agents"}</h1>
          <p className="muted">
            {status === "signed-in"
              ? `You are ${workspace?.role === "owner" ? "an owner" : `a ${workspace?.role}`} of this workspace.`
              : "Single-user mode - sign in to use workspaces and teams."}
          </p>
        </div>
        {canEdit && (
          <button className="btn primary" onClick={() => void create()} disabled={creating || status === "loading"}
                  title={BUTTON_HELP.newGraph}>
            {creating ? "Creating…" : "+ New workflow"}
          </button>
        )}
      </div>

      <p>
        API:{" "}
        <span className={`badge ${api === "ok" ? "ok" : api === "down" ? "err" : "wait"}`}>
          {api === "ok" ? "connected" : api === "down" ? "not reachable" : "checking"}
        </span>
      </p>
      {error && <p className="form-error" role="alert">{error}</p>}
      <p className="muted hint-line">
        A <strong>workflow</strong> is a set of blocks joined together - like a recipe your computer follows step by step.
        {canEdit && <> Press <strong>+ New workflow</strong> to start one, or click a workflow below to change it.</>}
      </p>

      <div className="home-role" role="group" aria-label="Your role">
        <span className="muted">What&apos;s your role? We&apos;ll show examples from your field:</span>
        {ROLES.map((r) => (
          <button key={r.id} type="button" className={`ge-role-chip${role === r.id ? " active" : ""}`}
                  aria-pressed={role === r.id} onClick={() => setRole(r.id)}>
            <span aria-hidden="true">{r.icon}</span> {r.label}
          </button>
        ))}
      </div>

      <div className="list">
        {graphs === null && <p className="muted">Loading…</p>}
        {graphs?.length === 0 && !error && <p className="muted">No workflows yet. Press + New workflow to make your first one.</p>}
        {graphs?.map((g) => (
          <Link key={g.id} href={`/editor/${g.id}`} className="card">
            <div>
              <strong>{g.name}</strong>
              <div className="muted">ID: {g.id} · {g.definition.nodes?.length ?? 0} nodes</div>
            </div>
            <span className="muted">{new Date(g.updated_at).toLocaleString()}</span>
          </Link>
        ))}
      </div>
    </main>
  );
}

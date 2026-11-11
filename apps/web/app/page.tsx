"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { listGraphs, createGraph, type Graph } from "@/lib/api";

type Item = { id: string; name: string; updatedAt: number; nodes: number };

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
  const [api, setApi] = useState<"checking" | "ok" | "down">("checking");
  const [items, setItems] = useState<Item[]>([]);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? setApi("ok") : setApi("down")))
      .catch(() => setApi("down"));

    // Fetch workflows from Postgres database
    listGraphs()
      .then((graphs: Graph[]) => {
        setItems(
          graphs.map((g) => ({
            id: String(g.id),
            name: g.name,
            updatedAt: new Date(g.updated_at).getTime(),
            nodes: g.definition.nodes?.length ?? 0,
          }))
        );
      })
      .catch((err) => {
        console.warn("Could not load graphs from Postgres, checking local storage as fallback:", err);
        // Seamless fallback to legacy local storage if DB is initially connecting
        try {
          const PREFIX = "agentforge:graph:";
          const found: Item[] = [];
          for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (!key?.startsWith(PREFIX)) continue;
            const g = JSON.parse(localStorage.getItem(key) ?? "{}");
            found.push({
              id: key.slice(PREFIX.length),
              name: g.name ?? "Untitled",
              updatedAt: g.updatedAt ?? 0,
              nodes: g.nodes?.length ?? 0,
            });
          }
          if (found.length > 0) {
            setItems(found.sort((a, b) => b.updatedAt - a.updatedAt));
          }
        } catch {
          /* ignore */
        }
      });
  }, []);

  async function create() {
    setCreating(true);
    try {
      const created = await createGraph({
        name: "New Agent Workflow",
        description: "Created in AgentForge",
        definition: defaultGraphDefinition,
      });
      router.push(`/editor/${created.id}`);
    } catch (err) {
      console.error("Failed to create graph in database:", err);
      // Fallback
      const id = Math.random().toString(36).slice(2, 10);
      router.push(`/editor/${id}`);
    } finally {
      setCreating(false);
    }
  }

  return (
    <main className="container">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 style={{ margin: 0 }}>Your agents</h1>
          <p className="muted">Workflows persisted in Postgres (JSONB storage).</p>
        </div>
        <button className="btn primary" onClick={create} disabled={creating}>
          {creating ? "Creating…" : "+ New graph"}
        </button>
      </div>

      <p>
        Database:{" "}
        <span className={`badge ${api === "ok" ? "ok" : api === "down" ? "err" : "wait"}`}>
          {api === "ok" ? "Postgres connected" : api === "down" ? "not reachable" : "checking"}
        </span>
      </p>

      <div className="list">
        {items.length === 0 && <p className="muted">No graphs yet. Create your first one.</p>}
        {items.map((g) => (
          <Link key={g.id} href={`/editor/${g.id}`} className="card">
            <div>
              <strong>{g.name}</strong>
              <div className="muted">ID: {g.id} · {g.nodes} nodes</div>
            </div>
            <span className="muted">
              {g.updatedAt ? new Date(g.updatedAt).toLocaleString() : ""}
            </span>
          </Link>
        ))}
      </div>
    </main>
  );
}
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createGraph, type GraphDefinition } from "@/lib/api";

const starterDefinition: GraphDefinition = {
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

export default function NewGraphForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const graph = await createGraph({ name, description: "", definition: starterDefinition });
      router.push(`/editor/${graph.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create graph.");
      setSaving(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2>Create a workflow</h2>
      <p>Start with a simple input-to-template graph and customize it in the editor.</p>
      <div className="field">
        <label htmlFor="graph-name">Workflow name</label>
        <input id="graph-name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={200} />
      </div>
      {error && <p className="message">{error}</p>}
      <button className="button" disabled={saving}>{saving ? "Creating…" : "Create workflow"}</button>
    </form>
  );
}

"use client";
/**
 * "Add integration tool" - shown under an AI Agent's tools box.
 * You pick the service, the action and the saved key; it appends a ready tool entry:
 *   {"name": "wikipedia_summary", "node": {"type": "wikipedia", "config": {"action": "summary"}}}
 * The AI then fills in only that action's details (title, message text …). It can never change
 * the action or the key. Actions that send or change something need "allow_writes" on the agent.
 */
import { useEffect, useState } from "react";
import { listIntegrations, type CredentialSummary, type Integration } from "@/lib/api";

type Tool = { name: string; node: { type: string; config: Record<string, unknown> } };

/** Tool names must be letters, digits, _ or - (max 64) and unique within the agent. */
function toolName(integration: string, action: string, taken: string[]): string {
  const base = `${integration}_${action}`.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 60);
  let name = base;
  for (let n = 2; taken.includes(name); n++) name = `${base}_${n}`;
  return name;
}

export default function IntegrationToolPicker({ tools, onAdd, credentials, allowWrites }: {
  tools: unknown; onAdd: (tools: Tool[]) => void; credentials: CredentialSummary[]; allowWrites: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<Integration[]>([]);
  const [service, setService] = useState("");
  const [action, setAction] = useState("");
  const [keyId, setKeyId] = useState("");
  const current = Array.isArray(tools) ? (tools as Tool[]) : [];

  useEffect(() => {
    if (open && !catalog.length) listIntegrations().then(setCatalog).catch(() => setCatalog([]));
  }, [open, catalog.length]);

  const chosen = catalog.find((i) => i.id === service);
  const chosenAction = chosen?.actions.find((a) => a.id === action);
  // keys named like the Integrations page names them ("Slack - main") are listed first
  const prefix = chosen?.name.split(" ")[0].toLowerCase() ?? "";
  const keys = [...credentials].sort((a, b) =>
    Number(b.name.toLowerCase().startsWith(prefix)) - Number(a.name.toLowerCase().startsWith(prefix)));

  /** Appends the tool entry and closes the picker. */
  function add() {
    if (!chosen || !chosenAction) return;
    const config: Record<string, unknown> = { action: chosenAction.id };
    if (chosen.needs_key) config.credential_id = Number(keyId);
    const name = toolName(chosen.id, chosenAction.id, current.map((t) => t.name));
    onAdd([...current, { name, node: { type: chosen.id, config } }]);
    setService(""); setAction(""); setKeyId(""); setOpen(false);
  }

  if (!open) {
    return <button type="button" className="btn nf-add-tool" onClick={() => setOpen(true)}>＋ Add integration tool</button>;
  }
  const ready = Boolean(chosen && chosenAction && (!chosen.needs_key || keyId));
  return (
    <div className="nf-tool-picker">
      <select className="nf-input" value={service} aria-label="Integration"
              onChange={(e) => { setService(e.target.value); setAction(""); setKeyId(""); }}>
        <option value="">- choose a service -</option>
        {catalog.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
      </select>
      {chosen && (
        <select className="nf-input" value={action} aria-label="Action" onChange={(e) => setAction(e.target.value)}>
          <option value="">- choose what it can do -</option>
          {chosen.actions.map((a) => <option key={a.id} value={a.id}>{a.name}{a.writes ? " (sends/changes)" : ""}</option>)}
        </select>
      )}
      {chosen?.needs_key && (
        <select className="nf-input" value={keyId} aria-label="Saved key" onChange={(e) => setKeyId(e.target.value)}>
          <option value="">- choose the saved key -</option>
          {keys.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
        </select>
      )}
      {chosen?.needs_key && credentials.length === 0 && (
        <small className="nf-help">No saved keys yet - connect {chosen.name} on the Integrations page first.</small>
      )}
      {chosenAction?.writes && !allowWrites && (
        <small className="nf-error">This action sends or changes something: also tick “allow writes” above, or the workflow will not save.</small>
      )}
      {chosenAction && (
        <small className="nf-help">
          {chosenAction.description} The AI fills in: {chosenAction.fields.map((f) => f.label).join(", ") || "nothing, it just runs"}.
        </small>
      )}
      <div className="row-actions">
        <button type="button" className="btn primary" disabled={!ready} onClick={add}>Add tool</button>
        <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}

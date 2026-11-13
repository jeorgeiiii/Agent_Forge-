"use client";
/**
 * Picker for the "Company database" block (data_source_id) and the "Add company database tools" button.
 * Loads the current workspace's connected databases itself, so the editor needs no extra wiring.
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { listDataSources, type DataSource } from "@/lib/api";
import { useSession } from "@/lib/session";

/** The workspace's connected databases ([] while loading or when there are none). */
export function useDataSources(): DataSource[] {
  const { workspace } = useSession();
  const [sources, setSources] = useState<DataSource[]>([]);
  useEffect(() => {
    if (workspace) listDataSources(workspace.id).then(setSources).catch(() => setSources([]));
  }, [workspace]);
  return sources;
}

export default function DataSourceSelect({ id, value, onChange }: {
  id: string; value: unknown; onChange: (value: number | undefined) => void;
}) {
  const sources = useDataSources();
  return (
    <>
      <select id={id} className="nf-input" value={value === undefined ? "" : String(value)}
              onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}>
        <option value="">- choose a database -</option>
        {sources.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.table_count} tables)</option>)}
      </select>
      {sources.length === 0 && (
        <small className="nf-help">No database connected yet - <Link href="/data">connect one on the Company data page</Link>.</small>
      )}
    </>
  );
}

type Tool = { name: string; node: { type: string; config: Record<string, unknown> } };

/** Gives an AI agent the three database tools at once: list tables, describe a table, run a SELECT. */
export function DatabaseToolsButton({ tools, onAdd }: { tools: unknown; onAdd: (tools: Tool[]) => void }) {
  const sources = useDataSources();
  const [open, setOpen] = useState(false);
  const current = Array.isArray(tools) ? (tools as Tool[]) : [];

  function add(source: DataSource) {
    const prefix = `db${source.id}`;
    const fresh = (["list_tables", "describe_table", "query"] as const)
      .map((action) => ({ name: `${prefix}_${action}`, node: { type: "database", config: { data_source_id: source.id, action } } }))
      .filter((t) => !current.some((c) => c.name === t.name));
    onAdd([...current, ...fresh]);
    setOpen(false);
  }

  if (!open) return <button type="button" className="btn nf-add-tool" onClick={() => setOpen(true)}>＋ Add company database tools</button>;
  return (
    <div className="nf-tool-picker">
      {sources.length === 0 && <small className="nf-help">No database connected yet - <Link href="/data">connect one first</Link>.</small>}
      {sources.map((s) => (
        <button key={s.id} type="button" className="btn" onClick={() => add(s)}>Use “{s.name}”</button>
      ))}
      <small className="nf-help">The AI gets 3 tools: see the tables, look at one table, and ask with a read-only SELECT.</small>
      <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
    </div>
  );
}

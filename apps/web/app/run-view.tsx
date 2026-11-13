"use client";
/**
 * Shared helpers for showing a workflow's result to people who don't read JSON.
 *   findInputNames(configs) - the {{ $input.x }} names a workflow asks for (one box per name)
 *   friendlyText(output)    - the most useful sentence in an output (AI answer, Wikipedia extract ...)
 *   <ResultView output/>    - text as text, lists / sheet rows as a table, links clickable, JSON only on request
 * Used by the simple "Use" page (/use/[gid]) and the editor's run result panel.
 */
import { useState } from "react";

// run input fields: {{ $trigger.x }} anywhere, or {{ $input.x }} (= the run input in the first block after Start)
const INPUT_REF = /\$(?:trigger|input)\.([A-Za-z_][A-Za-z0-9_]*)/g;
const TEXT_KEYS = ["text", "answer", "extract", "summary", "message", "content", "result", "description"];
const NESTED_KEYS = ["data", "json", "main", "body", "output"];
const MAX_ROWS = 50;
const MAX_COLUMNS = 8;

/** Input names used anywhere in the blocks' settings, in first-seen order. */
export function findInputNames(configs: unknown[]): string[] {
  const names = new Set<string>();
  for (const config of configs) {
    for (const match of JSON.stringify(config ?? {}).matchAll(INPUT_REF)) names.add(match[1]);
  }
  return [...names];
}

/** Text boxes give strings; "42", "true", "[1,2]" are turned into a number / boolean / list. */
export function guessValue(text: string): unknown {
  const trimmed = text.trim();
  if (/^(-?\d+(\.\d+)?|true|false|null|\[[\s\S]*\]|\{[\s\S]*\})$/.test(trimmed)) {
    try { return JSON.parse(trimmed); } catch { /* keep as text */ }
  }
  return text;
}

/** Finds the most useful human-readable text in an output (looks a few levels into data/json/main). */
export function friendlyText(output: unknown, depth = 0): string | null {
  if (typeof output === "string") return output.trim() ? output : null;
  if (!output || typeof output !== "object" || Array.isArray(output) || depth > 3) return null;
  const record = output as Record<string, unknown>;
  for (const key of TEXT_KEYS) {
    if (typeof record[key] === "string" && (record[key] as string).trim()) return record[key] as string;
  }
  for (const key of NESTED_KEYS) {
    const found = friendlyText(record[key], depth + 1);
    if (found) return found;
  }
  return null;
}

/** A list worth showing as a table: rows of cells (Sheets) or a list of objects (search results). */
function findTable(output: unknown, depth = 0): unknown[] | null {
  if (Array.isArray(output)) return output.length && output.every((r) => r && typeof r === "object") ? output : null;
  if (!output || typeof output !== "object" || depth > 3) return null;
  for (const key of [...NESTED_KEYS, "values", "files", "results", "items", "records", "pages", "organic"]) {
    const found = findTable((output as Record<string, unknown>)[key], depth + 1);
    if (found) return found;
  }
  return null;
}

function Cell({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="muted">-</span>;
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  if (/^https?:\/\/\S+$/.test(text)) {
    return <a href={text} target="_blank" rel="noreferrer noopener">{text.length > 40 ? text.slice(0, 40) + "…" : text}</a>;
  }
  return <>{text.length > 160 ? text.slice(0, 160) + "…" : text}</>;
}

function Table({ rows }: { rows: unknown[] }) {
  const shown = rows.slice(0, MAX_ROWS);
  const isGrid = shown.every((r) => Array.isArray(r)); // Google Sheets: first row = headings
  const header = isGrid
    ? (shown[0] as unknown[]).map(String)
    : [...new Set(shown.flatMap((r) => Object.keys(r as object)))].slice(0, MAX_COLUMNS);
  const body = isGrid ? shown.slice(1) : shown;
  return (
    <div className="rv-table-wrap">
      <table className="rv-table">
        <thead><tr>{header.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>
          {body.map((row, i) => (
            <tr key={i}>
              {header.map((h, j) => (
                <td key={j}><Cell value={isGrid ? (row as unknown[])[j] : (row as Record<string, unknown>)[h]} /></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > MAX_ROWS && <small className="muted">Showing the first {MAX_ROWS} of {rows.length}.</small>}
    </div>
  );
}

type QueryOutput = { columns: string[]; rows: Record<string, unknown>[]; row_count?: number; truncated?: boolean };

function isQueryOutput(value: unknown): value is QueryOutput {
  const v = value as QueryOutput | null;
  return !!v && typeof v === "object" && Array.isArray(v.columns) && Array.isArray(v.rows);
}

/** The exact rows each "Company database" step returned - straight from the database, never re-typed by the AI.
 * steps: run details nodes ({node_id, output}); labels: node id -> the block's name in the workflow. */
export function DatabaseTables({ steps, labels }: {
  steps: { node_id: string; output: unknown }[]; labels: Record<string, string>;
}) {
  const tables = steps.filter((s) => isQueryOutput(s.output));
  if (!tables.length) return null;
  return (
    <div className="rv-data">
      {tables.map((step) => {
        const data = step.output as QueryOutput;
        return (
          <details key={step.node_id} className="rv-data-block" open={tables.length <= 3}>
            <summary>
              <strong>{labels[step.node_id] ?? step.node_id}</strong>
              <span className="muted help-small"> · {data.rows.length} row{data.rows.length === 1 ? "" : "s"}
                {data.truncated ? " (more exist - only the first ones are shown)" : ""} · exact data from the database</span>
            </summary>
            {data.rows.length === 0 ? <p className="muted help-small">No rows.</p> : (
              <div className="rv-table-wrap">
                <table className="rv-table">
                  <thead><tr>{data.columns.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
                  <tbody>
                    {data.rows.slice(0, MAX_ROWS).map((row, i) => (
                      <tr key={i}>{data.columns.map((c) => <td key={c}><Cell value={row[c]} /></td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </details>
        );
      })}
    </div>
  );
}

export function ResultView({ output }: { output: unknown }) {
  const [showJson, setShowJson] = useState(false);
  const text = friendlyText(output);
  const table = findTable(output);
  const empty = output === null || output === undefined || (!text && !table && JSON.stringify(output) === "{}");
  return (
    <div className="rv">
      {text && <p className="rv-text">{text}</p>}
      {table && <Table rows={table} />}
      {empty && <p className="muted">The workflow finished but returned nothing to show.</p>}
      {!text && !table && !empty && <pre className="rv-json">{JSON.stringify(output, null, 2)}</pre>}
      {(text || table) && (
        <>
          <button type="button" className="link-button rv-toggle" onClick={() => setShowJson(!showJson)}>
            {showJson ? "Hide" : "Show"} all details
          </button>
          {showJson && <pre className="rv-json">{JSON.stringify(output, null, 2)}</pre>}
        </>
      )}
    </div>
  );
}

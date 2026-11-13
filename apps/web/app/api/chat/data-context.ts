/**
 * Company-database knowledge for the editor Copilot, so it writes SQL with REAL table and column names.
 *
 * 1. loadDataContext: finds the workflow's workspace, its connected databases, and - to stay small enough for
 *    the AI plan - only the tables that match words in the question / canvas (plus a name-only list of the rest).
 * 2. testDatabaseNodes: test-runs every "Company database" query the Copilot put on the canvas (read-only, through
 *    the API with the person's own session), so a broken query is caught before anyone presses Run.
 * Calls go to the API with the person's cookie: they only ever see databases their own workspace can see.
 */

const API_URL = (process.env.INTERNAL_API_URL ?? process.env.API_PROXY_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const MAX_DETAILED_TABLES = 10;
const MAX_TABLE_NAMES = 80;

type Source = {
  id: number; name: string; kind: string; description: string; table_count: number;
  dictionary?: { today?: string; notes?: string; concepts?: Record<string, { table: string; where?: string }> };
};
type Table = { name: string; columns: { name: string; type: string }[] };

async function api<T>(path: string, cookie: string, init?: RequestInit): Promise<T | null> {
  try {
    const response = await fetch(`${API_URL}/api${path}`, {
      ...init, cache: "no-store", signal: AbortSignal.timeout(15000),
      headers: { "Content-Type": "application/json", cookie, ...(init?.headers as Record<string, string>) },
    });
    return response.ok ? ((await response.json()) as T) : null;
  } catch {
    return null;
  }
}

/** "customers having min_balance_alert" -> customer, min, balance, alert ... (simple stems for matching). */
function words(text: string): Set<string> {
  return new Set(text.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 3)
    .map((w) => w.replace(/(ies)$/, "y").replace(/s$/, "")));
}

function score(table: Table, wanted: Set<string>): number {
  const name = words(table.name.replace(/_/g, " "));
  const cols = words(table.columns.map((c) => c.name.replace(/_/g, " ")).join(" "));
  let total = 0;
  for (const w of wanted) total += (name.has(w) ? 3 : 0) + (cols.has(w) ? 1 : 0);
  return total;
}

export async function loadDataContext(cookie: string, graphId: string | undefined, question: string,
                                      canvas: unknown): Promise<string> {
  if (!cookie || !graphId) return "";
  const graph = await api<{ workspace_id?: number | null }>(`/graphs/${encodeURIComponent(graphId)}`, cookie);
  if (!graph?.workspace_id) return "";
  const sources = await api<Source[]>(`/workspaces/${graph.workspace_id}/data-sources`, cookie);
  if (!sources?.length) return "";

  const canvasText = JSON.stringify(canvas ?? {});
  const usedIds = new Set([...canvasText.matchAll(/"data_source_id":\s*(\d+)/g)].map((m) => Number(m[1])));
  const wanted = words(question);
  // the database the question names, else the one already on the canvas, else all (small ones)
  const named = sources.filter((s) => words(s.name).size && [...words(s.name)].some((w) => wanted.has(w)));
  const focus = named.length ? named : sources.filter((s) => usedIds.has(s.id)).length
    ? sources.filter((s) => usedIds.has(s.id)) : sources;

  const lines: string[] = ["COMPANY DATABASES (read-only; use ONLY these real table/column names):"];
  for (const s of sources) {
    lines.push(`- data_source_id ${s.id}: "${s.name}" (${s.kind}, ${s.table_count} tables)` +
      (s.dictionary?.today ? `, today=${s.dictionary.today}` : "") + (s.description ? ` - ${s.description.slice(0, 160)}` : ""));
  }
  for (const s of focus.slice(0, 2)) {
    const schema = await api<{ tables: Table[] }>(`/data-sources/${s.id}/schema`, cookie);
    const tables = schema?.tables ?? [];
    if (!tables.length) continue;
    const ranked = [...tables].sort((a, b) => score(b, wanted) - score(a, wanted));
    const detailed = ranked.filter((t) => score(t, wanted) > 0).slice(0, MAX_DETAILED_TABLES);
    lines.push(`\nTables of "${s.name}" (data_source_id ${s.id}) that match the question, with ALL their columns:`);
    for (const t of detailed.length ? detailed : ranked.slice(0, 5)) {
      lines.push(`  ${t.name}(${t.columns.map((c) => c.name).join(", ")})`);
    }
    const others = tables.filter((t) => !detailed.includes(t)).map((t) => t.name);
    if (others.length) lines.push(`  Other tables (names only): ${others.slice(0, MAX_TABLE_NAMES).join(", ")}${others.length > MAX_TABLE_NAMES ? " …" : ""}`);
    if (s.dictionary?.notes) lines.push(`  Notes: ${s.dictionary.notes.slice(0, 300)}`);
  }
  lines.push("If the user names a column that is not in the table they mention, say which table really has it " +
             "(look at the columns above) instead of inventing it. Use a database block: {\"type\": \"database\", " +
             "\"config\": {\"data_source_id\": <id>, \"action\": \"query\", \"sql\": \"SELECT ... LIMIT 50\"}}. " +
             "Put run inputs in params with :name placeholders, e.g. \"params\": {\"email\": \"{{ $trigger.email }}\"}.");
  return lines.join("\n");
}

type GraphNode = { id: string; label?: string; type: string; config?: Record<string, unknown> };

/** Test-runs each Company database query (read-only). Returns problems like "List customers: column x does not exist". */
export async function testDatabaseNodes(cookie: string, graph: { nodes: GraphNode[] }): Promise<string[]> {
  const problems: string[] = [];
  for (const node of graph.nodes) {
    const config = node.config ?? {};
    if (node.type !== "database" || config.action !== "query" || typeof config.sql !== "string") continue;
    if (!config.data_source_id) {
      problems.push(`${node.label ?? node.id}: no database chosen (data_source_id).`);
      continue;
    }
    // placeholders get a harmless sample value - we only check that the SQL is valid for this database
    const params = Object.fromEntries(Object.keys((config.params as Record<string, unknown>) ?? {}).map((k) => [k, "0"]));
    const response = await fetch(`${API_URL}/api/data-sources/${Number(config.data_source_id)}/query`, {
      method: "POST", cache: "no-store", signal: AbortSignal.timeout(20000),
      headers: { "Content-Type": "application/json", cookie },
      body: JSON.stringify({ sql: config.sql, params }),
    }).catch(() => null);
    if (!response) {
      problems.push(`${node.label ?? node.id}: could not reach the database to test the query.`);
    } else if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      // a value like "0" can fail a date/number cast - that is not a broken query
      const detail = body?.detail ?? `HTTP ${response.status}`;
      if (!/invalid input syntax|date\/time field value/i.test(detail)) problems.push(`${node.label ?? node.id}: ${detail}`);
    }
  }
  return problems;
}

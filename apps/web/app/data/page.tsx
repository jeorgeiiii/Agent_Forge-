"use client";
/**
 * Company data page - /data
 * Owners connect the company's own database (READ-ONLY). AgentForge reads the list of tables so agents know
 * what is inside. Editors can browse tables and try a query; nothing here can change company data.
 *
 * Sections: what this is (help) -> connected databases -> connect form (owners) -> selected database:
 * tables + "Try a query".
 */
import { useCallback, useEffect, useState } from "react";
import {
  createDataSource, deleteDataSource, getDataSourceSchema, listDataSources, testDataSource, tryDataSourceQuery,
  type DataSource, type DataTable, type QueryResult,
} from "@/lib/api";
import { useSession } from "@/lib/session";
import { ResultView } from "../run-view";
import DictionaryEditor from "./dictionary-editor";

const READ_ONLY_USER_SQL = `-- PostgreSQL: a login that can only READ (run once as an admin)
CREATE USER agentforge_reader WITH PASSWORD 'choose-a-strong-password';
GRANT CONNECT ON DATABASE your_database TO agentforge_reader;
GRANT USAGE ON SCHEMA public TO agentforge_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agentforge_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agentforge_reader;`;

export default function CompanyDataPage() {
  const { status, workspace } = useSession();
  const ws = workspace?.id ?? null;
  const isOwner = workspace?.role === "owner";
  const canQuery = workspace?.role === "owner" || workspace?.role === "editor";
  const [sources, setSources] = useState<DataSource[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    if (ws === null) return;
    listDataSources(ws).then((list) => {
      setSources(list);
      setSelected((current) => current ?? list[0]?.id ?? null);
    }).catch((e) => setError(e instanceof Error ? e.message : "Could not load."));
  }, [ws]);
  useEffect(() => { if (status === "signed-in") load(); }, [status, load]);

  if (status === "signed-out") return <main className="container"><p>Please sign in.</p></main>;
  const current = sources?.find((s) => s.id === selected) ?? null;

  return (
    <main className="container">
      <h1>Company data</h1>
      <div className="help-box">
        <p><strong>Connect your company&apos;s database once.</strong> Your workflows and AI agents can then look things up in it
          - customers, orders, loans, properties - using the <strong>Company database</strong> block.
          AgentForge only <strong>reads</strong>: it can never change or delete your data.</p>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}

      <h2 className="cd-h2">Connected databases</h2>
      {sources === null && <p className="muted">Loading…</p>}
      {sources?.length === 0 && <p className="muted">No database connected yet{isOwner ? " - use the form below." : ". Ask a workspace owner to connect one."}</p>}
      <div className="cd-list">
        {sources?.map((s) => (
          <button key={s.id} type="button" className={`cd-source${s.id === selected ? " active" : ""}`} onClick={() => setSelected(s.id)}>
            <strong>{s.name}</strong>
            <span className="muted help-small">{s.address} · {s.table_count} tables</span>
            {s.last_error && <span className="form-error help-small">⚠ {s.last_error}</span>}
          </button>
        ))}
      </div>

      {current && <SourceDetails key={current.id} source={current} isOwner={isOwner} canQuery={canQuery}
                                 onChanged={load} onDeleted={() => { setSelected(null); load(); }} />}
      {isOwner && ws !== null && <ConnectForm workspaceId={ws} onDone={(id) => { setSelected(id); load(); }} />}
    </main>
  );
}

/** Owners: name + address + "what is in it" (the AI reads this) -> tested and saved. */
function ConnectForm({ workspaceId, onDone }: { workspaceId: number; onDone: (id: number) => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [maxRows, setMaxRows] = useState(200);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showHelp, setShowHelp] = useState(false);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const created = await createDataSource(workspaceId, { name, url, description, max_rows: maxRows });
      setName(""); setUrl(""); setDescription("");
      onDone(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not connect.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel-box stack cd-form" onSubmit={(e) => void save(e)}>
      <h2>Connect a database</h2>
      <label className="field"><span>Name</span>
        <input className="input-field" value={name} onChange={(e) => setName(e.target.value)} required maxLength={200}
               autoComplete="off" name="data-source-name" placeholder="Customer database" /></label>
      <label className="field"><span>Database address (kept encrypted, never shown again)</span>
        <input className="input-field" type="password" value={url} onChange={(e) => setUrl(e.target.value)} required
               autoComplete="new-password" name="data-source-address" data-1p-ignore data-lpignore="true" placeholder="postgresql://agentforge_reader:password@db.company.com:5432/sales" />
        <small className="muted">PostgreSQL (postgresql://…) or MySQL (mysql://…). Add <code>?sslmode=require</code> for databases on the internet.</small>
      </label>
      <label className="field"><span>What is in it? (the AI reads this)</span>
        <textarea className="input-field" rows={3} value={description} onChange={(e) => setDescription(e.target.value)}
                  placeholder="Retail bank. customers = one row per customer; loans = loan applications with status approved/rejected; amounts in INR." /></label>
      <label className="field cd-rows"><span>Most rows per question</span>
        <input className="input-field" type="number" min={1} max={1000} value={maxRows} onChange={(e) => setMaxRows(Number(e.target.value))} /></label>
      <button type="button" className="link-button cd-help-toggle" onClick={() => setShowHelp(!showHelp)}>
        {showHelp ? "Hide" : "Recommended:"} create a read-only login for AgentForge
      </button>
      {showHelp && <pre className="rv-json">{READ_ONLY_USER_SQL}</pre>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="btn primary" type="submit" disabled={busy}>{busy ? "Testing the connection…" : "Test & connect"}</button>
    </form>
  );
}

/** Tables + columns, "Try a query" (editors), test / delete (owners). */
function SourceDetails({ source, isOwner, canQuery, onChanged, onDeleted }: {
  source: DataSource; isOwner: boolean; canQuery: boolean; onChanged: () => void; onDeleted: () => void;
}) {
  const [tables, setTables] = useState<DataTable[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [sql, setSql] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getDataSourceSchema(source.id).then((s) => {
      setTables(s.tables);
      if (s.tables[0]) setSql(`SELECT * FROM ${s.tables[0].name} LIMIT 10`);
    }).catch(() => setTables([]));
  }, [source.id, source.last_tested_at]);

  async function run() {
    setBusy(true); setError(""); setResult(null);
    try { setResult(await tryDataSourceQuery(source.id, sql)); }
    catch (e) { setError(e instanceof Error ? e.message : "The query failed."); }
    finally { setBusy(false); }
  }

  async function retest() {
    try { await testDataSource(source.id); onChanged(); } catch (e) { setError(e instanceof Error ? e.message : "Test failed."); }
  }

  async function remove() {
    if (!window.confirm(`Disconnect "${source.name}"? Workflows using it will stop working. Your database itself is not touched.`)) return;
    try { await deleteDataSource(source.id); onDeleted(); } catch (e) { setError(e instanceof Error ? e.message : "Could not disconnect."); }
  }

  return (
    <section className="panel-box stack">
      <div className="cd-head">
        <h2>{source.name}</h2>
        <div className="row-actions">
          <button type="button" className="btn" onClick={() => void retest()}>Test connection</button>
          {isOwner && <button type="button" className="btn danger" onClick={() => void remove()}>Disconnect</button>}
        </div>
      </div>
      <p className="muted help-small">{source.address} · up to {source.max_rows} rows per question
        {source.last_tested_at && ` · checked ${new Date(source.last_tested_at).toLocaleString()}`}</p>
      {source.description && <p>{source.description}</p>}

      <DictionaryEditor key={source.last_tested_at ?? source.id} source={source} isOwner={isOwner} onSaved={onChanged} />

      <h3 className="cd-h3">Tables ({tables?.length ?? "…"})</h3>
      <ul className="cd-tables">
        {tables?.map((t) => (
          <li key={t.name}>
            <button type="button" className="link-button" onClick={() => setOpen(open === t.name ? null : t.name)}>
              {open === t.name ? "▾" : "▸"} {t.name} <span className="muted">({t.columns.length} columns)</span>
            </button>
            {open === t.name && (
              <div className="cd-columns">
                {t.columns.map((c) => <span key={c.name} className="code-inline">{c.name} <span className="muted">{c.type.toLowerCase()}</span></span>)}
              </div>
            )}
          </li>
        ))}
      </ul>

      {canQuery && (
        <>
          <h3 className="cd-h3">Try a question (SQL)</h3>
          <textarea className="input-field nf-mono" rows={3} value={sql} onChange={(e) => setSql(e.target.value)} aria-label="SQL query" />
          <div className="row-actions">
            <button type="button" className="btn primary" disabled={busy || !sql.trim()} onClick={() => void run()}>{busy ? "Running…" : "▶ Run query"}</button>
            <span className="muted help-small">Read-only: SELECT / WITH only.</span>
          </div>
        </>
      )}
      {error && <p className="form-error" role="alert">{error}</p>}
      {result && (
        <>
          <p className="muted help-small">{result.row_count} rows{result.truncated ? ` (only the first ${result.row_count} - add WHERE or LIMIT)` : ""}</p>
          <ResultView output={result.rows} />
        </>
      )}
    </section>
  );
}

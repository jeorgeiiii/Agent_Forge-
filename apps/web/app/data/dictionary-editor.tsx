"use client";
/**
 * Data dictionary editor (Company data page) - tells AgentForge's role agents where things are in THIS company.
 *
 * Role agents (Treasury, Accounts Receivable ...) only think in standard business concepts ("customer payment",
 * "bank statement line"). This dictionary maps each concept to this company's table and columns, plus the company's
 * policies (limits, fees) and the "today" date for reports. The server checks it against the real tables and
 * returns warnings for tables/columns that don't exist.
 */
import { useEffect, useState } from "react";
import { listBusinessConcepts, updateDataSource, type BusinessConcept, type DataSource } from "@/lib/api";

const EXAMPLE = {
  today: "",
  currency: "INR",
  notes: "How this company records things, e.g. 'TDS is stored as a separate row'.",
  policies: { large_payment_alert: "500000" },
  concepts: {
    customer_payment: { table: "receipts", columns: { id: "receipt_id", date: "received_on", amount: "amount_inr" },
                        notes: "remarks = free text from the payer" },
  },
};

export default function DictionaryEditor({ source, isOwner, onSaved }: {
  source: DataSource; isOwner: boolean; onSaved: () => void;
}) {
  const hasDictionary = Object.keys(source.dictionary ?? {}).length > 0;
  const [text, setText] = useState(JSON.stringify(hasDictionary ? source.dictionary : EXAMPLE, null, 2));
  const [concepts, setConcepts] = useState<BusinessConcept[]>([]);
  const [showConcepts, setShowConcepts] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => { listBusinessConcepts().then(setConcepts).catch(() => setConcepts([])); }, []);
  const mapped = new Set(Object.keys(source.dictionary?.concepts ?? {}));

  async function save() {
    setError(""); setNotice("");
    let parsed: unknown;
    try { parsed = JSON.parse(text); } catch { return setError("This is not valid JSON yet (check commas and quotes)."); }
    setBusy(true);
    try {
      const saved = await updateDataSource(source.id, { dictionary: parsed as DataSource["dictionary"] });
      setText(JSON.stringify(saved.dictionary, null, 2));
      setNotice(saved.dictionary_warnings.length ? "Saved - but check the warnings below." : "Saved. Every mapped table and column exists.");
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="stack dd-section">
      <h3 className="cd-h3">Data dictionary {hasDictionary
        ? <span className="badge ok">{mapped.size} concepts mapped</span>
        : <span className="badge wait">not set up</span>}</h3>
      <p className="muted help-small">
        AgentForge&apos;s ready-made agents (Treasury, Collections …) work with any company&apos;s database. This tells them
        where things are <strong>here</strong>: which table holds each business concept, your policies and limits, and
        the &quot;today&quot; date for reports. Nothing company-specific lives in the agents themselves.
      </p>
      {source.dictionary_warnings.length > 0 && (
        <ul className="dd-warnings">{source.dictionary_warnings.map((w) => <li key={w}>⚠ {w}</li>)}</ul>
      )}
      <textarea className="input-field nf-mono" rows={14} value={text} onChange={(e) => setText(e.target.value)}
                readOnly={!isOwner} aria-label="Data dictionary (JSON)" spellCheck={false} />
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="ge-ok" role="status">{notice}</p>}
      <div className="row-actions">
        {isOwner && <button type="button" className="btn primary" disabled={busy} onClick={() => void save()}>
          {busy ? "Checking…" : "Save & check"}</button>}
        <button type="button" className="link-button" onClick={() => setShowConcepts(!showConcepts)}>
          {showConcepts ? "Hide" : "Show"} the standard concepts ({concepts.length})
        </button>
      </div>
      {showConcepts && (
        <div className="rv-table-wrap">
          <table className="rv-table">
            <thead><tr><th>Concept</th><th>Means</th><th>Usual fields</th><th>Here</th></tr></thead>
            <tbody>
              {concepts.map((c) => (
                <tr key={c.name}>
                  <td><code>{c.name}</code></td>
                  <td>{c.label}{c.about ? <div className="muted help-small">{c.about}</div> : null}</td>
                  <td className="help-small">{c.fields.join(", ")}</td>
                  <td>{mapped.has(c.name) ? <span className="badge ok">mapped</span> : <span className="muted">-</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

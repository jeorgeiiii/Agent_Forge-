"use client";
/**
 * Integrations page - /integrations
 * Shows every service a workflow can use (Slack, GitHub ...), grouped by category, with search.
 * "Connect" saves the service's key in this workspace's Saved keys (encrypted on the server; never shown again).
 * Then: in the editor, add the service's node, pick an action and the saved key.
 */
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  createCredential, listCredentials, listIntegrations, startIntegrationSignIn, type CredentialSummary, type Integration,
} from "@/lib/api";
import { useSession } from "@/lib/session";

/** How saved keys are matched to a service: "Google Sheets - …" (sign-in services) or "Slack - …" (first word). */
function keyPrefix(i: Integration): string {
  return (i.oauth_provider ? `${i.name} -` : i.name.split(" ")[0]).toLowerCase();
}

export default function IntegrationsPage() {
  const { status, workspace } = useSession();
  const workspaceId = status === "signed-in" ? workspace?.id ?? null : null;
  const canEdit = status !== "signed-in" || workspace?.role !== "viewer";
  const [items, setItems] = useState<Integration[] | null>(null);
  const [keys, setKeys] = useState<CredentialSummary[]>([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [signingIn, setSigningIn] = useState<string | null>(null);

  useEffect(() => { listIntegrations().then(setItems).catch((e) => setError(e.message)); }, []);
  /** Saved keys, to show which services already have one (matched by the name we give them). */
  const loadKeys = () => { listCredentials(workspaceId).then(setKeys).catch(() => setKeys([])); };
  useEffect(() => { if (status !== "loading") loadKeys(); }, [status, workspaceId]); // eslint-disable-line react-hooks/exhaustive-deps

  /** "Sign in with Google": opens Google's page in a popup; the vault stores the account's tokens. */
  async function signIn(i: Integration) {
    setError("");
    try {
      const { authorize_url } = await startIntegrationSignIn(i.id, `${i.name} - ${workspace?.name ?? "main"}`, workspaceId);
      const popup = window.open(authorize_url, "agentforge-signin", "width=520,height=680");
      if (!popup) return setError("Your browser blocked the sign-in window. Allow pop-ups for this site and try again.");
      setSigningIn(i.id);
      const finish = () => { setSigningIn(null); loadKeys(); window.removeEventListener("message", onMessage); clearInterval(watch); };
      const onMessage = (event: MessageEvent) => {
        if (typeof event.data?.type === "string" && event.data.type.startsWith("agentforge-oauth-")) finish();
      };
      window.addEventListener("message", onMessage);
      const watch = setInterval(() => { if (popup.closed) finish(); }, 800);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the sign-in.");
    }
  }

  const categories = useMemo(() => ["All", ...new Set((items ?? []).map((i) => i.category))], [items]);
  const connectedCount = (items ?? []).filter((i) => i.needs_key &&
    keys.some((k) => k.name.toLowerCase().startsWith(keyPrefix(i)))).length;
  const shown = (items ?? []).filter((i) =>
    (category === "All" || i.category === category) &&
    `${i.name} ${i.description} ${i.actions.map((a) => a.name).join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()));

  return (
    <main className="container">
      <h1>Integrations</h1>
      <p className="muted">Connect a service once, then use it in any workflow: add its node in the editor, pick an action and the saved key.</p>
      {items && <p className="help-small muted">{items.length} integrations · {connectedCount} connected in this workspace</p>}
      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="int-toolbar">
        <input className="input-field" placeholder="Search integrations…" value={query} onChange={(e) => setQuery(e.target.value)}
               aria-label="Search integrations" />
        <div className="int-cats" role="tablist">
          {categories.map((c) => (
            <button key={c} type="button" role="tab" aria-selected={c === category}
                    className={`chip${c === category ? " active" : ""}`} onClick={() => setCategory(c)}>{c}</button>
          ))}
        </div>
      </div>

      {items === null && !error && <p className="muted">Loading…</p>}
      {items && shown.length === 0 && <p className="muted">No integration matches “{query}”.</p>}
      <div className="int-grid">
        {shown.map((i) => {
          const saved = keys.filter((k) => k.name.toLowerCase().startsWith(keyPrefix(i)));
          return (
            <section key={i.id} className="int-card">
              <header className="int-head">
                <span className="int-logo" aria-hidden>{i.name[0]}</span>
                <div>
                  <h2>{i.name}</h2>
                  <span className="muted help-small">{i.category}</span>
                </div>
                {!i.needs_key ? <span className="badge ok">No key needed</span>
                  : saved.length > 0 && <span className="badge ok">Connected</span>}
              </header>
              <p className="muted">{i.description}</p>
              <ul className="int-actions">
                {i.actions.map((a) => (
                  <li key={a.id} title={a.description}>
                    {a.name}{a.writes && <span className="badge wait" title="Changes or sends something">writes</span>}
                  </li>
                ))}
              </ul>
              {open === i.id ? (
                <ConnectForm integration={i} workspaceId={workspaceId}
                             onDone={() => { setOpen(null); loadKeys(); }} onCancel={() => setOpen(null)} />
              ) : (
                <div className="row-actions">
                  {canEdit && i.oauth_provider && (
                    <button className="btn primary" type="button" disabled={signingIn === i.id} onClick={() => void signIn(i)}>
                      {signingIn === i.id ? "Waiting for Google…" : saved.length ? "Connect another account" : "Sign in with Google"}
                    </button>
                  )}
                  {canEdit && i.needs_key && !i.oauth_provider && <button className="btn primary" type="button" onClick={() => setOpen(i.id)}>
                    {saved.length ? "Add another key" : "Connect"}</button>}
                  {i.docs_url && <a className="btn" href={i.docs_url} target="_blank" rel="noreferrer">API docs ↗</a>}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </main>
  );
}

/** Saves the service's key as an "api_key" Saved key named "<Service> - <label>". */
function ConnectForm({ integration, workspaceId, onDone, onCancel }: {
  integration: Integration; workspaceId: number | null; onDone: () => void; onCancel: () => void;
}) {
  const [label, setLabel] = useState("main");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await createCredential({ name: `${integration.name.split(" ")[0]} - ${label.trim() || "main"}`, type: "api_key",
                               data: { api_key: secret.trim() }, workspace_id: workspaceId });
      setSecret(""); setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the key.");
    } finally {
      setBusy(false);
    }
  }

  if (saved) {
    return (
      <div className="help-box">
        <p><strong>{integration.name} is connected.</strong> Open a workflow, add the <strong>{integration.name}</strong> node,
          choose an action and this key.</p>
        <div className="row-actions">
          <Link className="btn primary" href="/">Go to workflows</Link>
          <button className="btn" type="button" onClick={onDone}>Done</button>
        </div>
      </div>
    );
  }
  return (
    <form className="stack int-connect" onSubmit={(e) => void save(e)}>
      <p className="help-small">{integration.key_help}</p>
      <label className="field">
        <span>Name (to tell keys apart)</span>
        <input className="input-field" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={60} />
      </label>
      <label className="field">
        <span>{integration.key_label}</span>
        <input className="input-field" type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
               autoComplete="off" required minLength={8} />
      </label>
      <small className="muted">Stored encrypted. It is never shown again - not even to you.</small>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="row-actions">
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Save key"}</button>
        <button className="btn" type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

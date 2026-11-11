"use client";
/**
 * Saved keys page - /credentials
 * Lists the keys of the current workspace, adds new ones, renames or replaces them, and deletes them.
 * Secrets are sent to the server once and never come back to the browser.
 */
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  ApiError, createCredential, deleteCredential, listCredentials, updateCredential,
  type CredentialSummary, type CredentialType,
} from "@/lib/api";
import { CREDENTIAL_KINDS, PAGE_HELP, credentialKindLabel } from "@/lib/page-help";
import { useSession } from "@/lib/session";
import RoleHelpBox from "../role-help-box";

export default function CredentialsPage() {
  const router = useRouter();
  const { status, workspace } = useSession();
  const workspaceId = status === "signed-in" ? workspace?.id ?? null : null;
  const canEdit = status !== "signed-in" || workspace?.role !== "viewer";

  const [items, setItems] = useState<CredentialSummary[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  /** Loads the list again (after any change). */
  const load = useCallback(() => {
    listCredentials(workspaceId)
      .then(setItems)
      .catch((reason) => {
        if (reason instanceof ApiError && reason.status === 401) return router.replace("/login?next=/credentials");
        setError(reason instanceof Error ? reason.message : "Could not load saved keys.");
        setItems([]);
      });
  }, [workspaceId, router]);

  useEffect(() => { if (status !== "loading") load(); }, [status, load]);

  async function remove(item: CredentialSummary) {
    if (!window.confirm(`Delete "${item.name}"? Blocks that use it will stop working until you pick another key.`)) return;
    try {
      await deleteCredential(item.id);
      setNotice(`Deleted "${item.name}".`);
      load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete.");
    }
  }

  return (
    <main className="container">
      <div className="page-head">
        <div>
          <h1 style={{ margin: 0 }}>Saved keys</h1>
          <p className="muted">{workspace ? `For the workspace "${workspace.name}"` : "Your keys"}</p>
        </div>
      </div>
      <RoleHelpBox help={PAGE_HELP.credentials} />

      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="ge-ok" role="status">{notice}</p>}

      {canEdit && <AddKeyForm workspaceId={workspaceId} onSaved={(name) => { setNotice(`Saved "${name}".`); setError(""); load(); }} />}

      <div className="list">
        {items === null && <p className="muted">Loading…</p>}
        {items?.length === 0 && <p className="muted">No saved keys yet.</p>}
        {items?.map((item) => (
          <KeyRow key={item.id} item={item} canEdit={canEdit} onDelete={() => void remove(item)}
                  onChanged={(text) => { setNotice(text); load(); }} onError={setError} />
        ))}
      </div>
    </main>
  );
}

/** The "Add a key" form: pick a kind, fill its boxes, save. */
function AddKeyForm({ workspaceId, onSaved }: { workspaceId: number | null; onSaved: (name: string) => void }) {
  const [kind, setKind] = useState<CredentialType>("api_key");
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const spec = CREDENTIAL_KINDS.find((k) => k.id === kind)!;

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const data = Object.fromEntries(Object.entries(values).filter(([key, v]) => v.trim() && spec.fields.some((f) => f.key === key)));
    if (!name.trim()) return setError("Give the key a name, e.g. \"Groq AI key\".");
    if (!spec.fields.some((f) => f.secret && data[f.key])) return setError("Fill in the secret value.");
    setSaving(true);
    setError("");
    try {
      await createCredential({ name: name.trim(), type: kind, data, workspace_id: workspaceId });
      setName("");
      setValues({});
      onSaved(name.trim());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="panel-box stack" onSubmit={(e) => void save(e)} autoComplete="off">
      <h2>Add a key</h2>
      <div className="field">
        <label htmlFor="key-name">Name (so you can recognise it)</label>
        <input id="key-name" className="input-field" value={name} maxLength={200} placeholder="e.g. Groq AI key"
               onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="key-kind">Kind of key</label>
        <select id="key-kind" className="input-field" value={kind} onChange={(e) => { setKind(e.target.value as CredentialType); setValues({}); }}>
          {CREDENTIAL_KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
        </select>
        <small className="muted">{spec.hint}</small>
      </div>
      {spec.fields.map((f) => (
        <div className="field" key={f.key}>
          <label htmlFor={`key-${f.key}`}>{f.label}</label>
          <input id={`key-${f.key}`} className="input-field" type={f.secret ? "password" : "text"} placeholder={f.placeholder}
                 value={values[f.key] ?? ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
        </div>
      ))}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div><button className="btn primary" type="submit" disabled={saving}>{saving ? "Saving…" : "Save key"}</button></div>
      <small className="muted">After saving, the secret is locked - you can replace it, but not see it again.</small>
    </form>
  );
}

/** One saved key: name, kind, and Rename / Replace secret / Delete. */
function KeyRow({ item, canEdit, onDelete, onChanged, onError }: {
  item: CredentialSummary; canEdit: boolean; onDelete: () => void;
  onChanged: (text: string) => void; onError: (text: string) => void;
}) {
  async function rename() {
    const name = window.prompt("New name", item.name)?.trim();
    if (!name || name === item.name) return;
    try { await updateCredential(item.id, { name }); onChanged(`Renamed to "${name}".`); }
    catch (reason) { onError(reason instanceof Error ? reason.message : "Could not rename."); }
  }

  async function replaceSecret() {
    const spec = CREDENTIAL_KINDS.find((k) => k.id === item.type);
    if (!spec) return onError("This kind of key can't be replaced here.");
    // The server replaces ALL the values at once, so ask for every box (e.g. username AND password).
    const data: Record<string, string> = {};
    for (const f of spec.fields) {
      const value = window.prompt(`${f.label} for "${item.name}"${f.secret ? "" : " (leave empty if not needed)"}`)?.trim();
      if (value === undefined) return;                       // Cancel pressed
      if (f.secret && !value) return onError(`${f.label} can't be empty.`);
      if (value) data[f.key] = value;
    }
    try { await updateCredential(item.id, { data }); onChanged(`Replaced the secret of "${item.name}".`); }
    catch (reason) { onError(reason instanceof Error ? reason.message : "Could not replace."); }
  }

  return (
    <div className="card">
      <div>
        <strong>🔑 {item.name}</strong>
        <div className="muted">{credentialKindLabel(item.type)} · ID {item.id}</div>
      </div>
      {canEdit && (
        <div className="row-actions">
          <button className="btn" type="button" onClick={() => void rename()}>Rename</button>
          <button className="btn" type="button" onClick={() => void replaceSecret()}>Replace secret</button>
          <button className="btn danger" type="button" onClick={onDelete}>Delete</button>
        </div>
      )}
    </div>
  );
}

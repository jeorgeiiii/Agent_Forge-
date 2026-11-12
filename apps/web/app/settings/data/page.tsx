"use client";
/**
 * Data keeping tab - /settings/data
 * Owners choose how many days each kind of old data is kept (or go back to the default).
 * Also: "Delete my account" (permanent, asks for the password).
 */
import { useEffect, useState } from "react";
import { deleteMyAccount, getRetention, setRetention, type Retention, type RetentionKey } from "@/lib/api";
import { PAGE_HELP, RETENTION_FIELDS } from "@/lib/page-help";
import { useSession } from "@/lib/session";
import RoleHelpBox from "../../role-help-box";

export default function DataPage() {
  const { workspace } = useSession();
  const ws = workspace!.id;
  const isOwner = workspace!.role === "owner";
  const [data, setData] = useState<Retention | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  /** Puts the current values into the boxes. */
  const fill = (r: Retention) => {
    setData(r);
    setDraft(Object.fromEntries(RETENTION_FIELDS.map((f) => [f.key, String(r.effective[f.key])])));
  };
  useEffect(() => { getRetention(ws).then(fill).catch((e) => setError(e.message)); }, [ws]);

  /** Sends only the boxes that changed; a value equal to the default removes the override. */
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!data) return;
    const changes: Partial<Record<RetentionKey, number | null>> = {};
    for (const f of RETENTION_FIELDS) {
      const value = Number(draft[f.key]);
      if (!Number.isInteger(value)) return setError(`${f.label}: please enter a whole number of days.`);
      if (value === data.effective[f.key]) continue;
      changes[f.key] = value === data.defaults[f.key] ? null : value;
    }
    if (Object.keys(changes).length === 0) return setNotice("Nothing changed.");
    setSaving(true); setError("");
    try { fill(await setRetention(ws, changes)); setNotice("Saved. Old data is cleaned up automatically once a day."); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save."); }
    finally { setSaving(false); }
  }

  async function resetAll() {
    if (!data) return;
    const changes = Object.fromEntries(Object.keys(data.overrides).map((k) => [k, null])) as Partial<Record<RetentionKey, null>>;
    try { fill(await setRetention(ws, changes)); setNotice("Back to the defaults."); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not reset."); }
  }

  return (
    <>
      <RoleHelpBox help={PAGE_HELP.retention} />
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="ge-ok" role="status">{notice}</p>}

      <form className="panel-box stack" onSubmit={(e) => void save(e)}>
        <h2>How long to keep things</h2>
        {data === null && <p className="muted">Loading…</p>}
        {data && RETENTION_FIELDS.map((f) => {
          const custom = f.key in data.overrides;
          return (
            <div className="retention-row" key={f.key}>
              <div>
                <label htmlFor={`ret-${f.key}`}><strong>{f.label}</strong>{custom && <span className="badge wait" style={{ marginLeft: 6 }}>custom</span>}</label>
                <div className="muted help-small">{f.hint} Default: {data.defaults[f.key]} days.</div>
              </div>
              <div className="days-input">
                <input id={`ret-${f.key}`} className="input-field" type="number" min={f.min} max={3650} disabled={!isOwner}
                       value={draft[f.key] ?? ""} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
                <span className="muted">days</span>
              </div>
            </div>
          );
        })}
        {isOwner ? (
          <div className="row-actions">
            <button className="btn primary" type="submit" disabled={saving || !data}>{saving ? "Saving…" : "Save"}</button>
            {data && Object.keys(data.overrides).length > 0 && <button className="btn" type="button" onClick={() => void resetAll()}>Use defaults</button>}
          </div>
        ) : <p className="muted">Only owners can change these.</p>}
      </form>

      <DeleteAccount />
    </>
  );
}

/** Permanently deletes the signed-in person's account (GDPR). */
function DeleteAccount() {
  const { logout } = useSession();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function remove(event: React.FormEvent) {
    event.preventDefault();
    if (!window.confirm("Delete your account for good? Your personal data, chats and workspaces where you are the only member will be removed. This cannot be undone.")) return;
    try { await deleteMyAccount(password); await logout(); window.location.href = "/login"; }
    catch (e) { setError(e instanceof Error ? e.message : "Could not delete the account."); }
  }

  return (
    <form className="panel-box stack danger-zone" onSubmit={(e) => void remove(e)}>
      <h2>Delete my account</h2>
      <p className="muted">Removes your account and personal data. Workspaces you share with others stay, without you. If you are the only owner of a shared workspace, make someone else an owner first (Team tab).</p>
      <div className="inline-fields">
        <input className="input-field" type="password" placeholder="Your password" value={password} autoComplete="current-password"
               onChange={(e) => setPassword(e.target.value)} aria-label="Password to confirm" required />
        <button className="btn danger" type="submit">Delete my account</button>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>
  );
}

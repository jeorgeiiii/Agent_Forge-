"use client";
/**
 * Team tab - /settings/team
 * Members (change role, remove), invitations (invite, copy link, cancel),
 * and workspace actions for owners (rename, hand over, delete).
 */
import { useCallback, useEffect, useState } from "react";
import {
  changeMemberRole, deleteWorkspace, inviteMember, listInvitations, listMembers, removeMember, renameWorkspace,
  revokeInvitation, transferOwnership, type Invitation, type Member, type Role,
} from "@/lib/api";
import { PAGE_HELP } from "@/lib/page-help";
import { useSession } from "@/lib/session";
import RoleHelpBox from "../../role-help-box";

const ROLE_WORDS: Record<Role, string> = {
  owner: "Owner - everything, incl. people & settings",
  editor: "Editor - build and run workflows",
  viewer: "Viewer - look only",
};

export default function TeamPage() {
  const { workspace, user, refresh } = useSession();
  const ws = workspace!.id;
  const isOwner = workspace!.role === "owner";

  const [members, setMembers] = useState<Member[] | null>(null);
  const [invites, setInvites] = useState<Invitation[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [shareLink, setShareLink] = useState("");

  /** Loads members (everyone) and invitations (owners only). */
  const load = useCallback(() => {
    listMembers(ws).then(setMembers).catch((e) => setError(e.message));
    if (isOwner) listInvitations(ws).then(setInvites).catch(() => setInvites([]));
  }, [ws, isOwner]);
  useEffect(load, [load]);

  /** Runs one change, shows the result, reloads the lists. */
  async function act(action: () => Promise<unknown>, done: string) {
    setError("");
    try { await action(); setNotice(done); load(); }
    catch (e) { setError(e instanceof Error ? e.message : "That didn't work."); }
  }

  return (
    <>
      <RoleHelpBox help={PAGE_HELP.team} />
      {error && <p className="form-error" role="alert">{error}</p>}
      {notice && <p className="ge-ok" role="status">{notice}</p>}

      {isOwner && <InviteForm ws={ws} onInvited={(link, sent, email) => {
        setShareLink(sent ? "" : link);
        setNotice(sent ? `Invitation emailed to ${email}.` : `Invitation created for ${email}. Email isn't set up, so share the link below.`);
        load();
      }} onError={setError} />}
      {shareLink && (
        <div className="panel-box copy-row">
          <code className="code-inline">{shareLink}</code>
          <button className="btn" type="button" onClick={() => void navigator.clipboard.writeText(shareLink).then(() => setNotice("Link copied."))}>Copy link</button>
        </div>
      )}

      <section className="panel-box stack">
        <h2>Members</h2>
        {members === null && <p className="muted">Loading…</p>}
        <table className="run-table">
          <thead><tr><th>Person</th><th>Access</th>{isOwner && <th />}</tr></thead>
          <tbody>
            {members?.map((m) => {
              const isMe = m.user_id === user?.id;
              return (
                <tr key={m.user_id}>
                  <td><strong>{m.name || m.email}</strong>{isMe && <span className="muted"> (you)</span>}<div className="muted">{m.email}</div></td>
                  <td>
                    {isOwner && !isMe ? (
                      <select className="input-field compact" value={m.role} aria-label={`Access for ${m.email}`}
                              onChange={(e) => void act(() => changeMemberRole(ws, m.user_id, e.target.value as Role), `${m.email} is now ${e.target.value}.`)}>
                        {(Object.keys(ROLE_WORDS) as Role[]).map((r) => <option key={r} value={r}>{ROLE_WORDS[r]}</option>)}
                      </select>
                    ) : ROLE_WORDS[m.role]}
                  </td>
                  {isOwner && (
                    <td className="row-actions">
                      {!isMe && <button className="btn" type="button" title="They become owner, you become editor"
                                        onClick={() => window.confirm(`Make ${m.email} the owner? You will become an editor.`) &&
                                          void act(async () => { await transferOwnership(ws, m.user_id); await refresh(); }, `${m.email} is now the owner.`)}>Make owner</button>}
                      {!isMe && <button className="btn danger" type="button"
                                        onClick={() => window.confirm(`Remove ${m.email} from this workspace?`) &&
                                          void act(() => removeMember(ws, m.user_id), `Removed ${m.email}.`)}>Remove</button>}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {isOwner && invites.length > 0 && (
        <section className="panel-box stack">
          <h2>Invitations</h2>
          <table className="run-table">
            <thead><tr><th>Email</th><th>Access</th><th>Status</th><th>Expires</th><th /></tr></thead>
            <tbody>
              {invites.map((inv) => (
                <tr key={inv.id}>
                  <td>{inv.email}</td><td>{inv.role}</td>
                  <td><span className={`badge ${inv.status === "accepted" ? "ok" : inv.status === "pending" ? "wait" : "err"}`}>{inv.status}</span></td>
                  <td className="muted">{new Date(inv.expires_at).toLocaleDateString()}</td>
                  <td>{inv.status === "pending" && (
                    <button className="btn danger" type="button" onClick={() => void act(() => revokeInvitation(ws, inv.id), `Cancelled the invitation for ${inv.email}.`)}>Cancel</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {isOwner && <WorkspaceActions ws={ws} name={workspace!.name} onDone={async (text) => { setNotice(text); await refresh(); }} onError={setError} />}
      {!isOwner && <p className="muted">Only owners can invite people or change access.</p>}
    </>
  );
}

/** "Invite someone" form: email + access level. */
function InviteForm({ ws, onInvited, onError }: {
  ws: number; onInvited: (link: string, emailSent: boolean, email: string) => void; onError: (text: string) => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("editor");
  const [busy, setBusy] = useState(false);

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!email.trim()) return;
    setBusy(true);
    try {
      const created = await inviteMember(ws, email.trim(), role);
      onInvited(created.accept_url, created.email_sent, created.email);
      setEmail("");
    } catch (e) { onError(e instanceof Error ? e.message : "Could not invite."); }
    finally { setBusy(false); }
  }

  return (
    <form className="panel-box stack" onSubmit={(e) => void send(e)}>
      <h2>Invite someone</h2>
      <div className="inline-fields">
        <input className="input-field" type="email" placeholder="colleague@company.com" value={email}
               onChange={(e) => setEmail(e.target.value)} aria-label="Email to invite" required />
        <select className="input-field" value={role} onChange={(e) => setRole(e.target.value as Role)} aria-label="Access level">
          <option value="editor">{ROLE_WORDS.editor}</option>
          <option value="viewer">{ROLE_WORDS.viewer}</option>
          <option value="owner">{ROLE_WORDS.owner}</option>
        </select>
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Sending…" : "Send invite"}</button>
      </div>
      <small className="muted">They get a link that works for 7 days. They must sign in with this same email.</small>
    </form>
  );
}

/** Owner-only: rename or delete the workspace. */
function WorkspaceActions({ ws, name, onDone, onError }: {
  ws: number; name: string; onDone: (text: string) => Promise<void>; onError: (text: string) => void;
}) {
  async function rename() {
    const next = window.prompt("New workspace name", name)?.trim();
    if (!next || next === name) return;
    try { await renameWorkspace(ws, next); await onDone(`Renamed to "${next}".`); }
    catch (e) { onError(e instanceof Error ? e.message : "Could not rename."); }
  }
  async function remove() {
    const typed = window.prompt(`This deletes "${name}" with all its workflows (it can be restored for a while).\nType the workspace name to confirm:`);
    if (typed === null) return;
    try { await deleteWorkspace(ws, typed); await onDone(`Deleted "${name}".`); }
    catch (e) { onError(e instanceof Error ? e.message : "Could not delete."); }
  }
  return (
    <section className="panel-box stack danger-zone">
      <h2>Workspace</h2>
      <div className="row-actions">
        <button className="btn" type="button" onClick={() => void rename()}>Rename workspace</button>
        <button className="btn danger" type="button" onClick={() => void remove()}>Delete workspace</button>
      </div>
    </section>
  );
}

"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { acceptInvitation, previewInvitation, type InvitePreview } from "@/lib/api";
import { useSession } from "@/lib/session";

function InviteView() {
  const token = useSearchParams().get("token") ?? "";
  const router = useRouter();
  const { status, user, refresh, selectWorkspace, logout } = useSession();
  const [invite, setInvite] = useState<InvitePreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!token) {
      setError("This invitation link is incomplete.");
      return;
    }
    previewInvitation(token)
      .then(setInvite)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Invitation not found."));
  }, [token]);

  async function accept() {
    setBusy(true);
    setError("");
    try {
      const joined = await acceptInvitation(token);
      await refresh();
      selectWorkspace(joined.workspace_id);
      router.replace("/");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not accept the invitation.");
      setBusy(false);
    }
  }

  const here = `/invite?token=${encodeURIComponent(token)}`;
  const emailMatches = !!(user && invite && user.email.toLowerCase() === invite.email.toLowerCase());
  return (
    <main className="auth-page">
      <div className="auth-card">
        <h1>Workspace invitation</h1>
        {!invite && !error && <p className="muted">Checking your invitation…</p>}
        {invite && (
          <p>
            {invite.invited_by ?? "Someone"} invited <strong>{invite.email}</strong> to join{" "}
            <strong>{invite.workspace ?? "a workspace"}</strong> as <strong>{invite.role}</strong>.
          </p>
        )}
        {error && <p className="form-error" role="alert">{error}</p>}

        {invite && invite.status !== "pending" && (
          <p className="form-error">This invitation is {invite.status}. Ask the workspace owner for a new one.</p>
        )}

        {invite?.status === "pending" && status === "signed-out" && (
          <div className="stack">
            <p className="muted">Sign in or create an account with <strong>{invite.email}</strong> to accept.</p>
            <Link className="btn primary wide" href={`/login?next=${encodeURIComponent(here)}&email=${encodeURIComponent(invite.email)}`}>
              Sign in
            </Link>
            <Link className="btn wide" href={`/login?mode=signup&next=${encodeURIComponent(here)}&email=${encodeURIComponent(invite.email)}`}>
              Create an account
            </Link>
          </div>
        )}

        {invite?.status === "pending" && status === "signed-in" && user && (
          emailMatches ? (
            <button className="btn primary wide" onClick={() => void accept()} disabled={busy}>
              {busy ? "Joining…" : `Join ${invite.workspace ?? "workspace"}`}
            </button>
          ) : (
            <div className="stack">
              <p className="form-error">
                You are signed in as {user.email}, but this invitation is for {invite.email}.
              </p>
              <button className="btn wide" onClick={() => void logout()}>Sign out and switch account</button>
            </div>
          )
        )}
      </div>
    </main>
  );
}

export default function InvitePage() {
  return (
    <Suspense fallback={<main className="auth-page"><p className="muted">Loading…</p></main>}>
      <InviteView />
    </Suspense>
  );
}

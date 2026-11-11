"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { createWorkspace } from "@/lib/api";
import { BUTTON_HELP } from "@/lib/help";
import { useSession } from "@/lib/session";

const NEW_WORKSPACE = "__new__";

export default function SiteHeader() {
  const { status, user, workspaces, workspace, selectWorkspace, refresh, logout } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const [error, setError] = useState("");

  async function onWorkspaceChange(value: string) {
    setError("");
    if (value !== NEW_WORKSPACE) {
      selectWorkspace(Number(value));
      return;
    }
    const name = window.prompt("Name of the new workspace");
    if (!name?.trim()) return;
    try {
      const created = await createWorkspace(name.trim());
      await refresh();
      selectWorkspace(created.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create the workspace.");
    }
  }

  async function onLogout() {
    await logout();
    router.push("/login");
  }

  return (
    <header className="header">
      <Link href="/" className="brand">AgentForge</Link>
      <div className="header-right">
        {status === "signed-in" && user && (
          <>
            <label className="sr-only" htmlFor="workspace-switcher">Workspace</label>
            <select
              id="workspace-switcher"
              className="workspace-switcher"
              title={BUTTON_HELP.workspace}
              value={workspace?.id ?? ""}
              onChange={(event) => void onWorkspaceChange(event.target.value)}
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} · {w.role}
                </option>
              ))}
              <option value={NEW_WORKSPACE}>+ New workspace…</option>
            </select>
            <span className="muted user-email" title={user.email}>{user.email}</span>
            <button className="btn" onClick={() => void onLogout()}>Sign out</button>
          </>
        )}
        {status === "signed-out" && pathname !== "/login" && (
          <Link className="btn primary" href={`/login?next=${encodeURIComponent(pathname || "/")}`}>Sign in</Link>
        )}
        {error && <span className="form-error" role="alert">{error}</span>}
      </div>
    </header>
  );
}

"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ApiError, getMe, listWorkspaces, logout as apiLogout, type User, type Workspace } from "./api";

type SessionState = {
  status: "loading" | "signed-in" | "signed-out";
  user: User | null;
  workspaces: Workspace[];
  workspace: Workspace | null;
  selectWorkspace: (id: number) => void;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const SessionContext = createContext<SessionState | null>(null);
const STORAGE_KEY = "agentforge:workspace";

function rememberedWorkspace(): number | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value ? Number(value) : null;
  } catch {
    return null; // private mode / storage blocked
  }
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<SessionState["status"]>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const me = await getMe();
      const list = await listWorkspaces();
      setUser(me);
      setWorkspaces(list);
      const remembered = rememberedWorkspace();
      setWorkspaceId(list.some((w) => w.id === remembered) ? remembered : list[0]?.id ?? null);
      setStatus("signed-in");
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) console.warn("Session check failed:", error);
      setUser(null);
      setWorkspaces([]);
      setWorkspaceId(null);
      setStatus("signed-out");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectWorkspace = useCallback((id: number) => {
    setWorkspaceId(id);
    try {
      window.localStorage.setItem(STORAGE_KEY, String(id));
    } catch {
      /* storage blocked: selection just isn't remembered */
    }
  }, []);

  const logout = useCallback(async () => {
    await apiLogout().catch(() => undefined);
    await refresh();
  }, [refresh]);

  const value = useMemo<SessionState>(
    () => ({
      status,
      user,
      workspaces,
      workspace: workspaces.find((w) => w.id === workspaceId) ?? null,
      selectWorkspace,
      refresh,
      logout,
    }),
    [status, user, workspaces, workspaceId, selectWorkspace, refresh, logout],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <SessionProvider>.");
  return value;
}

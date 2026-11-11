export type NodeType =
  | "start"
  | "template"
  | "http"
  | "end"
  | "trigger"
  | "llm"
  | "tool"
  | "condition"
  | "output"
  | string;

export type GraphNode = {
  id: string;
  type: NodeType;
  label?: string;
  config: Record<string, unknown>;
};

export type GraphEdge = { source: string; target: string; sourceHandle?: string | null };

export type GraphDefinition = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  positions?: Record<string, { x: number; y: number }>;
};

export type Graph = {
  id: number;
  name: string;
  description: string;
  definition: GraphDefinition;
  workspace_id?: number | null;
  created_at: string;
  updated_at: string;
};

export type Run = {
  id: number;
  graph_id: number;
  workflow_id: string;
  status: string;
  input: Record<string, unknown>;
  created_at: string;
};

// Browser: same-origin "/api" (proxied by next.config.mjs rewrites) so the HttpOnly session
// cookie is first-party and sent automatically. Server: call the API directly and forward
// the visitor's cookie.
const serverApiUrl = process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const onServer = typeof window === "undefined";
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init?.headers as Record<string, string>) };
  if (onServer) {
    const { cookies } = await import("next/headers");
    const cookie = (await cookies()).toString();
    if (cookie) headers.cookie = cookie;
  }
  const response = await fetch(`${onServer ? serverApiUrl : ""}/api${path}`, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => undefined);
    const detail = typeof body?.detail === "string" ? body.detail : body?.detail?.message;
    throw new ApiError(detail ?? `API request failed (${response.status}).`, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

// ------------------------------------------------------------------ auth & workspaces

export type User = { id: number; email: string; name: string; created_at: string };
export type Workspace = { id: number; name: string; role: "owner" | "editor" | "viewer"; created_at: string };
export type InvitePreview = {
  workspace: string | null;
  role: string;
  email: string;
  invited_by: string | null;
  status: "pending" | "accepted" | "revoked" | "expired";
  expires_at: string;
};

export const signup = (email: string, password: string, name = "") =>
  request<{ user: User }>("/auth/signup", { method: "POST", body: JSON.stringify({ email, password, name }) });
export const login = (email: string, password: string) =>
  request<{ user: User }>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
export const logout = () => request<void>("/auth/logout", { method: "POST" });
export const getMe = () => request<User>("/auth/me");
export const listWorkspaces = () => request<Workspace[]>("/workspaces");
export const createWorkspace = (name: string) =>
  request<Workspace>("/workspaces", { method: "POST", body: JSON.stringify({ name }) });
export const previewInvitation = (token: string) =>
  request<InvitePreview>(`/invitations/preview?token=${encodeURIComponent(token)}`);
export const acceptInvitation = (token: string) =>
  request<{ workspace_id: number; role: string }>("/invitations/accept", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

import {
  isSupabaseConfigured,
  supabaseGetGraphs,
  supabaseGetGraphById,
  supabaseGetChatHistory,
  supabaseSaveChatMessage,
} from "./supabase";

export const listGraphs = async (workspaceId?: number | null): Promise<Graph[]> => {
  if (isSupabaseConfigured()) {
    try {
      return await supabaseGetGraphs();
    } catch (err) {
      console.warn("Supabase query failed, falling back to backend API:", err);
    }
  }
  return request<Graph[]>(workspaceId ? `/graphs?workspace_id=${workspaceId}` : "/graphs");
};

export const getGraph = async (id: number): Promise<Graph> => {
  if (isSupabaseConfigured()) {
    try {
      const g = await supabaseGetGraphById(id);
      if (g) return g;
    } catch (err) {
      console.warn("Supabase query failed, falling back to backend API:", err);
    }
  }
  return request<Graph>(`/graphs/${id}`);
};

export const createGraph = (body: Omit<Graph, "id" | "created_at" | "updated_at"> & { workspace_id?: number | null }) =>
  request<Graph>("/graphs", { method: "POST", body: JSON.stringify(body) });
export const updateGraph = (id: number, body: Omit<Graph, "id" | "created_at" | "updated_at">) =>
  request<Graph>(`/graphs/${id}`, { method: "PUT", body: JSON.stringify(body) });
export const startRun = (id: number, input: Record<string, unknown>) =>
  request<Run>(`/graphs/${id}/runs`, { method: "POST", body: JSON.stringify({ input }) });

export type StoredChatMessage = {
  id: number;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  graph_data?: any;
  created_at: string;
};

export const getChatHistory = async (sessionId: string): Promise<StoredChatMessage[]> => {
  if (isSupabaseConfigured()) {
    try {
      return await supabaseGetChatHistory(sessionId);
    } catch (err) {
      console.warn("Supabase chat query failed, falling back to backend API:", err);
    }
  }
  return request<StoredChatMessage[]>(`/chat/${sessionId}/messages`);
};

export const saveChatMessage = async (
  sessionId: string,
  message: { role: string; content: string; graph_data?: any }
): Promise<StoredChatMessage> => {
  if (isSupabaseConfigured()) {
    try {
      return await supabaseSaveChatMessage(
        sessionId,
        message.role,
        message.content,
        message.graph_data
      );
    } catch (err) {
      console.warn("Supabase chat insert failed, falling back to backend API:", err);
    }
  }
  return request<StoredChatMessage>(`/chat/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, ...message }),
  });
};


// ------------------------------------------------------------------ node registry (editor)

export type JsonSchema = {
  type?: "string" | "number" | "integer" | "boolean" | "object" | "array";
  enum?: (string | number)[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
};

export type NodeTypeSpec = {
  type: string;
  display_name: string;
  category: string;
  description: string;
  parameters: JsonSchema;
  outputs: string[];
  is_entry: boolean;
  is_end: boolean;
  runs_in: string;
};

export type CredentialSummary = { id: number; name: string; type: string; workspace_id: number | null };

export const listNodeTypes = () => request<NodeTypeSpec[]>("/node-types");
export const validateNodeConfig = (type: string, config: Record<string, unknown>) =>
  request<{ valid: boolean; errors: string[] }>(`/node-types/${encodeURIComponent(type)}/validate`, {
    method: "POST",
    body: JSON.stringify(config),
  });
export const listCredentials = (workspaceId?: number | null) =>
  request<CredentialSummary[]>(workspaceId ? `/credentials?workspace_id=${workspaceId}` : "/credentials");

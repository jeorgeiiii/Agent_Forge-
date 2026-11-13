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
/** PAUSED (SSO) - used only by app/login/sso-login-form.tsx when NEXT_PUBLIC_SSO_ENABLED=true.
 * Which way should this email sign in? "sso" = send them to their company's login page. */
export type LoginMethod =
  | { method: "password" }
  | { method: "sso"; required: boolean; connection_id: number; display_name: string };
export const discoverLogin = (email: string) =>
  request<LoginMethod>("/auth/sso/discover", { method: "POST", body: JSON.stringify({ email }) });
/** PAUSED (SSO). Where the "Continue with <company>" button goes. The API redirects to the company's login (SSO step 3). */
export const ssoStartUrl = (connectionId: number, next: string) =>
  `/api/auth/sso/${connectionId}/start?next=${encodeURIComponent(next)}`;
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
  // friendly form hints (integrations): label, help, which actions use the field, action names, big text box
  title?: string;
  description?: string;
  "x-actions"?: string[];
  "x-enum-labels"?: Record<string, string>;
  "x-multiline"?: boolean;
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

// ------------------------------------------------------------------ integrations

export type IntegrationField = { name: string; label: string; type: string; required: boolean; help: string };
export type IntegrationAction = { id: string; name: string; description: string; writes: boolean; fields: IntegrationField[] };
export type Integration = {
  id: string; name: string; category: string; description: string; needs_key: boolean;
  oauth_provider: string; oauth_scopes: string[]; // "google" = connect with "Sign in with Google"
  key_label: string; key_help: string; docs_url: string; actions: IntegrationAction[];
};
/** Services workflows can use (Slack, GitHub ...). Each one is also a node in the editor. */
export const listIntegrations = () => request<Integration[]>("/integrations");
/** "Sign in with Google" etc.: returns the provider page to open in a popup (scopes come from the integration). */
export const startIntegrationSignIn = (connector: string, credentialName: string, workspaceId: number | null) =>
  request<{ authorize_url: string }>("/credentials/oauth/start", {
    method: "POST", body: JSON.stringify({ connector, credential_name: credentialName, workspace_id: workspaceId }) });

export const listNodeTypes = () => request<NodeTypeSpec[]>("/node-types");
export const validateNodeConfig = (type: string, config: Record<string, unknown>) =>
  request<{ valid: boolean; errors: string[] }>(`/node-types/${encodeURIComponent(type)}/validate`, {
    method: "POST",
    body: JSON.stringify(config),
  });
export const listCredentials = (workspaceId?: number | null) =>
  request<CredentialSummary[]>(workspaceId ? `/credentials?workspace_id=${workspaceId}` : "/credentials");

// ------------------------------------------------------------------ runs (inspector)

export type RunSummary = {
  id: number; graph_id: number; workflow_id: string; status: string; input: Record<string, unknown>;
  created_at: string; trigger_kind: string; output: unknown; error: string | null; finished_at: string | null;
  retry_of_run_id: number | null; graph_version: number | null;
};

export type RunNodeDetail = {
  node_id: string; status: string; error: string | null; started_at: string | null; finished_at: string | null;
  duration_ms: number | null; output: unknown; agent: { steps: unknown[]; usage: Record<string, number> } | null;
};

export type CostBucket = { calls: number; input_tokens: number; output_tokens: number; cost_usd: number | null };

export type RunDetails = {
  run: {
    id: number; graph_id: number; graph_version: number | null; status: string; trigger: string;
    input: unknown; output: unknown; error: string | null; created_at: string; finished_at: string | null;
    duration_ms: number | null; retry_of_run_id: number | null;
    waiting_for: { node_id: string; message: string } | null;
  };
  nodes: RunNodeDetail[];
  usage: Record<string, number | null>;
  cost: {
    total_cost_usd: number | null; input_tokens: number; output_tokens: number; llm_calls: number;
    by_model: Record<string, CostBucket>; by_agent: Record<string, CostBucket>; unpriced_models: string[];
  };
  event_counts: Record<string, number>;
  last_event_id: number;
  can_retry: boolean;
};

export type RunEvent = {
  id: number; source: string; seq: number; type: string; node_id: string | null; at: string | null;
  data: Record<string, unknown>;
};

/** Run history page; `nextCursor` comes from the X-Next-Cursor header. */
export async function listRunsPage(graphId: number, opts: { status?: string; beforeId?: number | null; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(opts.limit ?? 25) });
  if (opts.status) params.set("status", opts.status);
  if (opts.beforeId) params.set("before_id", String(opts.beforeId));
  const response = await fetch(`/api/graphs/${graphId}/runs?${params}`, { credentials: "include", cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => undefined);
    throw new ApiError(body?.detail ?? `Could not load runs (${response.status}).`, response.status);
  }
  const cursor = response.headers.get("x-next-cursor");
  return { runs: (await response.json()) as RunSummary[], nextCursor: cursor ? Number(cursor) : null };
}

export const getRunDetails = (graphId: number, runId: number) => request<RunDetails>(`/graphs/${graphId}/runs/${runId}/details`);
export const getRunEvents = (graphId: number, runId: number, after = 0) =>
  request<RunEvent[]>(`/graphs/${graphId}/runs/${runId}/events?after=${after}&limit=1000`);
export const cancelRun = (graphId: number, runId: number) =>
  request<RunSummary>(`/graphs/${graphId}/runs/${runId}/cancel`, { method: "POST" });
export const retryRun = (graphId: number, runId: number) =>
  request<RunSummary>(`/graphs/${graphId}/runs/${runId}/retry`, { method: "POST" });
export const decideApproval = (graphId: number, runId: number, nodeId: string, approved: boolean, comment = "") =>
  request<RunSummary>(`/graphs/${graphId}/runs/${runId}/approval`, {
    method: "POST",
    body: JSON.stringify({ node_id: nodeId, approved, comment }),
  });
/** Same-origin URL for EventSource (the Next proxy forwards it; the session cookie goes with it). */
export const runEventsStreamUrl = (graphId: number, runId: number) => `/api/graphs/${graphId}/runs/${runId}/events/stream`;

// ------------------------------------------------------------------ saved keys (credentials)

export type CredentialType = "api_key" | "bearer" | "basic" | "header" | "oauth2" | "custom";
export type Credential = CredentialSummary & { created_at: string; updated_at: string };

/** Saves a new key. The secret is encrypted on the server and can never be read back by the browser. */
export const createCredential = (body: { name: string; type: CredentialType; data: Record<string, unknown>; workspace_id?: number | null }) =>
  request<Credential>("/credentials", { method: "POST", body: JSON.stringify(body) });
/** Renames a key and/or replaces its secret (leave `data` out to keep the old secret). */
export const updateCredential = (id: number, body: { name?: string; data?: Record<string, unknown> }) =>
  request<Credential>(`/credentials/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteCredential = (id: number) => request<void>(`/credentials/${id}`, { method: "DELETE" });

// ------------------------------------------------------------------ triggers (how a workflow starts by itself)

export type Trigger = { id: number; node_id: string; kind: string; active: boolean; url: string | null;
  config: Record<string, unknown>; schedule_id: string | null };
export type GraphStatus = Graph & { published_version?: number | null; is_active?: boolean };

export const getGraphStatus = (id: number) => request<GraphStatus>(`/graphs/${id}`);
export const listTriggers = (graphId: number) => request<Trigger[]>(`/graphs/${graphId}/triggers`);
/** Publishing makes the latest saved version "live" and creates its web addresses / timetables. */
export const publishGraph = (graphId: number) =>
  request<{ published_version: number; triggers: Trigger[] }>(`/graphs/${graphId}/publish`, { method: "POST", body: "{}" });
export const unpublishGraph = (graphId: number) => request<GraphStatus>(`/graphs/${graphId}/unpublish`, { method: "POST" });
/** Turn the live triggers on or off without unpublishing. */
export const setGraphActive = (graphId: number, active: boolean) =>
  request<GraphStatus>(`/graphs/${graphId}/${active ? "activate" : "deactivate"}`, { method: "POST" });

// ------------------------------------------------------------------ files

export type StoredFile = { id: string; name: string; mime: string; size: number; status: string;
  workspace_id: number | null; graph_id: number | null; run_id: number | null; created_at: string;
  ref: Record<string, unknown> };

export const listFiles = (workspaceId?: number | null) =>
  request<StoredFile[]>(workspaceId ? `/files?workspace_id=${workspaceId}` : "/files");
export const deleteFile = (id: string) => request<void>(`/files/${encodeURIComponent(id)}`, { method: "DELETE" });
/** Where the browser downloads a file from (the session cookie proves access). */
export const fileDownloadUrl = (id: string) => `/api/files/${encodeURIComponent(id)}/content`;

/** Uploads one file. Uses FormData, so it skips the JSON helper (the browser sets the content type). */
export async function uploadFile(file: File, workspaceId?: number | null): Promise<StoredFile> {
  const form = new FormData();
  form.append("file", file);
  if (workspaceId) form.append("workspace_id", String(workspaceId));
  const response = await fetch("/api/files", { method: "POST", body: form, credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => undefined);
    const detail = typeof body?.detail === "string" ? body.detail : body?.detail?.message;
    throw new ApiError(detail ?? `Upload failed (${response.status}).`, response.status);
  }
  return response.json();
}

// ------------------------------------------------------------------ settings: team (members + invitations)

export type Role = Workspace["role"];
export type Member = { user_id: number; email: string; name: string; role: Role };
export type Invitation = { id: number; workspace_id: number; email: string; role: Role; status: string;
  invited_by: number | null; created_at: string; expires_at: string; accepted_at: string | null };
/** Returned once when inviting: the link to share if the email could not be sent. */
export type InvitationCreated = Invitation & { accept_url: string; email_sent: boolean; email_error?: string };

export const listMembers = (ws: number) => request<Member[]>(`/workspaces/${ws}/members`);
export const changeMemberRole = (ws: number, userId: number, role: Role) =>
  request<Member>(`/workspaces/${ws}/members/${userId}`, { method: "PATCH", body: JSON.stringify({ role }) });
export const removeMember = (ws: number, userId: number) =>
  request<void>(`/workspaces/${ws}/members/${userId}`, { method: "DELETE" });
export const listInvitations = (ws: number) => request<Invitation[]>(`/workspaces/${ws}/invitations`);
export const inviteMember = (ws: number, email: string, role: Role) =>
  request<InvitationCreated>(`/workspaces/${ws}/invitations`, { method: "POST", body: JSON.stringify({ email, role }) });
export const revokeInvitation = (ws: number, id: number) =>
  request<Invitation>(`/workspaces/${ws}/invitations/${id}`, { method: "DELETE" });
export const renameWorkspace = (ws: number, name: string) =>
  request<{ id: number; name: string }>(`/workspaces/${ws}`, { method: "PATCH", body: JSON.stringify({ name }) });
/** Hands the workspace to another member; you become an editor. */
export const transferOwnership = (ws: number, userId: number) =>
  request<unknown>(`/workspaces/${ws}/transfer-ownership`, { method: "POST", body: JSON.stringify({ user_id: userId }) });
/** Soft delete - can be restored until the cleanup removes it. `confirmName` must match exactly. */
export const deleteWorkspace = (ws: number, confirmName: string) =>
  request<unknown>(`/workspaces/${ws}`, { method: "DELETE", body: JSON.stringify({ confirm_name: confirmName }) });

// ------------------------------------------------------------------ settings: usage + limits

export type UsageBucket = { runs: number; failed_runs: number; llm_calls: number; input_tokens: number;
  output_tokens: number; cost_usd: number };
export type UsageSummary = { group_by: string; totals: UsageBucket; groups: (UsageBucket & { key: string })[] };
export type LimitsStatus = {
  limits: { max_concurrent_runs: number; runs_per_day: number; tokens_per_minute: number; tokens_per_day: number };
  usage: { concurrent_runs: number; runs_today: number; tokens_last_minute: number; tokens_today: number };
};

export const getUsage = (ws: number, groupBy: "day" | "graph" | "model" | "trigger", since?: string) =>
  request<UsageSummary>(`/usage?workspace_id=${ws}&group_by=${groupBy}${since ? `&since=${encodeURIComponent(since)}` : ""}`);
export const getLimits = (ws: number) => request<LimitsStatus>(`/workspaces/${ws}/limits`);

// ------------------------------------------------------------------ settings: activity (audit) log

export type AuditEvent = { id: number; at: string; action: string; outcome: string;
  actor: { user_id: number | null; type: string; label: string }; resource: { type: string | null; id: string | null };
  workspace_id: number | null; ip: string | null; details: Record<string, unknown> };

/** One page of the activity log, newest first. `next` is passed back as beforeId for the next page. */
export async function listAudit(ws: number, opts: { action?: string; outcome?: string; beforeId?: number | null } = {}) {
  const q = new URLSearchParams({ limit: "50" });
  if (opts.action) q.set("action", opts.action);
  if (opts.outcome) q.set("outcome", opts.outcome);
  if (opts.beforeId) q.set("before_id", String(opts.beforeId));
  const response = await fetch(`/api/workspaces/${ws}/audit?${q}`, { credentials: "include", cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => undefined);
    throw new ApiError(body?.detail?.message ?? body?.detail ?? `API request failed (${response.status}).`, response.status);
  }
  const next = response.headers.get("X-Next-Cursor");
  return { events: (await response.json()) as AuditEvent[], next: next ? Number(next) : null };
}
/** Re-checks the log's fingerprint chain: proves nothing was edited or removed. */
export const verifyAudit = (ws: number) => request<{ valid: boolean; checked: number; broken_at_id?: number | null; reason?: string }>(`/workspaces/${ws}/audit/verify`);
export const auditExportUrl = (ws: number) => `/api/workspaces/${ws}/audit/export.csv`;

// ------------------------------------------------------------------ settings: data keeping (retention)

export type RetentionKey = "run_data_days" | "run_rows_days" | "memory_days" | "deleted_graph_days" | "audit_days";
export type Retention = { effective: Record<RetentionKey, number>; overrides: Partial<Record<RetentionKey, number>>;
  defaults: Record<RetentionKey, number> };

export const getRetention = (ws: number) => request<Retention>(`/workspaces/${ws}/retention`);
/** null for a key = go back to the default. */
export const setRetention = (ws: number, changes: Partial<Record<RetentionKey, number | null>>) =>
  request<Retention>(`/workspaces/${ws}/retention`, { method: "PATCH", body: JSON.stringify(changes) });
/** Permanently deletes your account and personal data. Needs your password. */
export const deleteMyAccount = (password: string) =>
  request<unknown>("/auth/me", { method: "DELETE", body: JSON.stringify({ password }) });

// ------------------------------------------------------------------ company databases (read-only)

export type DataSource = {
  id: number; workspace_id: number; name: string; kind: string; description: string;
  address: string; // "postgres · host:5432/db" - never the user or password
  max_rows: number; table_count: number; last_tested_at: string | null; last_error: string | null; created_at: string;
  dictionary: DataDictionary; dictionary_warnings: string[];
};
/** Where each standard business concept lives in this company's tables + its policies and "today" date. */
export type DataDictionary = {
  today?: string; currency?: string; notes?: string; policies?: Record<string, string>;
  concepts?: Record<string, { table: string; columns?: Record<string, string>; notes?: string }>;
};
export type BusinessConcept = { name: string; label: string; fields: string[]; about?: string };
export const listBusinessConcepts = () => request<BusinessConcept[]>("/business-concepts");
export type DataTable = { name: string; columns: { name: string; type: string }[] };
export type QueryResult = { columns: string[]; rows: Record<string, unknown>[]; row_count: number; truncated: boolean };

export const listDataSources = (workspaceId: number) => request<DataSource[]>(`/workspaces/${workspaceId}/data-sources`);
/** Owners only. The server tests the connection and reads the tables before saving. */
export const createDataSource = (workspaceId: number, body: { name: string; url: string; description: string; max_rows: number }) =>
  request<DataSource>(`/workspaces/${workspaceId}/data-sources`, { method: "POST", body: JSON.stringify(body) });
export const updateDataSource = (id: number, body: Partial<{ name: string; url: string; description: string; max_rows: number; dictionary: DataDictionary | null }>) =>
  request<DataSource>(`/data-sources/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteDataSource = (id: number) => request<void>(`/data-sources/${id}`, { method: "DELETE" });
export const testDataSource = (id: number) => request<DataSource>(`/data-sources/${id}/test`, { method: "POST" });
export const getDataSourceSchema = (id: number) => request<{ tables: DataTable[] }>(`/data-sources/${id}/schema`);
export const tryDataSourceQuery = (id: number, sql: string) =>
  request<QueryResult>(`/data-sources/${id}/query`, { method: "POST", body: JSON.stringify({ sql }) });

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

export type GraphDefinition = {
  nodes: GraphNode[];
  edges: { source: string; target: string }[];
  positions?: Record<string, { x: number; y: number }>;
};

export type Graph = {
  id: number;
  name: string;
  description: string;
  definition: GraphDefinition;
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

const browserApiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const serverApiUrl = process.env.INTERNAL_API_URL ?? browserApiUrl;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const baseUrl = typeof window === "undefined" ? serverApiUrl : browserApiUrl;
  const response = await fetch(`${baseUrl}/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => undefined);
    throw new Error(body?.detail ?? `API request failed (${response.status}).`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

import {
  isSupabaseConfigured,
  supabaseGetGraphs,
  supabaseGetGraphById,
  supabaseGetChatHistory,
  supabaseSaveChatMessage,
} from "./supabase";

export const listGraphs = async (): Promise<Graph[]> => {
  if (isSupabaseConfigured()) {
    try {
      return await supabaseGetGraphs();
    } catch (err) {
      console.warn("Supabase query failed, falling back to backend API:", err);
    }
  }
  return request<Graph[]>("/graphs");
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

export const createGraph = (body: Omit<Graph, "id" | "created_at" | "updated_at">) =>
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


import { createClient, SupabaseClient } from "@supabase/supabase-js";
import type { Graph, StoredChatMessage } from "./api";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || "https://your-project.supabase.co";
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "your-anon-key";

// Lazy-initialized singleton Supabase client
let _supabaseInstance: SupabaseClient | null = null;

export function getSupabase(): SupabaseClient {
  if (!_supabaseInstance) {
    _supabaseInstance = createClient(supabaseUrl, supabaseAnonKey, {
      auth: { persistSession: false },
    });
  }
  return _supabaseInstance;
}

export const isSupabaseConfigured = (): boolean => {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  return Boolean(url && !url.includes("your-project"));
};

// --- Direct Supabase Database Operations (Stateless HTTP Connection Pool) ---

export async function supabaseGetGraphs(): Promise<Graph[]> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("graphs")
    .select("*")
    .order("updated_at", { ascending: false });

  if (error) throw new Error(`Supabase query failed: ${error.message}`);
  return (data || []) as Graph[];
}

export async function supabaseGetGraphById(id: number): Promise<Graph | null> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("graphs")
    .select("*")
    .eq("id", id)
    .single();

  if (error) return null;
  return data as Graph;
}

export async function supabaseSaveGraph(
  name: string,
  description: string,
  definition: Record<string, unknown>,
  id?: number
): Promise<Graph> {
  const supabase = getSupabase();
  const payload = {
    name,
    description,
    definition,
    updated_at: new Date().toISOString(),
  };

  if (id) {
    const { data, error } = await supabase
      .from("graphs")
      .update(payload)
      .eq("id", id)
      .select()
      .single();
    if (error) throw new Error(error.message);
    return data as Graph;
  }

  const { data, error } = await supabase
    .from("graphs")
    .insert([payload])
    .select()
    .single();

  if (error) throw new Error(error.message);
  return data as Graph;
}

export async function supabaseGetChatHistory(sessionId: string): Promise<StoredChatMessage[]> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("chat_messages")
    .select("*")
    .eq("session_id", sessionId)
    .order("created_at", { ascending: true });

  if (error) throw new Error(error.message);
  return (data || []) as StoredChatMessage[];
}

export async function supabaseSaveChatMessage(
  sessionId: string,
  role: string,
  content: string,
  graphData?: Record<string, unknown>
): Promise<StoredChatMessage> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("chat_messages")
    .insert([
      {
        session_id: sessionId,
        role,
        content,
        graph_data: graphData || null,
      },
    ])
    .select()
    .single();

  if (error) throw new Error(error.message);
  return data as StoredChatMessage;
}

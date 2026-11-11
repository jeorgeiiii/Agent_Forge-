/**
 * Plain-language help for the Saved keys, Files and Triggers pages,
 * with one example per job role (same roles as lib/roles.ts).
 * Simple ideas keep everyday words; field words are used only where they make it clearer.
 */
import type { Role } from "./roles";

export type PageHelp = { what: string; examples: Record<Role, string> };

export const PAGE_HELP: Record<"credentials" | "files" | "triggers", PageHelp> = {
  credentials: {
    what:
      "A saved key is the password or access code another app gives you. Save it here once, then pick it in any " +
      "block (like Talk to a website or Ask AI). It is locked away safely - nobody can see it again, not even you.",
    examples: {
      general: "Save your AI provider's key once, then every AI block can use it without pasting it again.",
      sales: "Save your CRM's API key so workflows can create deals and update lead stages.",
      marketing: "Save your email platform's key so workflows can add new subscribers to your list.",
      tech: "Store a bearer token or API key (encrypted at rest); HTTP/LLM nodes reference it by credential_id.",
      analyst: "Save the key for your analytics tool so workflows can pull the latest numbers.",
      finance: "Save your accounting software's key so workflows can create bills and read invoices.",
    },
  },
  files: {
    what:
      "Upload documents, pictures or spreadsheets so your workflows can read them. Files made by a workflow " +
      "(like a downloaded PDF) also appear here.",
    examples: {
      general: "Upload a product guide so an AI helper can answer questions about it.",
      sales: "Upload your price list so the AI can quote correct prices in follow-up emails.",
      marketing: "Upload your brand guidelines so AI-written posts match your tone of voice.",
      tech: "Upload a file and pass its {\"$file\": ...} reference into an HTTP node's body_file.",
      analyst: "Upload a CSV export so the workflow can summarise it into a weekly report.",
      finance: "Upload a supplier invoice PDF so the AI can pull out amount, tax and due date.",
    },
  },
  triggers: {
    what:
      "Triggers start your workflow by themselves - when another app sends data, on a timetable, or when someone " +
      "chats. They only work after you Publish. Publish = make the latest saved version live.",
    examples: {
      general: "Publish a workflow with a Schedule block and it runs every morning without you pressing Run.",
      sales: "Publish a lead-capture workflow so every new demo request is answered within a minute.",
      marketing: "Publish a welcome-email workflow so every new sign-up gets an email right away.",
      tech: "Publish to register webhook endpoints and Temporal schedules for the latest compiled version.",
      analyst: "Publish a daily KPI workflow so the summary is ready before the morning meeting.",
      finance: "Publish an invoice-check workflow so every new bill is checked as soon as it arrives.",
    },
  },
};

/** The kinds of saved key, explained in plain words, and the boxes each one needs. */
export const CREDENTIAL_KINDS: {
  id: "api_key" | "bearer" | "basic" | "header";
  label: string;
  hint: string;
  fields: { key: string; label: string; secret?: boolean; placeholder?: string }[];
}[] = [
  {
    id: "api_key", label: "API key", hint: "The most common kind - one long code from the app's settings page (AI providers, most tools).",
    fields: [
      { key: "api_key", label: "Key", secret: true, placeholder: "paste the key here" },
      { key: "base_url", label: "Address (only for AI providers, optional)", placeholder: "https://api.groq.com/openai/v1" },
    ],
  },
  {
    id: "bearer", label: "Access token", hint: "A token the app calls a \"bearer token\" or \"access token\".",
    fields: [{ key: "token", label: "Token", secret: true }],
  },
  {
    id: "basic", label: "Username + password", hint: "For older services that sign in with a username and password.",
    fields: [{ key: "username", label: "Username" }, { key: "password", label: "Password", secret: true }],
  },
  {
    id: "header", label: "Custom header", hint: "When the app says \"send your key in the X-Something header\".",
    fields: [
      { key: "header_name", label: "Header name", placeholder: "X-API-Key" },
      { key: "value", label: "Value", secret: true },
    ],
  },
];

/** Readable label for any credential type (including ones made outside this page). */
export function credentialKindLabel(type: string): string {
  return CREDENTIAL_KINDS.find((k) => k.id === type)?.label ?? (type === "oauth2" ? "Sign-in connection" : type);
}

/** Plain names for trigger kinds, and which block help to show (see lib/help.ts). */
export const TRIGGER_KINDS: Record<string, { label: string; icon: string; helpType: string }> = {
  webhook: { label: "Web address (webhook)", icon: "⇄", helpType: "webhook_trigger" },
  schedule: { label: "Timetable (schedule)", icon: "⏰", helpType: "schedule_trigger" },
  chat: { label: "Chat", icon: "💬", helpType: "chat_trigger" },
};

/** 1536000 -> "1.5 MB" */
export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Plain-language help for the Saved keys, Files and Triggers pages,
 * with one example per job role (same roles as lib/roles.ts).
 * Simple ideas keep everyday words; field words are used only where they make it clearer.
 */
import type { Role } from "./roles";

export type PageHelp = { what: string; examples: Record<Role, string> };

export const PAGE_HELP: Record<"credentials" | "files" | "triggers" | "team" | "usage" | "audit" | "retention", PageHelp> = {
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
  team: {
    what:
      "Invite the people you work with. Owner = can do everything (people, settings). Editor = can build and run " +
      "workflows. Viewer = can only look.",
    examples: {
      general: "Invite a colleague as Editor so you can build workflows together.",
      sales: "Add your sales reps as Editors and the sales director as Viewer to follow results.",
      marketing: "Give your content team Editor access and the agency Viewer access to review campaigns.",
      tech: "RBAC per workspace: owner / editor / viewer. Invites are signed, single-use tokens that expire.",
      analyst: "Let stakeholders open run results as Viewers without being able to change your workflows.",
      finance: "Keep approvals safe: only the finance manager is Owner; clerks are Editors; auditors are Viewers.",
    },
  },
  usage: {
    what:
      "See how much your workflows are used: how many runs, how many failed, how much AI text was used and " +
      "what it cost. Limits stop surprise bills (0 = no limit).",
    examples: {
      general: "Check that last week's AI cost stayed within your budget.",
      sales: "See which lead workflow runs most and what each follow-up email costs.",
      marketing: "Compare the AI cost of the newsletter workflow with the social-post workflow.",
      tech: "Aggregated runs, failures, LLM calls, tokens and cost by day / graph / model / trigger; quota headroom.",
      analyst: "Group by day to spot usage trends, or by AI model to compare costs.",
      finance: "Use the cost per workflow to charge each department for its AI spend.",
    },
  },
  audit: {
    what:
      "A record of who did what and when - every login, change, deletion and approval. Entries are linked like a " +
      "chain, so nobody can secretly edit or remove one. Only owners can see it.",
    examples: {
      general: "Find out who deleted a workflow yesterday.",
      sales: "See who changed the discount-approval workflow before a big deal went out.",
      marketing: "Check who published the campaign workflow and when.",
      tech: "Hash-chained, append-only audit log with filters, chain verification and CSV export.",
      analyst: "Export the log as a spreadsheet to review changes over the quarter.",
      finance: "Show auditors every approval and change, and prove the record was never altered.",
    },
  },
  retention: {
    what:
      "Choose how long old information is kept before it is cleaned up automatically. Shorter = more private and " +
      "tidy; longer = more history to look back on.",
    examples: {
      general: "Keep run details for 30 days, then remove them automatically.",
      sales: "Delete old chat conversations with leads after 90 days to respect their privacy.",
      marketing: "Remove sign-up data from old campaigns after the campaign ends.",
      tech: "Per-workspace TTL overrides for run payloads, run rows, memory, soft-deleted graphs and audit events.",
      analyst: "Keep run history for a full year so you can compare year over year.",
      finance: "Keep the activity log for 7 years (2555 days) to meet record-keeping rules.",
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

/** Plain names for each "data keeping" setting. */
export const RETENTION_FIELDS: { key: "run_data_days" | "run_rows_days" | "memory_days" | "deleted_graph_days" | "audit_days";
  label: string; hint: string; min: number }[] = [
  { key: "run_data_days", label: "Run details", hint: "What went in and out of each step (inputs, answers, AI text).", min: 1 },
  { key: "run_rows_days", label: "Run list", hint: "The list of runs with their status and cost. Must be at least as long as run details.", min: 1 },
  { key: "memory_days", label: "Chat memory", hint: "What AI helpers remember from earlier chat messages.", min: 1 },
  { key: "deleted_graph_days", label: "Deleted workflows", hint: "How long a deleted workflow can still be brought back. 0 = remove right away.", min: 0 },
  { key: "audit_days", label: "Activity log", hint: "Security record of who did what. At least 30 days.", min: 30 },
];

/** Plain words for activity-log actions, e.g. "graph.publish" -> "Published a workflow". */
const ACTION_WORDS: Record<string, string> = {
  "auth.login": "Signed in", "auth.login_failed": "Failed sign-in", "auth.logout": "Signed out", "auth.signup": "Created an account",
  "graph.create": "Created a workflow", "graph.update": "Changed a workflow", "graph.delete": "Deleted a workflow",
  "graph.publish": "Published a workflow", "graph.unpublish": "Unpublished a workflow",
  "graph.activate": "Turned triggers on", "graph.deactivate": "Paused triggers",
  "run.start": "Started a run", "run.started": "A run started", "run.finished": "A run finished", "run.cancel": "Cancelled a run",
  "run.retry": "Retried a run", "run.approve": "Approved a step", "run.reject": "Rejected a step",
  "graph.rename": "Renamed a workflow", "graph.duplicate": "Copied a workflow", "graph.restore": "Restored a workflow",
  "graph.rollback": "Went back to an older version", "credential.refresh": "Refreshed a sign-in key", "auth.logout_all": "Signed out everywhere",
  "memory.clear_session": "Cleared a chat memory", "memory.clear_all": "Cleared all chat memory",
  "api_key.create": "Created a developer key", "api_key.revoke": "Revoked a developer key",
  "credential.create": "Saved a key", "credential.update": "Changed a key", "credential.delete": "Deleted a key",
  "file.upload": "Uploaded a file", "file.delete": "Deleted a file",
  "member.add": "Added a member", "member.role_change": "Changed a member's role", "member.remove": "Removed a member",
  "invitation.create": "Invited someone", "invitation.accept": "Accepted an invitation", "invitation.revoke": "Cancelled an invitation",
  "workspace.create": "Created the workspace", "workspace.update": "Renamed the workspace", "workspace.delete": "Deleted the workspace",
  "workspace.restore": "Restored the workspace", "workspace.transfer_ownership": "Handed over ownership",
  "retention.update": "Changed data keeping",
};
export function actionLabel(action: string): string {
  return ACTION_WORDS[action] ?? action;
}

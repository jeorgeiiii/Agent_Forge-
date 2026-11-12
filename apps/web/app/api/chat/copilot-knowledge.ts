/**
 * What the editor's Copilot chat knows about AgentForge.
 *
 * - AGENTFORGE_GUIDE: how the product really works (Run button, run input, history, triggers, integrations,
 *   AI agents). Without it the model answers like a generic chatbot and invents URLs.
 * - nodeCatalogue(): the block list, fetched live from the API (/node-types) so new blocks and
 *   integrations show up automatically. Cached for 5 minutes; falls back to the core blocks if the API is down.
 */

const API_URL = (process.env.INTERNAL_API_URL ?? process.env.API_PROXY_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const CACHE_MS = 5 * 60 * 1000;

export const AGENTFORGE_GUIDE = `HOW AGENTFORGE WORKS (answer questions with these facts - never invent buttons or URLs):
- Editor: blocks are added from the list on the left and connected with lines. Every workflow needs one Start block and at least one End block.
- Save: the "Save workflow" button. Run: the "▶ Run" button saves, then asks for the run input as JSON (e.g. {"name": "Ada"}; {} if nothing is needed) and opens the run page with each step's result. "Run history" lists past runs.
- Run input is read in blocks with expressions: {{ $input.name }} or {{ $json.field }}; another block's output: {{ $node["Label"].json.field }}.
- Automatic starts: "⚡ Publish & triggers" publishes the workflow and shows its triggers: a webhook URL (POST to /webhooks/<token>; the body becomes the input), schedules (need a Schedule Trigger block) and chat. Workflows must be published for triggers to fire.
- From code: POST /api/graphs/<workflow id>/runs with body {"input": {...}} and a signed-in session or API key. There is no /api/workflows/... endpoint.
- Integrations (Slack, GitHub, Notion, Wikipedia, Tavily ...): connect a service once on the Integrations page (top menu); its key is saved encrypted. In the block, choose the Action and the saved key (credential_id). Wikipedia needs no key.
- AI Agent block: an AI that can use tools. Add tools with "+ Add integration tool" under its Tools box; the AI fills in only the details. Tools that send or change things (Slack message, GitHub issue ...) need "allow writes" ticked. The agent needs a model, base_url (Groq: https://api.groq.com/openai/v1) and an AI key from Saved keys.
- Runs need the worker and Temporal running; a run stuck on "queued" means one of them is not running.
- You (the Copilot) cannot press buttons or run workflows yourself; you change the canvas and explain the exact steps.`;

const RULES = `RULES:
- For questions and conversation, answer briefly in plain words using the facts above. Do NOT return a graph.
- When the user asks to create, build, modify, add, remove, connect or update the canvas/workflow:
  1. If a CURRENT CANVAS STATE is given, change that canvas.
  2. Return ONLY a JSON object in this exact shape:
{
  "explanation": "A short explanation of the changes",
  "nodes": [ { "id": "node_id", "type": "node_type", "label": "Node Label", "config": {} } ],
  "edges": [ { "source": "node_id_1", "target": "node_id_2" } ]
}
- Use ONLY the block types listed under AVAILABLE BLOCKS, with their exact config field names.
- Integration blocks need "action" (one of the listed actions) and, unless marked no key, "credential_id" (leave it out if unknown and tell the user to pick the saved key).
- Node ids must be unique; connect edges in logical order from start to end.`;

type Spec = {
  type: string; display_name: string; category: string; description: string;
  parameters?: { properties?: Record<string, { enum?: unknown[]; "x-enum-labels"?: Record<string, string> }> ; required?: string[] };
};

const FALLBACK_BLOCKS = `- start, end, manual_trigger, webhook_trigger, schedule_trigger
- template (config: template), set (fields), http (url, method, headers, body, credential_id), llm (prompt, model, base_url, credential_id), agent (prompt, model, base_url, credential_id, tools, allow_writes), if (condition), switch, merge`;

let cache: { at: number; text: string } | null = null;

/** One line per block: type, category, what it does, its settings; integrations also list their actions. */
function describe(spec: Spec): string {
  const props = spec.parameters?.properties ?? {};
  const fields = Object.keys(props).filter((k) => k !== "action");
  const actions = props.action?.enum?.map((a) => `${a}${props.action?.["x-enum-labels"]?.[String(a)] ? ` (${props.action["x-enum-labels"][String(a)]})` : ""}`);
  const noKey = spec.category === "integration" && !("credential_id" in props) ? " [no key needed]" : "";
  return `- ${spec.type} [${spec.category}]${noKey}: ${spec.description}` +
    (actions?.length ? ` actions: ${actions.join(", ")}.` : "") +
    (fields.length ? ` config: ${fields.join(", ")}` : "");
}

export async function nodeCatalogue(): Promise<string> {
  if (cache && Date.now() - cache.at < CACHE_MS) return cache.text;
  try {
    const response = await fetch(`${API_URL}/api/node-types`, { cache: "no-store", signal: AbortSignal.timeout(3000) });
    if (!response.ok) throw new Error(String(response.status));
    const specs = (await response.json()) as Spec[];
    const text = specs.filter((s) => !["trigger", "output"].includes(s.type)).map(describe).join("\n");
    cache = { at: Date.now(), text };
    return text;
  } catch {
    return FALLBACK_BLOCKS;
  }
}

/** The full system prompt for the editor chat. */
export async function buildSystemPrompt(): Promise<string> {
  return `You are AgentForge Copilot, the assistant inside the AgentForge workflow editor. You explain AgentForge and design or edit its workflow canvas.

${AGENTFORGE_GUIDE}

AVAILABLE BLOCKS:
${await nodeCatalogue()}

${RULES}`;
}

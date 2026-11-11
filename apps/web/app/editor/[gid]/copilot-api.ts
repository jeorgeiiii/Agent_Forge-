/**
 * Copilot helpers - everything the chat panel needs that is NOT drawing the screen.
 *
 * How the Copilot works, step by step:
 *   1. loadChatHistory()   - fetch the earlier messages saved in the database for this workflow.
 *   2. sendChatPrompt()    - send the new question (plus recent chat and the current canvas) to /api/chat.
 *   3. The answer streams back word by word (onToken), then arrives complete (onDone).
 *   4. If the answer contains a workflow, toGraphDefinition() turns it into canvas format
 *      so the editor can draw it.
 */
import { getChatHistory, type GraphDefinition } from "@/lib/api";
import { readSSEStream } from "@/lib/sse";

// ------------------------------------------------------------------ types

/** One block in a workflow suggested by the Copilot. */
export type AgentNode = { id: string; type: string; label: string; config: any };

/** A whole workflow suggested by the Copilot, plus its plain-English explanation. */
export type AgentGraph = {
  explanation: string;
  nodes: AgentNode[];
  edges: { source: string; target: string }[];
};

/** One chat bubble. `graph` is set when the assistant suggested a workflow. */
export type Message = { role: "user" | "assistant"; content: string; graph?: AgentGraph | null };

/** Callbacks used while an answer is arriving. */
type ReplyHandlers = {
  onToken: (textSoFar: string) => void;                                  // more words arrived
  onDone: (text: string, graph: AgentGraph | null) => void;              // answer is complete
  onError: (message: string) => void;                                    // the stream reported a problem
};

// ------------------------------------------------------------------ constants

/** First message shown when a workflow has no saved chat yet. */
export const WELCOME_MESSAGE: Message = {
  role: "assistant",
  content:
    "Hi! I am your Canvas Copilot. You can tell me in plain English how to build or modify your canvas. For example:\n" +
    "• 'Add an HTTP node to call weather API'\n• 'Delete the template node'\n" +
    "• 'Add a Slack notification node before the end node'",
};

/** One-click example prompts shown above the message box. */
export const QUICK_PROMPTS = [
  "Add HTTP node to call weather API",
  "Add Slack alert node before End",
  "Create email summarizer workflow",
];

/** How many recent messages are sent along as context (keeps requests small). */
const HISTORY_LIMIT = 20;

// ------------------------------------------------------------------ helpers

/**
 * Loads the saved conversation for this workflow from the database.
 * Returns an empty list when there is no history yet.
 */
export async function loadChatHistory(graphId: string): Promise<Message[]> {
  const stored = await getChatHistory(graphId);
  return (stored ?? []).map((item) => ({
    role: item.role as Message["role"],
    content: item.content,
    graph: item.graph_data || null,
  }));
}

/**
 * Converts a Copilot workflow into the format the canvas editor understands
 * (fills in an empty config for blocks that have none).
 */
export function toGraphDefinition(graph: AgentGraph): GraphDefinition {
  return {
    nodes: graph.nodes.map((n) => ({ id: n.id, type: n.type, label: n.label, config: n.config || {} })),
    edges: graph.edges,
  };
}

/** Returns a copy of the chat with the last bubble replaced (used while an answer streams in). */
export function replaceLastMessage(messages: Message[], next: Message): Message[] {
  if (messages.length === 0) return messages;
  return [...messages.slice(0, -1), next];
}

/** Colour of the left stripe for each block type in the "Generated Workflow" preview. */
export function previewColor(type: string): string {
  if (type === "llm") return "#6366f1";
  if (type === "tool") return "#0891b2";
  if (type === "condition") return "#d97706";
  return "#059669";
}

/**
 * Checks whether a piece of text is a workflow (JSON with `nodes` and `edges` lists).
 * Returns the workflow, or null when it is just a normal chat answer.
 */
function parseGraph(text: string): AgentGraph | null {
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && Array.isArray(parsed.nodes) && Array.isArray(parsed.edges)) {
      return parsed as AgentGraph;
    }
  } catch {
    /* plain conversational message */
  }
  return null;
}

/**
 * Reads the text out of a non-streaming answer. Handles three shapes:
 * plain JSON ({ text } or { error }), raw "data: ..." event lines, or plain text.
 */
function parsePlainReply(raw: string): { text?: string; error?: string } {
  try {
    return JSON.parse(raw);
  } catch {
    if (!raw.includes("data:")) return { text: raw };
  }
  let extracted = "";
  for (const line of raw.split("\n")) {
    if (!line.startsWith("data:")) continue;
    const payload = line.slice(5).trim();
    if (!payload || payload === "[DONE]") continue;
    try {
      const event = JSON.parse(payload);
      if (event.type === "token") extracted += event.content;
      else if (event.type === "done") extracted = event.text || extracted;
    } catch {
      /* ignore broken lines */
    }
  }
  return { text: extracted || raw };
}

/** Turns a failed HTTP response into a readable error message. */
async function errorFromResponse(res: Response): Promise<Error> {
  let text = `Chat request failed (HTTP ${res.status}).`;
  try {
    const body = await res.json();
    if (body.error) text = body.error;
  } catch {
    /* body was not JSON */
  }
  return new Error(text);
}

// ------------------------------------------------------------------ main call

/**
 * Sends a question to the Copilot and reports the answer through `handlers`.
 *
 * @param prompt       what the user typed
 * @param graphId      the workflow id (also used as the chat session id)
 * @param history      the conversation so far, including the new question
 * @param currentGraph the blocks currently on the canvas, so the AI can edit them
 *
 * Throws an Error when the request itself fails (network, HTTP error, empty answer).
 */
export async function sendChatPrompt(
  prompt: string,
  graphId: string,
  history: Message[],
  currentGraph: GraphDefinition | null,
  handlers: ReplyHandlers,
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      prompt,
      sessionId: graphId,
      stream: true,
      context: {
        history: history.slice(-HISTORY_LIMIT).map(({ role, content }) => ({ role, content })),
        currentGraph,
      },
    }),
  });
  if (!res.ok) throw await errorFromResponse(res);

  // Normal case: the answer streams in word by word.
  if ((res.headers.get("content-type") || "").includes("text/event-stream")) {
    let soFar = "";
    await readSSEStream(
      res,
      (token) => {
        soFar += token;
        handlers.onToken(soFar);
      },
      ({ text, graph }) => handlers.onDone(text || soFar, graph || null),
      handlers.onError,
    );
    return;
  }

  // Fallback: the whole answer arrives at once.
  const data = parsePlainReply(await res.text());
  if (data.error) throw new Error(data.error);
  if (typeof data.text !== "string" || !data.text) throw new Error("Chat API returned an empty response.");
  const graph = parseGraph(data.text);
  handlers.onDone(graph ? graph.explanation : data.text, graph);
}

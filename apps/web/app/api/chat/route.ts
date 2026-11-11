import { NextResponse } from "next/server";
import { saveChatMessage } from "@/lib/api";
import { checkRateLimit } from "@/lib/rate-limiter";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

const SYSTEM_PROMPT = `You are AgentForge Copilot, an AI assistant and workflow canvas designer.
You have the power to create and directly manipulate workflow canvas graphs based on user prompts.

RULES:
- For general conversation, questions, or greetings (e.g. "Hello", "How does this work?"), respond naturally and conversationally. Do NOT return a graph for casual conversation.
- When the user asks to create, build, modify, add nodes to, remove nodes from, connect, or update the canvas/workflow:
  1. If a CURRENT CANVAS STATE is provided below, modify that canvas (e.g. add the requested node, remove requested node, update node config, connect edges).
  2. Return ONLY a valid JSON object in this exact shape:
{
  "explanation": "A concise explanation of the changes made to the canvas",
  "nodes": [
    { "id": "node_id", "type": "node_type", "label": "Node Label", "config": {} }
  ],
  "edges": [
    { "source": "node_id_1", "target": "node_id_2" }
  ]
}
Supported node types include: "start", "end", "template", "http", "llm", "tool", "condition", "trigger", "output".
Always ensure node IDs are unique and edges connect the nodes in logical order.`;

const DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b";

function isChatMessage(value: unknown): value is ChatMessage {
  if (!value || typeof value !== "object") return false;
  const message = value as Record<string, unknown>;
  return (
    (message.role === "user" || message.role === "assistant") &&
    typeof message.content === "string" &&
    message.content.length > 0 &&
    message.content.length <= 10000
  );
}

function getChatHistory(value: unknown): ChatMessage[] | null {
  if (value === undefined) return [];
  if (!Array.isArray(value)) return null;

  return value
    .slice(-20)
    .filter(isChatMessage)
    .map(({ role, content }) => ({ role, content }));
}

function normalizeAgentGraph(value: unknown): {
  explanation: string;
  nodes: { id: string; type: string; label: string; config: Record<string, unknown> }[];
  edges: { source: string; target: string }[];
} | null {
  if (!value || typeof value !== "object") return null;
  const graph = value as Record<string, unknown>;
  if (!Array.isArray(graph.nodes) || graph.nodes.length < 2) return null;

  const ids = new Set<string>();
  const nodes: {
    id: string;
    type: string;
    label: string;
    config: Record<string, unknown>;
  }[] = [];
  for (const item of graph.nodes) {
    if (!item || typeof item !== "object") return null;
    const node = item as Record<string, unknown>;
    if (
      typeof node.id !== "string" ||
      !node.id ||
      ids.has(node.id) ||
      typeof node.type !== "string" ||
      typeof node.label !== "string"
    ) {
      return null;
    }
    ids.add(node.id);
    nodes.push({
      id: node.id,
      type: node.type,
      label: node.label,
      config:
        node.config && typeof node.config === "object" && !Array.isArray(node.config)
          ? node.config as Record<string, unknown>
          : {},
    });
  }

  const edges = Array.isArray(graph.edges)
    ? graph.edges.flatMap((item) => {
        if (!item || typeof item !== "object") return [];
        const edge = item as Record<string, unknown>;
        if (
          typeof edge.source !== "string" ||
          typeof edge.target !== "string" ||
          !ids.has(edge.source) ||
          !ids.has(edge.target)
        ) {
          return [];
        }
        return [{ source: edge.source, target: edge.target }];
      })
    : [];
  const connectedEdges = edges.length > 0
    ? edges
    : nodes.slice(1).map((node, index) => ({
        source: nodes[index].id,
        target: node.id,
      }));

  return {
    explanation:
      typeof graph.explanation === "string" && graph.explanation.trim()
        ? graph.explanation
        : "Here is a workflow based on your request.",
    nodes,
    edges: connectedEdges,
  };
}

export async function POST(request: Request) {
  // Enforce sliding window rate limiting (20 requests per minute)
  const forwarded = request.headers.get("x-forwarded-for");
  const clientIp = forwarded ? forwarded.split(",")[0].trim() : "127.0.0.1";
  const rateLimitResult = checkRateLimit(`chat:${clientIp}`, 20, 60 * 1000);

  if (!rateLimitResult.allowed) {
    return NextResponse.json(
      {
        error: `Rate limit exceeded: maximum 20 requests per minute. Please retry in ${rateLimitResult.retryAfterSeconds}s.`,
      },
      {
        status: 429,
        headers: {
          "Retry-After": String(rateLimitResult.retryAfterSeconds),
          "X-RateLimit-Limit": "20",
          "X-RateLimit-Remaining": "0",
        },
      }
    );
  }

  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "GROQ_API_KEY is missing from apps/web/.env.local. Restart the Next.js server after setting it." },
      { status: 503 },
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Request body must be valid JSON." }, { status: 400 });
  }

  if (!body || typeof body !== "object") {
    return NextResponse.json({ error: "Request body must be a JSON object." }, { status: 400 });
  }

  const { prompt, context, sessionId, stream: wantsStreamParam } = body as {
    prompt?: unknown;
    sessionId?: string;
    stream?: boolean;
    context?: { history?: unknown; currentGraph?: unknown };
  };
  if (typeof prompt !== "string" || !prompt.trim() || prompt.length > 10000) {
    return NextResponse.json({ error: "Prompt must contain 1–10,000 characters." }, { status: 400 });
  }

  // Persist user message to PostgreSQL chat_messages table
  if (typeof sessionId === "string" && sessionId) {
    saveChatMessage(sessionId, { role: "user", content: prompt }).catch((err) =>
      console.warn("Could not persist user message to Postgres:", err)
    );
  }

  const history = getChatHistory(context?.history);
  if (!history) {
    return NextResponse.json({ error: "Chat history must be a list of messages." }, { status: 400 });
  }

  const messages = history;
  if (messages.at(-1)?.role === "user" && messages.at(-1)?.content === prompt) {
    messages.pop();
  }

  const acceptHeader = request.headers.get("accept") || "";
  const isStreaming = wantsStreamParam === true || acceptHeader.includes("text/event-stream");

  try {
    const model = process.env.GROQ_MODEL || DEFAULT_GROQ_MODEL;

    // Append canvas context if available so AI can manipulate existing nodes & edges
    let systemInstruction = SYSTEM_PROMPT;
    if (context?.currentGraph && typeof context.currentGraph === "object") {
      try {
        const graphSummary = JSON.stringify(context.currentGraph, null, 2);
        systemInstruction += `\n\nCURRENT CANVAS STATE:\n${graphSummary}\n\nWhen asked to add, delete, connect, or edit nodes, manipulate this current canvas state and return the complete updated graph.`;
      } catch {
        // ignore serialization error
      }
    }

    const requestPayload = {
      model,
      messages: [
        { role: "system", content: systemInstruction },
        ...messages,
        { role: "user", content: prompt },
      ],
      temperature: 0.3,
    };

    if (isStreaming) {
      const groqResponse = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ...requestPayload,
          stream: true,
        }),
        signal: AbortSignal.timeout(45000),
      });

      if (!groqResponse.ok) {
        const errorResult = await groqResponse.json().catch(() => null);
        const providerMessage = (errorResult as { error?: { message?: string } })?.error?.message;
        console.error("Groq chat streaming request failed:", groqResponse.status, providerMessage);
        return NextResponse.json(
          { error: providerMessage || `Groq returned HTTP ${groqResponse.status}.` },
          { status: 502 }
        );
      }

      const encoder = new TextEncoder();
      const decoder = new TextDecoder("utf-8");

      const sseStream = new ReadableStream({
        async start(controller) {
          if (!groqResponse.body) {
            controller.enqueue(
              encoder.encode(`data: ${JSON.stringify({ type: "error", error: "No response body from Groq" })}\n\n`)
            );
            controller.close();
            return;
          }

          const reader = groqResponse.body.getReader();
          let buffer = "";
          let accumulatedContent = "";

          try {
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;

              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split("\n");
              buffer = lines.pop() || "";

              for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || !trimmed.startsWith("data:")) continue;
                const dataStr = trimmed.slice(5).trim();
                if (dataStr === "[DONE]") continue;

                try {
                  const parsed = JSON.parse(dataStr);
                  const delta = parsed.choices?.[0]?.delta?.content;
                  if (typeof delta === "string" && delta.length > 0) {
                    accumulatedContent += delta;
                    controller.enqueue(
                      encoder.encode(`data: ${JSON.stringify({ type: "token", content: delta })}\n\n`)
                    );
                  }
                } catch {
                  // Partial JSON chunk, continue buffering
                }
              }
            }

            // Check if trailing content in buffer
            if (buffer.trim()) {
              const trimmed = buffer.trim();
              if (trimmed.startsWith("data:")) {
                const dataStr = trimmed.slice(5).trim();
                if (dataStr !== "[DONE]") {
                  try {
                    const parsed = JSON.parse(dataStr);
                    const delta = parsed.choices?.[0]?.delta?.content;
                    if (typeof delta === "string" && delta.length > 0) {
                      accumulatedContent += delta;
                      controller.enqueue(
                        encoder.encode(`data: ${JSON.stringify({ type: "token", content: delta })}\n\n`)
                      );
                    }
                  } catch {}
                }
              }
            }

            // Parse for workflow graph if any
            let graph: unknown = null;
            try {
              const trimmed = accumulatedContent.trim();
              const unquoted = trimmed.startsWith("```")
                ? trimmed.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "")
                : trimmed;
              graph = JSON.parse(unquoted);
            } catch {
              const match = accumulatedContent.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
              if (match) {
                try {
                  graph = JSON.parse(match[1].trim());
                } catch {
                  graph = null;
                }
              }
            }
            const normalizedGraph = graph ? normalizeAgentGraph(graph) : null;

            // Persist assistant message to PostgreSQL chat_messages table
            if (typeof sessionId === "string" && sessionId) {
              saveChatMessage(sessionId, {
                role: "assistant",
                content: normalizedGraph ? normalizedGraph.explanation : accumulatedContent,
                graph_data: normalizedGraph || undefined,
              }).catch((err) => console.warn("Could not persist assistant message to Postgres:", err));
            }

            // Send done event with final text and graph (if any)
            controller.enqueue(
              encoder.encode(
                `data: ${JSON.stringify({
                  type: "done",
                  text: normalizedGraph ? normalizedGraph.explanation : accumulatedContent,
                  graph: normalizedGraph || undefined,
                })}\n\n`
              )
            );
            controller.enqueue(encoder.encode("data: [DONE]\n\n"));
            controller.close();
          } catch (err) {
            const errMsg = err instanceof Error ? err.message : "Error reading Groq stream";
            controller.enqueue(
              encoder.encode(`data: ${JSON.stringify({ type: "error", error: errMsg })}\n\n`)
            );
            controller.close();
          } finally {
            reader.releaseLock();
          }
        },
      });

      return new Response(sseStream, {
        headers: {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-cache, no-transform",
          Connection: "keep-alive",
        },
      });
    }

    // Non-streaming fallback
    const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(requestPayload),
      signal: AbortSignal.timeout(45000),
    });

    const result: unknown = await response.json().catch(() => null);

    if (!response.ok) {
      const errorBody = result as { error?: { message?: string } } | null;
      const providerMessage = errorBody?.error?.message;
      console.error("Groq chat request failed:", response.status, providerMessage);
      const modelUnavailable =
        typeof providerMessage === "string" &&
        providerMessage.toLowerCase().includes("model") &&
        (providerMessage.toLowerCase().includes("does not exist") ||
          providerMessage.toLowerCase().includes("do not have access"));
      return NextResponse.json(
        {
          error: modelUnavailable
            ? `Groq model "${model}" is unavailable for this API key. Unset GROQ_MODEL or set it to an active model enabled for your Groq account.`
            : providerMessage || `Groq returned HTTP ${response.status}.`,
        },
        { status: 502 },
      );
    }

    const choice = (result as {
      choices?: { message?: { content?: unknown } }[];
    } | null)?.choices?.[0];
    const content = choice?.message?.content;
    if (typeof content !== "string" || !content.trim()) {
      console.error("Groq returned no chat completion content.");
      return NextResponse.json({ error: "Groq returned an empty response. Try again." }, { status: 502 });
    }

    let graph: unknown = null;
    try {
      const trimmed = content.trim();
      const unquoted = trimmed.startsWith("```")
        ? trimmed.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "")
        : trimmed;
      graph = JSON.parse(unquoted);
    } catch {
      const match = content.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
      if (match) {
        try {
          graph = JSON.parse(match[1].trim());
        } catch {
          graph = null;
        }
      }
    }
    const normalizedGraph = graph ? normalizeAgentGraph(graph) : null;
    const finalContent = normalizedGraph ? JSON.stringify(normalizedGraph) : content;

    // Persist assistant message to PostgreSQL chat_messages table
    if (typeof sessionId === "string" && sessionId) {
      saveChatMessage(sessionId, {
        role: "assistant",
        content: normalizedGraph ? normalizedGraph.explanation : content,
        graph_data: normalizedGraph || undefined,
      }).catch((err) => console.warn("Could not persist assistant message to Postgres:", err));
    }

    return NextResponse.json({
      text: finalContent,
    });
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "TimeoutError";
    console.error("Groq chat request failed:", error);
    return NextResponse.json(
      { error: timedOut ? "The AI request timed out. Please try again." : "Could not reach Groq. Check your connection and try again." },
      { status: timedOut ? 504 : 502 },
    );
  }
}

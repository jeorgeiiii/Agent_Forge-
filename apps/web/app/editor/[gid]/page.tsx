"use client";

import { use, useState, useEffect, useRef } from "react";
import GraphEditor from "./graph-editor";
import { getChatHistory, type GraphDefinition } from "@/lib/api";
import { readSSEStream } from "@/lib/sse";

type AgentNode = { id: string; type: string; label: string; config: any };
type AgentGraph = { explanation: string; nodes: AgentNode[]; edges: { source: string; target: string }[] };
type Message = {
  role: "user" | "assistant";
  content: string;
  graph?: AgentGraph | null;
};

export default function Page({ params }: { params: Promise<{ gid: string }> }) {
  const { gid } = use(params);
  const [tab, setTab] = useState<"canvas" | "chat" | "split">("split");
  const [canvasGraph, setCanvasGraph] = useState<GraphDefinition | null>(null);
  const [aiAppliedGraph, setAiAppliedGraph] = useState<GraphDefinition | null>(null);
  const [lastAction, setLastAction] = useState<string>("");

  function handleApplyGraph(graph: AgentGraph) {
    const formatted: GraphDefinition = {
      nodes: graph.nodes.map((n) => ({
        id: n.id,
        type: n.type,
        label: n.label,
        config: n.config || {},
      })),
      edges: graph.edges,
    };
    setAiAppliedGraph(formatted);
    setLastAction("Canvas modified by AI Agent");
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <header
        style={{
          height: 52,
          background: "#fff",
          borderBottom: "1px solid #e2e8f0",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 16px",
          zIndex: 20,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <a href="/" style={{ textDecoration: "none", color: "#64748b", fontSize: 13, fontWeight: 500 }}>
            ← Agents
          </a>
          <span style={{ color: "#cbd5e1" }}>|</span>
          <span style={{ fontWeight: 600, fontSize: 15, color: "#0f172a" }}>AgentForge Studio</span>
          <span style={{ fontSize: 12, background: "#f1f5f9", padding: "2px 8px", borderRadius: 4, color: "#64748b" }}>
            ID: {gid}
          </span>
          {lastAction && (
            <span style={{ fontSize: 12, background: "#ecfdf5", color: "#059669", padding: "2px 8px", borderRadius: 4, fontWeight: 500 }}>
              ✨ {lastAction}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            onClick={() => setTab("canvas")}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: tab === "canvas" ? "#0f172a" : "#fff",
              color: tab === "canvas" ? "#fff" : "#0f172a",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 500,
            }}
          >
            🎨 Canvas
          </button>
          <button
            onClick={() => setTab("split")}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: tab === "split" ? "#0f172a" : "#fff",
              color: tab === "split" ? "#fff" : "#0f172a",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 500,
            }}
          >
            ◫ Split View
          </button>
          <button
            onClick={() => setTab("chat")}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid #cbd5e1",
              background: tab === "chat" ? "#0f172a" : "#fff",
              color: tab === "chat" ? "#fff" : "#0f172a",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 500,
            }}
          >
            💬 Copilot
          </button>
        </div>
      </header>

      <div style={{ display: "flex", flex: 1, height: "calc(100vh - 52px)", width: "100%", overflow: "hidden" }}>
        {(tab === "canvas" || tab === "split") && (
          <div style={{ flex: tab === "split" ? "1 1 58%" : "1 1 100%", width: tab === "split" ? "58%" : "100%", minWidth: 0, height: "100%", position: "relative" }}>
            <GraphEditor
              graphId={gid}
              externalGraph={aiAppliedGraph}
              onGraphChange={(g) => setCanvasGraph(g)}
            />
          </div>
        )}
        {(tab === "chat" || tab === "split") && (
          <div
            style={{
              flex: tab === "split" ? "0 0 460px" : "1 1 100%",
              width: tab === "split" ? "460px" : "100%",
              minWidth: tab === "split" ? 360 : 0,
              height: "100%",
              borderLeft: tab === "split" ? "1px solid #e2e8f0" : "none",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <ChatUI
              gid={gid}
              currentCanvasGraph={canvasGraph}
              onApplyGraph={handleApplyGraph}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function ChatUI({
  gid,
  currentCanvasGraph,
  onApplyGraph,
}: {
  gid: string;
  currentCanvasGraph: GraphDefinition | null;
  onApplyGraph: (graph: AgentGraph) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content: "Hi! I am your Canvas Copilot. You can tell me in plain English how to build or modify your canvas. For example:\n• 'Add an HTTP node to call weather API'\n• 'Delete the template node'\n• 'Add a Slack notification node before the end node'",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // Restore persistent conversation memory from PostgreSQL for this session
  useEffect(() => {
    let active = true;
    getChatHistory(gid)
      .then((stored) => {
        if (!active || !stored || stored.length === 0) return;
        const loaded: Message[] = stored.map((item) => ({
          role: item.role as "user" | "assistant",
          content: item.content,
          graph: item.graph_data || null,
        }));
        setMessages(loaded);
      })
      .catch((err) => {
        console.warn("Could not load chat memory from Postgres:", err);
      });

    return () => {
      active = false;
    };
  }, [gid]);

  async function handleSendPrompt(promptText: string) {
    if (!promptText.trim() || loading) return;

    const userMsg: Message = { role: "user", content: promptText.trim() };
    const placeholderMsg: Message = { role: "assistant", content: "" };
    const newMessages = [...messages, userMsg];

    setMessages([...newMessages, placeholderMsg]);
    setInput("");
    setLoading(true);
    setMsg("");

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify({
          prompt: userMsg.content,
          sessionId: gid,
          stream: true,
          context: {
            history: newMessages.slice(-20).map(({ role, content }) => ({ role, content })),
            // Provide current canvas nodes & edges to the AI agent
            currentGraph: currentCanvasGraph,
          },
        }),
      });

      if (!res.ok) {
        let errText = `Chat request failed (HTTP ${res.status}).`;
        try {
          const errJson = await res.json();
          if (errJson.error) errText = errJson.error;
        } catch {}
        throw new Error(errText);
      }

      const contentType = res.headers.get("content-type") || "";
      if (contentType.includes("text/event-stream")) {
        let accumulatedText = "";
        await readSSEStream(
          res,
          (token) => {
            accumulatedText += token;
            setMessages((prev) => {
              const updated = [...prev];
              if (updated.length > 0) {
                updated[updated.length - 1] = {
                  role: "assistant",
                  content: accumulatedText,
                };
              }
              return updated;
            });
          },
          ({ text, graph }) => {
            if (graph) {
              onApplyGraph(graph);
            }
            setMessages((prev) => {
              const updated = [...prev];
              if (updated.length > 0) {
                updated[updated.length - 1] = {
                  role: "assistant",
                  content: text || accumulatedText,
                  graph: graph || null,
                };
              }
              return updated;
            });
            setLoading(false);
          },
          (errorMsg) => {
            setMsg(`Error: ${errorMsg}`);
            setLoading(false);
          }
        );
      } else {
        // Non-streaming fallback
        const textData = await res.text();
        let data: { text?: string; error?: string } = {};
        try {
          data = JSON.parse(textData);
        } catch {
          if (textData.includes("data:")) {
            const lines = textData.split("\n");
            let extracted = "";
            for (const line of lines) {
              if (line.startsWith("data:")) {
                const s = line.slice(5).trim();
                if (s && s !== "[DONE]") {
                  try {
                    const p = JSON.parse(s);
                    if (p.type === "token") extracted += p.content;
                    else if (p.type === "done") extracted = p.text || extracted;
                  } catch {}
                }
              }
            }
            data = { text: extracted || textData };
          } else {
            data = { text: textData };
          }
        }

        if (data.error) throw new Error(data.error);
        const responseText = data.text;
        if (typeof responseText !== "string" || !responseText) {
          throw new Error("Chat API returned an empty response.");
        }

        let graph: AgentGraph | null = null;
        try {
          const parsed = JSON.parse(responseText);
          if (
            parsed &&
            typeof parsed === "object" &&
            Array.isArray(parsed.nodes) &&
            Array.isArray(parsed.edges)
          ) {
            graph = parsed as AgentGraph;
          }
        } catch {
          // Plain conversational message
        }

        if (graph) {
          onApplyGraph(graph);
        }

        setMessages((prev) => {
          const updated = [...prev];
          if (updated.length > 0) {
            updated[updated.length - 1] = {
              role: "assistant",
              content: graph ? graph.explanation : responseText,
              graph: graph,
            };
          }
          return updated;
        });
      }
    } catch (err: unknown) {
      setMsg(`Error: ${err instanceof Error ? err.message : "The chat request failed."}`);
      // Remove empty placeholder message if request failed before streaming
      setMessages((prev) => {
        if (prev.length > 0 && prev[prev.length - 1].content === "") {
          return prev.slice(0, -1);
        }
        return prev;
      });
    } finally {
      setLoading(false);
    }
  }

  const quickPrompts = [
    "Add HTTP node to call weather API",
    "Add Slack alert node before End",
    "Create email summarizer workflow",
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", background: "#f8fafc", fontFamily: "system-ui, sans-serif" }}>
      
      {/* Header */}
      <header style={{ height: 56, background: "#fff", borderBottom: "1px solid #e2e8f0", display: "flex", alignItems: "center", padding: "0 20px", justifyContent: "space-between" }}>
        <div style={{ fontWeight: 600, fontSize: 16 }}>AgentForge Copilot</div>
        <div style={{ color: "#64748b", fontSize: 13 }}>Session: {gid}</div>
      </header>

      {/* Chat History */}
      <div style={{ flex: 1, overflowY: "auto", padding: "20px", display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ maxWidth: 800, width: "100%", margin: "0 auto", display: "flex", flexDirection: "column", gap: 16 }}>
          
          {messages.map((m, i) => (
            <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
              <div style={{
                background: m.role === "user" ? "#0f172a" : "#fff",
                color: m.role === "user" ? "#fff" : "#0f172a",
                padding: "12px 16px",
                borderRadius: 12,
                maxWidth: "85%",
                border: m.role === "assistant" ? "1px solid #e2e8f0" : "none",
                boxShadow: "0 1px 2px rgba(0,0,0,0.05)",
                lineHeight: 1.5,
                fontSize: 14,
                width: m.graph ? "100%" : "auto",
              }}>
                <div style={{ whiteSpace: "pre-wrap" }}>
                  {m.content}
                  {loading && i === messages.length - 1 && (
                    <span style={{ display: "inline-block", width: 6, height: 14, background: "#4f46e5", marginLeft: 4, verticalAlign: "middle" }} />
                  )}
                  {!m.content && loading && i === messages.length - 1 && (
                    <span style={{ color: "#94a3b8", fontStyle: "italic" }}>Thinking...</span>
                  )}
                </div>
                
                {/* Render the Agent Graph visually if it exists */}
                {m.graph && (
                  <div style={{ marginTop: 16, padding: 16, background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5 }}>
                        Generated Workflow
                      </div>
                      <button
                        type="button"
                        onClick={() => onApplyGraph(m.graph!)}
                        style={{
                          background: "#4f46e5",
                          color: "#fff",
                          border: "none",
                          padding: "4px 10px",
                          borderRadius: 6,
                          fontSize: 11,
                          fontWeight: 600,
                          cursor: "pointer",
                        }}
                      >
                        ⚡ Re-apply to Canvas
                      </button>
                    </div>
                    
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {m.graph.nodes.map((node, idx) => (
                        <div key={node.id} style={{ 
                          display: "flex", alignItems: "center", gap: 12, 
                          background: "#fff", padding: "10px 12px", borderRadius: 6, 
                          borderLeft: `4px solid ${
                            node.type === "llm" ? "#6366f1" : 
                            node.type === "tool" ? "#0891b2" : 
                            node.type === "condition" ? "#d97706" : "#059669"
                          }` 
                        }}>
                          <div style={{ fontWeight: 600, fontSize: 13, width: 100, color: "#475569" }}>
                            {node.type.toUpperCase()}
                          </div>
                          <div style={{ fontSize: 14, flex: 1 }}>{node.label}</div>
                          {idx < m.graph!.nodes.length - 1 && (
                             <div style={{ color: "#94a3b8", fontSize: 16 }}>↓</div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div style={{ display: "flex", justifyContent: "flex-start" }}>
              <div style={{ background: "#fff", padding: "12px 16px", borderRadius: 12, border: "1px solid #e2e8f0", color: "#64748b", fontSize: 14 }}>
                Designing workflow...
              </div>
            </div>
          )}
          
          {msg && <div style={{ color: "#dc2626", fontSize: 13, textAlign: "center" }}>{msg}</div>}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input Area with Quick Canvas Prompts */}
      <div style={{ borderTop: "1px solid #e2e8f0", background: "#fff", padding: "12px 16px" }}>
        {/* Quick prompt buttons for canvas manipulation */}
        <div style={{ display: "flex", gap: 6, marginBottom: 10, overflowX: "auto", paddingBottom: 2 }}>
          {quickPrompts.map((qp, idx) => (
            <button
              key={idx}
              type="button"
              disabled={loading}
              onClick={() => handleSendPrompt(qp)}
              style={{
                background: "#f1f5f9",
                border: "1px solid #e2e8f0",
                borderRadius: 16,
                padding: "4px 10px",
                fontSize: 12,
                color: "#475569",
                cursor: loading ? "not-allowed" : "pointer",
                whiteSpace: "nowrap",
                fontWeight: 500,
              }}
            >
              🪄 {qp}
            </button>
          ))}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSendPrompt(input);
          }}
          style={{ maxWidth: 800, margin: "0 auto", display: "flex", gap: 10 }}
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="e.g., Add an HTTP node before End to fetch user profile..."
            disabled={loading}
            style={{ flex: 1, padding: "10px 14px", borderRadius: 8, border: "1px solid #cbd5e1", fontSize: 13, outline: "none" }}
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            style={{
              background: input.trim() && !loading ? "#4f46e5" : "#cbd5e1",
              color: "#fff",
              border: "none",
              borderRadius: 8,
              padding: "0 18px",
              fontWeight: 600,
              fontSize: 13,
              cursor: input.trim() && !loading ? "pointer" : "not-allowed",
            }}
          >
            Send
          </button>
        </form>
      </div>
    </div>
  );
}
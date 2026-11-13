"use client";
/**
 * Copilot chat panel - the "💬 Copilot" side of the editor.
 * The user describes a change in plain English; the AI answers and, when it
 * suggests a workflow, the canvas is updated through `onApplyGraph`.
 * All network and parsing work lives in ./copilot-api.ts - this file only draws the chat.
 * Styles: the `.cp-*` classes in app/globals.css.
 */
import { useEffect, useRef, useState } from "react";
import type { GraphDefinition } from "@/lib/api";
import {
  QUICK_PROMPTS, WELCOME_MESSAGE, loadChatHistory, previewColor, replaceLastMessage, sendChatPrompt,
  type AgentGraph, type Message,
} from "./copilot-api";

type Props = {
  gid: string;                                  // workflow id (also the chat session id)
  currentCanvasGraph: GraphDefinition | null;   // what is on the canvas right now
  onApplyGraph: (graph: AgentGraph) => void;    // draw a suggested workflow on the canvas (it is saved automatically)
  onRun: () => void;                             // run the saved workflow; the result panel opens
};

export default function CopilotChat({ gid, currentCanvasGraph, onApplyGraph, onRun }: Props) {
  const [messages, setMessages] = useState<Message[]>([WELCOME_MESSAGE]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  // Keep the newest message in view.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // Restore the saved conversation from the database when the editor opens.
  useEffect(() => {
    let active = true;
    loadChatHistory(gid)
      .then((loaded) => { if (active && loaded.length > 0) setMessages(loaded); })
      .catch((err) => console.warn("Could not load chat memory:", err));
    return () => { active = false; };
  }, [gid]);

  /** Sends one question, shows an empty bubble, then fills it in as the answer arrives. */
  async function send(promptText: string) {
    const prompt = promptText.trim();
    if (!prompt || loading) return;

    const history: Message[] = [...messages, { role: "user", content: prompt }];
    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setLoading(true);
    setError("");

    try {
      await sendChatPrompt(prompt, gid, history, currentCanvasGraph, {
        onToken: (soFar) => setMessages((prev) => replaceLastMessage(prev, { role: "assistant", content: soFar })),
        onDone: (text, graph) => {
          if (graph) onApplyGraph(graph);
          setMessages((prev) => replaceLastMessage(prev, { role: "assistant", content: text, graph }));
          setLoading(false);
        },
        onError: (message) => {
          setError(`Error: ${message}`);
          setLoading(false);
        },
      });
    } catch (err: unknown) {
      setError(`Error: ${err instanceof Error ? err.message : "The chat request failed."}`);
      // drop the empty bubble if nothing arrived
      setMessages((prev) => (prev.length > 0 && prev[prev.length - 1].content === "" ? prev.slice(0, -1) : prev));
    } finally {
      setLoading(false);
    }
  }

  const canSend = !loading && input.trim().length > 0;

  return (
    <div className="cp-root">
      <header className="cp-header">
        <div className="cp-title">AgentForge Copilot</div>
        <div className="cp-muted">Session: {gid}</div>
      </header>

      {/* conversation */}
      <div className="cp-scroll">
        <div className="cp-thread">
          {messages.map((m, i) => {
            const isLast = i === messages.length - 1;
            return (
              <div key={i} className={`cp-row ${m.role}`}>
                <div className={`cp-bubble ${m.role}${m.graph ? " wide" : ""}`}>
                  <div className="cp-text">
                    {m.content}
                    {loading && isLast && <span className="cp-cursor" />}
                    {!m.content && loading && isLast && <span className="cp-thinking">Thinking...</span>}
                  </div>
                  {m.graph && <GraphPreview graph={m.graph} onApply={() => onApplyGraph(m.graph!)} onRun={onRun} />}
                </div>
              </div>
            );
          })}

          {loading && (
            <div className="cp-row assistant">
              <div className="cp-bubble status">Designing workflow...</div>
            </div>
          )}
          {error && <div className="cp-error">{error}</div>}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* quick prompts + message box */}
      <div className="cp-footer">
        <div className="cp-quick">
          {QUICK_PROMPTS.map((qp) => (
            <button key={qp} type="button" className="cp-chip" disabled={loading} onClick={() => void send(qp)}>
              🪄 {qp}
            </button>
          ))}
        </div>
        <form className="cp-form" onSubmit={(e) => { e.preventDefault(); void send(input); }}>
          <input
            className="cp-input"
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="e.g., Add an HTTP node before End to fetch user profile..."
            disabled={loading}
          />
          <button type="submit" className="cp-send" disabled={!canSend}>Send</button>
        </form>
      </div>
    </div>
  );
}

/** Small list of the blocks the Copilot suggested, with a button to draw them again. */
function GraphPreview({ graph, onApply, onRun }: { graph: AgentGraph; onApply: () => void; onRun: () => void }) {
  return (
    <div className="cp-preview">
      <div className="cp-preview-head">
        <div className="cp-preview-title">Generated Workflow</div>
        <button type="button" className="cp-apply cp-run" onClick={onRun} title="Runs the saved workflow and shows the result">▶ Run</button>
        <button type="button" className="cp-apply" onClick={onApply}>⚡ Re-apply to Canvas</button>
      </div>
      <div className="cp-preview-list">
        {graph.nodes.map((node, idx) => (
          <div key={node.id} className="cp-preview-node" style={{ borderLeftColor: previewColor(node.type) }}>
            <div className="cp-preview-type">{node.type.toUpperCase()}</div>
            <div className="cp-preview-label">{node.label}</div>
            {idx < graph.nodes.length - 1 && <div className="cp-muted">↓</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

"use client";
/**
 * Editor page - /editor/[gid]
 *
 * Layout:  top bar (back link, title, view tabs)
 *          + the canvas (./graph-editor.tsx) and/or the Copilot chat (./copilot-chat.tsx).
 *
 * Data flow between the two panels:
 *   canvas  ──onGraphChange──▶ canvasGraph   ──▶ Copilot (so the AI knows what is on the board)
 *   Copilot ──onApplyGraph──▶  aiAppliedGraph ──▶ canvas  (draws the AI's suggestion)
 *
 * Styles: the `.ep-*` classes in app/globals.css.
 */
import { use, useState } from "react";
import type { GraphDefinition } from "@/lib/api";
import GraphEditor from "./graph-editor";
import CopilotChat from "./copilot-chat";
import { toGraphDefinition, type AgentGraph } from "./copilot-api";

type View = "canvas" | "split" | "chat";

/** The three view buttons in the top bar. */
const VIEWS: { id: View; label: string }[] = [
  { id: "canvas", label: "🎨 Canvas" },
  { id: "split", label: "◫ Split View" },
  { id: "chat", label: "💬 Copilot" },
];

export default function Page({ params }: { params: Promise<{ gid: string }> }) {
  const { gid } = use(params);
  const [view, setView] = useState<View>("split");
  const [canvasGraph, setCanvasGraph] = useState<GraphDefinition | null>(null);       // latest canvas state
  const [aiAppliedGraph, setAiAppliedGraph] = useState<GraphDefinition | null>(null); // last AI suggestion
  const [lastAction, setLastAction] = useState("");
  const [runRequest, setRunRequest] = useState(0);  // "▶ Run" pressed in the chat -> the canvas runs (any view)

  /** Called when the Copilot suggests a workflow: draw it on the canvas. */
  function applyGraph(graph: AgentGraph) {
    setAiAppliedGraph(toGraphDefinition(graph));
    setLastAction("Canvas modified by AI Agent");
  }

  const showCanvas = view !== "chat";
  const showChat = view !== "canvas";

  return (
    <div className="ep-root">
      <header className="ep-header">
        <div className="ep-header-left">
          <a href="/" className="ep-back">← Agents</a>
          <span className="ep-divider">|</span>
          <span className="ep-title">AgentForge Studio</span>
          <span className="ep-pill">ID: {gid}</span>
          {lastAction && <span className="ep-pill ok">✨ {lastAction}</span>}
        </div>
        <div className="ep-tabs">
          {VIEWS.map((v) => (
            <button key={v.id} type="button" className={`ep-tab${view === v.id ? " active" : ""}`}
                    aria-pressed={view === v.id} onClick={() => setView(v.id)}>
              {v.label}
            </button>
          ))}
        </div>
      </header>

      <div className="ep-body">
        {/* the canvas stays loaded (hidden) in the Copilot-only view, so Copilot changes are saved and Run works */}
        <div className={`ep-canvas${view === "split" ? " split" : ""}`} hidden={!showCanvas}>
          <GraphEditor graphId={gid} externalGraph={aiAppliedGraph} onGraphChange={setCanvasGraph} runRequest={runRequest} />
        </div>
        {showChat && (
          <div className={`ep-chat${view === "split" ? " split" : ""}`}>
            <CopilotChat gid={gid} currentCanvasGraph={canvasGraph} onApplyGraph={applyGraph}
                         onRun={() => setRunRequest((n) => n + 1)} />
          </div>
        )}
      </div>
    </div>
  );
}

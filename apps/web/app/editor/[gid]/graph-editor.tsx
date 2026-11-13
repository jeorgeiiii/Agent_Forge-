"use client";

import {
  addEdge,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import {
  getGraph,
  listCredentials,
  listNodeTypes,
  startRun,
  updateGraph,
  validateNodeConfig,
  type CredentialSummary,
  type GraphDefinition,
  type GraphEdge,
  type NodeType,
  type NodeTypeSpec,
} from "@/lib/api";
import { BUTTON_HELP, nodeHelp } from "@/lib/help";
import { ROLES, roleHelp, useRole } from "@/lib/roles";
import NodeForm from "./node-form";
import RunDialog, { findInputNames } from "./run-dialog";
import RunResult from "./run-result";

// runStatus: set while a run started from the editor is shown (block turns green / red); never saved
type NodeData = { label: string; nodeType: NodeType; config: Record<string, unknown>; runStatus?: string };
type FlowNode = Node<NodeData, "agent">;

const HIDDEN_TYPES = new Set(["trigger", "output"]); // legacy aliases kept only for old graphs
const CATEGORY_ORDER = ["trigger", "ai", "flow", "data", "integration", "core"];
const CATEGORY_TITLES: Record<string, string> = {
  trigger: "How it starts", ai: "AI helpers", flow: "Decide & wait", data: "Work with information",
  integration: "Other apps & websites", core: "Basics",
};
const CATEGORY_COLORS: Record<string, string> = {
  trigger: "#059669", ai: "#6366f1", flow: "#ea580c", data: "#3b82f6", integration: "#0891b2", core: "#10b981",
};

/** Node specs from the API, shared with the custom node renderer. */
const SpecsContext = createContext<Record<string, NodeTypeSpec>>({});

function outputsOf(spec: NodeTypeSpec | undefined, config: Record<string, unknown>): string[] {
  if (!spec) return ["main"];
  if (spec.type === "switch") {
    const cases = Array.isArray(config.cases) ? (config.cases as { id?: unknown }[]) : [];
    return [...cases.map((c) => String(c?.id ?? "")).filter(Boolean), "default"];
  }
  return spec.outputs.length ? spec.outputs : ["main"];
}

function toFlowNodes(definition: GraphDefinition, specs: Record<string, NodeTypeSpec>): FlowNode[] {
  const positions = definition.positions ?? {};
  return definition.nodes.map((node, index) => ({
    id: node.id,
    type: "agent",
    position: positions[node.id] ?? { x: 80 + (index % 4) * 240, y: 100 + Math.floor(index / 4) * 160 },
    data: { label: node.label || specs[node.type]?.display_name || node.id, nodeType: node.type, config: node.config || {} },
  }));
}

function toFlowEdges(edges: GraphEdge[] = []): Edge[] {
  return edges.map((edge, index) => {
    const handle = edge.sourceHandle ?? (edge as { source_handle?: string }).source_handle ?? null;
    return {
      id: `edge-${index}-${edge.source}-${handle ?? "main"}-${edge.target}`,
      source: edge.source,
      target: edge.target,
      sourceHandle: handle && handle !== "main" ? handle : null,
      label: handle && handle !== "main" ? handle : undefined,
      animated: true,
    };
  });
}

const CATEGORY_ICONS: Record<string, string> = {
  trigger: "⚡", ai: "✦", flow: "⑂", data: "▤", integration: "⇄", core: "●",
};

function AgentNode({ data, selected }: NodeProps<FlowNode>) {
  const specs = useContext(SpecsContext);
  const spec = specs[data.nodeType];
  const category = spec?.category ?? "core";
  const color = CATEGORY_COLORS[category] ?? "#6366f1";
  const outputs = outputsOf(spec, data.config);
  const isEntry = spec?.is_entry ?? (data.nodeType === "start" || data.nodeType === "trigger");
  const isEnd = spec?.is_end ?? (data.nodeType === "end" || data.nodeType === "output");
  const multi = outputs.length > 1;
  const handleStyle = { background: color, width: 11, height: 11, border: "2px solid #16181f" };

  return (
    <div
      className={`af-node${selected ? " selected" : ""}${isEntry ? " entry" : ""}${data.runStatus ? ` run-${data.runStatus}` : ""}`}
      style={{
        ["--node-color" as string]: color,
        paddingRight: multi ? 76 : undefined,
        minHeight: multi ? outputs.length * 24 + 22 : undefined,
      }}
    >
      {!isEntry && <Handle type="target" position={Position.Left} style={handleStyle} />}
      <div className="af-node-row">
        <span className="af-node-icon" aria-hidden="true">{CATEGORY_ICONS[category] ?? "●"}</span>
        <div className="af-node-text">
          <span className="af-node-type">{spec?.display_name ?? data.nodeType}</span>
          <strong className="af-node-name">{data.label}</strong>
        </div>
      </div>
      {!isEnd && !multi && <Handle type="source" position={Position.Right} style={handleStyle} />}
      {!isEnd && multi && outputs.map((output, index) => {
        const top = `${((index + 1) / (outputs.length + 1)) * 100}%`;
        return (
          <div key={output}>
            <span className="af-node-port" style={{ top }}>{output}</span>
            <Handle id={output} type="source" position={Position.Right} style={{ ...handleStyle, top }} />
          </div>
        );
      })}
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

function GraphEditorCanvas({
  graphId,
  externalGraph,
  onGraphChange,
  runRequest = 0,
}: {
  graphId: string;
  externalGraph?: GraphDefinition | null;
  onGraphChange?: (definition: GraphDefinition) => void;
  runRequest?: number;
}) {
  const [specs, setSpecs] = useState<Record<string, NodeTypeSpec>>({});
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string>();
  const [graphName, setGraphName] = useState("Agent Workflow");
  const [description, setDescription] = useState("");
  const [credentials, setCredentials] = useState<CredentialSummary[]>([]);
  const [search, setSearch] = useState("");
  const [rawMode, setRawMode] = useState(false);
  const [rawText, setRawText] = useState("{}");
  const [nodeErrors, setNodeErrors] = useState<string[]>([]);
  const [notice, setNotice] = useState<{ text: string; kind: "ok" | "error" | "info" } | null>(null);
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const router = useRouter();

  // --- load node types + graph + the graph's workspace credentials
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [specList, graph] = await Promise.all([listNodeTypes(), getGraph(Number(graphId))]);
        if (cancelled) return;
        const byType = Object.fromEntries(specList.map((s) => [s.type, s]));
        setSpecs(byType);
        setGraphName(graph.name || "Agent Workflow");
        setDescription(graph.description || "");
        setNodes(toFlowNodes(graph.definition, byType));
        setEdges(toFlowEdges(graph.definition.edges));
        listCredentials(graph.workspace_id).then((c) => !cancelled && setCredentials(c)).catch(() => undefined);
      } catch (error) {
        if (!cancelled) setNotice({ text: error instanceof Error ? error.message : "Could not load the graph.", kind: "error" });
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => { cancelled = true; };
  }, [graphId, setEdges, setNodes]);

  const selectedNode = nodes.find((node) => node.id === selectedId);
  const selectedSpec = selectedNode ? specs[selectedNode.data.nodeType] : undefined;

  useEffect(() => {
    setRawText(selectedNode ? JSON.stringify(selectedNode.data.config, null, 2) : "{}");
    setNodeErrors([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  // --- validate the selected node's settings against the API (debounced)
  const selectedConfig = selectedNode?.data.config;
  const selectedType = selectedNode?.data.nodeType;
  useEffect(() => {
    if (!selectedType || !selectedConfig) return;
    const timer = setTimeout(() => {
      validateNodeConfig(selectedType, selectedConfig)
        .then((result) => setNodeErrors(result.errors))
        .catch(() => undefined);
    }, 400);
    return () => clearTimeout(timer);
  }, [selectedType, selectedConfig]);

  const definition = useMemo<GraphDefinition>(() => ({
    nodes: nodes.map((node) => ({ id: node.id, type: node.data.nodeType, label: node.data.label, config: node.data.config })),
    edges: edges.map(({ source, target, sourceHandle }) => (sourceHandle ? { source, target, sourceHandle } : { source, target })),
  }), [edges, nodes]);

  useEffect(() => { onGraphChange?.(definition); }, [definition, onGraphChange]);

  // Apply canvas modifications generated by the AI Copilot
  useEffect(() => {
    if (externalGraph && Array.isArray(externalGraph.nodes) && externalGraph.nodes.length > 0) {
      setNodes(toFlowNodes(externalGraph, specs));
      setEdges(toFlowEdges(externalGraph.edges));
      setNotice({ text: "Canvas updated by the AI Copilot - saving…", kind: "info" });
      setPendingAutoSave(true);   // saved right after React has drawn the new blocks (effect below)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalGraph, setEdges, setNodes]);

  const onConnect = useCallback((connection: Connection) => {
    setEdges((current) => addEdge({
      ...connection, animated: true, label: connection.sourceHandle ?? undefined,
    }, current));
  }, [setEdges]);

  function updateNode(id: string, patch: Partial<NodeData>) {
    setNodes((current) => current.map((node) => (node.id === id ? { ...node, data: { ...node.data, ...patch } } : node)));
  }

  function addNode(spec: NodeTypeSpec) {
    if (spec.is_entry && nodes.some((node) => specs[node.data.nodeType]?.is_entry)) {
      setNotice({ text: "A graph has exactly one start/trigger node. Delete the current one first.", kind: "error" });
      return;
    }
    const id = `${spec.type}-${crypto.randomUUID().slice(0, 6)}`;
    setNodes((current) => [...current, {
      id,
      type: "agent",
      position: { x: 120 + (current.length % 3) * 240, y: 120 + Math.floor(current.length / 3) * 150 },
      data: { label: spec.display_name, nodeType: spec.type, config: {} },
    }]);
    setSelectedId(id);
    setNotice(null);
  }

  function deleteSelected() {
    if (!selectedId) return;
    setNodes((current) => current.filter((node) => node.id !== selectedId));
    setEdges((current) => current.filter((edge) => edge.source !== selectedId && edge.target !== selectedId));
    setSelectedId(undefined);
  }

  function applyRawJson() {
    if (!selectedId) return;
    try {
      const config = JSON.parse(rawText) as Record<string, unknown>;
      if (!config || Array.isArray(config) || typeof config !== "object") throw new Error("Settings must be a JSON object.");
      updateNode(selectedId, { config });
      setNotice({ text: "Settings updated.", kind: "ok" });
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : "Invalid JSON.", kind: "error" });
    }
  }

  // Copilot changes are saved automatically, so "Run" (from the chat or here) always uses them
  const [pendingAutoSave, setPendingAutoSave] = useState(false);
  useEffect(() => {
    if (!pendingAutoSave || !ready) return;
    setPendingAutoSave(false);
    void saveGraph();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingAutoSave, ready, nodes, edges]);

  async function saveGraph(): Promise<boolean> {
    setSaving(true);
    setNotice({ text: "Saving…", kind: "info" });
    try {
      const positions = Object.fromEntries(nodes.map((node) => [node.id, node.position]));
      const saved = await updateGraph(Number(graphId), {
        name: graphName,
        description,
        definition: { ...definition, positions },
      });
      setNotice({ text: `Saved (version ${(saved as { version?: number }).version ?? "?"}).`, kind: "ok" });
      return true;
    } catch (error) {
      // 422 = the compiler's message (e.g. "Node 'check': condition is required"); 403 = no edit rights
      setNotice({ text: error instanceof Error ? error.message : "Could not save.", kind: "error" });
      return false;
    } finally {
      setSaving(false);
    }
  }

  /** ▶ Run opens the Run window (run-dialog.tsx), which asks for the inputs the blocks use. */
  const [runOpen, setRunOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);

  /** Colours the canvas blocks with the run's step results (cleared when the result panel closes). */
  const showNodeStatus = useCallback((statuses: Record<string, string>) => {
    setNodes((current) => current.map((node) =>
      node.data.runStatus === statuses[node.id] ? node : { ...node, data: { ...node.data, runStatus: statuses[node.id] } }));
  }, [setNodes]);
  const runInputNames = useMemo(() => findInputNames(nodes.map((n) => n.data.config)), [nodes]);

  // "Run" pressed in the Copilot chat: ask for inputs if the workflow needs any, otherwise run straight away
  useEffect(() => {
    if (!runRequest || !ready) return;
    if (runInputNames.length) setRunOpen(true);
    else void runGraph({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runRequest]);

  /** Runs use the SAVED graph, so save first; then start and open the run inspector. */
  async function runGraph(input: Record<string, unknown>) {
    setStarting(true);
    try {
      if (!(await saveGraph())) return;
      const run = await startRun(Number(graphId), input);
      setRunOpen(false);
      showNodeStatus({});
      setActiveRunId(run.id); // the result panel opens over the canvas - no page change
    } catch (error) {
      setNotice({ text: error instanceof Error ? error.message : "Could not start the run.", kind: "error" });
      setRunOpen(false);
    } finally {
      setStarting(false);
    }
  }

  const [role, setRole] = useRole();
  const help = (type: string) => roleHelp(nodeHelp(type), type, role);

  const palette = useMemo(() => {
    const term = search.trim().toLowerCase();
    const visible = Object.values(specs).filter((spec) => !HIDDEN_TYPES.has(spec.type) &&
      (!term || `${spec.display_name} ${spec.type} ${spec.description}`.toLowerCase().includes(term)));
    const groups: Record<string, NodeTypeSpec[]> = {};
    for (const spec of visible) (groups[spec.category] ??= []).push(spec);
    return CATEGORY_ORDER.filter((c) => groups[c]).map((c) => [c, groups[c]] as const)
      .concat(Object.keys(groups).filter((c) => !CATEGORY_ORDER.includes(c)).map((c) => [c, groups[c]] as const));
  }, [specs, search]);

  return (
    <SpecsContext.Provider value={specs}>
      <div className="graph-editor" style={{ display: "flex", width: "100%", height: "100%", minWidth: 0, minHeight: 0, overflow: "hidden", position: "relative" }}>
        <aside className="ge-sidebar">
          <input className="ge-title" value={graphName} onChange={(e) => setGraphName(e.target.value)} aria-label="Graph name" maxLength={200} />
          <div className="ge-role" role="group" aria-label="Your role">
            <span className="ge-role-label">Show examples for my job:</span>
            <div className="ge-role-chips">
              {ROLES.map((r) => (
                <button key={r.id} type="button" className={`ge-role-chip${role === r.id ? " active" : ""}`}
                        aria-pressed={role === r.id} onClick={() => setRole(r.id)}>
                  <span aria-hidden="true">{r.icon}</span> {r.label}
                </button>
              ))}
            </div>
          </div>
          <details className="ge-guide" open={nodes.length <= 3}>
            <summary>How to build a workflow</summary>
            <ol>
              <li><strong>Add blocks:</strong> click a block below to put it on the board.</li>
              <li><strong>Connect them:</strong> drag from the dot on a block&apos;s right side to the next block. Work flows left to right.</li>
              <li><strong>Fill in settings:</strong> click a block to open its settings on the right.</li>
              <li><strong>Try it:</strong> press ▶ Run and watch each step happen.</li>
            </ol>
          </details>
          <input className="ge-search" placeholder="Search blocks, e.g. email, AI, wait…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search blocks" />
          <div className="ge-palette" aria-label="Add a node">
            {palette.map(([category, items]) => (
              <div key={category}>
                <div className="ge-category">{CATEGORY_TITLES[category] ?? category}</div>
                {items.map((spec) => (
                  <button key={spec.type} type="button" className="ge-palette-item" onClick={() => addNode(spec)}
                          title={`${help(spec.type).what}

Example: ${help(spec.type).example}`}>
                    <span className="ge-dot" style={{ background: CATEGORY_COLORS[spec.category] ?? "#6366f1" }} />
                    <span className="ge-palette-name">
                      {spec.display_name}
                      <small className="ge-palette-short">{help(spec.type).short}</small>
                    </span>
                    <span aria-hidden="true" className="ge-plus">+</span>
                  </button>
                ))}
              </div>
            ))}
            {ready && palette.length === 0 && <p className="ge-help">No block matches “{search}”. Try another word.</p>}
          </div>
          <div className="ge-save">
            <button type="button" className="ge-primary" onClick={() => void saveGraph()} disabled={!ready || saving}
                    title={BUTTON_HELP.save}>
              {saving ? "Saving…" : "Save workflow"}
            </button>
            <small className="ge-btn-help">{BUTTON_HELP.save}</small>
            <div className="ge-run-row">
              <button type="button" className="ge-secondary" onClick={() => setRunOpen(true)} disabled={!ready || saving}
                      title={BUTTON_HELP.run}>▶ Run</button>
              <Link className="ge-secondary" href={`/graphs/${graphId}/runs`} title={BUTTON_HELP.history}>Run history</Link>
            </div>
            <Link className="ge-secondary ge-full" href={`/use/${graphId}`}
                  title="A simple page where anyone can fill in the inputs and see the result - share it with your team">
              👤 Simple page for users
            </Link>
            <Link className="ge-secondary ge-full" href={`/graphs/${graphId}/triggers`} title={BUTTON_HELP.triggers}>⚡ Publish &amp; triggers</Link>
            <small className="ge-btn-help"><strong>Run:</strong> {BUTTON_HELP.run}</small>
            <small className="ge-btn-help"><strong>Run history:</strong> {BUTTON_HELP.history}</small>
            <small className="ge-btn-help"><strong>Publish &amp; triggers:</strong> {BUTTON_HELP.triggers}</small>
            {notice && <p role="status" className={`ge-notice ge-${notice.kind}`}>{notice.text}</p>}
          </div>
        </aside>
        {activeRunId !== null && (
          <RunResult graphId={Number(graphId)} runId={activeRunId} onNodeStatus={showNodeStatus}
                     onClose={() => { setActiveRunId(null); showNodeStatus({}); }} />
        )}
        {runOpen && (
          <RunDialog inputNames={runInputNames} busy={starting} onCancel={() => setRunOpen(false)}
                     onRun={(input) => void runGraph(input)} />
        )}

        <div className="graph-canvas" style={{ flex: 1, height: "100%", minWidth: 0, minHeight: 0, position: "relative" }}>
          <ReactFlow
            colorMode="dark"
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            defaultEdgeOptions={{ animated: true, style: { stroke: "#5b6478", strokeWidth: 2 } }}
            proOptions={{ hideAttribution: true }}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_event, node) => setSelectedId(node.id)}
            onPaneClick={() => setSelectedId(undefined)}
            fitView
            deleteKeyCode={["Backspace", "Delete"]}
            style={{ width: "100%", height: "100%" }}
          >
            <Background color="#2b3040" gap={22} size={1.6} bgColor="#16181f" />
            {/* Default minimap colours are light grey on white - nearly invisible. Colour blocks by type instead. */}
            <MiniMap
              pannable
              zoomable
              ariaLabel="Mini map of the whole workflow"
              bgColor="#1c1f2a"
              maskColor="rgba(0, 0, 0, 0.45)"
              maskStrokeColor="#818cf8"
              maskStrokeWidth={2}
              nodeBorderRadius={6}
              nodeStrokeWidth={3}
              nodeColor={(node) => CATEGORY_COLORS[specs[(node.data as NodeData).nodeType]?.category ?? "core"] ?? "#6366f1"}
              nodeStrokeColor={(node) => (node.id === selectedId ? "#ffffff" : "#1c1f2a")}
              style={{ border: "1px solid #2f3446", borderRadius: 10, boxShadow: "0 6px 20px rgba(0, 0, 0, 0.45)" }}
            />
            <Controls />
          </ReactFlow>
          {!ready && <div className="ge-loading">Loading workflow…</div>}
          {ready && nodes.length === 0 && (
            <div className="ge-empty">Click a block on the left to start building.</div>
          )}
        </div>

        {selectedNode && (
          <aside className="ge-settings" aria-label="Node settings">
            <div className="ge-settings-head">
              <div>
                <div className="ge-category">{selectedSpec?.display_name ?? selectedNode.data.nodeType}</div>
                <small className="ge-help">{selectedNode.id}</small>
              </div>
              <button type="button" className="ge-close" onClick={() => setSelectedId(undefined)} aria-label="Close settings">×</button>
            </div>
            <div className="ge-help-card">
              <p><strong>What it does:</strong> {help(selectedNode.data.nodeType).what}</p>
              <p><strong>Example:</strong> {help(selectedNode.data.nodeType).example}</p>
              <p><strong>Where to put it:</strong> {help(selectedNode.data.nodeType).where}</p>
            </div>
            <label className="nf-label" htmlFor="ge-node-label">name of this block</label>
            <input id="ge-node-label" className="nf-input" value={selectedNode.data.label}
                   onChange={(e) => updateNode(selectedNode.id, { label: e.target.value })} />

            <div className="ge-tabs">
              <button type="button" className={!rawMode ? "active" : ""} onClick={() => setRawMode(false)} title={BUTTON_HELP.formTab}>Form</button>
              <button type="button" className={rawMode ? "active" : ""} title={BUTTON_HELP.jsonTab}
                      onClick={() => { setRawText(JSON.stringify(selectedNode.data.config, null, 2)); setRawMode(true); }}>JSON</button>
            </div>
            {!rawMode && selectedSpec && (
              <NodeForm schema={selectedSpec.parameters} config={selectedNode.data.config} credentials={credentials}
                        onChange={(config) => updateNode(selectedNode.id, { config })} />
            )}
            {!rawMode && !selectedSpec && <p className="ge-help">Unknown node type - edit it as JSON.</p>}
            {rawMode && (
              <>
                <textarea className="nf-input nf-mono" rows={12} value={rawText} onChange={(e) => setRawText(e.target.value)} />
                <button type="button" className="ge-primary" onClick={applyRawJson}>Apply JSON</button>
              </>
            )}
            {nodeErrors.length > 0 && (
              <ul className="ge-errors" role="alert">
                {nodeErrors.map((error) => <li key={error}>{error}</li>)}
              </ul>
            )}
            <button type="button" className="ge-danger" onClick={deleteSelected} title={BUTTON_HELP.deleteNode}>Delete block</button>
            <small className="ge-btn-help">{BUTTON_HELP.deleteNode}</small>
          </aside>
        )}
      </div>
    </SpecsContext.Provider>
  );
}

export default function GraphEditor(props: {
  graphId: string;
  externalGraph?: GraphDefinition | null;
  onGraphChange?: (definition: GraphDefinition) => void;
  runRequest?: number;   // increases when "Run" is pressed outside the canvas (the Copilot chat)
}) {
  return (
    <ReactFlowProvider>
      <GraphEditorCanvas {...props} />
    </ReactFlowProvider>
  );
}

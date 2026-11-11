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
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import {
  getGraph,
  listCredentials,
  listNodeTypes,
  updateGraph,
  validateNodeConfig,
  type CredentialSummary,
  type GraphDefinition,
  type GraphEdge,
  type NodeType,
  type NodeTypeSpec,
} from "@/lib/api";
import NodeForm from "./node-form";

type NodeData = { label: string; nodeType: NodeType; config: Record<string, unknown> };
type FlowNode = Node<NodeData, "agent">;

const HIDDEN_TYPES = new Set(["trigger", "output"]); // legacy aliases kept only for old graphs
const CATEGORY_ORDER = ["trigger", "ai", "flow", "data", "integration", "core"];
const CATEGORY_TITLES: Record<string, string> = {
  trigger: "Triggers", ai: "AI", flow: "Flow control", data: "Data", integration: "Integrations", core: "Core",
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

function AgentNode({ data, selected }: NodeProps<FlowNode>) {
  const specs = useContext(SpecsContext);
  const spec = specs[data.nodeType];
  const color = CATEGORY_COLORS[spec?.category ?? "core"] ?? "#6366f1";
  const outputs = outputsOf(spec, data.config);
  const isEntry = spec?.is_entry ?? (data.nodeType === "start" || data.nodeType === "trigger");
  const isEnd = spec?.is_end ?? (data.nodeType === "end" || data.nodeType === "output");
  const multi = outputs.length > 1;

  return (
    <div
      style={{
        background: "#ffffff",
        border: selected ? "2px solid #4f46e5" : "1px solid #cbd5e1",
        borderLeft: `5px solid ${color}`,
        borderRadius: 8,
        padding: "10px 14px",
        paddingRight: multi ? 64 : 14,
        minWidth: 170,
        minHeight: multi ? outputs.length * 22 + 16 : undefined,
        boxShadow: selected ? "0 4px 12px rgba(79, 70, 229, 0.2)" : "0 1px 3px rgba(0,0,0,0.08)",
        fontSize: 13,
        position: "relative",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      {!isEntry && <Handle type="target" position={Position.Left} style={{ background: color, width: 8, height: 8 }} />}
      <div style={{ fontSize: 10, textTransform: "uppercase", fontWeight: 700, color, marginBottom: 2 }}>
        {spec?.display_name ?? data.nodeType}
      </div>
      <strong style={{ display: "block", color: "#1e293b", fontSize: 14 }}>{data.label}</strong>
      {!isEnd && !multi && (
        <Handle type="source" position={Position.Right} style={{ background: color, width: 8, height: 8 }} />
      )}
      {!isEnd && multi && outputs.map((output, index) => {
        const top = `${((index + 1) / (outputs.length + 1)) * 100}%`;
        return (
          <div key={output}>
            <span style={{ position: "absolute", right: 12, top, transform: "translateY(-50%)", fontSize: 10,
                           color: "#475569", background: "#f1f5f9", borderRadius: 4, padding: "0 4px" }}>
              {output}
            </span>
            <Handle id={output} type="source" position={Position.Right}
                    style={{ top, background: color, width: 8, height: 8 }} />
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
}: {
  graphId: string;
  externalGraph?: GraphDefinition | null;
  onGraphChange?: (definition: GraphDefinition) => void;
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
      setNotice({ text: "Canvas updated by the AI Copilot - review and save.", kind: "info" });
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

  async function saveGraph() {
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
    } catch (error) {
      // 422 = the compiler's message (e.g. "Node 'check': condition is required"); 403 = no edit rights
      setNotice({ text: error instanceof Error ? error.message : "Could not save.", kind: "error" });
    } finally {
      setSaving(false);
    }
  }

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
          <input className="ge-search" placeholder="Search nodes…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search nodes" />
          <div className="ge-palette" aria-label="Add a node">
            {palette.map(([category, items]) => (
              <div key={category}>
                <div className="ge-category">{CATEGORY_TITLES[category] ?? category}</div>
                {items.map((spec) => (
                  <button key={spec.type} type="button" className="ge-palette-item" onClick={() => addNode(spec)} title={spec.description}>
                    <span className="ge-dot" style={{ background: CATEGORY_COLORS[spec.category] ?? "#6366f1" }} />
                    <span className="ge-palette-name">{spec.display_name}</span>
                    <span aria-hidden="true" className="ge-plus">+</span>
                  </button>
                ))}
              </div>
            ))}
            {ready && palette.length === 0 && <p className="ge-help">No node matches “{search}”.</p>}
          </div>
          <div className="ge-save">
            <button type="button" className="ge-primary" onClick={() => void saveGraph()} disabled={!ready || saving}>
              {saving ? "Saving…" : "Save workflow"}
            </button>
            {notice && <p role="status" className={`ge-notice ge-${notice.kind}`}>{notice.text}</p>}
          </div>
        </aside>

        <div className="graph-canvas" style={{ flex: 1, height: "100%", minWidth: 0, minHeight: 0, position: "relative" }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_event, node) => setSelectedId(node.id)}
            onPaneClick={() => setSelectedId(undefined)}
            fitView
            deleteKeyCode={["Backspace", "Delete"]}
            style={{ width: "100%", height: "100%" }}
          >
            <Background color="#dbe2ef" gap={22} />
            <MiniMap pannable zoomable />
            <Controls />
          </ReactFlow>
          {!ready && <div className="ge-loading">Loading workflow…</div>}
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
            {selectedSpec?.description && <p className="ge-help">{selectedSpec.description}</p>}
            <label className="nf-label" htmlFor="ge-node-label">label</label>
            <input id="ge-node-label" className="nf-input" value={selectedNode.data.label}
                   onChange={(e) => updateNode(selectedNode.id, { label: e.target.value })} />

            <div className="ge-tabs">
              <button type="button" className={!rawMode ? "active" : ""} onClick={() => setRawMode(false)}>Form</button>
              <button type="button" className={rawMode ? "active" : ""}
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
            <button type="button" className="ge-danger" onClick={deleteSelected}>Delete node</button>
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
}) {
  return (
    <ReactFlowProvider>
      <GraphEditorCanvas {...props} />
    </ReactFlowProvider>
  );
}

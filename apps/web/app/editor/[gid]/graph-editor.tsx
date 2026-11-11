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
import { useCallback, useEffect, useMemo, useState } from "react";

import { getGraph, updateGraph, createGraph, type GraphDefinition, type NodeType } from "@/lib/api";

type NodeData = {
  label: string;
  nodeType: NodeType;
  config: Record<string, unknown>;
};

type FlowNode = Node<NodeData, "agent">;

const initialDefinition: GraphDefinition = {
  nodes: [
    { id: "start", type: "start", config: {} },
    { id: "message", type: "template", config: { template: "Hello {{ input.name }}!" } },
    { id: "end", type: "end", config: {} },
  ],
  edges: [
    { source: "start", target: "message" },
    { source: "message", target: "end" },
  ],
};

const nodeTitles: Record<string, string> = {
  start: "Start Trigger",
  trigger: "Trigger",
  template: "Prompt Template",
  http: "HTTP Request",
  llm: "LLM Model",
  tool: "Tool / API",
  condition: "Condition / Branch",
  output: "Output Reply",
  end: "End Result",
};

const nodeColors: Record<string, string> = {
  start: "#059669",
  trigger: "#059669",
  template: "#3b82f6",
  http: "#0891b2",
  llm: "#6366f1",
  tool: "#d97706",
  condition: "#ea580c",
  output: "#10b981",
  end: "#10b981",
};

function toFlowNodes(
  definition: GraphDefinition,
  positions: Record<string, { x: number; y: number }> = {},
): FlowNode[] {
  return definition.nodes.map((node, index) => {
    const defaultX = 80 + (index % 4) * 240;
    const defaultY = 100 + Math.floor(index / 4) * 160;
    return {
      id: node.id,
      type: "agent",
      position: positions[node.id] ?? { x: defaultX, y: defaultY },
      data: {
        label: node.label || nodeTitles[node.type] || node.id,
        nodeType: node.type,
        config: node.config || {},
      },
    };
  });
}

function AgentNode({ data, selected }: NodeProps<FlowNode>) {
  const isStart = data.nodeType === "start" || data.nodeType === "trigger";
  const isEnd = data.nodeType === "end" || data.nodeType === "output";
  const accentColor = nodeColors[data.nodeType] || "#6366f1";

  return (
    <div
      style={{
        background: "#ffffff",
        border: selected ? "2px solid #4f46e5" : "1px solid #cbd5e1",
        borderLeft: `5px solid ${accentColor}`,
        borderRadius: 8,
        padding: "10px 14px",
        minWidth: 160,
        boxShadow: selected ? "0 4px 12px rgba(79, 70, 229, 0.2)" : "0 1px 3px rgba(0,0,0,0.08)",
        fontSize: 13,
        position: "relative",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      {!isStart && <Handle type="target" position={Position.Left} style={{ background: accentColor, width: 8, height: 8 }} />}
      <div style={{ fontSize: 10, textTransform: "uppercase", fontWeight: 700, color: accentColor, marginBottom: 2 }}>
        {data.nodeType}
      </div>
      <strong style={{ display: "block", color: "#1e293b", fontSize: 14 }}>
        {data.label}
      </strong>
      {!isEnd && <Handle type="source" position={Position.Right} style={{ background: accentColor, width: 8, height: 8 }} />}
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
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>(toFlowNodes(initialDefinition));
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialDefinition.edges.map((edge, index) => ({
    id: `edge-${index}`,
    source: edge.source,
    target: edge.target,
    animated: true,
  })));
  const [selectedId, setSelectedId] = useState<string>();
  const [configText, setConfigText] = useState("{}");
  const [notice, setNotice] = useState("");
  const [ready, setReady] = useState(false);

  const [graphName, setGraphName] = useState("Agent Workflow");

  useEffect(() => {
    let cancelled = false;

    async function loadWorkflow() {
      const numId = Number(graphId);
      if (!isNaN(numId) && numId > 0) {
        try {
          const graph = await getGraph(numId);
          if (cancelled) return;
          if (graph && graph.definition) {
            setGraphName(graph.name || "Agent Workflow");
            const definition = graph.definition;
            const positions = definition.positions || {};
            setNodes(toFlowNodes(definition, positions));
            if (Array.isArray(definition.edges)) {
              setEdges(definition.edges.map((edge, index) => ({
                id: `edge-${index}`,
                source: edge.source,
                target: edge.target,
                animated: true,
              })));
            }
            setNotice("Loaded from Postgres (JSONB storage).");
            setReady(true);
            return;
          }
        } catch (error) {
          console.warn("Could not load from API, checking local storage as fallback:", error);
        }
      }

      try {
        const saved = localStorage.getItem(`agentforge-graph-${graphId}`);
        if (saved && !cancelled) {
          const stored = JSON.parse(saved) as {
            definition?: GraphDefinition;
            positions?: Record<string, { x: number; y: number }>;
          };
          const definition = stored.definition ?? stored as GraphDefinition;
          if (Array.isArray(definition.nodes) && Array.isArray(definition.edges)) {
            setNodes(toFlowNodes(definition, stored.positions));
            setEdges(definition.edges.map((edge, index) => ({
              id: `edge-${index}`,
              source: edge.source,
              target: edge.target,
              animated: true,
            })));
          }
        }
      } catch (error) {
        setNotice(error instanceof Error ? `Could not load saved graph: ${error.message}` : "Could not load saved graph.");
      } finally {
        if (!cancelled) setReady(true);
      }
    }

    loadWorkflow();
    return () => { cancelled = true; };
  }, [graphId, setEdges, setNodes]);

  const selectedNode = nodes.find((node) => node.id === selectedId);
  useEffect(() => {
    setConfigText(selectedNode ? JSON.stringify(selectedNode.data.config, null, 2) : "{}");
  }, [selectedId, selectedNode]);

  const definition = useMemo<GraphDefinition>(() => ({
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.nodeType,
      label: node.data.label,
      config: node.data.config,
    })),
    edges: edges.map(({ source, target }) => ({ source, target })),
  }), [edges, nodes]);

  // Sync canvas state to parent for AI context
  useEffect(() => {
    onGraphChange?.(definition);
  }, [definition, onGraphChange]);

  // Apply canvas modifications generated by AI Copilot
  useEffect(() => {
    if (externalGraph && Array.isArray(externalGraph.nodes) && externalGraph.nodes.length > 0) {
      const positions = externalGraph.positions || {};
      setNodes(toFlowNodes(externalGraph, positions));
      if (Array.isArray(externalGraph.edges)) {
        setEdges(
          externalGraph.edges.map((edge, index) => ({
            id: `edge-${index}-${edge.source}-${edge.target}`,
            source: edge.source,
            target: edge.target,
            animated: true,
          }))
        );
      }
      setNotice("✨ Canvas updated by AI Copilot!");
    }
  }, [externalGraph, setEdges, setNodes]);

  const onConnect = useCallback((connection: Connection) => {
    setEdges((current) => addEdge({ ...connection, animated: true }, current));
  }, [setEdges]);

  function addNode(nodeType: NodeType) {
    if ((nodeType === "start" || nodeType === "end") && nodes.some((node) => node.data.nodeType === nodeType)) {
      setNotice(`This graph already has a ${nodeType} node.`);
      return;
    }
    const id = `${nodeType}-${crypto.randomUUID().slice(0, 8)}`;
    setNodes((current) => [...current, {
      id,
      type: "agent",
      position: { x: 100 + (current.length % 3) * 240, y: 100 + Math.floor(current.length / 3) * 150 },
      data: { label: nodeTitles[nodeType], nodeType, config: {} },
    }]);
    setSelectedId(id);
    setNotice("");
  }

  function updateSelectedConfig() {
    if (!selectedId) return;
    try {
      const config = JSON.parse(configText) as Record<string, unknown>;
      if (!config || Array.isArray(config) || typeof config !== "object") {
        throw new Error("Node config must be a JSON object.");
      }
      setNodes((current) => current.map((node) =>
        node.id === selectedId ? { ...node, data: { ...node.data, config } } : node
      ));
      setNotice("Node configuration updated.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Invalid node configuration JSON.");
    }
  }

  async function saveGraph() {
    setNotice("Saving workflow to Postgres...");
    const positions = Object.fromEntries(nodes.map((node) => [node.id, node.position]));
    const graphDefinition: GraphDefinition = {
      nodes: nodes.map((node) => ({
        id: node.id,
        type: node.data.nodeType,
        label: node.data.label,
        config: node.data.config,
      })),
      edges: edges.map(({ source, target }) => ({ source, target })),
      positions,
    };

    const numId = Number(graphId);
    if (!isNaN(numId) && numId > 0) {
      try {
        await updateGraph(numId, {
          name: graphName,
          description: "Saved from AgentForge editor",
          definition: graphDefinition,
        });
        setNotice("Workflow saved to Postgres database (JSONB column)!");
        return;
      } catch (error) {
        console.error("Postgres update failed:", error);
      }
    }

    // If string ID or DB update failed, create or store
    try {
      const created = await createGraph({
        name: graphName,
        description: "Saved from AgentForge editor",
        definition: graphDefinition,
      });
      setNotice(`Workflow created in Postgres as #${created.id} (JSONB column)!`);
    } catch (error) {
      // LocalStorage fallback
      try {
        localStorage.setItem(
          `agentforge-graph-${graphId}`,
          JSON.stringify({ definition: graphDefinition, positions }),
        );
        setNotice("Saved to browser local storage (DB offline).");
      } catch (localErr) {
        setNotice("Failed to save workflow.");
      }
    }
  }

  return (
    <div className="graph-editor" style={{ display: "flex", width: "100%", height: "100%", minWidth: 0, minHeight: 0, overflow: "hidden", position: "relative" }}>
      <aside className="graph-sidebar" style={{ width: 280, minWidth: 280, height: "100%", overflowY: "auto", borderRight: "1px solid #e2e8f0", background: "#ffffff", padding: "16px", display: "flex", flexDirection: "column", gap: "16px", flexShrink: 0 }}>
        <div>
          <span className="graph-kicker" style={{ fontSize: 11, textTransform: "uppercase", fontWeight: 700, color: "#64748b", letterSpacing: 0.5 }}>Canvas</span>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: "4px 0" }}>Workflow</h2>
          <p style={{ fontSize: 13, color: "#64748b", margin: 0 }}>Drag nodes onto the canvas and connect their handles.</p>
        </div>
        <div className="node-palette" aria-label="Add workflow node" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {(["start", "template", "http", "end"] as NodeType[]).map((nodeType) => (
            <button
              key={nodeType}
              type="button"
              onClick={() => addNode(nodeType)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 12px",
                background: "#f8fafc",
                border: "1px solid #e2e8f0",
                borderRadius: 6,
                fontSize: 13,
                fontWeight: 500,
                cursor: "pointer",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", background: nodeColors[nodeType] || "#6366f1" }} />
                {nodeTitles[nodeType]}
              </div>
              <span aria-hidden="true" style={{ fontSize: 16, color: "#64748b" }}>+</span>
            </button>
          ))}
        </div>
        <div className="node-settings" style={{ borderTop: "1px solid #e2e8f0", paddingTop: 16 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 8px 0" }}>{selectedNode ? selectedNode.data.label : "Node settings"}</h3>
          {selectedNode ? (
            <>
              <p className="graph-help" style={{ fontSize: 12, color: "#64748b", margin: "0 0 8px 0" }}>{selectedNode.id} · {selectedNode.data.nodeType}</p>
              <label htmlFor="node-config" style={{ fontSize: 12, fontWeight: 500, color: "#475569", display: "block", marginBottom: 4 }}>Configuration (JSON)</label>
              <textarea
                id="node-config"
                value={configText}
                onChange={(event) => setConfigText(event.target.value)}
                style={{ width: "100%", height: 100, padding: 8, fontSize: 12, fontFamily: "monospace", borderRadius: 6, border: "1px solid #cbd5e1", boxSizing: "border-box" }}
              />
              <button
                className="graph-primary"
                type="button"
                onClick={updateSelectedConfig}
                style={{ marginTop: 8, width: "100%", padding: "6px 12px", background: "#4f46e5", color: "#fff", border: "none", borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: "pointer" }}
              >
                Apply config
              </button>
            </>
          ) : (
            <p className="graph-help" style={{ fontSize: 12, color: "#94a3b8", margin: 0 }}>Select a node to edit its configuration.</p>
          )}
        </div>
        <div style={{ marginTop: "auto" }}>
          <button
            className="graph-primary graph-save"
            type="button"
            onClick={saveGraph}
            disabled={!ready}
            style={{ width: "100%", padding: "10px 14px", background: "#0f172a", color: "#fff", border: "none", borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: ready ? "pointer" : "not-allowed" }}
          >
            Save workflow
          </button>
          {notice && <p className="graph-notice" role="status" style={{ fontSize: 12, color: "#2563eb", marginTop: 8 }}>{notice}</p>}
        </div>
      </aside>
      <div className="graph-canvas" style={{ flex: 1, width: "100%", height: "100%", minWidth: 0, minHeight: 0, position: "relative" }}>
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
        {!ready && <div className="graph-loading" style={{ position: "absolute", top: 16, right: 16, background: "rgba(255,255,255,0.9)", padding: "6px 12px", borderRadius: 6, fontSize: 12 }}>Loading workflow…</div>}
      </div>
    </div>
  );
}

export default function GraphEditor({
  graphId,
  externalGraph,
  onGraphChange,
}: {
  graphId: string;
  externalGraph?: GraphDefinition | null;
  onGraphChange?: (definition: GraphDefinition) => void;
}) {
  return (
    <ReactFlowProvider>
      <GraphEditorCanvas
        graphId={graphId}
        externalGraph={externalGraph}
        onGraphChange={onGraphChange}
      />
    </ReactFlowProvider>
  );
}

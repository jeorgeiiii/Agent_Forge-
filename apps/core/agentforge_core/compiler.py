"""Cluster 4 - Graph compilation & validation (pure logic, no DB).

Turns a graph definition (``{"nodes": [...], "edges": [...]}``) into a frozen,
hashable executable IR::

    {
      "ir_version": 2,
      "start": "start",
      "ends": ["end"],
      "nodes": {id: {"id", "type", "label", "config", "runs_in", "outputs"}},
      "edges": [{"source", "target", "source_handle"}],
      "order": [ids in topological order],
      "supersteps": [[ids that can run in parallel], ...],
      "hash": "<sha256 of everything above>"
    }
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from typing import Any, Iterable

from .expressions import ExpressionError, has_expression, parse_expression
from .registry import NodeTypeSpec, UnknownNodeTypeError, get_node_type, validate_node_config

IR_VERSION = 2
DEFAULT_HANDLE = "main"


class GraphValidationError(ValueError):
    def __init__(self, errors: str | list[str]):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


# --------------------------------------------------------------------------- #
# Normalisation helpers
# --------------------------------------------------------------------------- #


def _as_dict(definition: Any) -> dict[str, Any]:
    if hasattr(definition, "model_dump"):
        return definition.model_dump(mode="json")
    return definition


def _nodes(definition: dict[str, Any]) -> list[dict[str, Any]]:
    return list(definition.get("nodes") or [])


def _spec_or_none(node_type: str) -> NodeTypeSpec | None:
    try:
        return get_node_type(node_type)
    except UnknownNodeTypeError:
        return None


def _outputs_for(node: dict[str, Any], spec: NodeTypeSpec | None) -> list[str]:
    if spec is None:
        return [DEFAULT_HANDLE]
    if spec.type == "switch":
        cases = (node.get("config") or {}).get("cases") or []
        return [str(c.get("id")) for c in cases if isinstance(c, dict)] + ["default"]
    return list(spec.outputs)


def _edge_handle(edge: dict[str, Any], outputs: list[str]) -> str | None:
    handle = edge.get("source_handle") or edge.get("sourceHandle") or edge.get("branch")
    if outputs == [DEFAULT_HANDLE]:
        return DEFAULT_HANDLE  # single-output nodes ignore whatever handle the UI sent
    return str(handle) if handle is not None else None


def _normalised_edges(definition: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = {n.get("id"): n for n in _nodes(definition)}
    result = []
    for edge in definition.get("edges") or []:
        source = nodes.get(edge.get("source"))
        outputs = _outputs_for(source, _spec_or_none(source.get("type", ""))) if source else [DEFAULT_HANDLE]
        result.append({
            "source": edge.get("source"),
            "target": edge.get("target"),
            "source_handle": _edge_handle(edge, outputs),
        })
    return result


# --------------------------------------------------------------------------- #
# Public functions
# --------------------------------------------------------------------------- #


def extract_start_node(definition: Any) -> dict[str, Any]:
    """Return the single entry node (start or any trigger)."""
    starts = [n for n in _nodes(_as_dict(definition)) if (s := _spec_or_none(n.get("type", ""))) and s.is_entry]
    if len(starts) != 1:
        raise GraphValidationError(
            "A graph must have exactly one Start/Trigger node." if starts else "A graph must have a Start/Trigger node."
        )
    return starts[0]


def extract_end_nodes(definition: Any) -> list[dict[str, Any]]:
    return [n for n in _nodes(_as_dict(definition)) if (s := _spec_or_none(n.get("type", ""))) and s.is_end]


def resolve_node_types(definition: Any) -> dict[str, NodeTypeSpec]:
    """Map node id -> registered spec. Raises on unknown types."""
    resolved: dict[str, NodeTypeSpec] = {}
    unknown: list[str] = []
    for node in _nodes(_as_dict(definition)):
        spec = _spec_or_none(node.get("type", ""))
        if spec is None:
            unknown.append(f"Node '{node.get('id')}' has unknown type '{node.get('type')}'.")
        else:
            resolved[node["id"]] = spec
    if unknown:
        raise GraphValidationError(unknown)
    return resolved


def detect_cycles(definition: Any) -> list[list[str]]:
    """Return every simple cycle found by DFS (as lists of node ids). [] means acyclic."""
    definition = _as_dict(definition)
    graph: dict[str, list[str]] = defaultdict(list)
    for edge in definition.get("edges") or []:
        graph[edge.get("source")].append(edge.get("target"))
    color: dict[str, int] = {}
    stack: list[str] = []
    cycles: list[list[str]] = []

    def visit(node_id: str) -> None:
        color[node_id] = 1
        stack.append(node_id)
        for nxt in graph.get(node_id, []):
            if color.get(nxt, 0) == 0:
                visit(nxt)
            elif color.get(nxt) == 1:
                cycles.append(stack[stack.index(nxt):] + [nxt])
        stack.pop()
        color[node_id] = 2

    for node in _nodes(definition):
        if color.get(node.get("id"), 0) == 0:
            visit(node.get("id"))
    return cycles


def topological_sort(definition: Any) -> list[str]:
    """Order nodes so each comes after all of its inputs (Kahn's algorithm, stable)."""
    definition = _as_dict(definition)
    ids = [n.get("id") for n in _nodes(definition)]
    incoming = {i: 0 for i in ids}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in definition.get("edges") or []:
        if edge.get("source") in incoming and edge.get("target") in incoming:
            outgoing[edge["source"]].append(edge["target"])
            incoming[edge["target"]] += 1
    ready = deque(i for i in ids if incoming[i] == 0)
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for target in outgoing[current]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if len(order) != len(ids):
        raise GraphValidationError("Cycles are not allowed in a graph.")
    return order


def group_into_supersteps(order: list[str], edges: Iterable[dict[str, Any]]) -> list[list[str]]:
    """Layer nodes by longest distance from the entry; each layer runs in parallel."""
    parents: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        parents[edge["target"]].append(edge["source"])
    depth: dict[str, int] = {}
    for node_id in order:
        depth[node_id] = 1 + max((depth[p] for p in parents[node_id]), default=-1)
    layers: dict[int, list[str]] = defaultdict(list)
    for node_id in order:
        layers[depth[node_id]].append(node_id)
    return [layers[i] for i in sorted(layers)]


def compute_graph_hash(ir: dict[str, Any]) -> str:
    """sha256 over the canonical JSON of the IR (excluding the hash field itself)."""
    payload = {k: v for k, v in ir.items() if k != "hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _expression_errors(value: Any, path: str) -> list[str]:
    if isinstance(value, dict):
        return [e for k, v in value.items() for e in _expression_errors(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [e for i, v in enumerate(value) for e in _expression_errors(v, f"{path}[{i}]")]
    if has_expression(value):
        try:
            parse_expression(value)
        except ExpressionError as error:
            return [f"{path}: {error}"]
    return []


def validate_graph(definition: Any) -> list[str]:
    """Structural + config checks. Returns a list of human-readable errors ([] = valid).

    Cycles are reported separately by ``detect_cycles``.
    """
    definition = _as_dict(definition)
    nodes = _nodes(definition)
    errors: list[str] = []
    if not nodes:
        return ["A graph must contain at least one node."]

    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = node.get("id")
        if not node_id:
            errors.append("Every node needs an id.")
            continue
        if node_id in by_id:
            errors.append(f"Duplicate node id '{node_id}'.")
        by_id[node_id] = node

    specs: dict[str, NodeTypeSpec | None] = {i: _spec_or_none(n.get("type", "")) for i, n in by_id.items()}
    for node_id, spec in specs.items():
        if spec is None:
            errors.append(f"Node '{node_id}' has unknown type '{by_id[node_id].get('type')}'.")
            continue
        config = by_id[node_id].get("config") or {}
        errors.extend(f"Node '{node_id}': {e}" for e in validate_node_config(spec.type, config))
        errors.extend(f"Node '{node_id}' {e}" for e in _expression_errors(config, "config"))

    entries = [i for i, s in specs.items() if s and s.is_entry]
    ends = [i for i, s in specs.items() if s and s.is_end]
    if len(entries) == 0:
        errors.append("A graph must have a Start/Trigger node.")
    elif len(entries) > 1:
        errors.append(f"A graph must have exactly one Start/Trigger node (found {len(entries)}).")
    if not ends:
        errors.append("A graph must have at least one End node.")

    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, int] = defaultdict(int)
    seen_pairs: set[tuple[str, str, str | None]] = set()
    for edge in _normalised_edges(definition):
        source, target, handle = edge["source"], edge["target"], edge["source_handle"]
        if source not in by_id or target not in by_id:
            errors.append(f"Edge {source} -> {target} references a missing node.")
            continue
        if source == target:
            errors.append(f"Node '{source}' cannot connect to itself.")
            continue
        if (source, target, handle) in seen_pairs:
            errors.append(f"Duplicate edge {source} -> {target}.")
            continue
        seen_pairs.add((source, target, handle))
        spec = specs.get(source)
        if spec and spec.is_end:
            errors.append(f"End node '{source}' cannot have outgoing edges.")
        outputs = _outputs_for(by_id[source], spec)
        if handle not in outputs:
            errors.append(
                f"Edge from '{source}' must use one of its outputs {outputs} (got '{handle}')."
            )
        outgoing[source].append(target)
        incoming[target] += 1

    for entry in entries:
        if incoming[entry]:
            errors.append(f"Start/Trigger node '{entry}' cannot have incoming edges.")
    for end in ends:
        if not incoming[end]:
            errors.append(f"End node '{end}' must have an incoming edge.")

    if len(entries) == 1:
        reachable = {entries[0]}
        queue = deque(entries)
        while queue:
            for nxt in outgoing[queue.popleft()]:
                if nxt not in reachable:
                    reachable.add(nxt)
                    queue.append(nxt)
        for node_id in by_id:
            if node_id not in reachable:
                kind = "orphan" if not incoming[node_id] and not outgoing[node_id] else "unreachable"
                errors.append(f"Node '{node_id}' is {kind} (not reachable from the start node).")
    return errors


def _label_of(node: dict[str, Any]) -> str | None:
    data = node.get("data")
    return node.get("label") or (data.get("label") if isinstance(data, dict) else None)


def compile_graph(definition: Any) -> dict[str, Any]:
    """Validate and compile a definition into the frozen executable IR."""
    definition = _as_dict(definition)
    errors = validate_graph(definition)
    cycles = detect_cycles(definition)
    if cycles:
        errors.append("Cycles are not allowed: " + "; ".join(" -> ".join(c) for c in cycles[:3]))
    if errors:
        raise GraphValidationError(errors)

    specs = resolve_node_types(definition)
    order = topological_sort(definition)
    edges = _normalised_edges(definition)
    by_id = {n["id"]: n for n in _nodes(definition)}
    start = extract_start_node(definition)["id"]
    ir: dict[str, Any] = {
        "ir_version": IR_VERSION,
        "start": start,
        "ends": [n["id"] for n in extract_end_nodes(definition)],
        "nodes": {
            node_id: {
                "id": node_id,
                "type": specs[node_id].type,
                "label": _label_of(by_id[node_id]),
                "config": by_id[node_id].get("config") or {},
                "runs_in": specs[node_id].runs_in,
                "outputs": _outputs_for(by_id[node_id], specs[node_id]),
            }
            for node_id in order
        },
        "edges": edges,
        "order": order,
        "supersteps": group_into_supersteps(order, edges),
    }
    ir["hash"] = compute_graph_hash(ir)
    return ir

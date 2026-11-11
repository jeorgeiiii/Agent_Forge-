"""Cluster 6 (pure parts) - data flow along edges, conditions and merges.

Run state shape (also what gets checkpointed)::

    {
      "status": "running" | "paused" | "success" | "failed" | "cancelled",
      "superstep": <index of next superstep to run>,
      "nodes": {
        node_id: {"status": "success"|"skipped"|"failed"|"waiting",
                  "outputs": {handle: value}, "error": str|None,
                  "started_at": iso, "finished_at": iso}
      },
      "output": <final output once finished>,
    }
"""

from __future__ import annotations

from typing import Any

from .expressions import ExpressionError, evaluate_expression, has_expression, render_parameter

_OPERATORS = {
    "equals": lambda a, b: a == b,
    "not_equals": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and b is not None and a > b,
    "gte": lambda a, b: a is not None and b is not None and a >= b,
    "lt": lambda a, b: a is not None and b is not None and a < b,
    "lte": lambda a, b: a is not None and b is not None and a <= b,
    "contains": lambda a, b: a is not None and b in a,
    "not_contains": lambda a, b: a is None or b not in a,
    "starts_with": lambda a, b: isinstance(a, str) and a.startswith(str(b)),
    "ends_with": lambda a, b: isinstance(a, str) and a.endswith(str(b)),
    "is_empty": lambda a, _b: a in (None, "", [], {}),
    "is_not_empty": lambda a, _b: a not in (None, "", [], {}),
    "is_true": lambda a, _b: bool(a),
    "is_false": lambda a, _b: not a,
}


def primary_output(outputs: dict[str, Any] | None) -> Any:
    """The value other nodes see as this node's output (first produced handle)."""
    if not outputs:
        return None
    return next(iter(outputs.values()))


def node_outputs_for_context(state: dict[str, Any]) -> dict[str, Any]:
    return {
        node_id: primary_output(info.get("outputs"))
        for node_id, info in state.get("nodes", {}).items()
        if info.get("status") == "success"
    }


def resolve_inputs_for_node(ir: dict[str, Any], node_id: str, state: dict[str, Any], run_input: Any) -> dict[str, Any]:
    """Collect upstream outputs that flow into ``node_id``.

    Returns ``{"active": bool, "input": value, "inputs": {source_id: value}}``.
    A node is inactive (skipped) when none of its incoming edges carried data,
    e.g. it sits on the branch an IF node did not take.
    """
    if node_id == ir["start"]:
        return {"active": True, "input": run_input, "inputs": {}}
    inputs: dict[str, Any] = {}
    for edge in ir["edges"]:
        if edge["target"] != node_id:
            continue
        source = state["nodes"].get(edge["source"], {})
        outputs = source.get("outputs") or {}
        if source.get("status") == "success" and edge["source_handle"] in outputs:
            inputs[edge["source"]] = outputs[edge["source_handle"]]
    if not inputs:
        return {"active": False, "input": None, "inputs": {}}
    # deterministic: first active parent in topological order
    order = ir["order"]
    first = min(inputs, key=order.index)
    return {"active": True, "input": inputs[first], "inputs": inputs}


def evaluate_condition(condition: Any, context: dict[str, Any]) -> bool:
    """Evaluate an IF/Switch condition.

    Accepts an expression string (``"{{ $json.total > 100 }}"`` or bare
    ``"$json.total > 100"``), a boolean, or a rule dict
    ``{"left": ..., "operator": "gt", "right": ...}`` / ``{"all": [rules]}`` / ``{"any": [rules]}``.
    """
    if isinstance(condition, bool):
        return condition
    if isinstance(condition, str):
        if has_expression(condition):
            return bool(render_parameter(condition, context))
        try:
            return bool(evaluate_expression(condition, context))
        except ExpressionError:
            raise
    if isinstance(condition, dict):
        if "all" in condition:
            return all(evaluate_condition(c, context) for c in condition["all"])
        if "any" in condition:
            return any(evaluate_condition(c, context) for c in condition["any"])
        operator = condition.get("operator", "equals")
        if operator not in _OPERATORS:
            raise ExpressionError(f"Unknown condition operator '{operator}'.")
        left = render_parameter(condition.get("left"), context)
        right = render_parameter(condition.get("right"), context)
        try:
            return bool(_OPERATORS[operator](left, right))
        except TypeError as error:
            raise ExpressionError(f"Cannot compare {left!r} and {right!r}: {error}") from error
    raise ExpressionError(f"Unsupported condition: {condition!r}")


def merge_branches(inputs: dict[str, Any], mode: str = "append", key: str | None = None, order: list[str] | None = None) -> Any:
    """Combine outputs from several branches for a Merge node."""
    ordered = [inputs[k] for k in sorted(inputs, key=(order.index if order else str))] if inputs else []
    if mode == "first":
        return ordered[0] if ordered else None
    if mode == "wait_for_all":
        return dict(inputs)
    if mode == "combine_by_key":
        if not key:
            raise ValueError("combine_by_key merge requires a 'key'.")
        combined: dict[Any, dict[str, Any]] = {}
        for value in ordered:
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, dict) and key in item:
                    combined.setdefault(item[key], {}).update(item)
        return list(combined.values())
    if mode != "append":
        raise ValueError(f"Unknown merge mode '{mode}'.")
    result: list[Any] = []
    for value in ordered:
        if isinstance(value, list):
            result.extend(value)
        else:
            result.append(value)
    return result


def new_run_state() -> dict[str, Any]:
    return {"status": "running", "superstep": 0, "nodes": {}, "output": None}


def final_output(ir: dict[str, Any], state: dict[str, Any]) -> Any:
    """Output of the run: the End node that ran (or the last successful node)."""
    for end in ir["ends"]:
        info = state["nodes"].get(end, {})
        if info.get("status") == "success":
            return primary_output(info.get("outputs"))
    for node_id in reversed(ir["order"]):
        info = state["nodes"].get(node_id, {})
        if info.get("status") == "success":
            return primary_output(info.get("outputs"))
    return None

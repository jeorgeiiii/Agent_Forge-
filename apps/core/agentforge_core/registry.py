"""Cluster 5 - Node registry / plugin system.

A node type is described by a ``NodeTypeSpec`` (metadata + JSON-schema-like
parameter schema). The API uses specs to validate graphs and build editor
forms; the worker additionally attaches an executor function per type.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

NodeExecutor = Callable[..., Any]


@dataclass(frozen=True)
class NodeTypeSpec:
    type: str
    display_name: str
    category: str  # trigger | core | flow | data | ai | integration
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    outputs: tuple[str, ...] = ("main",)
    is_entry: bool = False  # start / trigger nodes
    is_end: bool = False
    # How the runtime executes it: "activity" (worker activity) or
    # "workflow" (handled inside the workflow itself, e.g. wait / approval).
    runs_in: str = "activity"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["outputs"] = list(self.outputs)
        return data


_SPECS: dict[str, NodeTypeSpec] = {}
_EXECUTORS: dict[str, NodeExecutor] = {}


class UnknownNodeTypeError(KeyError):
    pass


def register_node_type(spec: NodeTypeSpec, executor: NodeExecutor | None = None, *, replace: bool = False) -> None:
    """Add a node definition (and optionally its executor) to the global registry."""
    if spec.type in _SPECS and not replace and _SPECS[spec.type] != spec:
        raise ValueError(f"Node type '{spec.type}' is already registered.")
    _SPECS[spec.type] = spec
    if executor is not None:
        _EXECUTORS[spec.type] = executor


def register_node_executor(node_type: str, executor: NodeExecutor) -> None:
    """Attach an executor to an already-registered node type (used by the worker)."""
    get_node_type(node_type)
    _EXECUTORS[node_type] = executor


def get_node_type(node_type: str) -> NodeTypeSpec:
    try:
        return _SPECS[node_type]
    except KeyError as error:
        raise UnknownNodeTypeError(f"Unknown node type: {node_type}") from error


def list_node_types(category: str | None = None) -> list[NodeTypeSpec]:
    specs = sorted(_SPECS.values(), key=lambda s: (s.category, s.display_name))
    return [s for s in specs if category is None or s.category == category]


def get_node_schema(node_type: str) -> dict[str, Any]:
    return get_node_type(node_type).parameters


def get_node_executor(node_type: str) -> NodeExecutor:
    get_node_type(node_type)
    try:
        return _EXECUTORS[node_type]
    except KeyError as error:
        raise UnknownNodeTypeError(f"No executor registered for node type: {node_type}") from error


# --------------------------------------------------------------------------- #
# Minimal JSON-schema validator (type, required, enum, min/max, items, nested)
# --------------------------------------------------------------------------- #

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def _is_expression(value: Any) -> bool:
    return isinstance(value, str) and "{{" in value and "}}" in value


def _validate(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected = schema.get("type")
    # Expressions are resolved at runtime, so any field may hold one.
    if _is_expression(value) and expected not in (None, "object", "array"):
        return
    if expected:
        allowed = _TYPES.get(expected, ())
        ok = isinstance(value, allowed) and not (expected in ("number", "integer") and isinstance(value, bool))
        if not ok:
            errors.append(f"{path or 'config'} must be of type {expected}.")
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']}.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} must be >= {schema['minimum']}.")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path} must be <= {schema['maximum']}.")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path} must be at least {schema['minLength']} characters.")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path} must be at most {schema['maxLength']} characters.")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value or value[key] in (None, ""):
                errors.append(f"{(path + '.') if path else ''}{key} is required.")
        for key, sub in value.items():
            # null on an optional field means "not set" (editors often send it); required
            # fields are already reported above.
            if key in props and sub is not None:
                _validate(sub, props[key], f"{(path + '.') if path else ''}{key}", errors)
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]", errors)


_CONFIG_VALIDATORS: dict[str, Callable[[dict[str, Any]], list[str]]] = {}


def register_config_validator(node_type: str, validator: Callable[[dict[str, Any]], list[str]]) -> None:
    """Extra checks a JSON schema can't express (e.g. unique tool names)."""
    _CONFIG_VALIDATORS[node_type] = validator


def validate_node_config(node_type: str, config: dict[str, Any] | None) -> list[str]:
    """Check a node instance's settings against its schema. Returns a list of errors."""
    errors: list[str] = []
    _validate(config or {}, get_node_schema(node_type), "", errors)
    if not errors and node_type in _CONFIG_VALIDATORS:
        errors.extend(_CONFIG_VALIDATORS[node_type](config or {}))
    return errors

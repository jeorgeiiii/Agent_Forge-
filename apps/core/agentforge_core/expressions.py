"""Cluster 7 - Expression engine.

Lets node parameters reference data from the run, e.g.::

    {{ $json.email }}
    {{ $node["HTTP Request"].json.body.id }}
    {{ $trigger.message | default }}      <- (no filters; use Python-ish syntax)
    {{ "VIP" if $json.total > 100 else "normal" }}

Expressions are parsed with Python's ``ast`` module and then *interpreted* by a
small whitelist-based evaluator - ``eval`` is never called, attribute access
only reads dict keys, and only a fixed set of functions/methods is callable.

Legacy Jinja-style names (``input``, ``nodes``, ``last``) are also available so
existing graphs keep working.
"""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

MAX_EXPRESSION_LENGTH = 2_000
MAX_RESULT_LENGTH = 1_000_000

_DOLLAR_PREFIX = "__d_"
_TEMPLATE_RE = re.compile(r"\{\{(.*?)\}\}", re.S)

GLOBAL_VARIABLES = {
    "$json": "The current node's input data.",
    "$input": "Alias of $json.",
    "$node": 'Outputs of earlier nodes by label or id, e.g. $node["HTTP"].json.',
    "$trigger": "The data the run was started with (webhook body, chat message, manual input).",
    "$now": "Current UTC time as an ISO-8601 string.",
    "$run": "Run metadata: id, graph_id.",
    "$vars": "Workspace/graph variables (if provided).",
    "$args": "Inside an agent tool: the arguments the model passed to the tool.",
}
LEGACY_VARIABLES = ("input", "nodes", "last")


class ExpressionError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _rewrite_dollars(source: str) -> str:
    """Turn ``$name`` into ``__d_name`` outside of string literals."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(source):
        ch = source[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(source):
                out.append(source[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "$":
            out.append(_DOLLAR_PREFIX)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


_ALLOWED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Attribute, ast.Subscript, ast.Slice,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.List, ast.Tuple, ast.Dict,
    ast.Call, ast.keyword,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Not, ast.USub, ast.UAdd, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
)

SAFE_FUNCTIONS: dict[str, Any] = {
    "len": len, "str": str, "int": int, "float": float, "bool": bool, "round": round,
    "abs": abs, "min": min, "max": max, "sum": sum, "sorted": sorted, "list": list,
    "dict": dict, "any": any, "all": all,
    "json_dumps": lambda v: json.dumps(v, default=str),
    "json_loads": json.loads,
    "floor": math.floor, "ceil": math.ceil,
}
SAFE_METHODS: dict[type, set[str]] = {
    str: {"lower", "upper", "strip", "lstrip", "rstrip", "startswith", "endswith", "split",
          "replace", "title", "capitalize", "join", "find", "count", "isdigit"},
    dict: {"get", "keys", "values", "items"},
    list: {"index", "count"},
}


@dataclass(frozen=True)
class Expression:
    source: str
    tree: ast.Expression


@dataclass(frozen=True)
class Template:
    """A parameter string split into literal text and expression parts."""
    source: str
    parts: tuple[str | Expression, ...]

    @property
    def is_single_expression(self) -> bool:
        return len(self.parts) == 1 and isinstance(self.parts[0], Expression)

    @property
    def expressions(self) -> list[Expression]:
        return [p for p in self.parts if isinstance(p, Expression)]


def _compile(source: str) -> Expression:
    source = source.strip()
    if not source:
        raise ExpressionError("Empty expression.")
    if len(source) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError("Expression is too long.")
    try:
        tree = ast.parse(_rewrite_dollars(source), mode="eval")
    except SyntaxError as error:
        raise ExpressionError(f"Invalid expression '{source}': {error.msg}") from error
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(f"'{type(node).__name__}' is not allowed in expressions.")
        if isinstance(node, ast.Name) and node.id.startswith("__") and not node.id.startswith(_DOLLAR_PREFIX):
            raise ExpressionError("Dunder names are not allowed.")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExpressionError("Private attributes are not allowed.")
    return Expression(source=source, tree=tree)


def parse_expression(text: str) -> Template:
    """Parse a parameter string such as ``"Hi {{ $json.name }}"`` into a Template.

    A string with no ``{{ }}`` is returned as a single literal part.
    """
    parts: list[str | Expression] = []
    position = 0
    for match in _TEMPLATE_RE.finditer(text):
        if match.start() > position:
            parts.append(text[position:match.start()])
        parts.append(_compile(match.group(1)))
        position = match.end()
    if position < len(text):
        parts.append(text[position:])
    return Template(source=text, parts=tuple(parts))


def has_expression(value: Any) -> bool:
    return isinstance(value, str) and _TEMPLATE_RE.search(value) is not None


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #

_BINOPS = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b, ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
}
_CMPOPS = {
    ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
    ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b,
}


def _guard_size(value: Any) -> Any:
    if isinstance(value, (str, list)) and len(value) > MAX_RESULT_LENGTH:
        raise ExpressionError("Expression result is too large.")
    return value


def _get_member(base: Any, key: Any, source: str) -> Any:
    if isinstance(base, dict):
        if key in base:
            return base[key]
        raise ExpressionError(f"Key '{key}' not found in {source}.")
    if isinstance(base, (list, tuple, str)):
        if key == "length":
            return len(base)
        if isinstance(key, int):
            try:
                return base[key]
            except IndexError as error:
                raise ExpressionError(f"Index {key} out of range in {source}.") from error
    if base is None:
        raise ExpressionError(f"Cannot read '{key}' of null in {source}.")
    raise ExpressionError(f"Cannot read '{key}' of {type(base).__name__} in {source}.")


class _Evaluator:
    def __init__(self, context: dict[str, Any], source: str):
        self.context = context
        self.source = source

    def eval(self, node: ast.AST) -> Any:
        method = getattr(self, f"_{type(node).__name__}", None)
        if method is None:
            raise ExpressionError(f"Unsupported syntax: {type(node).__name__}")
        return _guard_size(method(node))

    def _Expression(self, node: ast.Expression) -> Any:
        return self.eval(node.body)

    def _Constant(self, node: ast.Constant) -> Any:
        return node.value

    def _Name(self, node: ast.Name) -> Any:
        name = node.id
        if name.startswith(_DOLLAR_PREFIX):
            key = "$" + name[len(_DOLLAR_PREFIX):]
            if key not in self.context:
                raise ExpressionError(f"Unknown variable {key}.")
            return self.context[key]
        if name in ("true", "True"):
            return True
        if name in ("false", "False"):
            return False
        if name in ("null", "None"):
            return None
        if name in self.context:
            return self.context[name]
        if name in SAFE_FUNCTIONS:
            return SAFE_FUNCTIONS[name]
        raise ExpressionError(f"Unknown name '{name}'.")

    def _Attribute(self, node: ast.Attribute) -> Any:
        return _get_member(self.eval(node.value), node.attr, self.source)

    def _Subscript(self, node: ast.Subscript) -> Any:
        base = self.eval(node.value)
        if isinstance(node.slice, ast.Slice):
            if not isinstance(base, (list, tuple, str)):
                raise ExpressionError("Slicing requires a list or string.")
            lower = self.eval(node.slice.lower) if node.slice.lower else None
            upper = self.eval(node.slice.upper) if node.slice.upper else None
            step = self.eval(node.slice.step) if node.slice.step else None
            return base[lower:upper:step]
        key = self.eval(node.slice)
        if isinstance(base, (list, tuple, str)) and isinstance(key, int):
            try:
                return base[key]
            except IndexError as error:
                raise ExpressionError(f"Index {key} out of range.") from error
        return _get_member(base, key, self.source)

    def _BinOp(self, node: ast.BinOp) -> Any:
        left, right = self.eval(node.left), self.eval(node.right)
        if isinstance(node.op, ast.Mult) and isinstance(left, (str, list)) and isinstance(right, int):
            if len(left) * right > MAX_RESULT_LENGTH:
                raise ExpressionError("Expression result is too large.")
        try:
            return _BINOPS[type(node.op)](left, right)
        except Exception as error:  # TypeError, ZeroDivisionError ...
            raise ExpressionError(f"{error} in '{self.source}'") from error

    def _UnaryOp(self, node: ast.UnaryOp) -> Any:
        value = self.eval(node.operand)
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.USub):
            return -value
        return +value

    def _BoolOp(self, node: ast.BoolOp) -> Any:
        if isinstance(node.op, ast.And):
            result: Any = True
            for value in node.values:
                result = self.eval(value)
                if not result:
                    return result
            return result
        result = False
        for value in node.values:
            result = self.eval(value)
            if result:
                return result
        return result

    def _Compare(self, node: ast.Compare) -> Any:
        left = self.eval(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self.eval(comparator)
            try:
                ok = _CMPOPS[type(op)](left, right)
            except TypeError as error:
                raise ExpressionError(f"{error} in '{self.source}'") from error
            if not ok:
                return False
            left = right
        return True

    def _IfExp(self, node: ast.IfExp) -> Any:
        return self.eval(node.body) if self.eval(node.test) else self.eval(node.orelse)

    def _List(self, node: ast.List) -> Any:
        return [self.eval(e) for e in node.elts]

    def _Tuple(self, node: ast.Tuple) -> Any:
        return [self.eval(e) for e in node.elts]

    def _Dict(self, node: ast.Dict) -> Any:
        return {self.eval(k): self.eval(v) for k, v in zip(node.keys, node.values) if k is not None}

    def _Call(self, node: ast.Call) -> Any:
        args = [self.eval(a) for a in node.args]
        kwargs = {k.arg: self.eval(k.value) for k in node.keywords if k.arg}
        if isinstance(node.func, ast.Name):
            func = SAFE_FUNCTIONS.get(node.func.id)
            if func is None:
                raise ExpressionError(f"Function '{node.func.id}' is not allowed.")
        elif isinstance(node.func, ast.Attribute):
            base = self.eval(node.func.value)
            allowed = next((m for t, m in SAFE_METHODS.items() if isinstance(base, t)), set())
            if node.func.attr not in allowed:
                raise ExpressionError(f"Method '{node.func.attr}' is not allowed on {type(base).__name__}.")
            func = getattr(base, node.func.attr)
        else:
            raise ExpressionError("Only named functions can be called.")
        try:
            return func(*args, **kwargs)
        except ExpressionError:
            raise
        except Exception as error:
            raise ExpressionError(f"{error} in '{self.source}'") from error


def evaluate_expression(expression: Expression | str, context: dict[str, Any]) -> Any:
    """Evaluate a single expression (without the ``{{ }}``) against a context."""
    if isinstance(expression, str):
        expression = _compile(expression)
    return _Evaluator(context, expression.source).eval(expression.tree)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_parameter(value: Any, context: dict[str, Any]) -> Any:
    """Resolve expressions inside a node parameter (recursively for dicts/lists).

    A string that is exactly one ``{{ expr }}`` keeps the expression's native
    type (dict, number, ...); mixed strings are interpolated as text.
    """
    if isinstance(value, dict):
        return {k: render_parameter(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_parameter(v, context) for v in value]
    if not has_expression(value):
        return value
    template = parse_expression(value)
    if template.is_single_expression:
        return evaluate_expression(template.parts[0], context)  # type: ignore[arg-type]
    return "".join(
        _stringify(evaluate_expression(part, context)) if isinstance(part, Expression) else part
        for part in template.parts
    )


def render_template(template: str, context: dict[str, Any]) -> str:
    """Jinja-style template rendering.

    ``{{ $... }}`` segments are resolved by this engine; everything else
    (filters, ``{% for %}`` blocks, legacy ``{{ input.x }}``) is rendered by a
    sandboxed Jinja environment when Jinja2 is installed.
    """
    placeholders: dict[str, str] = {}

    def substitute(match: re.Match[str]) -> str:
        inner = match.group(1)
        if "$" not in _strip_strings(inner):
            return match.group(0)
        token = f"AFX{len(placeholders)}XFA"
        placeholders[token] = _stringify(evaluate_expression(inner, context))
        return token

    pre_rendered = _TEMPLATE_RE.sub(substitute, template)
    try:
        from jinja2 import StrictUndefined
        from jinja2.sandbox import SandboxedEnvironment
    except ImportError:  # pragma: no cover - API side may not ship Jinja
        rendered = str(render_parameter(pre_rendered, context))
    else:
        env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
        jinja_context = {k: v for k, v in context.items() if not k.startswith("$")}
        # $json -> json, $node -> node ... but never shadow the legacy names
        # (``input`` means the run input in old templates, not ``$input``).
        for key, value in context.items():
            if key.startswith("$"):
                jinja_context.setdefault(key[1:], value)
        rendered = env.from_string(pre_rendered).render(**jinja_context)
    for token, text in placeholders.items():
        rendered = rendered.replace(token, text)
    return rendered


def _strip_strings(source: str) -> str:
    return re.sub(r"'[^']*'|\"[^\"]*\"", "", source)


# --------------------------------------------------------------------------- #
# Validation & autocomplete
# --------------------------------------------------------------------------- #


def _node_references(expression: Expression) -> list[str]:
    """Constant keys used as $node["..."] / $node.x."""
    refs: list[str] = []
    for node in ast.walk(expression.tree):
        target = None
        if isinstance(node, ast.Subscript):
            target, key = node.value, node.slice
            key_value = key.value if isinstance(key, ast.Constant) else None
        elif isinstance(node, ast.Attribute):
            target, key_value = node.value, node.attr
        else:
            continue
        if isinstance(target, ast.Name) and target.id == f"{_DOLLAR_PREFIX}node" and isinstance(key_value, str):
            refs.append(key_value)
    return refs


def validate_expression(text: str, known_nodes: Iterable[str] | None = None) -> list[str]:
    """Return a list of problems with a parameter string ([] when valid).

    ``known_nodes`` (labels and/or ids) enables checking ``$node[...]`` references.
    """
    try:
        template = parse_expression(text)
    except ExpressionError as error:
        return [str(error)]
    errors: list[str] = []
    known = set(known_nodes) if known_nodes is not None else None
    for expression in template.expressions:
        for node in ast.walk(expression.tree):
            if isinstance(node, ast.Name) and node.id.startswith(_DOLLAR_PREFIX):
                name = "$" + node.id[len(_DOLLAR_PREFIX):]
                if name not in GLOBAL_VARIABLES:
                    errors.append(f"Unknown variable {name}.")
        if known is not None:
            for ref in _node_references(expression):
                if ref not in known:
                    errors.append(f"$node[\"{ref}\"] does not refer to an earlier node.")
    return errors


def list_available_references(ir: dict[str, Any], node_id: str) -> list[dict[str, str]]:
    """What a node can reference: global variables plus every upstream node."""
    refs = [{"expression": name, "description": desc} for name, desc in GLOBAL_VARIABLES.items()]
    parents: dict[str, list[str]] = {}
    for edge in ir.get("edges", []):
        parents.setdefault(edge["target"], []).append(edge["source"])
    seen: set[str] = set()
    stack = list(parents.get(node_id, []))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(parents.get(current, []))
    order = ir.get("order", list(seen))
    for upstream in [n for n in order if n in seen]:
        node = ir["nodes"][upstream]
        name = node.get("label") or upstream
        refs.append({
            "expression": f'$node["{name}"].json',
            "description": f"Output of '{name}' ({node['type']}).",
        })
    return refs


# --------------------------------------------------------------------------- #
# Context helper
# --------------------------------------------------------------------------- #


def build_expression_context(
    *,
    run_input: Any,
    node_outputs: dict[str, Any],
    labels: dict[str, str] | None = None,
    current_input: Any = None,
    run_meta: dict[str, Any] | None = None,
    variables: dict[str, Any] | None = None,
    now: str | None = None,
    tool_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    labels = labels or {}
    by_name: dict[str, Any] = {}
    for node_id, output in node_outputs.items():
        entry = {"json": output, "output": output}
        by_name[node_id] = entry
        label = labels.get(node_id)
        if label and label not in by_name:
            by_name[label] = entry
    return {
        "$json": current_input,
        "$input": current_input,
        "$node": by_name,
        "$trigger": run_input,
        "$now": now or datetime.now(timezone.utc).isoformat(),
        "$run": run_meta or {},
        "$vars": variables or {},
        "$args": tool_args or {},
        # legacy names used by existing Jinja templates
        "input": run_input,
        "nodes": dict(node_outputs),
        "last": current_input,
    }

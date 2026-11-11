import pytest

import agentforge_core  # noqa: F401
from agentforge_core.compiler import (
    GraphValidationError,
    compile_graph,
    detect_cycles,
    group_into_supersteps,
    topological_sort,
    validate_graph,
)
from agentforge_core.expressions import (
    ExpressionError,
    build_expression_context,
    evaluate_expression,
    list_available_references,
    render_parameter,
    render_template,
    validate_expression,
)
from agentforge_core.registry import validate_node_config
from agentforge_core.runtime import (
    evaluate_condition,
    final_output,
    merge_branches,
    new_run_state,
    resolve_inputs_for_node,
)


def node(id, type, **config):
    return {"id": id, "type": type, "label": id.upper(), "config": config}


BRANCHING = {
    "nodes": [
        node("start", "start"),
        node("check", "if", condition="{{ $json.total > 100 }}"),
        node("vip", "set", fields={"tier": "vip"}),
        node("normal", "set", fields={"tier": "normal"}),
        node("merge", "merge", mode="append"),
        node("end", "end"),
    ],
    "edges": [
        {"source": "start", "target": "check"},
        {"source": "check", "target": "vip", "sourceHandle": "true"},
        {"source": "check", "target": "normal", "sourceHandle": "false"},
        {"source": "vip", "target": "merge"},
        {"source": "normal", "target": "merge"},
        {"source": "merge", "target": "end"},
    ],
}


# ---------------------------------------------------------------- compiler


def test_compile_branching_graph_produces_parallel_supersteps():
    ir = compile_graph(BRANCHING)
    assert ir["start"] == "start" and ir["ends"] == ["end"]
    assert ir["supersteps"] == [["start"], ["check"], ["vip", "normal"], ["merge"], ["end"]]
    assert len(ir["hash"]) == 64
    assert compile_graph(BRANCHING)["hash"] == ir["hash"]  # deterministic


def test_legacy_linear_graph_still_compiles():
    legacy = {
        "nodes": [node("start", "start"), node("t", "template", template="Hi {{ input.name }}"), node("end", "end")],
        "edges": [{"source": "start", "target": "t"}, {"source": "t", "target": "end"}],
    }
    assert compile_graph(legacy)["order"] == ["start", "t", "end"]


def test_validate_reports_friendly_errors():
    bad = {
        "nodes": [node("start", "start"), node("x", "unknown_type"), node("orphan", "noop"), node("end", "end")],
        "edges": [{"source": "start", "target": "end"}],
    }
    errors = validate_graph(bad)
    assert any("unknown type" in e for e in errors)
    assert any("orphan" in e for e in errors)


def test_if_edges_must_use_true_or_false():
    graph = {**BRANCHING, "edges": [dict(e, sourceHandle=None) if e["source"] == "check" else e for e in BRANCHING["edges"]]}
    with pytest.raises(GraphValidationError, match="outputs"):
        compile_graph(graph)


def test_cycles_detected_and_rejected():
    cyclic = {
        "nodes": [node("start", "start"), node("a", "noop"), node("b", "noop"), node("end", "end")],
        "edges": [
            {"source": "start", "target": "a"}, {"source": "a", "target": "b"},
            {"source": "b", "target": "a"}, {"source": "b", "target": "end"},
        ],
    }
    assert detect_cycles(cyclic)
    with pytest.raises(GraphValidationError):
        topological_sort(cyclic)
    with pytest.raises(GraphValidationError, match="Cycles"):
        compile_graph(cyclic)


def test_supersteps_use_longest_path():
    edges = [{"source": "s", "target": "a"}, {"source": "a", "target": "b"}, {"source": "s", "target": "b"}]
    assert group_into_supersteps(["s", "a", "b"], edges) == [["s"], ["a"], ["b"]]


def test_node_config_schema_validation():
    assert validate_node_config("http", {}) == ["url is required."]
    assert validate_node_config("http", {"url": "https://x", "method": "TRACE"})
    assert validate_node_config("wait", {"seconds": "{{ $json.delay }}"}) == []  # expressions allowed


# ------------------------------------------------------------- expressions


@pytest.fixture
def ctx():
    return build_expression_context(
        run_input={"name": "Ada"},
        node_outputs={"http1": {"body": {"id": 7, "tags": ["a", "b"]}}},
        labels={"http1": "HTTP"},
        current_input={"total": 150, "email": "ADA@X.IO"},
    )


def test_expression_references(ctx):
    assert evaluate_expression('$node["HTTP"].json.body.id', ctx) == 7
    assert evaluate_expression("$node.http1.json.body.tags.length", ctx) == 2
    assert evaluate_expression("$json.email.lower()", ctx) == "ada@x.io"
    assert evaluate_expression('"vip" if $json.total > 100 else "normal"', ctx) == "vip"
    assert evaluate_expression("input.name", ctx) == "Ada"  # legacy name


def test_render_parameter_keeps_types_and_interpolates(ctx):
    assert render_parameter("{{ $json.total }}", ctx) == 150
    assert render_parameter("Hi {{ $trigger.name }} #{{ $node.HTTP.json.body.id }}", ctx) == "Hi Ada #7"
    assert render_parameter({"a": ["{{ $json.total * 2 }}"]}, ctx) == {"a": [300]}


def test_render_template_mixes_dollar_and_jinja(ctx):
    assert render_template("{{ input.name | upper }} / {{ $json.total }}", ctx) == "ADA / 150"


@pytest.mark.parametrize("source", [
    "__import__('os')", "$json.__class__", "open('x')", "(lambda: 1)()", "[x for x in $json]", "$json.email.format()",
])
def test_unsafe_expressions_rejected(ctx, source):
    with pytest.raises(ExpressionError):
        evaluate_expression(source, ctx)


def test_validate_expression_and_references():
    assert validate_expression("{{ $json.a }}") == []
    assert validate_expression("{{ $nope }}") == ["Unknown variable $nope."]
    assert validate_expression('{{ $node["Missing"].json }}', known_nodes=["HTTP"])
    assert validate_expression("{{ 1 + }}")
    refs = list_available_references(compile_graph(BRANCHING), "merge")
    names = [r["expression"] for r in refs]
    assert '$node["VIP"].json' in names and '$node["START"].json' in names
    assert '$node["END"].json' not in names


# ----------------------------------------------------------------- runtime


def _simulate(graph, run_input, results):
    """Walk supersteps the way the workflow does, with fake node results."""
    ir = compile_graph(graph)
    state = new_run_state()
    for layer in ir["supersteps"]:
        for node_id in layer:
            resolved = resolve_inputs_for_node(ir, node_id, state, run_input)
            if not resolved["active"]:
                state["nodes"][node_id] = {"status": "skipped", "outputs": {}}
                continue
            state["nodes"][node_id] = {"status": "success", "outputs": results(node_id, resolved)}
    return ir, state


def test_if_branch_skips_the_other_side():
    def results(node_id, resolved):
        if node_id in ("start", "end"):
            return {"main": resolved["input"]}
        if node_id == "check":
            return {"true" if resolved["input"]["total"] > 100 else "false": resolved["input"]}
        if node_id == "merge":
            return {"main": merge_branches(resolved["inputs"])}
        return {"main": {"via": node_id}}

    ir, state = _simulate(BRANCHING, {"total": 150}, results)
    assert state["nodes"]["normal"]["status"] == "skipped"
    assert state["nodes"]["vip"]["status"] == "success"
    assert final_output(ir, state) == [{"via": "vip"}]


def test_conditions_and_merges(ctx):
    assert evaluate_condition({"left": "{{ $json.total }}", "operator": "gte", "right": 150}, ctx)
    assert evaluate_condition({"any": [False, "$json.total < 10"]}, ctx) is False
    assert evaluate_condition("$json.email.endswith('IO')", ctx)
    assert merge_branches({"a": [1], "b": 2}, order=["a", "b"]) == [1, 2]
    assert merge_branches({"a": [{"id": 1, "x": 1}], "b": [{"id": 1, "y": 2}]}, "combine_by_key", "id") == [{"id": 1, "x": 1, "y": 2}]

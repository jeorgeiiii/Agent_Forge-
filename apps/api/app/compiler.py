"""Cluster 4 - Graph compilation & validation.

The implementation lives in the shared ``agentforge_core`` package so the API
(validation, publishing) and the worker use exactly the same rules.
"""

import agentforge_core  # noqa: F401 - registers built-in node types
from agentforge_core.compiler import (
    GraphValidationError,
    compile_graph,
    compute_graph_hash,
    detect_cycles,
    extract_end_nodes,
    extract_start_node,
    group_into_supersteps,
    resolve_node_types,
    topological_sort,
    validate_graph,
)

__all__ = [
    "GraphValidationError",
    "compile_graph",
    "compute_graph_hash",
    "detect_cycles",
    "extract_end_nodes",
    "extract_start_node",
    "group_into_supersteps",
    "resolve_node_types",
    "topological_sort",
    "validate_graph",
]

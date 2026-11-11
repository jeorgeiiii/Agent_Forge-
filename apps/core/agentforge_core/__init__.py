"""Shared AgentForge logic used by both the API and the Temporal worker.

Everything in this package is pure Python with no I/O, so it is safe to import
inside Temporal workflow code and easy to unit test.
"""

from .builtin_nodes import register_builtin_nodes

register_builtin_nodes()

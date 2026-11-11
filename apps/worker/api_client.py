"""Tiny client for the API's internal endpoints (checkpoints, credentials, schedule fires).

The worker never talks to Postgres directly; the API stays the single owner of
the database. Requests carry ``X-Internal-Token`` which must match the API's
``INTERNAL_API_TOKEN``.
"""

import os
from typing import Any

import httpx

# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 (::1) first and
# each new connection stalls ~0.3-2 s before falling back to IPv4.
API_URL = os.getenv("AGENTFORGE_API_URL", "http://127.0.0.1:8000").rstrip("/")
INTERNAL_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

_client: httpx.AsyncClient | None = None


class InternalApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    return bool(INTERNAL_TOKEN)


def _shared_client() -> httpx.AsyncClient:
    """One keep-alive connection pool for the whole worker process."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=API_URL,
            trust_env=False,
            headers={"X-Internal-Token": INTERNAL_TOKEN},
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _client


async def call(method: str, path: str, json: Any = None, timeout: float = 10) -> Any:
    if not INTERNAL_TOKEN:
        raise InternalApiError("INTERNAL_API_TOKEN is not set for the worker.")
    try:
        response = await _shared_client().request(method, path, json=json, timeout=timeout)
    except httpx.HTTPError as error:
        raise InternalApiError(f"{method} {path} failed: {error}") from error
    if response.status_code >= 400:
        raise InternalApiError(f"{method} {path} failed with {response.status_code}: {response.text[:300]}",
                               response.status_code)
    return response.json() if response.content else None

import base64
import os
import tempfile
from pathlib import Path

import pytest

# Configure before the app (and its engine) is imported.
_DB = Path(tempfile.mkdtemp()) / "test.db"
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_DB.as_posix()}",
    "INTERNAL_API_TOKEN": "test-internal-token",
    "CREDENTIALS_MASTER_KEY": base64.urlsafe_b64encode(b"k" * 32).decode(),
    "REDIS_URL": "redis://127.0.0.1:1/0",  # unreachable -> in-memory rate limiter
    "TEMPORAL_ADDRESS": "127.0.0.1:1",  # unreachable -> runs return 503
    "AUTH_REQUIRED": "false",
    "TRUSTED_PROXIES": "testclient",  # TestClient's peer; tests send X-Forwarded-For
    "RUN_RECONCILE_INTERVAL_SECONDS": "0",
    "MODEL_PRICES": "{\"llama-test\": {\"input\": 1.0, \"output\": 2.0}}",
    "COPILOT_API_KEY": "",
    "GROQ_API_KEY": "",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client

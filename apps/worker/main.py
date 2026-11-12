import asyncio
import os
from pathlib import Path


def _load_env_files() -> None:
    """Local runs: read KEY=VALUE lines from the repo-root .env, then apps/worker/.env (same files as the API).

    Real environment variables always win, so Docker/production settings are never overridden.
    Must run before importing activities/api_client, which read their settings at import time.
    """
    here = Path(__file__).resolve().parent
    for path in (here.parents[1] / ".env", here / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_files()

from temporalio.client import Client  # noqa: E402
from temporalio.worker import Worker

from activities import (checkpoint_state, emit_events, execute_node, fire_schedule_trigger, llm_chat,
                        load_memory, save_memory, save_summary)
from workflow import AgentWorkflow, RunGraphWorkflow, ScheduleFireWorkflow


def _configure_logging() -> None:
    """LOG_FORMAT=json -> one JSON object per line (same shape as the API's logs)."""
    import json
    import logging
    import sys
    from datetime import datetime, timezone

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    if os.getenv("LOG_FORMAT", "text") == "json":
        reserved = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

        class JsonFormatter(logging.Formatter):
            def format(self, record):
                entry = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                         "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
                         "service": "worker"}
                entry.update({k: v for k, v in vars(record).items() if k not in reserved and not k.startswith("_")})
                if record.exc_info:
                    entry["exc"] = self.formatException(record.exc_info)
                return json.dumps(entry, default=str)

        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)


async def main() -> None:
    _configure_logging()
    client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    worker = Worker(
        client,
        task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "agentforge"),
        workflows=[RunGraphWorkflow, AgentWorkflow, ScheduleFireWorkflow],
        activities=[execute_node, llm_chat, load_memory, save_memory, save_summary, emit_events, checkpoint_state, fire_schedule_trigger],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

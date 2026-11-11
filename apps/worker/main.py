import asyncio
import os

from temporalio.client import Client
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

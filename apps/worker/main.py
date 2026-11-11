import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from activities import (checkpoint_state, emit_events, execute_node, fire_schedule_trigger, llm_chat,
                        load_memory, save_memory, save_summary)
from workflow import AgentWorkflow, RunGraphWorkflow, ScheduleFireWorkflow


async def main() -> None:
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

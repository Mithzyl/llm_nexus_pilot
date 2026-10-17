"""Process-owned execution tasks; HTTP observers never cancel these tasks.

Durable nodes/events remain in SQL. This owner does not resume tasks after a
process crash; graceful shutdown waits and then lets existing cancellation
handling persist cancelled/unknown outcomes before shared resources close.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowExecutionTasks:
    """Retain running tasks independently of responses and drain them at shutdown."""

    def __init__(self) -> None:
        """Create an empty process-local owner; SQL creation still enforces idempotency."""
        self.active: dict[str, asyncio.Task] = {}

    def start(self, workflow_id: str, execution: Coroutine[Any, Any, Any]) -> asyncio.Task:
        """Start once and hold a strong reference until completion; observe all failures."""
        existing = self.active.get(workflow_id)
        if existing is not None:
            execution.close()
            return existing
        task = asyncio.create_task(execution, name=f"workflow:{workflow_id}")
        self.active[workflow_id] = task

        def completed(done: asyncio.Task) -> None:
            """Release the reference and consume exceptions without logging private payloads."""
            self.active.pop(workflow_id, None)
            if not done.cancelled() and done.exception() is not None:
                logger.error("Workflow execution failed outside its runner: %s", workflow_id)

        task.add_done_callback(completed)
        return task

    async def shutdown(self, timeout_seconds: float = 30) -> None:
        """Wait for active work, cancel leftovers, and await their persistence cleanup."""
        tasks = list(self.active.values())
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


workflow_execution_tasks = WorkflowExecutionTasks()

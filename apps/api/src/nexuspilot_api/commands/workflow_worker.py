"""Local independent Worker prototype; disabled unless explicitly selected in private config.

Run from apps/api: PYTHONPATH=src .venv/bin/python -m nexuspilot_api.commands.workflow_worker
Requires migrated MySQL, MinIO and configured Providers. This can invoke paid
models for accepted jobs; use test Providers for verification. Do not enable for
long autonomous tasks until RabbitMQ and the remaining inter-node recovery cases pass.
"""

import asyncio
import uuid

from sqlalchemy import func, or_, select

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
from nexuspilot_api.features.agent_runtime.services.workflow_worker import execute_claim
from nexuspilot_api.infrastructure.database import database_session_factory
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.main import app, lifespan
from nexuspilot_api.models import AgentWorkflowStatus, LlmAgentWorkflowExecution, LlmOutboxEvent


async def execute_owned_job(factory, storage, registry, prices, claim):
    """Keep polling after job cancellation, but propagate a stop of the consumer itself."""
    try:
        await execute_claim(factory, storage, registry, prices, claim)
    except asyncio.CancelledError:
        # A cancelled child task does not increment the consumer's cancellation count.
        # Explicit process shutdown does; swallowing it would defeat a requested stop.
        if asyncio.current_task().cancelling():
            raise


async def main():
    """Poll only explicitly Worker-owned Outbox workflows; existing inline runs are untouched."""
    if get_settings().workflow_execution_backend != "worker":
        raise RuntimeError("Worker backend is not enabled")
    owner = "worker-" + str(uuid.uuid4())
    async with lifespan(app):
        while True:
            async with database_session_factory() as db:
                ids = list(await db.scalars(select(
                    LlmAgentWorkflowExecution.workflow_execution_id,
                ).join(LlmOutboxEvent,
                    LlmOutboxEvent.aggregate_id == LlmAgentWorkflowExecution.workflow_execution_id,
                ).where(
                    LlmOutboxEvent.event_type == "agent_workflow.ready",
                    LlmAgentWorkflowExecution.execution_backend == "worker",
                    LlmAgentWorkflowExecution.status.in_([
                        AgentWorkflowStatus.PENDING, AgentWorkflowStatus.RUNNING]),
                    or_(LlmAgentWorkflowExecution.execution_lease_expires_at.is_(None),
                        LlmAgentWorkflowExecution.execution_lease_expires_at
                        < func.current_timestamp()),
                ).order_by(LlmOutboxEvent.created_at).limit(8)))
            for workflow_id in ids:
                claim = await claim_workflow(database_session_factory, workflow_id, owner)
                if claim:
                    await execute_owned_job(database_session_factory, ObjectStorage(),
                        app.state.provider_registry, app.state.price_catalog, claim)
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())

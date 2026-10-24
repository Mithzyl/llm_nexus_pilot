"""Database-owned Worker leases and commit fencing, independent of message delivery."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.models import AgentWorkflowStatus, LlmAgentWorkflowExecution


@dataclass(frozen=True)
class WorkflowClaim:
    """Bind every execution write to a single database-assigned ownership version."""

    workflow_id: str
    owner: str
    version: int


async def claim_workflow(factory, workflow_id, owner, lease_seconds=60):
    """Lock one workflow and claim only pending or expired Worker-owned execution."""
    if not owner or len(owner) > 128 or not 5 <= lease_seconds <= 300:
        raise ValueError("Invalid worker lease parameters")
    async with factory() as db:
        # Read columns only: ownership does not require loading private request objects.
        result = await AsyncSession.execute(db, select(
            LlmAgentWorkflowExecution.status,
            LlmAgentWorkflowExecution.execution_backend,
            LlmAgentWorkflowExecution.execution_lease_version,
            LlmAgentWorkflowExecution.execution_lease_expires_at,
        ).where(LlmAgentWorkflowExecution.workflow_execution_id == workflow_id).with_for_update())
        row = result.first()
        now = await AsyncSession.scalar(db, select(func.current_timestamp()))
        if row is None or row.execution_backend != "worker" or row.status not in {
            AgentWorkflowStatus.PENDING, AgentWorkflowStatus.RUNNING,
        }:
            return None
        expiry = row.execution_lease_expires_at
        if expiry and expiry.replace(tzinfo=None) > now.replace(tzinfo=None):
            return None
        version = row.execution_lease_version + 1
        await AsyncSession.execute(db, update(LlmAgentWorkflowExecution).where(
            LlmAgentWorkflowExecution.workflow_execution_id == workflow_id,
        ).values(execution_owner=owner, execution_lease_version=version,
                 execution_lease_expires_at=now + timedelta(seconds=lease_seconds),
                 status=AgentWorkflowStatus.RUNNING))
        await db.commit()
        return WorkflowClaim(workflow_id, owner, version)


async def verify_execution_claim(db):
    """Fence a Worker transaction under the ownership row lock; do not affect HTTP observers."""
    claim = db.info.get("workflow_execution_claim")
    if claim is None:
        return
    result = await AsyncSession.execute(db, select(
        LlmAgentWorkflowExecution.workflow_execution_id,
    ).where(
        LlmAgentWorkflowExecution.workflow_execution_id == claim.workflow_id,
        LlmAgentWorkflowExecution.execution_owner == claim.owner,
        LlmAgentWorkflowExecution.execution_lease_version == claim.version,
        LlmAgentWorkflowExecution.execution_lease_expires_at > func.current_timestamp(),
    ).with_for_update())
    if result.first() is None:
        await db.rollback()
        raise ResourceConflictError("Workflow execution ownership expired or changed")


async def renew_claim(factory, claim, lease_seconds=60):
    """Extend only the still-current, unexpired running owner's lease."""
    async with factory() as db:
        now = await AsyncSession.scalar(db, select(func.current_timestamp()))
        result = await AsyncSession.execute(db, update(LlmAgentWorkflowExecution).where(
            LlmAgentWorkflowExecution.workflow_execution_id == claim.workflow_id,
            LlmAgentWorkflowExecution.status == AgentWorkflowStatus.RUNNING,
            LlmAgentWorkflowExecution.execution_owner == claim.owner,
            LlmAgentWorkflowExecution.execution_lease_version == claim.version,
            LlmAgentWorkflowExecution.execution_lease_expires_at > now,
        ).values(execution_lease_expires_at=now + timedelta(seconds=lease_seconds)))
        await db.commit()
        return result.rowcount == 1

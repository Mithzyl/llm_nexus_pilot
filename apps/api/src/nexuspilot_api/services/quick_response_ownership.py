"""Database leases fence quick-response writers, including commits inside model/tool services."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.models.quick_response import QuickResponseExecution as Execution


@dataclass(frozen=True)
class ResponseClaim:
    """Identify the one consumer allowed to commit this execution's current version."""

    execution_id: str
    owner: str
    version: int


async def claim_response(factory, execution_id, owner, lease_seconds=60):
    """Claim queued or expired running work under a row lock, without deciding replay safety."""
    if not owner or len(owner) > 128 or not 5 <= lease_seconds <= 300:
        raise ValueError("Invalid response lease")
    async with factory() as db:
        row = await db.scalar(
            select(Execution)
            .where(Execution.response_execution_id == execution_id)
            .with_for_update()
        )
        now = await db.scalar(select(func.current_timestamp()))
        if row is None or row.status not in {"queued", "running"}:
            return None
        if row.lease_expires_at and row.lease_expires_at.replace(tzinfo=None) > now.replace(
            tzinfo=None
        ):
            return None
        row.execution_owner = owner
        row.lease_version += 1
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.status = "running"
        await db.commit()
        return ResponseClaim(execution_id, owner, row.lease_version)


async def verify_response_claim(db):
    """Lock and validate ownership before a transaction can publish new execution facts."""
    claim = db.info.get("quick_response_claim")
    if claim is None:
        return
    valid = await AsyncSession.scalar(
        db,
        select(Execution.response_execution_id)
        .where(
            Execution.response_execution_id == claim.execution_id,
            Execution.execution_owner == claim.owner,
            Execution.lease_version == claim.version,
            Execution.lease_expires_at > func.current_timestamp(),
        )
        .with_for_update(),
    )
    if valid is None:
        await db.rollback()
        raise ResourceConflictError("Quick response ownership expired or changed")


async def renew_response_claim(factory, claim, lease_seconds=60):
    """Renew the still-current live consumer only; a replacement owner is never extended."""
    async with factory() as db:
        now = await db.scalar(select(func.current_timestamp()))
        changed = await db.execute(
            update(Execution)
            .where(
                Execution.response_execution_id == claim.execution_id,
                Execution.execution_owner == claim.owner,
                Execution.lease_version == claim.version,
                Execution.status == "running",
                Execution.lease_expires_at > now,
            )
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
        )
        await db.commit()
        return changed.rowcount == 1

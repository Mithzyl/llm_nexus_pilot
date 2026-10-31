"""Shared disclosure checks for live queries, restored tool messages and network restrictions."""

from sqlalchemy import select

from nexuspilot_api.features.memory.models.product_memory import (
    UserMemoryDisclosure,
    UserMemoryEntry,
)


def disclosure_condition(run_id, recipient_entry=None):
    """Match only an unrevoked grant for the current content version, never another Run."""
    query = select(UserMemoryDisclosure.disclosure_id).where(
        UserMemoryDisclosure.entry_id == UserMemoryEntry.entry_id,
        UserMemoryDisclosure.entry_version == UserMemoryEntry.version,
        UserMemoryDisclosure.run_id == run_id,
        UserMemoryDisclosure.revoked_at.is_(None),
    )
    if recipient_entry is not None:
        query = query.where(UserMemoryDisclosure.recipient_entry == recipient_entry)
    return query.exists()


async def is_disclosed(db, row, run_id, recipient_entry):
    """Check current persisted permission before restoring sensitive text to model messages."""
    if not run_id:
        return False
    return bool(
        await db.scalar(
            select(UserMemoryEntry.entry_id).where(
                UserMemoryEntry.entry_id == row.entry_id,
                disclosure_condition(run_id, recipient_entry),
            )
        )
    )

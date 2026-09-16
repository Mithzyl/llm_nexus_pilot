"""Retry only recorded private-upload cleanup keys; never enumerate or sweep a bucket.

Run from apps/api with ``PYTHONPATH=src .venv/bin/python -m
nexuspilot_api.services.user_file_cleanup``. Schedule externally if automatic cleanup
is required. Active upload leases and ready files are excluded; limit defaults to 100.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import or_, select

from nexuspilot_api.infrastructure.database import database_session_factory
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.models import UserFile
from nexuspilot_api.services.user_file_service import delete_file


async def cleanup_files(db, storage, limit=100):
    """Retry failed deletions and expired reservations; return explicit cleaned/failed counts."""

    ids = list(
        (
            await db.scalars(
                select(UserFile.file_id)
                .where(
                    or_(
                        UserFile.status.in_(["failed", "deleting"]),
                        (UserFile.status.in_(["reserved", "uploading"]))
                        & (UserFile.expires_at <= datetime.now(UTC)),
                    )
                )
                .order_by(UserFile.created_at)
                .limit(limit)
            )
        ).all()
    )
    cleaned, failed = 0, 0
    for file_id in ids:
        file = await db.get(UserFile, file_id, populate_existing=True)
        try:
            if await delete_file(db, storage, file, cleanup_only=True):
                cleaned += 1
        except HTTPException:
            await db.rollback()
            failed += 1
    return {"cleaned": cleaned, "failed": failed}


async def main(limit):
    """Use configured database and storage only after an operator explicitly runs this command."""

    async with database_session_factory() as db:
        result = await cleanup_files(db, get_object_storage(), limit)
    print(result)
    return int(result["failed"] > 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, choices=range(1, 501), default=100)
    raise SystemExit(asyncio.run(main(parser.parse_args().limit)))

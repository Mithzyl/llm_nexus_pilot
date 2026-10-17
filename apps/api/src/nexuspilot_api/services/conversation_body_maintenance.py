"""Bounded backfill/restore and exact-key body-receipt reconciliation.

Run with the configured API environment after Alembic upgrade:
  python -m nexuspilot_api.services.conversation_body_maintenance backfill --table llm_messages
Dry-run is default. --apply writes only the selected bounded batch; --after resumes
lexicographically by immutable primary key, not chronological insertion order.
Restore additionally requires --writers-stopped and is for rollback maintenance.
Reconcile requires stopped writers and receipts at least 24 hours old before deletion;
it never scans unrelated bucket prefixes or deletes an object still referenced in SQL.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from anyio import to_thread
from sqlalchemy import inspect, select, update
from sqlalchemy.orm.attributes import flag_dirty

from nexuspilot_api.infrastructure.conversation_body_fields import BODY_FIELDS
from nexuspilot_api.models import Base


def body_model(table):
    """Resolve only the fixed payload registry, never a caller-provided SQL identifier."""
    if table not in BODY_FIELDS:
        raise ValueError("Unknown body table")
    return next(
        mapper.class_ for mapper in Base.registry.mappers if mapper.local_table.name == table
    )


async def migrate_batch(db, table, *, limit=100, after=None, apply=False, restore=False):
    """Lock and convert one batch; old bodies are cleared only after verified object writes."""
    if not 1 <= limit <= 500:
        raise ValueError("Batch limit must be 1..500")
    model = body_model(table)
    key = inspect(model).primary_key[0]
    statement = select(model).where(
        model.body_ref_json.is_not(None) if restore else model.body_ref_json.is_(None)
    )
    if after is not None:
        statement = statement.where(key > after)
    with db.body_metadata_only():
        records = list(await db.scalars(statement.order_by(key).limit(limit).with_for_update()))
    identifiers = [str(getattr(record, key.key)) for record in records]
    if apply:
        if restore:
            for record in records:
                await db._read_body(record)
                # Deliberately bypass automatic externalization only in this offline
                # restore operation. Every value was read with record/hash verification.
                values = {name: getattr(record, name) for name in BODY_FIELDS[table]}
                values["body_ref_json"] = None
                connection = await db.connection()
                await connection.execute(
                    update(model.__table__).where(key == getattr(record, key.key)).values(**values)
                )
            await db.commit()
            db.expire_all()
        else:
            for record in records:
                flag_dirty(record)
            await db.commit()
    else:
        await db.rollback()
    return {
        "table": table,
        "operation": "restore" if restore else "backfill",
        "applied": apply,
        "count": len(records),
        "last_id": identifiers[-1] if identifiers else after,
    }


async def reconcile_receipt(db, storage, receipt, *, apply=False, now=None):
    """Check one immutable write receipt; delete only old unreferenced exact object keys offline."""
    now = now or datetime.now(UTC)
    table, identifier, reference = receipt["table"], receipt["record_id"], receipt["reference"]
    model = body_model(table)
    digest = reference["sha256"]
    if (
        len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
        or "/" in identifier
        or reference["uri"]
        != storage.uri_for_object(f"conversation-bodies/{table}/{identifier}/{digest}.json")
    ):
        raise ValueError("Invalid receipt object identity")
    created = datetime.fromisoformat(receipt["created_at"])
    if now - created < timedelta(hours=24):
        return "too_recent"
    with db.body_metadata_only():
        row = await db.get(model, identifier, populate_existing=True)
    if row is not None and row.body_ref_json and row.body_ref_json.get("uri") == reference["uri"]:
        return "referenced"
    if apply:
        await storage.delete_object(reference["uri"])
    return "deleted" if apply else "unreferenced"


async def run(args):
    """Use the explicitly configured database/storage, printing counts and keys, never bodies."""
    from nexuspilot_api.infrastructure.database import database_session_factory, engine

    try:
        async with database_session_factory() as db:
            if args.operation in {"backfill", "restore"}:
                return await migrate_batch(
                    db,
                    args.table,
                    limit=args.limit,
                    after=args.after,
                    apply=args.apply,
                    restore=args.operation == "restore",
                )
            storage = db.body_storage
            names = await storage.list_object_page(
                "conversation-body-writes/", after=args.after, limit=args.limit
            )
            results = []
            for name in names:
                uri = storage.uri_for_object(name)
                opened = await storage.open_object(uri)

                def read_receipt(opened=opened):
                    """Bound receipt bytes and release its network stream even on invalid size."""
                    chunks, raw = iter(opened.chunks), bytearray()
                    try:
                        if opened.size_bytes > 8192:
                            raise ValueError("Receipt exceeds bounded metadata size")
                        for chunk in chunks:
                            raw.extend(chunk)
                            if len(raw) > 8192:
                                raise ValueError("Receipt exceeds bounded metadata size")
                        return bytes(raw)
                    finally:
                        close = getattr(chunks, "close", None)
                        if close:
                            close()

                raw = await to_thread.run_sync(read_receipt)
                receipt = json.loads(raw)
                result = await reconcile_receipt(db, storage, receipt, apply=args.apply)
                # Keep live receipts: a later update can supersede this object,
                # and reconciliation still needs its exact key then.
                if args.apply and result == "deleted":
                    await storage.delete_object(uri)
                results.append({"receipt": name, "status": result})
            return {"results": results, "last_id": names[-1] if names else args.after}
    finally:
        await engine.dispose()


def main():
    """Require explicit write and maintenance-mode acknowledgements for destructive operations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backfill", "restore", "reconcile"))
    parser.add_argument("--table", choices=tuple(BODY_FIELDS))
    parser.add_argument("--after")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error("--limit must be 1..500")
    if args.operation != "reconcile" and not args.table:
        parser.error("--table is required")
    if args.apply and args.operation != "backfill" and not args.writers_stopped:
        parser.error("Stop API/background writers, then acknowledge --writers-stopped")
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))


if __name__ == "__main__":
    main()

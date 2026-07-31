"""Opt-in integration tests for real MySQL transaction and MinIO storage behavior."""

import asyncio
import os
from uuid import uuid4

import pytest
from anyio import to_thread
from sqlalchemy import delete, func, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nexuspilot_api.core.config import Settings
from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import (
    LlmOutboxEvent,
    LlmRun,
    LlmTask,
    RunStatus,
    TaskStatus,
    User,
    new_id,
)
from nexuspilot_api.services.task_service import retry_task

pytestmark = pytest.mark.infrastructure


@pytest.mark.skipif(
    not os.getenv("NEXUSPILOT_TEST_MYSQL_URL"),
    reason="NEXUSPILOT_TEST_MYSQL_URL is not configured",
)
async def test_real_mysql_schema_and_concurrent_retry() -> None:
    """Verify migrated MySQL tables and serialize two competing Task retry requests."""

    database_url = os.environ["NEXUSPILOT_TEST_MYSQL_URL"]
    mysql_engine = create_async_engine(database_url, pool_pre_ping=True)
    mysql_session_factory = async_sessionmaker(mysql_engine, expire_on_commit=False)
    unique_suffix = uuid4().hex
    user_id = f"infra-{unique_suffix}"
    run_id = new_id()
    task_id = new_id()
    try:
        async with mysql_engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            tool_call_indexes = await connection.run_sync(
                lambda sync_connection: {
                    index["name"]
                    for index in inspect(sync_connection).get_indexes("llm_tool_calls")
                }
            )
            evaluation_indexes = await connection.run_sync(
                lambda sync_connection: {
                    index["name"]
                    for index in inspect(sync_connection).get_indexes("llm_evaluations")
                }
            )
            outbox_indexes = await connection.run_sync(
                lambda sync_connection: {
                    index["name"]
                    for index in inspect(sync_connection).get_indexes("llm_outbox_events")
                }
            )
            server_version = connection.dialect.server_version_info
        assert {"users", "llm_runs", "llm_tasks", "llm_outbox_events"} <= table_names
        assert "ix_tool_attempt_status_started_id" in tool_call_indexes
        assert "ix_evaluation_run_type_created_id" in evaluation_indexes
        assert "ix_outbox_status_created_id" in outbox_indexes
        assert server_version and server_version[0] >= 8

        async with mysql_session_factory() as db_session:
            db_session.add(User(user_id=user_id, display_name="Infrastructure Test"))
            await db_session.flush()
            db_session.add(
                LlmRun(
                    run_id=run_id,
                    user_id=user_id,
                    user_request="Verify concurrent retry locking",
                    status=RunStatus.PENDING,
                )
            )
            await db_session.flush()
            db_session.add(
                LlmTask(
                    task_id=task_id,
                    run_id=run_id,
                    task_type="infrastructure_test",
                    title="Concurrent retry",
                    objective="Prove row-lock serialization",
                    status=TaskStatus.FAILED,
                    current_attempt=1,
                    max_attempts=3,
                )
            )
            await db_session.commit()

        async def request_retry() -> str:
            """Attempt one retry in an isolated real-MySQL database session."""

            async with mysql_session_factory() as db_session:
                try:
                    await retry_task(db_session, task_id)
                    return "scheduled"
                except ResourceConflictError:
                    await db_session.rollback()
                    return "conflict"

        outcomes = await asyncio.gather(request_retry(), request_retry())
        assert sorted(outcomes) == ["conflict", "scheduled"]

        async with mysql_session_factory() as db_session:
            event_count = await db_session.scalar(
                select(func.count())
                .select_from(LlmOutboxEvent)
                .where(
                    LlmOutboxEvent.aggregate_id == task_id,
                    LlmOutboxEvent.event_type == "task.retry_requested",
                )
            )
            task = await db_session.get(LlmTask, task_id)
            assert event_count == 1
            assert task is not None
            assert task.status == TaskStatus.RETRY_SCHEDULED
            assert task.current_attempt == 2
    finally:
        async with mysql_session_factory() as db_session:
            await db_session.execute(
                delete(LlmOutboxEvent).where(LlmOutboxEvent.aggregate_id == task_id)
            )
            await db_session.execute(delete(LlmTask).where(LlmTask.task_id == task_id))
            await db_session.execute(delete(LlmRun).where(LlmRun.run_id == run_id))
            await db_session.execute(delete(User).where(User.user_id == user_id))
            await db_session.commit()
        await mysql_engine.dispose()


@pytest.mark.skipif(
    os.getenv("NEXUSPILOT_RUN_MINIO_TEST") != "1",
    reason="NEXUSPILOT_RUN_MINIO_TEST is not enabled",
)
async def test_real_minio_bucket_and_object_round_trip() -> None:
    """Initialize the configured bucket and verify real object metadata round-trip."""

    settings = Settings(
        minio_endpoint=os.getenv("NEXUSPILOT_TEST_MINIO_ENDPOINT", "localhost:9000"),
        minio_access_key=os.getenv("NEXUSPILOT_TEST_MINIO_ACCESS_KEY", "nexuspilot"),
        minio_secret_key=os.getenv(
            "NEXUSPILOT_TEST_MINIO_SECRET_KEY",
            "local-development-secret-change-me",
        ),
        minio_bucket=os.getenv("NEXUSPILOT_TEST_MINIO_BUCKET", "nexuspilot-artifacts"),
        minio_secure=False,
    )
    storage = ObjectStorage(settings)
    object_name = f"integration/{uuid4().hex}.txt"
    content = b"nexuspilot real minio verification"
    try:
        stored = await storage.put_bytes(object_name, content, "text/plain")
        metadata = await to_thread.run_sync(
            lambda: storage.client.stat_object(settings.minio_bucket, object_name)
        )
        streamed = await storage.open_object(
            stored.uri,
            expected_size_bytes=len(content),
        )
        assert stored.size_bytes == len(content)
        assert metadata.size == len(content)
        assert stored.uri == f"minio://{settings.minio_bucket}/{object_name}"
        assert streamed.size_bytes == len(content)
        assert b"".join(streamed.chunks) == content
    finally:
        await to_thread.run_sync(
            lambda: storage.client.remove_object(settings.minio_bucket, object_name)
        )

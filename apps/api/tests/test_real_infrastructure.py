"""Opt-in integration tests for real MySQL transaction and MinIO storage behavior."""

import asyncio
import os
from uuid import uuid4

import pytest
from anyio import to_thread
from sqlalchemy import delete, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nexuspilot_api.core.config import Settings
from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.features.memory.schemas.memories import (
    MemoryCreate,
    MemorySourceCreate,
    MemoryUpdate,
)
from nexuspilot_api.features.memory.services.memory_service import (
    create_memory,
    update_memory,
)
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import (
    LlmContextBuild,
    LlmContextSource,
    LlmEvaluationRule,
    LlmEvaluationRuleSet,
    LlmMemory,
    LlmMemoryMutation,
    LlmMemoryVersion,
    LlmMessage,
    LlmModelCatalogVersion,
    LlmOutboxEvent,
    LlmPromptTemplate,
    LlmPromptTemplateChange,
    LlmPromptTemplateVersion,
    LlmRun,
    LlmSession,
    LlmTask,
    LlmTaskEvaluation,
    MemoryMutationOperation,
    MemoryStatus,
    MemoryType,
    MessageRole,
    RunStatus,
    TaskStatus,
    User,
    new_id,
)
from nexuspilot_api.schemas.context_builds import ContextBuildCreate
from nexuspilot_api.schemas.evaluation import EvaluationCreate, EvaluationRuleSetCreate
from nexuspilot_api.schemas.prompt_catalog import (
    ModelCatalogVersionCreate,
    PromptTemplateCreate,
    PromptTemplateVersionCreate,
)
from nexuspilot_api.services.context_builder_service import build_context, get_context_build
from nexuspilot_api.services.evaluation_service import create_evaluation, create_rule_set
from nexuspilot_api.services.prompt_catalog_service import (
    create_model_catalog_version,
    create_prompt_template,
    create_prompt_template_version,
)
from nexuspilot_api.services.task_service import retry_task

pytestmark = pytest.mark.infrastructure


@pytest.mark.skipif(
    not os.getenv("NEXUSPILOT_TEST_MYSQL_URL"),
    reason="NEXUSPILOT_TEST_MYSQL_URL is not configured",
)
async def test_real_mysql_phase2_context_prompt_and_evaluation() -> None:
    """Verify stable phase 2 persistence, Prompt locking, and Evaluation idempotency."""

    database_url = os.environ["NEXUSPILOT_TEST_MYSQL_URL"]
    mysql_engine = create_async_engine(database_url, pool_pre_ping=True)
    mysql_session_factory = async_sessionmaker(mysql_engine, expire_on_commit=False)
    unique_suffix = uuid4().hex
    user_id = f"phase2-infra-{unique_suffix}"
    session_id = new_id()
    run_id = new_id()
    task_id = new_id()
    template_name = f"phase2-prompt-{unique_suffix}"
    rule_set_name = f"phase2-rules-{unique_suffix}"
    context_build_id: str | None = None
    catalog_version_id: str | None = None
    rule_set_id: str | None = None
    try:
        async with mysql_session_factory() as db_session:
            db_session.add(User(user_id=user_id, display_name="Phase 2 Infrastructure"))
            await db_session.flush()
            db_session.add(LlmSession(session_id=session_id, user_id=user_id))
            await db_session.flush()
            db_session.add_all(
                [
                    LlmMessage(
                        session_id=session_id,
                        role=MessageRole.USER,
                        content_text=f"message-{sequence}",
                        sequence=sequence,
                    )
                    for sequence in range(1, 4)
                ]
            )
            db_session.add(
                LlmRun(
                    run_id=run_id,
                    user_id=user_id,
                    session_id=session_id,
                    user_request="Verify phase 2 on MySQL",
                )
            )
            await db_session.flush()
            db_session.add(
                LlmTask(
                    task_id=task_id,
                    run_id=run_id,
                    task_type="phase2_infrastructure",
                    title="Verify stable phase 2 units",
                    objective="Exercise MySQL constraints and row locks",
                )
            )
            await db_session.commit()

        async with mysql_session_factory() as db_session:
            capability = await create_model_catalog_version(
                db_session,
                ModelCatalogVersionCreate(
                    provider="openai",
                    model=f"phase2-model-{unique_suffix}",
                    catalog_version="v1",
                    source="real-mysql-test",
                    context_window=8_192,
                    tokenizer_name="utf8_bytes_upper_bound",
                    tokenizer_version="v1",
                ),
            )
            catalog_version_id = capability.catalog_version_id
            context = await build_context(
                db_session,
                ContextBuildCreate(
                    user_id=user_id,
                    session_id=session_id,
                    provider="openai",
                    model=f"phase2-model-{unique_suffix}",
                    catalog_version="v1",
                    token_budget=2_048,
                    reserved_output_tokens=256,
                    recent_message_count=2,
                ),
            )
            context_build_id = context.context_build_id
            reloaded_context = await get_context_build(db_session, context_build_id)
            assert reloaded_context.messages == context.messages
            assert reloaded_context.catalog_version_id == catalog_version_id

        async with mysql_session_factory() as db_session:
            await create_prompt_template(
                db_session,
                PromptTemplateCreate(
                    template_name=template_name,
                    content_text="Hello {{ name }}",
                    variable_schema={"name": "string"},
                    created_by_actor_id="real-mysql-test",
                ),
            )

        async def add_prompt_version(version_label: str) -> int:
            """Append one Prompt version in an isolated row-locking transaction."""

            async with mysql_session_factory() as db_session:
                prompt = await create_prompt_template_version(
                    db_session,
                    template_name,
                    PromptTemplateVersionCreate(
                        content_text=f"{version_label} {{{{ name }}}}",
                        variable_schema={"name": "string"},
                        created_by_actor_id="real-mysql-test",
                    ),
                )
                return prompt.current_version_number

        prompt_versions = await asyncio.gather(
            add_prompt_version("Second"),
            add_prompt_version("Third"),
        )
        assert sorted(prompt_versions) == [2, 3]

        async with mysql_session_factory() as db_session:
            rule_set = await create_rule_set(
                db_session,
                EvaluationRuleSetCreate(
                    name=rule_set_name,
                    schema_version="v1",
                    rules=[
                        {
                            "rule_key": "length",
                            "rule_type": "input_length",
                            "config_json": {"max_characters": 100},
                        }
                    ],
                ),
            )
            rule_set_id = rule_set["rule_set_id"]

        evaluation_payload = EvaluationCreate(
            run_id=run_id,
            task_id=task_id,
            evaluation_type="deterministic",
            rule_set_id=rule_set_id,
            idempotency_key=f"phase2-evaluation-{unique_suffix}",
            input_text="bounded input",
        )

        async def create_same_evaluation() -> tuple[str, bool]:
            """Create or replay one Evaluation in an isolated concurrent transaction."""

            async with mysql_session_factory() as db_session:
                result = await create_evaluation(db_session, evaluation_payload)
                return result.record.evaluation_id, result.was_replayed

        evaluation_results = await asyncio.gather(
            create_same_evaluation(),
            create_same_evaluation(),
        )
        assert len({evaluation_id for evaluation_id, _ in evaluation_results}) == 1
        assert sorted(was_replayed for _, was_replayed in evaluation_results) == [False, True]
    finally:
        async with mysql_session_factory() as db_session:
            await db_session.execute(
                delete(LlmTaskEvaluation).where(LlmTaskEvaluation.task_id == task_id)
            )
            if rule_set_id is not None:
                await db_session.execute(
                    delete(LlmEvaluationRule).where(
                        LlmEvaluationRule.rule_set_id == rule_set_id
                    )
                )
                await db_session.execute(
                    delete(LlmEvaluationRuleSet).where(
                        LlmEvaluationRuleSet.rule_set_id == rule_set_id
                    )
                )
            await db_session.execute(
                delete(LlmContextSource).where(
                    LlmContextSource.context_build_id == context_build_id
                )
            )
            await db_session.execute(
                delete(LlmContextBuild).where(
                    LlmContextBuild.context_build_id == context_build_id
                )
            )
            await db_session.execute(
                delete(LlmPromptTemplateChange).where(
                    LlmPromptTemplateChange.template_name == template_name
                )
            )
            await db_session.execute(
                delete(LlmPromptTemplateVersion).where(
                    LlmPromptTemplateVersion.template_name == template_name
                )
            )
            await db_session.execute(
                delete(LlmPromptTemplate).where(
                    LlmPromptTemplate.template_name == template_name
                )
            )
            if catalog_version_id is not None:
                await db_session.execute(
                    delete(LlmModelCatalogVersion).where(
                        LlmModelCatalogVersion.catalog_version_id == catalog_version_id
                    )
                )
            await db_session.execute(delete(LlmTask).where(LlmTask.task_id == task_id))
            await db_session.execute(delete(LlmRun).where(LlmRun.run_id == run_id))
            await db_session.execute(
                delete(LlmMessage).where(LlmMessage.session_id == session_id)
            )
            await db_session.execute(
                delete(LlmSession).where(LlmSession.session_id == session_id)
            )
            await db_session.execute(delete(User).where(User.user_id == user_id))
            await db_session.commit()
        await mysql_engine.dispose()


@pytest.mark.skipif(
    not os.getenv("NEXUSPILOT_TEST_MYSQL_URL"),
    reason="NEXUSPILOT_TEST_MYSQL_URL is not configured",
)
async def test_real_mysql_schema_and_concurrent_retry() -> None:
    """Verify migrated MySQL schema, scope restrictions, and concurrent Task retries."""

    database_url = os.environ["NEXUSPILOT_TEST_MYSQL_URL"]
    mysql_engine = create_async_engine(database_url, pool_pre_ping=True)
    mysql_session_factory = async_sessionmaker(mysql_engine, expire_on_commit=False)
    unique_suffix = uuid4().hex
    user_id = f"infra-{unique_suffix}"
    run_id = new_id()
    task_id = new_id()
    scoped_memory_id = new_id()
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
        assert {
            "llm_memories",
            "llm_memory_versions",
            "llm_memory_mutations",
            "llm_memory_sources",
            "llm_memory_search_terms",
            "llm_memory_retrievals",
            "llm_memory_retrieval_results",
        } <= table_names
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
            await db_session.flush()
            db_session.add(
                LlmMemory(
                    memory_id=scoped_memory_id,
                    user_id=user_id,
                    run_id=run_id,
                    task_id=task_id,
                    memory_type=MemoryType.USER_FACT,
                    status=MemoryStatus.CANDIDATE,
                    current_version_number=1,
                    creation_idempotency_key="infra-scope-delete",
                    creation_request_hash="0" * 64,
                )
            )
            await db_session.commit()

        async with mysql_session_factory() as db_session:
            with pytest.raises(IntegrityError):
                await db_session.execute(delete(LlmTask).where(LlmTask.task_id == task_id))
                await db_session.commit()
            await db_session.rollback()

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
                delete(LlmMemory).where(LlmMemory.memory_id == scoped_memory_id)
            )
            await db_session.execute(
                delete(LlmOutboxEvent).where(LlmOutboxEvent.aggregate_id == task_id)
            )
            await db_session.execute(delete(LlmTask).where(LlmTask.task_id == task_id))
            await db_session.execute(delete(LlmRun).where(LlmRun.run_id == run_id))
            await db_session.execute(delete(User).where(User.user_id == user_id))
            await db_session.commit()
        await mysql_engine.dispose()


@pytest.mark.skipif(
    not os.getenv("NEXUSPILOT_TEST_MYSQL_URL"),
    reason="NEXUSPILOT_TEST_MYSQL_URL is not configured",
)
async def test_real_mysql_serializes_memory_semantic_slot_and_corrections() -> None:
    """Verify MySQL admits one active semantic fact and one stale-version correction."""

    database_url = os.environ["NEXUSPILOT_TEST_MYSQL_URL"]
    mysql_engine = create_async_engine(database_url, pool_pre_ping=True)
    mysql_session_factory = async_sessionmaker(mysql_engine, expire_on_commit=False)
    unique_suffix = uuid4().hex
    user_id = f"memory-infra-{unique_suffix}"
    try:
        async with mysql_session_factory() as db_session:
            db_session.add(User(user_id=user_id, display_name="Memory Infrastructure Test"))
            await db_session.commit()

        async def create_competing_memory(index: int) -> tuple[str, str | None]:
            """Try to claim one semantic slot from an isolated database transaction."""

            async with mysql_session_factory() as db_session:
                try:
                    result = await create_memory(
                        db_session,
                        MemoryCreate(
                            user_id=user_id,
                            memory_type="user_preference",
                            content_text=f"Preferred response style {index}",
                            status=MemoryStatus.ACTIVE,
                            semantic_key="response.style",
                            idempotency_key=f"memory-create-{index}",
                            sources=[
                                MemorySourceCreate(
                                    source_type="trusted_request",
                                    source_resource_id=f"infra-request-{index}",
                                )
                            ],
                        ),
                    )
                    return "created", result.memory.memory_id
                except ResourceConflictError as error:
                    await db_session.rollback()
                    return "conflict", str(error)

        creation_outcomes = await asyncio.gather(
            create_competing_memory(1),
            create_competing_memory(2),
        )
        assert sorted(outcome for outcome, _memory_id in creation_outcomes) == [
            "conflict",
            "created",
        ], creation_outcomes
        memory_id = next(
            memory_id
            for outcome, memory_id in creation_outcomes
            if outcome == "created" and memory_id is not None
        )

        async def correct_competing_memory(index: int) -> str:
            """Try one correction using the same expected version from another transaction."""

            async with mysql_session_factory() as db_session:
                try:
                    await update_memory(
                        db_session,
                        memory_id,
                        MemoryUpdate(
                            expected_version_number=1,
                            idempotency_key=f"memory-correction-{index}",
                            content_text=f"Corrected response style {index}",
                            sources=[
                                MemorySourceCreate(
                                    source_type="trusted_request",
                                    source_resource_id=f"correction-request-{index}",
                                )
                            ],
                        ),
                    )
                    return "corrected"
                except ResourceConflictError:
                    await db_session.rollback()
                    return "conflict"

        correction_outcomes = await asyncio.gather(
            correct_competing_memory(1),
            correct_competing_memory(2),
        )
        assert sorted(correction_outcomes) == ["conflict", "corrected"]

        async with mysql_session_factory() as db_session:
            memory = await db_session.get(LlmMemory, memory_id)
            version_count = await db_session.scalar(
                select(func.count())
                .select_from(LlmMemoryVersion)
                .where(LlmMemoryVersion.memory_id == memory_id)
            )
            mutation_operations = set(
                (
                    await db_session.scalars(
                        select(LlmMemoryMutation.operation)
                        .where(LlmMemoryMutation.memory_id == memory_id)
                    )
                ).all()
            )
            assert memory is not None
            assert memory.current_version_number == 2
            assert version_count == 2
            assert mutation_operations == {
                MemoryMutationOperation.CREATE,
                MemoryMutationOperation.CORRECT,
            }
    finally:
        async with mysql_session_factory() as db_session:
            await db_session.execute(delete(LlmMemory).where(LlmMemory.user_id == user_id))
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

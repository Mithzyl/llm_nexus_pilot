"""Spawn-only caller Worker fixture; fixed model, real SQL/MinIO and local HTTP only.

Invoked by test_application_worker_faults.py with isolated configuration in memory.
Fault wrappers exist only in the child test process; production has no crash switches.
"""

import asyncio
import os


def run_caller_worker(
    database_url,
    storage_config,
    app_config,
    workflow_id,
    crash_phase=None,
    ready=None,
    start=None,
    claimed=None,
):
    """Claim a saved workflow in a real process and optionally crash at a chosen boundary."""
    from conftest import fake_price_catalog
    from nexuspilot_models.contracts import ModelResponse
    from pydantic import SecretStr
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from test_agent_workflows import ScriptedAgentProvider, scripted_registry
    from test_application_body_processing import PROPOSAL

    from nexuspilot_api.core.config import Settings, get_settings
    from nexuspilot_api.features.agent_runtime.services import tool_loop_checkpoint, workflow_worker
    from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
    from nexuspilot_api.infrastructure.conversation_body_session import ConversationBodySession
    from nexuspilot_api.infrastructure.object_storage import ObjectStorage
    from nexuspilot_api.models import ApplicationResponseExecution, LlmAgentWorkflowExecution
    from nexuspilot_api.services.remote_tool_adapter import RemoteToolAdapter

    get_settings().app_tools_json = SecretStr(app_config)
    get_settings().workflow_execution_backend = "worker"
    get_settings().environment = "development"

    class Provider(ScriptedAgentProvider):
        """Decide only from saved dialogue, so a restarted process cannot reset turn numbering."""

        async def generate(self, request):
            """Return one proposal then one answer from actual saved platform evidence."""
            results = [
                m.content or ""
                for m in request.messages
                if "backend_execution_result" in (m.content or "")
            ]
            if results:
                assert len(results) == 1 and "todo-C" in results[0]
            return ModelResponse(
                text="Saved todo-C; notifications unavailable." if results else PROPOSAL,
                finish_reason="stop",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                raw_response={},
            )

    original_commit = ConversationBodySession.commit
    original_checkpoint = tool_loop_checkpoint.save_loop_checkpoint
    original_http = RemoteToolAdapter.execute_body
    original_renew = workflow_worker.renew_claim

    async def fault_commit(session):
        """Exit after the requested SQL commit, not after a merely in-memory state change."""
        await original_commit(session)
        target = {
            "prepared": "prepared",
            "dispatching": "dispatching",
            "result_saved": "completed",
        }.get(crash_phase)
        if target:
            match = await AsyncSession.scalar(
                session,
                select(ApplicationResponseExecution.execution_id)
                .join(
                    LlmAgentWorkflowExecution,
                    LlmAgentWorkflowExecution.run_id == ApplicationResponseExecution.run_id,
                )
                .where(
                    LlmAgentWorkflowExecution.workflow_execution_id == workflow_id,
                    ApplicationResponseExecution.status == target,
                )
                .limit(1),
            )
            if match:
                os._exit(86)

    async def fault_checkpoint(*args, **kwargs):
        """Crash around message checkpoints while retaining the persisted platform result."""
        if kwargs["phase"] == "application_result" and crash_phase == "messages_before_checkpoint":
            os._exit(86)
        result = await original_checkpoint(*args, **kwargs)
        if kwargs["phase"] == "application_result" and crash_phase == "messages_checkpoint":
            os._exit(86)
        return result

    async def fault_http(adapter, **kwargs):
        """Lose the real local HTTP result after execution but before SQL persistence."""
        result = await original_http(adapter, **kwargs)
        if crash_phase == "platform_returned":
            os._exit(86)
        return result

    async def short_test_lease(factory, claim):
        """Use five-second lease renewal and natural expiry, without editing lease timestamps."""
        return await original_renew(factory, claim, lease_seconds=5)

    async def execute():
        """Open child-owned connections and use the actual claim and execution implementation."""
        engine = create_async_engine(database_url)
        storage = ObjectStorage(Settings(_env_file=None, **storage_config))
        factory = async_sessionmaker(
            engine,
            class_=ConversationBodySession,
            expire_on_commit=False,
            info={"body_storage": storage},
        )
        try:
            if ready is not None:
                ready.put(os.getpid())
                if not await asyncio.to_thread(start.wait, 30):
                    raise RuntimeError("Competition start was not released")
            claim = await claim_workflow(
                factory, workflow_id, f"caller-test-{os.getpid()}", lease_seconds=5
            )
            if claimed is not None:
                claimed.put({"pid": os.getpid(), "claimed": claim is not None})
            if claim is None:
                return
            if crash_phase:
                ConversationBodySession.commit = fault_commit
                tool_loop_checkpoint.save_loop_checkpoint = fault_checkpoint
                RemoteToolAdapter.execute_body = fault_http
            workflow_worker.renew_claim = short_test_lease
            await workflow_worker.execute_claim(
                factory, storage, scripted_registry(Provider()), fake_price_catalog(), claim
            )
        finally:
            await engine.dispose()

    asyncio.run(execute())

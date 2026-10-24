"""Spawn-only fixed-provider Worker for opt-in local MySQL/MinIO fault tests.

Receives an isolated database URL and MinIO configuration over process arguments;
never reads a real provider registry or sends a paid model request.
"""

import asyncio
import os

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def run_test_worker(database_url, storage_config, workflow_id, crash_phase=None, wait=False):
    """Run one job in another OS process, optionally exiting after a durable checkpoint."""
    from conftest import fake_price_catalog
    from test_agent_workflows import scripted_registry
    from test_general_agent import GeneralProvider

    from nexuspilot_api.core.config import Settings
    from nexuspilot_api.features.agent_runtime.services import tool_loop_checkpoint
    from nexuspilot_api.features.agent_runtime.services.workflow_ownership import claim_workflow
    from nexuspilot_api.features.agent_runtime.services.workflow_worker import execute_claim
    from nexuspilot_api.infrastructure.conversation_body_session import ConversationBodySession
    from nexuspilot_api.infrastructure.object_storage import ObjectStorage

    class FixedProvider(GeneralProvider):
        """Return fixed tool/answer output or await cancellation; no HTTP transport exists."""

        async def generate(self, request):
            """Optionally keep the persisted invocation in flight for explicit stop testing."""
            if wait:
                await asyncio.Event().wait()
            return await super().generate(request)

    original = tool_loop_checkpoint.save_loop_checkpoint

    async def crash_after_checkpoint(*args, **kwargs):
        """Exit this test process after SQL and MinIO commits, without application cleanup."""
        result = await original(*args, **kwargs)
        if kwargs["phase"] == crash_phase:
            os._exit(86)
        return result

    async def execute():
        """Create fresh connections and claim ownership; never share inherited pool handles."""
        engine = create_async_engine(database_url)
        storage = ObjectStorage(Settings(_env_file=None, **storage_config))
        factory = async_sessionmaker(
            engine,
            class_=ConversationBodySession,
            expire_on_commit=False,
            info={"body_storage": storage},
        )
        try:
            claim = await claim_workflow(factory, workflow_id, f"test-process-{os.getpid()}")
            if claim is None:
                raise RuntimeError("Test workflow was not claimable")
            if crash_phase:
                tool_loop_checkpoint.save_loop_checkpoint = crash_after_checkpoint
            try:
                await execute_claim(
                    factory,
                    storage,
                    scripted_registry(FixedProvider(tool=True)),
                    fake_price_catalog(),
                    claim,
                )
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
        finally:
            await engine.dispose()

    asyncio.run(execute())

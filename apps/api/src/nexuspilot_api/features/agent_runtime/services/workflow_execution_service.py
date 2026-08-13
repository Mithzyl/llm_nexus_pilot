"""Create Agent workflows and manage the database session used for each execution."""

from nexuspilot_models.pricing import PriceCatalog
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    AgentWorkflowCreate,
    AgentWorkflowResultRead,
    AgentWorkflowSummaryRead,
    WorkflowErrorRead,
)
from nexuspilot_api.features.agent_runtime.services.model_only_workflow_orchestrator import (
    ModelOnlyWorkflowOrchestrator,
)
from nexuspilot_api.features.agent_runtime.services.workflow_definition_registry import (
    DEFAULT_WORKFLOW_DEFINITION_REGISTRY,
    WorkflowDefinitionRegistry,
)
from nexuspilot_api.features.agent_runtime.services.workflow_execution_state_service import (
    EventSink,
    WorkflowExecutionStateService,
)
from nexuspilot_api.features.agent_runtime.services.workflow_query_service import (
    get_workflow_result,
    get_workflow_summary,
)
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import AgentWorkflowStatus, LlmAgentWorkflowExecution


class AgentWorkflowExecutionService:
    """Handle workflow creation, result retrieval, and execution-scoped sessions."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        prices: PriceCatalog,
        db_session: AsyncSession,
        db_session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        workflow_definitions: WorkflowDefinitionRegistry | None = None,
    ) -> None:
        """Bind request dependencies and create the model-only workflow runner."""

        self.registry = registry
        self.prices = prices
        self.db_session = db_session
        self.db_session_factory = db_session_factory
        self.storage = storage
        self.workflow_definitions = workflow_definitions or DEFAULT_WORKFLOW_DEFINITION_REGISTRY
        self.workflow_orchestrator = self._create_model_only_workflow_orchestrator(db_session)
        self.workflow_state: WorkflowExecutionStateService = (
            self.workflow_orchestrator.workflow_state
        )

    async def create_and_execute(
        self,
        run_id: str,
        payload: AgentWorkflowCreate,
        *,
        event_sink: EventSink | None = None,
    ) -> tuple[AgentWorkflowResultRead, bool]:
        """Create one idempotent workflow, execute it once, and return its snapshot."""

        workflow, replayed = await self.prepare_workflow(
            run_id,
            payload,
            event_sink=event_sink,
        )
        if replayed:
            result = await get_workflow_result(
                self.db_session,
                workflow.workflow_execution_id,
            )
            return result, True
        result = await self.execute_prepared_workflow(workflow, payload)
        return result, False

    async def prepare_workflow(
        self,
        run_id: str,
        payload: AgentWorkflowCreate,
        *,
        event_sink: EventSink | None = None,
    ) -> tuple[LlmAgentWorkflowExecution, bool]:
        """Persist or replay a workflow before synchronous execution begins."""

        self.workflow_state.set_event_sink(event_sink)
        return await self.workflow_state.create_workflow(run_id, payload)

    async def execute_prepared_workflow(
        self,
        workflow: LlmAgentWorkflowExecution,
        payload: AgentWorkflowCreate,
    ) -> AgentWorkflowResultRead:
        """Run a prepared model-only workflow and return its durable result."""

        await self.workflow_orchestrator.execute(workflow, payload)
        return await get_workflow_result(
            self.db_session,
            workflow.workflow_execution_id,
        )

    async def execute_prepared_workflow_in_new_session(
        self,
        workflow_execution_id: str,
        payload: AgentWorkflowCreate,
        *,
        event_sink: EventSink,
    ) -> AgentWorkflowResultRead:
        """Execute streamed work in a session that lives as long as the response body."""

        async with self.db_session_factory() as db_session:
            workflow_orchestrator = self._create_model_only_workflow_orchestrator(db_session)
            workflow_orchestrator.workflow_state.set_event_sink(event_sink)
            workflow = await db_session.get(
                LlmAgentWorkflowExecution,
                workflow_execution_id,
            )
            if workflow is None:
                raise ResourceNotFoundError("Agent Workflow")
            await workflow_orchestrator.execute(workflow, payload)
            return await get_workflow_result(db_session, workflow_execution_id)

    async def cancel_workflow_execution(
        self,
        workflow_execution_id: str,
    ) -> AgentWorkflowSummaryRead:
        """Cancel future workflow nodes and finalize currently active execution facts."""

        workflow = await self.db_session.scalar(
            select(LlmAgentWorkflowExecution)
            .where(LlmAgentWorkflowExecution.workflow_execution_id == workflow_execution_id)
            .with_for_update()
        )
        if workflow is None:
            raise ResourceNotFoundError("Agent Workflow")
        if workflow.status == AgentWorkflowStatus.CANCELLED:
            await self.db_session.commit()
            return await get_workflow_summary(self.db_session, workflow_execution_id)
        if workflow.status in {
            AgentWorkflowStatus.COMPLETED,
            AgentWorkflowStatus.FAILED,
            AgentWorkflowStatus.OUTCOME_UNKNOWN,
        }:
            raise ResourceConflictError(
                f"Cannot cancel Agent Workflow in {workflow.status.value} status"
            )
        await self.workflow_state.cancel_workflow(
            workflow,
            error=WorkflowErrorRead(
                error_code="agent_workflow_cancelled",
                error_type="workflow_error",
                public_message="Workflow was cancelled by an explicit client request.",
                is_retryable=False,
                outcome_is_known=True,
            ),
        )
        return await get_workflow_summary(self.db_session, workflow_execution_id)

    def _create_model_only_workflow_orchestrator(
        self,
        db_session: AsyncSession,
    ) -> ModelOnlyWorkflowOrchestrator:
        """Create a model-only workflow runner bound to the supplied database session."""

        return ModelOnlyWorkflowOrchestrator(
            registry=self.registry,
            prices=self.prices,
            db_session=db_session,
            db_session_factory=self.db_session_factory,
            storage=self.storage,
            workflow_definitions=self.workflow_definitions,
        )

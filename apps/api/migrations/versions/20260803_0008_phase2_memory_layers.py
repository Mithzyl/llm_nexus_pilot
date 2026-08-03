"""Add phase 2 Memory layers (L0-L4), Knowledge, Prompt/Catalog, Context, and Evaluation.

Revision ID: 20260803_0008
Revises: 20260801_0007
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0008"
down_revision: str | None = "20260801_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str, name: str) -> sa.Enum:
    """Build a native-enum-free VARCHAR enum matching the ORM mappings."""

    return sa.Enum(*values, name=name, native_enum=False, length=32)


memory_status = _enum(
    "CANDIDATE", "ACTIVE", "REJECTED", "SUPERSEDED", "DELETED", name="memorystatus"
)
session_memory_status = _enum(
    "GENERATING", "ACTIVE", "FAILED", "SUPERSEDED", name="sessionmemorystatus"
)
agent_run_status = _enum(
    "PENDING", "RUNNING", "WAITING", "COMPLETED", "FAILED", "CANCELLED", name="agentrunstatus"
)
agent_turn_type = _enum("MODEL", "TOOL", name="agentturntype")
agent_turn_status = _enum("STARTED", "COMPLETED", "FAILED", name="agentturnstatus")
agent_working_state_status = _enum(
    "ACTIVE", "SUPERSEDED", "FINALIZED", "ERASED", name="agentworkingstatestatus"
)
handoff_status = _enum("COMPLETED", "PARTIAL", "CORRECTED", "SUPERSEDED", name="handoffstatus")
memory_packet_item_type = _enum(
    "AGENT_WORKING_STATE",
    "SESSION_STATE",
    "SESSION_SUMMARY",
    "RUN_MEMORY_SNAPSHOT",
    "USER_MEMORY_PROFILE",
    "PROJECT_MEMORY_PROFILE",
    "HANDOFF",
    "MEMORY",
    "MESSAGE",
    "ARTIFACT",
    name="memorypacketitemtype",
)
project_status = _enum("ACTIVE", "ARCHIVED", name="projectstatus")
project_memory_status = _enum("DISABLED", "ENABLED", "SUSPENDED", name="projectmemorystatus")
knowledge_document_status = _enum("PENDING", "READY", "INACTIVE", name="knowledgedocumentstatus")
knowledge_version_status = _enum(
    "PENDING", "PROCESSING", "READY", "FAILED", "INACTIVE", name="knowledgeversionstatus"
)
prompt_template_status = _enum("ENABLED", "DISABLED", name="prompttemplatestatus")
model_catalog_status = _enum("ENABLED", "DISABLED", name="modelcatalogstatus")
context_build_status = _enum("COMPLETED", "FAILED", name="contextbuildstatus")
context_source_selection_status = _enum("SELECTED", "EXCLUDED", name="contextsourceselectionstatus")
evaluation_status = _enum(
    "PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", name="evaluationstatus"
)
evaluation_verdict = _enum("PASS", "FAIL", "UNKNOWN", name="evaluationverdict")
evaluation_rule_set_status = _enum("ENABLED", "DISABLED", name="evaluationrulesetstatus")
lesson_candidate_status = _enum(
    "CANDIDATE", "VERIFIED", "PROMOTED", "REJECTED", "SUPERSEDED", name="lessoncandidatestatus"
)
snapshot_object_status = _enum("ACTIVE", "DELETED", "ORPHAN", name="snapshotobjectstatus")
approval_method = _enum("NONE", "TRUSTED_CALLER", "POLICY", "REVIEWER", name="approvalmethod")
sensitivity_classification = _enum(
    "NONE", "LOW", "MEDIUM", "HIGH", name="sensitivityclassification"
)
memory_trust_level = _enum(
    "DIRECT_USER_STATEMENT",
    "USER_CONFIRMED",
    "MODEL_INFERENCE",
    "INTERNAL_SYSTEM_RESULT",
    "EXTERNAL_UNTRUSTED",
    name="memorytrustlevel",
)
memory_mutation_operation = _enum(
    "CREATE",
    "CORRECT",
    "ACTIVATE",
    "REJECT",
    "SUPERSEDE",
    "DELETE",
    "UPDATE_METADATA",
    name="memorymutationoperation",
)
memory_mutation_actor_type = _enum(
    "TRUSTED_CALLER", "MODEL_ATTEMPT", "SYSTEM", name="memorymutationactortype"
)


def upgrade() -> None:
    """Create all phase 2 tables, then extend phase 1 tables with scope and audit columns."""

    _create_projects()
    _create_agent_memory_tables()
    _create_session_memory_tables()
    _create_collaboration_tables()
    _create_snapshot_registry()
    _create_profile_tables()
    _create_lesson_and_knowledge_tables()
    _create_prompt_and_catalog_tables()
    _create_context_tables()
    _create_evaluation_tables()
    _extend_existing_tables()


def _create_projects() -> None:
    """Create the minimal Project scope and credential-free workspace tables."""

    op.create_table(
        "llm_projects",
        sa.Column("project_id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.String(128),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_name", sa.String(255), nullable=False),
        sa.Column("status", project_status, nullable=False, server_default="active"),
        sa.Column(
            "memory_status",
            project_memory_status,
            nullable=False,
            server_default="disabled",
        ),
        sa.Column("memory_enabled_at", sa.DateTime(timezone=True)),
        sa.Column("memory_enabled_by_actor_id", sa.String(128)),
        sa.Column("current_memory_profile_snapshot_id", sa.String(36)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_llm_projects_owner_user_id",
        "llm_projects",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_llm_projects_status",
        "llm_projects",
        ["status"],
    )
    op.create_index(
        "ix_llm_projects_memory_status",
        "llm_projects",
        ["memory_status"],
    )
    op.create_index(
        "ix_llm_projects_current_memory_profile_snapshot_id",
        "llm_projects",
        ["current_memory_profile_snapshot_id"],
    )
    op.create_index(
        "ix_project_owner_status_created",
        "llm_projects",
        ["owner_user_id", "status", "created_at", "project_id"],
    )

    op.create_table(
        "llm_project_workspaces",
        sa.Column("project_workspace_id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("llm_projects.project_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_type", sa.String(64), nullable=False),
        sa.Column("credential_free_locator", sa.String(1024), nullable=False),
        sa.Column("locator_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id",
            "workspace_type",
            "locator_hash",
            name="uq_project_workspace_locator",
        ),
    )
    op.create_index(
        "ix_llm_project_workspaces_project_id",
        "llm_project_workspaces",
        ["project_id"],
    )
    op.create_index(
        "ix_project_workspace_project_active",
        "llm_project_workspaces",
        ["project_id", "is_active"],
    )


def _create_agent_memory_tables() -> None:
    """Create L0 Agent Run, Turn, and Working State version tables."""

    op.create_table(
        "llm_agent_runs",
        sa.Column("agent_run_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("llm_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_role", sa.String(64), nullable=False),
        sa.Column("status", agent_run_status, nullable=False, server_default="pending"),
        sa.Column("provider", sa.String(64)),
        sa.Column("model", sa.String(128)),
        sa.Column("current_working_state_version_id", sa.String(36)),
        sa.Column("current_turn_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("task_id", "agent_role", name="uq_agent_run_task_role"),
    )
    op.create_index("ix_llm_agent_runs_run_id", "llm_agent_runs", ["run_id"])
    op.create_index("ix_llm_agent_runs_task_id", "llm_agent_runs", ["task_id"])
    op.create_index("ix_llm_agent_runs_status", "llm_agent_runs", ["status"])
    op.create_index(
        "ix_llm_agent_runs_current_working_state_version_id",
        "llm_agent_runs",
        ["current_working_state_version_id"],
    )
    op.create_index(
        "ix_agent_run_run_status_created_id",
        "llm_agent_runs",
        ["run_id", "status", "created_at", "agent_run_id"],
    )
    op.create_index(
        "ix_agent_run_task_status_created_id",
        "llm_agent_runs",
        ["task_id", "status", "created_at", "agent_run_id"],
    )

    op.create_table(
        "llm_agent_turns",
        sa.Column("agent_turn_id", sa.String(36), primary_key=True),
        sa.Column(
            "agent_run_id",
            sa.String(36),
            sa.ForeignKey("llm_agent_runs.agent_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_sequence", sa.Integer(), nullable=False),
        sa.Column("turn_type", agent_turn_type, nullable=False),
        sa.Column("status", agent_turn_status, nullable=False, server_default="started"),
        sa.Column(
            "model_attempt_id",
            sa.String(36),
            sa.ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("agent_run_id", "turn_sequence", name="uq_agent_turn_sequence"),
    )
    op.create_index("ix_llm_agent_turns_agent_run_id", "llm_agent_turns", ["agent_run_id"])
    op.create_index("ix_llm_agent_turns_model_attempt_id", "llm_agent_turns", ["model_attempt_id"])
    op.create_index(
        "ix_agent_turn_run_sequence", "llm_agent_turns", ["agent_run_id", "turn_sequence"]
    )
    op.create_index("ix_agent_turn_attempt", "llm_agent_turns", ["model_attempt_id"])

    op.create_table(
        "llm_agent_working_state_versions",
        sa.Column("agent_working_state_version_id", sa.String(36), primary_key=True),
        sa.Column(
            "agent_run_id",
            sa.String(36),
            sa.ForeignKey("llm_agent_runs.agent_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_turn_id",
            sa.String(36),
            sa.ForeignKey("llm_agent_turns.agent_turn_id", ondelete="SET NULL"),
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            agent_working_state_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("previous_version_id", sa.String(36)),
        sa.Column("estimated_token_count", sa.Integer(), nullable=False),
        sa.Column("tokenizer_name", sa.String(64), nullable=False),
        sa.Column("tokenizer_version", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("content_erased_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("agent_run_id", "version", name="uq_agent_working_state_version"),
        sa.UniqueConstraint("agent_run_id", "idempotency_key", name="uq_agent_working_state_key"),
    )
    op.create_index(
        "ix_llm_agent_working_state_versions_agent_run_id",
        "llm_agent_working_state_versions",
        ["agent_run_id"],
    )
    op.create_index(
        "ix_llm_agent_working_state_versions_agent_turn_id",
        "llm_agent_working_state_versions",
        ["agent_turn_id"],
    )
    op.create_index(
        "ix_agent_working_state_run_status_version",
        "llm_agent_working_state_versions",
        ["agent_run_id", "status", "version"],
    )


def _create_session_memory_tables() -> None:
    """Create L1 Session State and Session Summary version tables."""

    op.create_table(
        "llm_session_states",
        sa.Column("session_state_id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("llm_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schema_version", sa.String(64), nullable=False, server_default="session_state.v1"
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", session_memory_status, nullable=False, server_default="generating"),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("previous_state_id", sa.String(36)),
        sa.Column("state_through_message_id", sa.String(36)),
        sa.Column("state_through_message_sequence", sa.Integer()),
        sa.Column(
            "generation_attempt_id",
            sa.String(36),
            sa.ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        ),
        sa.Column("json_snapshot_object_id", sa.String(36)),
        sa.Column("markdown_snapshot_object_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.UniqueConstraint("session_id", "version", name="uq_session_state_version"),
        sa.UniqueConstraint("session_id", "idempotency_key", name="uq_session_state_key"),
    )
    op.create_index("ix_llm_session_states_session_id", "llm_session_states", ["session_id"])
    op.create_index(
        "ix_llm_session_states_generation_attempt_id",
        "llm_session_states",
        ["generation_attempt_id"],
    )
    op.create_index(
        "ix_llm_session_states_json_snapshot_object_id",
        "llm_session_states",
        ["json_snapshot_object_id"],
    )
    op.create_index(
        "ix_llm_session_states_markdown_snapshot_object_id",
        "llm_session_states",
        ["markdown_snapshot_object_id"],
    )
    op.create_index(
        "ix_session_state_session_status_version",
        "llm_session_states",
        ["session_id", "status", "version"],
    )

    op.create_table(
        "llm_session_summaries",
        sa.Column("session_summary_id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("llm_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schema_version",
            sa.String(64),
            nullable=False,
            server_default="session_summary.v1",
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", session_memory_status, nullable=False, server_default="generating"),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("previous_summary_id", sa.String(36)),
        sa.Column("summary_from_message_sequence", sa.Integer()),
        sa.Column("summary_through_message_id", sa.String(36)),
        sa.Column("summary_through_message_sequence", sa.Integer()),
        sa.Column(
            "generation_attempt_id",
            sa.String(36),
            sa.ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        ),
        sa.Column("json_snapshot_object_id", sa.String(36)),
        sa.Column("markdown_snapshot_object_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.UniqueConstraint("session_id", "version", name="uq_session_summary_version"),
        sa.UniqueConstraint("session_id", "idempotency_key", name="uq_session_summary_key"),
    )
    op.create_index("ix_llm_session_summaries_session_id", "llm_session_summaries", ["session_id"])
    op.create_index(
        "ix_llm_session_summaries_generation_attempt_id",
        "llm_session_summaries",
        ["generation_attempt_id"],
    )
    op.create_index(
        "ix_llm_session_summaries_json_snapshot_object_id",
        "llm_session_summaries",
        ["json_snapshot_object_id"],
    )
    op.create_index(
        "ix_llm_session_summaries_markdown_snapshot_object_id",
        "llm_session_summaries",
        ["markdown_snapshot_object_id"],
    )
    op.create_index(
        "ix_session_summary_session_status_version",
        "llm_session_summaries",
        ["session_id", "status", "version"],
    )


def _create_collaboration_tables() -> None:
    """Create L2 Handoff, Run Memory Snapshot, and Memory Packet tables."""

    op.create_table(
        "llm_agent_handoffs",
        sa.Column("agent_handoff_id", sa.String(36), primary_key=True),
        sa.Column(
            "agent_run_id",
            sa.String(36),
            sa.ForeignKey("llm_agent_runs.agent_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("llm_tasks.task_id", ondelete="CASCADE"),
        ),
        sa.Column(
            "schema_version", sa.String(64), nullable=False, server_default="agent_handoff.v1"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", handoff_status, nullable=False, server_default="completed"),
        sa.Column("handoff_json", sa.JSON(), nullable=False),
        sa.Column(
            "supersedes_handoff_id",
            sa.String(36),
            sa.ForeignKey("llm_agent_handoffs.agent_handoff_id", ondelete="SET NULL"),
        ),
        sa.Column("json_snapshot_object_id", sa.String(36)),
        sa.Column("markdown_snapshot_object_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("agent_run_id", "idempotency_key", name="uq_agent_handoff_key"),
    )
    op.create_index("ix_llm_agent_handoffs_agent_run_id", "llm_agent_handoffs", ["agent_run_id"])
    op.create_index("ix_llm_agent_handoffs_run_id", "llm_agent_handoffs", ["run_id"])
    op.create_index("ix_llm_agent_handoffs_task_id", "llm_agent_handoffs", ["task_id"])
    op.create_index(
        "ix_llm_agent_handoffs_supersedes_handoff_id",
        "llm_agent_handoffs",
        ["supersedes_handoff_id"],
    )
    op.create_index(
        "ix_llm_agent_handoffs_json_snapshot_object_id",
        "llm_agent_handoffs",
        ["json_snapshot_object_id"],
    )
    op.create_index(
        "ix_llm_agent_handoffs_markdown_snapshot_object_id",
        "llm_agent_handoffs",
        ["markdown_snapshot_object_id"],
    )
    op.create_index(
        "ix_agent_handoff_run_task_created",
        "llm_agent_handoffs",
        ["run_id", "task_id", "created_at", "agent_handoff_id"],
    )
    op.create_index(
        "ix_agent_handoff_agent_run_created",
        "llm_agent_handoffs",
        ["agent_run_id", "created_at", "agent_handoff_id"],
    )

    op.create_table(
        "llm_run_memory_snapshots",
        sa.Column("run_memory_snapshot_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schema_version",
            sa.String(64),
            nullable=False,
            server_default="run_memory_state.v1",
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", session_memory_status, nullable=False, server_default="generating"),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("previous_snapshot_id", sa.String(36)),
        sa.Column("expected_previous_version", sa.Integer()),
        sa.Column("json_snapshot_object_id", sa.String(36)),
        sa.Column("markdown_snapshot_object_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.UniqueConstraint("run_id", "version", name="uq_run_memory_snapshot_version"),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_run_memory_snapshot_key"),
    )
    op.create_index(
        "ix_llm_run_memory_snapshots_run_id",
        "llm_run_memory_snapshots",
        ["run_id"],
    )
    op.create_index(
        "ix_llm_run_memory_snapshots_json_snapshot_object_id",
        "llm_run_memory_snapshots",
        ["json_snapshot_object_id"],
    )
    op.create_index(
        "ix_llm_run_memory_snapshots_markdown_snapshot_object_id",
        "llm_run_memory_snapshots",
        ["markdown_snapshot_object_id"],
    )
    op.create_index(
        "ix_run_memory_snapshot_run_status_version",
        "llm_run_memory_snapshots",
        ["run_id", "status", "version"],
    )

    op.create_table(
        "llm_run_memory_snapshot_handoffs",
        sa.Column(
            "run_memory_snapshot_id",
            sa.String(36),
            sa.ForeignKey("llm_run_memory_snapshots.run_memory_snapshot_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "agent_handoff_id",
            sa.String(36),
            sa.ForeignKey("llm_agent_handoffs.agent_handoff_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("handoff_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "run_memory_snapshot_id",
            "agent_handoff_id",
            name="uq_run_snapshot_handoff",
        ),
    )

    op.create_table(
        "llm_memory_packets",
        sa.Column("memory_packet_id", sa.String(36), primary_key=True),
        sa.Column(
            "agent_run_id",
            sa.String(36),
            sa.ForeignKey("llm_agent_runs.agent_run_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("llm_tasks.task_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schema_version",
            sa.String(64),
            nullable=False,
            server_default="agent_memory_packet.v1",
        ),
        sa.Column("packet_json", sa.JSON(), nullable=False),
        sa.Column("user_profile_snapshot_id", sa.String(36)),
        sa.Column("project_memory_profile_snapshot_id", sa.String(36)),
        sa.Column("session_state_id", sa.String(36)),
        sa.Column("session_summary_id", sa.String(36)),
        sa.Column("run_memory_snapshot_id", sa.String(36)),
        sa.Column("agent_working_state_version_id", sa.String(36)),
        sa.Column("estimated_token_count", sa.Integer(), nullable=False),
        sa.Column("tokenizer_name", sa.String(64), nullable=False),
        sa.Column("tokenizer_version", sa.String(64), nullable=False),
        sa.Column("json_snapshot_object_id", sa.String(36)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_llm_memory_packets_agent_run_id", "llm_memory_packets", ["agent_run_id"])
    op.create_index("ix_llm_memory_packets_run_id", "llm_memory_packets", ["run_id"])
    op.create_index("ix_llm_memory_packets_task_id", "llm_memory_packets", ["task_id"])
    op.create_index("ix_llm_memory_packets_user_id", "llm_memory_packets", ["user_id"])
    op.create_index(
        "ix_llm_memory_packets_json_snapshot_object_id",
        "llm_memory_packets",
        ["json_snapshot_object_id"],
    )
    op.create_index(
        "ix_memory_packet_run_agent_created",
        "llm_memory_packets",
        ["run_id", "agent_run_id", "created_at", "memory_packet_id"],
    )
    op.create_index(
        "ix_memory_packet_user_created",
        "llm_memory_packets",
        ["user_id", "created_at"],
    )

    op.create_table(
        "llm_memory_packet_items",
        sa.Column(
            "memory_packet_id",
            sa.String(36),
            sa.ForeignKey("llm_memory_packets.memory_packet_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("item_type", memory_packet_item_type, primary_key=True),
        sa.Column("resource_id", sa.String(36), primary_key=True),
        sa.Column("resource_version", sa.String(64)),
        sa.Column("item_order", sa.Integer(), nullable=False),
        sa.Column("selected_token_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "memory_packet_id",
            "item_order",
            name="uq_memory_packet_item_order",
        ),
    )
    op.create_index(
        "ix_memory_packet_item_resource",
        "llm_memory_packet_items",
        ["item_type", "resource_id"],
    )


def _create_snapshot_registry() -> None:
    """Create the shared immutable MinIO snapshot object registry."""

    op.create_table(
        "llm_memory_snapshot_objects",
        sa.Column("memory_snapshot_object_id", sa.String(36), primary_key=True),
        sa.Column("memory_layer", sa.String(16), nullable=False),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("llm_projects.project_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("llm_sessions.session_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("llm_tasks.task_id", ondelete="RESTRICT"),
        ),
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("object_version", sa.Integer(), nullable=False),
        sa.Column("storage_uri", sa.String(1024), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("status", snapshot_object_status, nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("cleanup_error", sa.String(512)),
        sa.UniqueConstraint("storage_uri", name="uq_memory_snapshot_object_uri"),
    )
    op.create_index(
        "ix_llm_memory_snapshot_objects_user_id",
        "llm_memory_snapshot_objects",
        ["user_id"],
    )
    op.create_index(
        "ix_llm_memory_snapshot_objects_project_id",
        "llm_memory_snapshot_objects",
        ["project_id"],
    )
    op.create_index(
        "ix_llm_memory_snapshot_objects_session_id",
        "llm_memory_snapshot_objects",
        ["session_id"],
    )
    op.create_index(
        "ix_llm_memory_snapshot_objects_run_id",
        "llm_memory_snapshot_objects",
        ["run_id"],
    )
    op.create_index(
        "ix_llm_memory_snapshot_objects_task_id",
        "llm_memory_snapshot_objects",
        ["task_id"],
    )
    op.create_index(
        "ix_llm_memory_snapshot_objects_status",
        "llm_memory_snapshot_objects",
        ["status"],
    )
    op.create_index(
        "ix_memory_snapshot_object_layer_created",
        "llm_memory_snapshot_objects",
        ["memory_layer", "created_at", "memory_snapshot_object_id"],
    )
    op.create_index(
        "ix_memory_snapshot_object_scope_created",
        "llm_memory_snapshot_objects",
        ["user_id", "session_id", "run_id", "task_id", "created_at"],
    )
    op.create_index(
        "ix_memory_snapshot_object_status_created",
        "llm_memory_snapshot_objects",
        ["status", "created_at", "memory_snapshot_object_id"],
    )


def _create_profile_tables() -> None:
    """Create Project and User core Profile snapshot tables."""

    op.create_table(
        "llm_project_memory_profile_snapshots",
        sa.Column("project_memory_profile_snapshot_id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("llm_projects.project_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schema_version", sa.String(64), nullable=False, server_default="project_profile.v1"
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", session_memory_status, nullable=False, server_default="generating"),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("estimated_token_count", sa.Integer(), nullable=False),
        sa.Column("tokenizer_name", sa.String(64), nullable=False),
        sa.Column("tokenizer_version", sa.String(64), nullable=False),
        sa.Column("previous_snapshot_id", sa.String(36)),
        sa.Column("json_snapshot_object_id", sa.String(36)),
        sa.Column("markdown_snapshot_object_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.UniqueConstraint("project_id", "version", name="uq_project_profile_version"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_project_profile_key"),
    )
    op.create_index(
        "ix_llm_project_memory_profile_snapshots_project_id",
        "llm_project_memory_profile_snapshots",
        ["project_id"],
    )
    op.create_index(
        "ix_llm_project_memory_profile_snapshots_json_snapshot_object_id",
        "llm_project_memory_profile_snapshots",
        ["json_snapshot_object_id"],
    )
    op.create_index(
        "ix_llm_project_memory_profile_snapshots_markdown_snapshot_object_id",
        "llm_project_memory_profile_snapshots",
        ["markdown_snapshot_object_id"],
    )
    op.create_index(
        "ix_project_profile_project_status_version",
        "llm_project_memory_profile_snapshots",
        ["project_id", "status", "version"],
    )

    op.create_table(
        "llm_project_memory_profile_items",
        sa.Column(
            "project_memory_profile_snapshot_id",
            sa.String(36),
            sa.ForeignKey(
                "llm_project_memory_profile_snapshots.project_memory_profile_snapshot_id",
                ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column(
            "memory_id",
            sa.String(36),
            sa.ForeignKey("llm_memories.memory_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "memory_version_id",
            sa.String(36),
            sa.ForeignKey("llm_memory_versions.memory_version_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_path", sa.String(128), nullable=False),
        sa.Column("item_order", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "project_memory_profile_snapshot_id",
            "memory_id",
            name="uq_project_profile_item_memory",
        ),
    )
    op.create_index(
        "ix_llm_project_memory_profile_items_memory_version_id",
        "llm_project_memory_profile_items",
        ["memory_version_id"],
    )

    op.create_table(
        "llm_user_memory_profile_snapshots",
        sa.Column("user_memory_profile_snapshot_id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "schema_version", sa.String(64), nullable=False, server_default="user_profile.v1"
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", session_memory_status, nullable=False, server_default="generating"),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("estimated_token_count", sa.Integer(), nullable=False),
        sa.Column("tokenizer_name", sa.String(64), nullable=False),
        sa.Column("tokenizer_version", sa.String(64), nullable=False),
        sa.Column("previous_snapshot_id", sa.String(36)),
        sa.Column("json_snapshot_object_id", sa.String(36)),
        sa.Column("markdown_snapshot_object_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.UniqueConstraint("user_id", "version", name="uq_user_profile_version"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_user_profile_key"),
    )
    op.create_index(
        "ix_llm_user_memory_profile_snapshots_user_id",
        "llm_user_memory_profile_snapshots",
        ["user_id"],
    )
    op.create_index(
        "ix_llm_user_memory_profile_snapshots_json_snapshot_object_id",
        "llm_user_memory_profile_snapshots",
        ["json_snapshot_object_id"],
    )
    op.create_index(
        "ix_llm_user_memory_profile_snapshots_markdown_snapshot_object_id",
        "llm_user_memory_profile_snapshots",
        ["markdown_snapshot_object_id"],
    )
    op.create_index(
        "ix_user_profile_user_status_version",
        "llm_user_memory_profile_snapshots",
        ["user_id", "status", "version"],
    )

    op.create_table(
        "llm_user_memory_profile_items",
        sa.Column(
            "user_memory_profile_snapshot_id",
            sa.String(36),
            sa.ForeignKey(
                "llm_user_memory_profile_snapshots.user_memory_profile_snapshot_id",
                ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column(
            "memory_id",
            sa.String(36),
            sa.ForeignKey("llm_memories.memory_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "memory_version_id",
            sa.String(36),
            sa.ForeignKey("llm_memory_versions.memory_version_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_path", sa.String(128), nullable=False),
        sa.Column("item_order", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "user_memory_profile_snapshot_id",
            "memory_id",
            name="uq_user_profile_item_memory",
        ),
    )
    op.create_index(
        "ix_llm_user_memory_profile_items_memory_version_id",
        "llm_user_memory_profile_items",
        ["memory_version_id"],
    )


def _create_lesson_and_knowledge_tables() -> None:
    """Create Lesson Candidate and Knowledge document/chunk tables."""

    op.create_table(
        "llm_lesson_candidates",
        sa.Column("lesson_candidate_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("llm_tasks.task_id", ondelete="CASCADE"),
        ),
        sa.Column(
            "agent_handoff_id",
            sa.String(36),
            sa.ForeignKey("llm_agent_handoffs.agent_handoff_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "evaluation_id",
            sa.String(36),
            sa.ForeignKey("llm_evaluations.evaluation_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "status",
            lesson_candidate_status,
            nullable=False,
            server_default="candidate",
        ),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("lesson_json", sa.JSON(), nullable=False),
        sa.Column("valid_conditions_json", sa.JSON(), nullable=False),
        sa.Column("invalid_conditions_json", sa.JSON(), nullable=False),
        sa.Column(
            "promoted_memory_id",
            sa.String(36),
            sa.ForeignKey("llm_memories.memory_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_llm_lesson_candidates_run_id", "llm_lesson_candidates", ["run_id"])
    op.create_index("ix_llm_lesson_candidates_task_id", "llm_lesson_candidates", ["task_id"])
    op.create_index(
        "ix_llm_lesson_candidates_agent_handoff_id",
        "llm_lesson_candidates",
        ["agent_handoff_id"],
    )
    op.create_index(
        "ix_llm_lesson_candidates_evaluation_id",
        "llm_lesson_candidates",
        ["evaluation_id"],
    )
    op.create_index(
        "ix_llm_lesson_candidates_status",
        "llm_lesson_candidates",
        ["status"],
    )
    op.create_index(
        "ix_llm_lesson_candidates_promoted_memory_id",
        "llm_lesson_candidates",
        ["promoted_memory_id"],
    )
    op.create_index(
        "ix_lesson_candidate_run_status_created",
        "llm_lesson_candidates",
        ["run_id", "status", "created_at", "lesson_candidate_id"],
    )
    op.create_index(
        "ix_lesson_candidate_task_status_created",
        "llm_lesson_candidates",
        ["task_id", "status", "created_at", "lesson_candidate_id"],
    )

    op.create_table(
        "llm_knowledge_documents",
        sa.Column("knowledge_document_id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("llm_sessions.session_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("llm_runs.run_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("llm_tasks.task_id", ondelete="RESTRICT"),
        ),
        sa.Column("document_key", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "status",
            knowledge_document_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("current_version_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "document_key", name="uq_knowledge_document_user_key"),
    )
    op.create_index(
        "ix_llm_knowledge_documents_user_id",
        "llm_knowledge_documents",
        ["user_id"],
    )
    op.create_index(
        "ix_llm_knowledge_documents_session_id",
        "llm_knowledge_documents",
        ["session_id"],
    )
    op.create_index(
        "ix_llm_knowledge_documents_run_id",
        "llm_knowledge_documents",
        ["run_id"],
    )
    op.create_index(
        "ix_llm_knowledge_documents_task_id",
        "llm_knowledge_documents",
        ["task_id"],
    )
    op.create_index(
        "ix_llm_knowledge_documents_status",
        "llm_knowledge_documents",
        ["status"],
    )
    op.create_index(
        "ix_knowledge_document_user_status_created",
        "llm_knowledge_documents",
        ["user_id", "status", "created_at", "knowledge_document_id"],
    )

    op.create_table(
        "llm_knowledge_document_versions",
        sa.Column("knowledge_document_version_id", sa.String(36), primary_key=True),
        sa.Column(
            "knowledge_document_id",
            sa.String(36),
            sa.ForeignKey("llm_knowledge_documents.knowledge_document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("llm_artifacts.artifact_id", ondelete="RESTRICT"),
        ),
        sa.Column("artifact_uri", sa.String(1024), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column(
            "status",
            knowledge_version_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(128)),
        sa.UniqueConstraint(
            "knowledge_document_id",
            "version_number",
            name="uq_knowledge_document_version_number",
        ),
    )
    op.create_index(
        "ix_llm_knowledge_document_versions_knowledge_document_id",
        "llm_knowledge_document_versions",
        ["knowledge_document_id"],
    )
    op.create_index(
        "ix_llm_knowledge_document_versions_artifact_id",
        "llm_knowledge_document_versions",
        ["artifact_id"],
    )
    op.create_index(
        "ix_knowledge_version_document_status",
        "llm_knowledge_document_versions",
        ["knowledge_document_id", "status", "version_number"],
    )
    op.create_index(
        "ix_knowledge_version_artifact",
        "llm_knowledge_document_versions",
        ["artifact_id"],
    )

    op.create_table(
        "llm_knowledge_chunks",
        sa.Column("knowledge_chunk_id", sa.String(36), primary_key=True),
        sa.Column(
            "knowledge_document_version_id",
            sa.String(36),
            sa.ForeignKey(
                "llm_knowledge_document_versions.knowledge_document_version_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("chunk_order", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("stable_locator", sa.String(512), nullable=False),
        sa.Column("artifact_uri", sa.String(1024), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "knowledge_document_version_id",
            "chunk_order",
            name="uq_knowledge_chunk_order",
        ),
    )
    op.create_index(
        "ix_llm_knowledge_chunks_knowledge_document_version_id",
        "llm_knowledge_chunks",
        ["knowledge_document_version_id"],
    )
    op.create_index(
        "ix_llm_knowledge_chunks_content_hash",
        "llm_knowledge_chunks",
        ["content_hash"],
    )
    op.create_index(
        "ix_knowledge_chunk_version_order",
        "llm_knowledge_chunks",
        ["knowledge_document_version_id", "chunk_order"],
    )
    op.create_index("ix_knowledge_chunk_hash", "llm_knowledge_chunks", ["content_hash"])


def _create_prompt_and_catalog_tables() -> None:
    """Create Prompt template and Model Catalog tables."""

    op.create_table(
        "llm_prompt_templates",
        sa.Column("template_name", sa.String(128), primary_key=True),
        sa.Column("purpose", sa.Text()),
        sa.Column("status", prompt_template_status, nullable=False, server_default="enabled"),
        sa.Column("current_version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_llm_prompt_templates_status", "llm_prompt_templates", ["status"])
    op.create_index(
        "ix_prompt_template_status_updated",
        "llm_prompt_templates",
        ["status", "updated_at", "template_name"],
    )

    op.create_table(
        "llm_prompt_template_versions",
        sa.Column("template_version_id", sa.String(36), primary_key=True),
        sa.Column(
            "template_name",
            sa.String(128),
            sa.ForeignKey("llm_prompt_templates.template_name", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("variable_schema_json", sa.JSON(), nullable=False),
        sa.Column("created_by_actor_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "template_name",
            "version_number",
            name="uq_prompt_template_version_number",
        ),
    )
    op.create_index(
        "ix_llm_prompt_template_versions_template_name",
        "llm_prompt_template_versions",
        ["template_name"],
    )
    op.create_index(
        "ix_prompt_template_version_name_status",
        "llm_prompt_template_versions",
        ["template_name", "version_number"],
    )

    op.create_table(
        "llm_prompt_template_changes",
        sa.Column("change_id", sa.String(36), primary_key=True),
        sa.Column(
            "template_name",
            sa.String(128),
            sa.ForeignKey("llm_prompt_templates.template_name", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("version_number", sa.Integer()),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_llm_prompt_template_changes_template_name",
        "llm_prompt_template_changes",
        ["template_name"],
    )
    op.create_index(
        "ix_prompt_template_change_name_created",
        "llm_prompt_template_changes",
        ["template_name", "created_at", "change_id"],
    )

    op.create_table(
        "llm_model_catalog_versions",
        sa.Column("catalog_version_id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("catalog_version", sa.String(64), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", model_catalog_status, nullable=False, server_default="enabled"),
        sa.Column("context_window", sa.Integer(), nullable=False),
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "supports_structured_output",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_caching", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tokenizer_name", sa.String(64), nullable=False),
        sa.Column("tokenizer_version", sa.String(64), nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider",
            "model",
            "catalog_version",
            name="uq_model_catalog_version",
        ),
    )
    op.create_index(
        "ix_llm_model_catalog_versions_status",
        "llm_model_catalog_versions",
        ["status"],
    )
    op.create_index(
        "ix_model_catalog_provider_model_status",
        "llm_model_catalog_versions",
        ["provider", "model", "status", "catalog_version"],
    )


def _create_context_tables() -> None:
    """Create Context Build and per-source selection evidence tables."""

    op.create_table(
        "llm_context_builds",
        sa.Column("context_build_id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("llm_sessions.session_id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("llm_projects.project_id", ondelete="RESTRICT"),
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("tokenizer_name", sa.String(64), nullable=False),
        sa.Column("tokenizer_version", sa.String(64), nullable=False),
        sa.Column("input_token_estimate", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            context_build_status,
            nullable=False,
            server_default="completed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_llm_context_builds_user_id", "llm_context_builds", ["user_id"])
    op.create_index("ix_llm_context_builds_session_id", "llm_context_builds", ["session_id"])
    op.create_index("ix_llm_context_builds_project_id", "llm_context_builds", ["project_id"])
    op.create_index(
        "ix_context_build_user_created",
        "llm_context_builds",
        ["user_id", "created_at", "context_build_id"],
    )
    op.create_index(
        "ix_context_build_session_created",
        "llm_context_builds",
        ["session_id", "created_at", "context_build_id"],
    )

    op.create_table(
        "llm_context_sources",
        sa.Column("context_source_id", sa.String(36), primary_key=True),
        sa.Column(
            "context_build_id",
            sa.String(36),
            sa.ForeignKey("llm_context_builds.context_build_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_version", sa.String(64)),
        sa.Column("source_order", sa.Integer(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column(
            "selection_status",
            context_source_selection_status,
            nullable=False,
            server_default="selected",
        ),
        sa.Column("exclusion_reason", sa.String(128)),
        sa.Column("content_hash", sa.String(64)),
        sa.UniqueConstraint(
            "context_build_id",
            "source_type",
            "source_id",
            name="uq_context_source_identity",
        ),
    )
    op.create_index(
        "ix_llm_context_sources_context_build_id",
        "llm_context_sources",
        ["context_build_id"],
    )
    op.create_index(
        "ix_context_source_build_order",
        "llm_context_sources",
        ["context_build_id", "source_order"],
    )


def _create_evaluation_tables() -> None:
    """Create Evaluation rule set and immutable rule tables."""

    op.create_table(
        "llm_evaluation_rule_sets",
        sa.Column("rule_set_id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", evaluation_rule_set_status, nullable=False, server_default="enabled"),
        sa.Column("rule_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name", "schema_version", name="uq_evaluation_rule_set_version"),
    )
    op.create_index(
        "ix_llm_evaluation_rule_sets_status",
        "llm_evaluation_rule_sets",
        ["status"],
    )
    op.create_index(
        "ix_evaluation_rule_set_status_updated",
        "llm_evaluation_rule_sets",
        ["status", "updated_at", "rule_set_id"],
    )

    op.create_table(
        "llm_evaluation_rules",
        sa.Column("rule_id", sa.String(36), primary_key=True),
        sa.Column(
            "rule_set_id",
            sa.String(36),
            sa.ForeignKey("llm_evaluation_rule_sets.rule_set_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_key", sa.String(128), nullable=False),
        sa.Column("rule_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, server_default="error"),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("rule_set_id", "rule_key", name="uq_evaluation_rule_key"),
    )
    op.create_index("ix_llm_evaluation_rules_rule_set_id", "llm_evaluation_rules", ["rule_set_id"])
    op.create_index(
        "ix_evaluation_rule_set_created",
        "llm_evaluation_rules",
        ["rule_set_id", "created_at"],
    )


def _extend_existing_tables() -> None:
    """Add phase 2 scope, audit, trust, and evaluation columns to phase 1 tables."""

    op.add_column(
        "users",
        sa.Column(
            "current_memory_profile_snapshot_id",
            sa.String(36),
            sa.ForeignKey(
                "llm_user_memory_profile_snapshots.user_memory_profile_snapshot_id",
                ondelete="SET NULL",
            ),
        ),
    )
    op.create_index(
        "ix_users_current_memory_profile_snapshot_id",
        "users",
        ["current_memory_profile_snapshot_id"],
    )

    op.add_column(
        "llm_sessions",
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("llm_projects.project_id", ondelete="RESTRICT"),
        ),
    )
    op.add_column(
        "llm_sessions",
        sa.Column(
            "current_state_id",
            sa.String(36),
            sa.ForeignKey("llm_session_states.session_state_id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "llm_sessions",
        sa.Column(
            "current_summary_id",
            sa.String(36),
            sa.ForeignKey("llm_session_summaries.session_summary_id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_llm_sessions_project_id", "llm_sessions", ["project_id"])
    op.create_index("ix_llm_sessions_current_state_id", "llm_sessions", ["current_state_id"])
    op.create_index("ix_llm_sessions_current_summary_id", "llm_sessions", ["current_summary_id"])

    op.add_column(
        "llm_runs",
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("llm_projects.project_id", ondelete="RESTRICT"),
        ),
    )
    op.add_column(
        "llm_runs",
        sa.Column(
            "current_memory_snapshot_id",
            sa.String(36),
            sa.ForeignKey("llm_run_memory_snapshots.run_memory_snapshot_id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_llm_runs_project_id", "llm_runs", ["project_id"])
    op.create_index(
        "ix_llm_runs_current_memory_snapshot_id",
        "llm_runs",
        ["current_memory_snapshot_id"],
    )

    op.add_column(
        "llm_memories",
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("llm_projects.project_id", ondelete="RESTRICT"),
        ),
    )
    op.add_column(
        "llm_memories",
        sa.Column(
            "approval_method",
            approval_method,
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column("llm_memories", sa.Column("approved_at", sa.DateTime(timezone=True)))
    op.add_column("llm_memories", sa.Column("approved_by_actor_id", sa.String(128)))
    op.add_column(
        "llm_memories",
        sa.Column(
            "sensitivity_classification",
            sensitivity_classification,
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "llm_memories",
        sa.Column(
            "is_core_profile_eligible", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index("ix_llm_memories_project_id", "llm_memories", ["project_id"])
    op.create_index(
        "ix_llm_memories_approval_method",
        "llm_memories",
        ["approval_method"],
    )
    op.create_index(
        "ix_llm_memories_sensitivity_classification",
        "llm_memories",
        ["sensitivity_classification"],
    )
    op.create_index(
        "ix_llm_memories_is_core_profile_eligible",
        "llm_memories",
        ["is_core_profile_eligible"],
    )

    op.add_column(
        "llm_memory_sources",
        sa.Column(
            "trust_level",
            memory_trust_level,
            nullable=False,
            server_default="internal_system_result",
        ),
    )

    op.add_column(
        "llm_memory_mutations",
        sa.Column(
            "operation",
            memory_mutation_operation,
            nullable=False,
            server_default="update_metadata",
        ),
    )
    op.add_column(
        "llm_memory_mutations",
        sa.Column(
            "actor_type",
            memory_mutation_actor_type,
            nullable=False,
            server_default="trusted_caller",
        ),
    )
    op.add_column("llm_memory_mutations", sa.Column("actor_id", sa.String(128)))
    op.add_column("llm_memory_mutations", sa.Column("before_version_number", sa.Integer()))
    op.add_column("llm_memory_mutations", sa.Column("after_version_number", sa.Integer()))
    op.add_column("llm_memory_mutations", sa.Column("before_status", memory_status))
    op.add_column("llm_memory_mutations", sa.Column("after_status", memory_status))
    op.add_column("llm_memory_mutations", sa.Column("change_json", sa.JSON()))
    op.execute(
        "UPDATE llm_memory_mutations SET after_version_number = result_version_number, "
        "after_status = result_status"
    )
    op.alter_column(
        "llm_memory_mutations",
        "after_version_number",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "llm_memory_mutations",
        "after_status",
        existing_type=memory_status,
        nullable=False,
    )
    op.create_index(
        "ix_llm_memory_mutations_operation",
        "llm_memory_mutations",
        ["operation"],
    )
    op.create_index(
        "ix_memory_mutation_operation_created",
        "llm_memory_mutations",
        ["operation", "created_at"],
    )

    op.add_column(
        "llm_attempts",
        sa.Column(
            "memory_packet_id",
            sa.String(36),
            sa.ForeignKey("llm_memory_packets.memory_packet_id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_llm_attempts_memory_packet_id", "llm_attempts", ["memory_packet_id"])

    op.add_column(
        "llm_evaluations",
        sa.Column(
            "rule_set_id",
            sa.String(36),
            sa.ForeignKey("llm_evaluation_rule_sets.rule_set_id", ondelete="RESTRICT"),
        ),
    )
    op.add_column("llm_evaluations", sa.Column("rule_set_schema_version", sa.String(64)))
    op.add_column("llm_evaluations", sa.Column("idempotency_key", sa.String(128)))
    op.add_column("llm_evaluations", sa.Column("input_evidence_uri", sa.String(1024)))
    op.add_column(
        "llm_evaluations",
        sa.Column("status", evaluation_status, nullable=False, server_default="pending"),
    )
    op.add_column("llm_evaluations", sa.Column("error_code", sa.String(128)))
    op.add_column("llm_evaluations", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE llm_evaluations SET status = 'completed' WHERE verdict IS NOT NULL")
    op.execute(
        "UPDATE llm_evaluations SET completed_at = created_at WHERE completed_at IS NULL "
        "AND status = 'completed'"
    )
    op.create_index("ix_llm_evaluations_rule_set_id", "llm_evaluations", ["rule_set_id"])
    op.create_index("ix_llm_evaluations_status", "llm_evaluations", ["status"])
    op.create_unique_constraint(
        "uq_evaluation_task_idempotency_key",
        "llm_evaluations",
        ["task_id", "idempotency_key"],
    )


def downgrade() -> None:
    """Reverse phase 2 tables and columns, keeping phase 1 schema intact."""

    op.drop_constraint(
        "uq_evaluation_task_idempotency_key",
        "llm_evaluations",
        type_="unique",
    )
    op.drop_index("ix_llm_evaluations_status", table_name="llm_evaluations")
    op.drop_index("ix_llm_evaluations_rule_set_id", table_name="llm_evaluations")
    op.drop_column("llm_evaluations", "completed_at")
    op.drop_column("llm_evaluations", "error_code")
    op.drop_column("llm_evaluations", "status")
    op.drop_column("llm_evaluations", "input_evidence_uri")
    op.drop_column("llm_evaluations", "idempotency_key")
    op.drop_column("llm_evaluations", "rule_set_schema_version")
    op.drop_column("llm_evaluations", "rule_set_id")

    op.drop_index("ix_llm_attempts_memory_packet_id", table_name="llm_attempts")
    op.drop_column("llm_attempts", "memory_packet_id")

    op.drop_index("ix_memory_mutation_operation_created", table_name="llm_memory_mutations")
    op.drop_index("ix_llm_memory_mutations_operation", table_name="llm_memory_mutations")
    op.drop_column("llm_memory_mutations", "change_json")
    op.drop_column("llm_memory_mutations", "after_status")
    op.drop_column("llm_memory_mutations", "before_status")
    op.drop_column("llm_memory_mutations", "after_version_number")
    op.drop_column("llm_memory_mutations", "before_version_number")
    op.drop_column("llm_memory_mutations", "actor_id")
    op.drop_column("llm_memory_mutations", "actor_type")
    op.drop_column("llm_memory_mutations", "operation")

    op.drop_column("llm_memory_sources", "trust_level")

    op.drop_index("ix_llm_memories_is_core_profile_eligible", table_name="llm_memories")
    op.drop_index("ix_llm_memories_sensitivity_classification", table_name="llm_memories")
    op.drop_index("ix_llm_memories_approval_method", table_name="llm_memories")
    op.drop_index("ix_llm_memories_project_id", table_name="llm_memories")
    op.drop_column("llm_memories", "is_core_profile_eligible")
    op.drop_column("llm_memories", "sensitivity_classification")
    op.drop_column("llm_memories", "approved_by_actor_id")
    op.drop_column("llm_memories", "approved_at")
    op.drop_column("llm_memories", "approval_method")
    op.drop_column("llm_memories", "project_id")

    op.drop_index("ix_llm_runs_current_memory_snapshot_id", table_name="llm_runs")
    op.drop_index("ix_llm_runs_project_id", table_name="llm_runs")
    op.drop_column("llm_runs", "current_memory_snapshot_id")
    op.drop_column("llm_runs", "project_id")

    op.drop_index("ix_llm_sessions_current_summary_id", table_name="llm_sessions")
    op.drop_index("ix_llm_sessions_current_state_id", table_name="llm_sessions")
    op.drop_index("ix_llm_sessions_project_id", table_name="llm_sessions")
    op.drop_column("llm_sessions", "current_summary_id")
    op.drop_column("llm_sessions", "current_state_id")
    op.drop_column("llm_sessions", "project_id")

    op.drop_index(
        "ix_users_current_memory_profile_snapshot_id",
        table_name="users",
    )
    op.drop_column("users", "current_memory_profile_snapshot_id")

    op.drop_table("llm_evaluation_rules")
    op.drop_table("llm_evaluation_rule_sets")
    op.drop_table("llm_context_sources")
    op.drop_table("llm_context_builds")
    op.drop_table("llm_model_catalog_versions")
    op.drop_table("llm_prompt_template_changes")
    op.drop_table("llm_prompt_template_versions")
    op.drop_table("llm_prompt_templates")
    op.drop_table("llm_knowledge_chunks")
    op.drop_table("llm_knowledge_document_versions")
    op.drop_table("llm_knowledge_documents")
    op.drop_table("llm_lesson_candidates")
    op.drop_table("llm_user_memory_profile_items")
    op.drop_table("llm_user_memory_profile_snapshots")
    op.drop_table("llm_project_memory_profile_items")
    op.drop_table("llm_project_memory_profile_snapshots")
    op.drop_table("llm_memory_snapshot_objects")
    op.drop_table("llm_memory_packet_items")
    op.drop_table("llm_memory_packets")
    op.drop_table("llm_run_memory_snapshot_handoffs")
    op.drop_table("llm_run_memory_snapshots")
    op.drop_table("llm_agent_handoffs")
    op.drop_table("llm_agent_working_state_versions")
    op.drop_table("llm_agent_turns")
    op.drop_table("llm_agent_runs")
    op.drop_table("llm_session_summaries")
    op.drop_table("llm_session_states")
    op.drop_table("llm_project_workspaces")
    op.drop_table("llm_projects")

"""Add durable Memory facts, versions, sources, lexical terms, and retrieval evidence.

Revision ID: 20260801_0007
Revises: 20260731_0006
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0007"
down_revision: str | None = "20260731_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

memory_status = sa.Enum(
    "CANDIDATE",
    "ACTIVE",
    "REJECTED",
    "SUPERSEDED",
    "DELETED",
    name="memorystatus",
    native_enum=False,
    length=32,
)
memory_type = sa.Enum(
    "WORKING_CONTEXT",
    "SESSION_EPISODE",
    "USER_FACT",
    "USER_PREFERENCE",
    "EXECUTION_LESSON",
    name="memorytype",
    native_enum=False,
    length=32,
)
memory_source_type = sa.Enum(
    "MESSAGE",
    "MODEL_ATTEMPT",
    "ARTIFACT",
    "TOOL_CALL",
    "TRUSTED_REQUEST",
    name="memorysourcetype",
    native_enum=False,
    length=32,
)
memory_created_by_type = sa.Enum(
    "TRUSTED_CALLER",
    "MODEL_ATTEMPT",
    "SYSTEM",
    name="memorycreatedbytype",
    native_enum=False,
    length=32,
)
memory_retrieval_status = sa.Enum(
    "COMPLETED",
    name="memoryretrievalstatus",
    native_enum=False,
    length=32,
)


def upgrade() -> None:
    """Create Memory truth, immutable versions, evidence, and deterministic retrieval tables."""

    op.create_table(
        "llm_memories",
        sa.Column("memory_id", sa.String(36), primary_key=True),
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
        sa.Column("memory_type", memory_type, nullable=False),
        sa.Column("status", memory_status, nullable=False),
        sa.Column("current_version_number", sa.Integer(), nullable=False),
        sa.Column("semantic_key", sa.String(255)),
        sa.Column("active_semantic_key_hash", sa.String(64)),
        sa.Column(
            "supersedes_memory_id",
            sa.String(36),
            sa.ForeignKey("llm_memories.memory_id", ondelete="SET NULL"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("creation_idempotency_key", sa.String(128), nullable=False),
        sa.Column("creation_request_hash", sa.String(64), nullable=False),
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
            "user_id",
            "creation_idempotency_key",
            name="uq_memory_user_creation_key",
        ),
        sa.UniqueConstraint(
            "active_semantic_key_hash",
            name="uq_memory_active_semantic_key_hash",
        ),
    )
    for column_name in [
        "user_id",
        "session_id",
        "run_id",
        "task_id",
        "memory_type",
        "status",
        "supersedes_memory_id",
        "expires_at",
    ]:
        op.create_index(
            f"ix_llm_memories_{column_name}",
            "llm_memories",
            [column_name],
        )
    op.create_index(
        "ix_memory_user_status_created_id",
        "llm_memories",
        ["user_id", "status", "created_at", "memory_id"],
    )
    op.create_index(
        "ix_memory_session_status_created_id",
        "llm_memories",
        ["session_id", "status", "created_at", "memory_id"],
    )
    op.create_index(
        "ix_memory_run_status_created_id",
        "llm_memories",
        ["run_id", "status", "created_at", "memory_id"],
    )
    op.create_index(
        "ix_memory_task_status_created_id",
        "llm_memories",
        ["task_id", "status", "created_at", "memory_id"],
    )

    op.create_table(
        "llm_memory_versions",
        sa.Column("memory_version_id", sa.String(36), primary_key=True),
        sa.Column(
            "memory_id",
            sa.String(36),
            sa.ForeignKey("llm_memories.memory_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("normalized_content_hash", sa.String(64), nullable=False),
        sa.Column("importance", sa.Numeric(5, 4), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("estimated_token_count", sa.Integer(), nullable=False),
        sa.Column("token_estimator_version", sa.String(64), nullable=False),
        sa.Column("unique_search_term_count", sa.Integer(), nullable=False),
        sa.Column("created_by_type", memory_created_by_type, nullable=False),
        sa.Column(
            "created_by_attempt_id",
            sa.String(36),
            sa.ForeignKey("llm_attempts.attempt_id", ondelete="SET NULL"),
        ),
        sa.Column("content_erased_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "importance >= 0 AND importance <= 1",
            name="ck_memory_version_importance",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memory_version_confidence",
        ),
        sa.UniqueConstraint(
            "memory_id",
            "version_number",
            name="uq_memory_version_number",
        ),
    )
    op.create_index(
        "ix_llm_memory_versions_memory_id",
        "llm_memory_versions",
        ["memory_id"],
    )
    op.create_index(
        "ix_llm_memory_versions_normalized_content_hash",
        "llm_memory_versions",
        ["normalized_content_hash"],
    )
    op.create_index(
        "ix_llm_memory_versions_created_by_attempt_id",
        "llm_memory_versions",
        ["created_by_attempt_id"],
    )

    op.create_table(
        "llm_memory_mutations",
        sa.Column("memory_mutation_id", sa.String(36), primary_key=True),
        sa.Column(
            "memory_id",
            sa.String(36),
            sa.ForeignKey("llm_memories.memory_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_version_number", sa.Integer(), nullable=False),
        sa.Column("result_status", memory_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "memory_id",
            "idempotency_key",
            name="uq_memory_mutation_key",
        ),
    )
    op.create_index(
        "ix_llm_memory_mutations_memory_id",
        "llm_memory_mutations",
        ["memory_id"],
    )
    op.create_index(
        "ix_memory_mutation_memory_created",
        "llm_memory_mutations",
        ["memory_id", "created_at"],
    )

    op.create_table(
        "llm_memory_sources",
        sa.Column("memory_source_id", sa.String(36), primary_key=True),
        sa.Column(
            "memory_version_id",
            sa.String(36),
            sa.ForeignKey("llm_memory_versions.memory_version_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", memory_source_type, nullable=False),
        sa.Column("source_resource_id", sa.String(128), nullable=False),
        sa.Column("source_content_hash", sa.String(64), nullable=False),
        sa.Column("source_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "memory_version_id",
            "source_type",
            "source_resource_id",
            name="uq_memory_version_source",
        ),
    )
    op.create_index(
        "ix_llm_memory_sources_memory_version_id",
        "llm_memory_sources",
        ["memory_version_id"],
    )
    op.create_index(
        "ix_llm_memory_sources_source_resource_id",
        "llm_memory_sources",
        ["source_resource_id"],
    )

    op.create_table(
        "llm_memory_search_terms",
        sa.Column(
            "memory_version_id",
            sa.String(36),
            sa.ForeignKey("llm_memory_versions.memory_version_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("term_hash", sa.String(64), primary_key=True),
        sa.Column("term_frequency", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_memory_search_term_hash_version",
        "llm_memory_search_terms",
        ["term_hash", "memory_version_id"],
    )

    op.create_table(
        "llm_memory_retrievals",
        sa.Column("memory_retrieval_id", sa.String(36), primary_key=True),
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
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("memory_types_json", sa.JSON(), nullable=False),
        sa.Column("result_limit", sa.Integer(), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("candidate_method", sa.String(64), nullable=False),
        sa.Column("ranker_version", sa.String(64), nullable=False),
        sa.Column("status", memory_retrieval_status, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_memory_retrieval_user_key",
        ),
    )
    for column_name in ["user_id", "session_id", "run_id", "task_id"]:
        op.create_index(
            f"ix_llm_memory_retrievals_{column_name}",
            "llm_memory_retrievals",
            [column_name],
        )
    op.create_index(
        "ix_memory_retrieval_user_created_id",
        "llm_memory_retrievals",
        ["user_id", "created_at", "memory_retrieval_id"],
    )

    op.create_table(
        "llm_memory_retrieval_results",
        sa.Column("memory_retrieval_result_id", sa.String(36), primary_key=True),
        sa.Column(
            "memory_retrieval_id",
            sa.String(36),
            sa.ForeignKey("llm_memory_retrievals.memory_retrieval_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "memory_id",
            sa.String(36),
            sa.ForeignKey("llm_memories.memory_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "memory_version_id",
            sa.String(36),
            sa.ForeignKey("llm_memory_versions.memory_version_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("lexical_score", sa.Integer(), nullable=False),
        sa.Column("scope_score", sa.Integer(), nullable=False),
        sa.Column("importance_score", sa.Integer(), nullable=False),
        sa.Column("confidence_score", sa.Integer(), nullable=False),
        sa.Column("recency_score", sa.Integer(), nullable=False),
        sa.Column("estimated_token_count", sa.Integer(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("exclusion_reason", sa.String(64)),
        sa.UniqueConstraint(
            "memory_retrieval_id",
            "rank",
            name="uq_memory_retrieval_result_rank",
        ),
        sa.UniqueConstraint(
            "memory_retrieval_id",
            "memory_id",
            name="uq_memory_retrieval_result_memory",
        ),
    )
    for column_name in ["memory_retrieval_id", "memory_id", "memory_version_id"]:
        op.create_index(
            f"ix_llm_memory_retrieval_results_{column_name}",
            "llm_memory_retrieval_results",
            [column_name],
        )


def downgrade() -> None:
    """Remove Memory retrieval evidence before versioned Memory truth."""

    op.drop_table("llm_memory_retrieval_results")
    op.drop_table("llm_memory_retrievals")
    op.drop_table("llm_memory_search_terms")
    op.drop_table("llm_memory_sources")
    op.drop_table("llm_memory_mutations")
    op.drop_table("llm_memory_versions")
    op.drop_table("llm_memories")

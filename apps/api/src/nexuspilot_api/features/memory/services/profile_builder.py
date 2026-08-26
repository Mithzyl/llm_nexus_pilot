"""Deterministic User/Project core Profile projection with hard token caps."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.features.memory.models.memory import (
    LlmMemorySource as MemorySourceRow,
)
from nexuspilot_api.features.memory.services.memory_policy import PROJECT_MEMORY_TYPES
from nexuspilot_api.models import (
    ApprovalMethod,
    LlmMemory,
    LlmMemoryVersion,
    MemoryStatus,
    MemoryTrustLevel,
    MemoryType,
    SensitivityClassification,
)

USER_PROFILE_TARGET_TOKENS = 500
USER_PROFILE_HARD_CAP_TOKENS = 800
PROJECT_PROFILE_TARGET_TOKENS = 800
PROJECT_PROFILE_HARD_CAP_TOKENS = 1500
PROFILE_EXCLUDED_SENSITIVITIES = frozenset({SensitivityClassification.HIGH})


@dataclass(frozen=True)
class ProfileSelection:
    """Contain one projected fact and its deterministic field path."""

    memory_id: str
    memory_version_id: str
    content_text: str
    field_path: str
    order: int
    content_hash: str
    token_estimate: int


@dataclass(frozen=True)
class BuiltProfile:
    """Contain the bounded profile document and its itemized selection evidence."""

    profile_json: dict
    selections: list[ProfileSelection]
    estimated_token_count: int
    excluded_count: int


async def build_user_profile(
    db_session: AsyncSession,
    *,
    user_id: str,
    hard_cap: int = USER_PROFILE_HARD_CAP_TOKENS,
) -> BuiltProfile:
    """Project only approved, user-confirmed, non-sensitive, unscoped formal facts."""

    rows = await _eligible_memory_rows(
        db_session,
        user_id=user_id,
        project_id=None,
        trusted_only=True,
        profile_eligible_only=True,
    )
    sections = {
        "explicit_prohibitions": [],
        "environment_constraints": [],
        "confirmed_preferences": [],
        "confirmed_facts": [],
        "ongoing_goals": [],
    }
    section_by_type_key = {
        "prohibition": "explicit_prohibitions",
        "environment": "environment_constraints",
        "preference": "confirmed_preferences",
        "goal": "ongoing_goals",
        "fact": "confirmed_facts",
    }
    return _select_profile_items(
        rows,
        sections,
        section_by_type_key,
        hard_cap=hard_cap,
    )


async def build_project_profile(
    db_session: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    hard_cap: int = PROJECT_PROFILE_HARD_CAP_TOKENS,
) -> BuiltProfile:
    """Project only active, non-expired facts explicitly bound to this Project."""

    rows = await _eligible_memory_rows(
        db_session,
        user_id=user_id,
        project_id=project_id,
        trusted_only=False,
        profile_eligible_only=False,
    )
    sections = {
        "project_identity": {"name": "", "scope": ""},
        "goals": [],
        "glossary": [],
        "architecture_decisions": [],
        "constraints": [],
        "conventions": [],
        "verified_environment": [],
        "known_risks": [],
        "explicit_prohibitions": [],
    }
    section_by_type_key = {
        "decision": "architecture_decisions",
        "rule": "constraints",
        "convention": "conventions",
        "environment": "verified_environment",
        "risk": "known_risks",
        "prohibition": "explicit_prohibitions",
        "term": "glossary",
        "goal": "goals",
        "fact": "goals",
    }
    built = _select_profile_items(
        rows,
        sections,
        section_by_type_key,
        hard_cap=hard_cap,
    )
    built.profile_json["project_identity"] = {
        "name": "",
        "scope": "",
    }
    return built


async def _eligible_memory_rows(
    db_session: AsyncSession,
    *,
    user_id: str,
    project_id: str | None,
    trusted_only: bool,
    profile_eligible_only: bool,
) -> list[tuple[LlmMemory, LlmMemoryVersion, MemoryTrustLevel]]:
    """Return current-version Memory rows that may enter a core Profile projection."""

    now = datetime.now(UTC)
    statement = (
        select(
            LlmMemory,
            LlmMemoryVersion,
            MemorySourceRow.trust_level,
        )
        .join(
            LlmMemoryVersion,
            and_(
                LlmMemoryVersion.memory_id == LlmMemory.memory_id,
                LlmMemoryVersion.version_number == LlmMemory.current_version_number,
            ),
        )
        .outerjoin(
            MemorySourceRow,
            and_(
                MemorySourceRow.memory_version_id == LlmMemoryVersion.memory_version_id,
                MemorySourceRow.source_order == 1,
            ),
        )
        .where(
            LlmMemory.user_id == user_id,
            LlmMemory.status == MemoryStatus.ACTIVE,
            (
                LlmMemory.project_id.is_(None)
                if project_id is None
                else LlmMemory.project_id == project_id
            ),
            (LlmMemory.expires_at.is_(None) | (LlmMemory.expires_at > now)),
            LlmMemory.approval_method != ApprovalMethod.NONE,
        )
    )
    if profile_eligible_only:
        statement = statement.where(
            LlmMemory.is_core_profile_eligible.is_(True),
            LlmMemory.sensitivity_classification.notin_(PROFILE_EXCLUDED_SENSITIVITIES),
        )
    if trusted_only:
        statement = statement.where(
            MemorySourceRow.trust_level.in_(
                {
                    MemoryTrustLevel.DIRECT_USER_STATEMENT,
                    MemoryTrustLevel.USER_CONFIRMED,
                }
            )
        )
    rows = list((await db_session.execute(statement)).all())
    rows.sort(
        key=lambda row: (
            _profile_priority_key(row[0].memory_type, row[0].semantic_key),
            row[0].created_at,
            row[0].memory_id,
        )
    )
    return [(row[0], row[1], row[2]) for row in rows]


def _profile_priority_key(memory_type: MemoryType, semantic_key: str | None) -> tuple[int, str]:
    """Order facts so prohibitions and constraints precede preferences and goals."""

    section = _section_key(memory_type, semantic_key)
    order = {
        "prohibition": 0,
        "environment": 1,
        "constraint": 2,
        "convention": 3,
        "decision": 4,
        "preference": 5,
        "term": 6,
        "goal": 7,
        "fact": 8,
        "risk": 9,
    }
    return (order.get(section, 10), semantic_key or "")


def _select_profile_items(
    rows: list[tuple[LlmMemory, LlmMemoryVersion, MemoryTrustLevel]],
    sections: dict,
    section_by_type_key: dict[str, str],
    *,
    hard_cap: int,
) -> BuiltProfile:
    """Select prioritized facts while keeping the complete serialized Profile under cap."""

    selections: list[ProfileSelection] = []
    excluded_count = 0
    running_tokens = max(1, len(json.dumps(sections, ensure_ascii=False).encode()))
    order = 0
    for memory, version, _trust in rows:
        section = _section_key(memory.memory_type, memory.semantic_key)
        field_path = section_by_type_key.get(section, "confirmed_facts")
        content_text = version.content_text or ""
        item_tokens = max(1, len(content_text.encode()))
        section_items = sections.get(field_path)
        if not isinstance(section_items, list):
            excluded_count += 1
            continue
        section_items.append({"text": content_text, "source": memory.memory_id})
        candidate_tokens = max(
            1,
            len(json.dumps(sections, ensure_ascii=False).encode()),
        )
        if candidate_tokens > hard_cap:
            section_items.pop()
            excluded_count += 1
            continue
        running_tokens = candidate_tokens
        order += 1
        selections.append(
            ProfileSelection(
                memory_id=memory.memory_id,
                memory_version_id=version.memory_version_id,
                content_text=content_text,
                field_path=field_path,
                order=order,
                content_hash=version.content_hash,
                token_estimate=item_tokens,
            )
        )
    return BuiltProfile(
        profile_json=sections,
        selections=selections,
        estimated_token_count=running_tokens,
        excluded_count=excluded_count,
    )


def _section_key(memory_type: MemoryType, semantic_key: str | None) -> str:
    """Map Memory type and semantic key to one deterministic profile section."""

    if memory_type in PROJECT_MEMORY_TYPES:
        if semantic_key:
            lowered = semantic_key.lower()
            for key in (
                "prohibition",
                "environment",
                "risk",
                "term",
                "goal",
                "decision",
                "rule",
                "convention",
                "fact",
            ):
                if lowered.startswith(f"{key}:"):
                    return key
        return {
            MemoryType.PROJECT_DECISION: "decision",
            MemoryType.PROJECT_RULE: "rule",
            MemoryType.PROJECT_CONVENTION: "convention",
            MemoryType.PROJECT_ENVIRONMENT: "environment",
            MemoryType.PROJECT_FACT: "fact",
        }[memory_type]
    if memory_type == MemoryType.USER_PREFERENCE:
        return "preference"
    if semantic_key:
        lowered = semantic_key.lower()
        for key in ("prohibition", "environment", "goal"):
            if lowered.startswith(f"{key}:"):
                return key
    return "fact"

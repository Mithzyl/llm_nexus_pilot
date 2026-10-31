"""Save explicit user preferences and pin their selected versions at Run submission."""

import json

from sqlalchemy import or_, select

from nexuspilot_api.features.identity.models.account_linking import EcosystemAccountLink
from nexuspilot_api.features.identity.models.user_instructions import (
    RunInstructionSelection,
    UserInstructionProfile,
    UserInstructionVersion,
)
from nexuspilot_api.features.memory.services.product_memory import MemoryFailure, MemoryService
from nexuspilot_api.features.memory.services.sharing import entry_available, resolve_share_target
from nexuspilot_api.models import LlmRun, User


def instruction_storage(db, storage, owner, entry, scope_key):
    """Reuse private AES storage with a distinct instruction-specific authenticated scope."""
    service = MemoryService(db, storage, owner, entry)
    service.scope_key = "instructions:" + scope_key
    return service


async def recipient_approval(db, storage, owner, target):
    """Bind global consent to one current recipient and its association revision."""
    if target == "native":
        if not await entry_available(db, owner, target):
            raise MemoryFailure("instruction_target_unavailable")
        return {"owner": owner, "link_id": None, "link_version": None}
    try:
        target_owner, _, link_id = await resolve_share_target(
            MemoryService(db, storage, owner, "native"), target, lock=True
        )
    except MemoryFailure:
        raise MemoryFailure("instruction_target_unavailable") from None
    link = await db.get(EcosystemAccountLink, link_id, populate_existing=True) if link_id else None
    return {
        "owner": target_owner,
        "link_id": link_id,
        "link_version": link.version if link else None,
    }


async def save_profile(
    db, storage, owner, entry, scope, *, text, enabled, allowed_entries, expected_version
):
    """Serialize edits per owner and preserve versions; App cannot edit global preferences."""
    if entry not in {"native", "personal_assistant", "retail"} or scope not in {"global", "entry"}:
        raise MemoryFailure("instruction_scope_invalid")
    if scope == "global" and entry != "native":
        raise MemoryFailure("instruction_global_requires_native")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
        raise MemoryFailure("instruction_text_invalid")
    user = await db.scalar(select(User).where(User.user_id == owner).with_for_update())
    if user is None or not user.is_active:
        raise MemoryFailure("memory_not_allowed")
    targets = list(dict.fromkeys(allowed_entries)) if scope == "global" else [entry]
    if scope == "entry" and allowed_entries:
        raise MemoryFailure("instruction_scope_invalid")
    approvals = {}
    for target in targets:
        if scope == "global":
            if enabled:
                approvals[target] = await recipient_approval(db, storage, owner, target)
        elif not await entry_available(db, owner, target):
            raise MemoryFailure("instruction_target_unavailable")
    key = "global" if scope == "global" else entry
    profile = await db.scalar(
        select(UserInstructionProfile)
        .where(
            UserInstructionProfile.owner_user_id == owner,
            UserInstructionProfile.scope_key == key,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    cipher = instruction_storage(db, storage, owner, entry, key)
    body = {"text": text.strip()}
    if profile is not None:
        version = await db.get(
            UserInstructionVersion, (profile.profile_id, profile.current_version)
        )
        if (
            profile.enabled == enabled
            and profile.allowed_entries_json == targets
            and profile.allowed_recipients_json == approvals
            and await cipher.read(version.body_uri) == body
        ):
            return {"version": profile.current_version, "enabled": enabled}
        if profile.current_version != expected_version:
            raise MemoryFailure("instruction_version_conflict")
    elif expected_version != 0:
        raise MemoryFailure("instruction_version_conflict")
    else:
        profile = UserInstructionProfile(owner_user_id=owner, scope_key=key, current_version=0)
        db.add(profile)
        await db.flush()
    profile.current_version += 1
    profile.enabled, profile.allowed_entries_json = enabled, targets
    profile.allowed_recipients_json = approvals
    db.add(
        UserInstructionVersion(
            profile_id=profile.profile_id,
            version=profile.current_version,
            body_uri=await cipher.put(body),
        )
    )
    await db.commit()
    return {"version": profile.current_version, "enabled": enabled}


async def pin_run_instructions(db, run, entry):
    """Record selected versions, including an empty selection, in the Run creation transaction."""
    global_owner = run.user_id
    if entry != "native":
        try:
            global_owner, _, _ = await resolve_share_target(
                MemoryService(db, None, run.user_id, entry), "native"
            )
        except MemoryFailure:
            global_owner = None
    profiles = list(
        await db.scalars(
            select(UserInstructionProfile).where(
                or_(
                    (UserInstructionProfile.owner_user_id == run.user_id)
                    & (UserInstructionProfile.scope_key == entry),
                    (UserInstructionProfile.owner_user_id == global_owner)
                    & (UserInstructionProfile.scope_key == "global"),
                ),
                UserInstructionProfile.enabled.is_(True),
            )
        )
    )
    selected = [
        {
            "profile_id": profile.profile_id,
            "version": profile.current_version,
            "scope_key": profile.scope_key,
            **(
                {"recipient": profile.allowed_recipients_json.get(entry)}
                if profile.owner_user_id != run.user_id
                else {}
            ),
        }
        for profile in sorted(profiles, key=lambda p: p.scope_key != "global")
        if entry in profile.allowed_entries_json
        and (
            profile.owner_user_id == run.user_id
            or (profile.allowed_recipients_json.get(entry) or {}).get("owner") == run.user_id
        )
    ]
    db.add(RunInstructionSelection(run_id=run.run_id, entry=entry, references_json=selected))


async def read_run_instructions(db, storage, run_id):
    """Read only pinned versions; legacy Runs with no selection do not adopt current settings."""
    selection = await db.get(RunInstructionSelection, run_id)
    if selection is None:
        return []
    run = await db.get(LlmRun, run_id)
    result = []
    for reference in selection.references_json:
        profile = await db.get(
            UserInstructionProfile, reference["profile_id"], populate_existing=True
        )
        if profile is None:
            raise MemoryFailure("instruction_reference_invalid")
        if profile.owner_user_id != run.user_id:
            approval = reference.get("recipient")
            if (
                profile.scope_key != "global"
                or not profile.enabled
                or not approval
                or selection.entry not in profile.allowed_entries_json
                or profile.allowed_recipients_json.get(selection.entry) != approval
                or approval.get("owner") != run.user_id
                or not approval.get("link_id")
            ):
                continue
            link = await db.get(EcosystemAccountLink, approval["link_id"], populate_existing=True)
            if (
                link is None
                or link.status != "active"
                or link.version != approval["link_version"]
                or link.primary_principal_id != profile.owner_user_id
                or link.linked_principal_id != run.user_id
            ):
                continue
            if not await entry_available(
                db, profile.owner_user_id, "native"
            ) or not await entry_available(db, run.user_id, selection.entry):
                continue
        version = await db.get(UserInstructionVersion, (profile.profile_id, reference["version"]))
        if version is None:
            raise MemoryFailure("instruction_reference_invalid")
        cipher = instruction_storage(
            db, storage, profile.owner_user_id, selection.entry, reference["scope_key"]
        )
        body = await cipher.read(version.body_uri)
        result.append({**reference, "text": body["text"]})
    return result


async def apply_run_instructions(db, storage, run_id, request):
    """Append selected user preferences after platform/role rules, before user and tool messages."""
    selected = await read_run_instructions(db, storage, run_id)
    if not selected:
        return request
    prefix = request.system_instruction or ""
    layers = [
        "User preferences (do not override platform rules or grant permissions):\n" + item["text"]
        for item in selected
    ]
    return request.model_copy(
        update={
            "system_instruction": "\n\n".join([prefix, *layers]),
            "metadata": {
                **request.metadata,
                "user_instruction_versions": json.dumps(
                    [
                        {key: item[key] for key in ("profile_id", "version", "scope_key")}
                        for item in selected
                    ]
                ),
            },
        }
    )


async def get_profile(db, storage, owner, entry, scope):
    """Read current entry settings, distinguishing absence from storage failure."""
    if scope == "global" and entry != "native":
        raise MemoryFailure("instruction_global_requires_native")
    key = "global" if scope == "global" else entry
    profile = await db.scalar(
        select(UserInstructionProfile).where(
            UserInstructionProfile.owner_user_id == owner,
            UserInstructionProfile.scope_key == key,
        )
    )
    if profile is None:
        return {"version": 0, "text": "", "enabled": False, "allowed_entries": []}
    version = await db.get(UserInstructionVersion, (profile.profile_id, profile.current_version))
    if version is None:
        raise MemoryFailure("instruction_reference_invalid")
    body = await instruction_storage(db, storage, owner, entry, key).read(version.body_uri)
    return {
        "version": profile.current_version,
        "enabled": profile.enabled,
        "allowed_entries": profile.allowed_entries_json if scope == "global" else [],
        **body,
    }

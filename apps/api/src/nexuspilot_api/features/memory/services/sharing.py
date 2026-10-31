"""Explicit versioned field sharing, with projection before plaintext leaves the service."""

from sqlalchemy import select, update

from nexuspilot_api.features.identity.models.account_linking import EcosystemAccountLink
from nexuspilot_api.features.identity.models.applications import Application, ApplicationUserMapping
from nexuspilot_api.features.memory.models.product_memory import (
    UserMemoryDisclosure,
    UserMemoryEntry,
    UserMemoryScope,
    UserMemoryShareGrant,
)
from nexuspilot_api.features.memory.services.product_memory import MemoryFailure, MemoryService
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.models.identity import User


async def entry_available(db, owner, entry):
    """Require a live application mapping; native access still uses the owner's browser identity."""
    user = await db.get(User, owner, populate_existing=True)
    if user is None or not user.is_active:
        return False
    if entry == "native":
        return True
    if entry not in {"personal_assistant", "retail"}:
        return False
    return bool(
        await db.scalar(
            select(ApplicationUserMapping.mapping_id)
            .join(Application)
            .where(
                ApplicationUserMapping.pilot_principal_id == owner,
                ApplicationUserMapping.application_id == entry,
                ApplicationUserMapping.is_enabled.is_(True),
                Application.is_enabled.is_(True),
            )
        )
    )


async def resolve_share_target(service, target_entry, *, lock=False):
    """Derive recipients through current links, never accepting an owner from client input.

    App-to-App sharing depends on both associations. Revoked source associations
    cannot silently fall back to the App's shadow native account.
    """
    db = service.db
    primary, source_link = service.owner, None
    if not await entry_available(db, service.owner, service.entry):
        raise MemoryFailure("memory_share_target_unavailable")
    if service.entry != "native":
        source_link = await db.scalar(
            select(EcosystemAccountLink)
            .join(ApplicationUserMapping)
            .where(
                ApplicationUserMapping.pilot_principal_id == service.owner,
                ApplicationUserMapping.application_id == service.entry,
            )
            .execution_options(populate_existing=True)
        )
        if source_link:
            if source_link.status != "active" or source_link.linked_principal_id != service.owner:
                raise MemoryFailure("memory_share_target_unavailable")
            primary = source_link.primary_principal_id
    if lock:
        await db.scalar(select(User).where(User.user_id == primary).with_for_update())
    if not await entry_available(db, primary, "native"):
        raise MemoryFailure("memory_share_target_unavailable")
    target_owner, target_link = primary, None
    if target_entry != "native":
        direct = await db.scalar(
            select(ApplicationUserMapping)
            .where(
                ApplicationUserMapping.pilot_principal_id == primary,
                ApplicationUserMapping.application_id == target_entry,
            )
            .execution_options(populate_existing=True)
        )
        if direct is None:
            target_link = await db.scalar(
                select(EcosystemAccountLink)
                .join(ApplicationUserMapping)
                .where(
                    EcosystemAccountLink.primary_principal_id == primary,
                    EcosystemAccountLink.status == "active",
                    ApplicationUserMapping.application_id == target_entry,
                    ApplicationUserMapping.pilot_principal_id
                    == EcosystemAccountLink.linked_principal_id,
                )
                .execution_options(populate_existing=True)
            )
            if not target_link:
                raise MemoryFailure("memory_share_target_unavailable")
            target_owner = target_link.linked_principal_id
    for candidate in sorted([r for r in (source_link, target_link) if r], key=lambda r: r.link_id):
        query = select(EcosystemAccountLink).where(
            EcosystemAccountLink.link_id == candidate.link_id
        )
        current = await db.scalar(
            (query.with_for_update() if lock else query).execution_options(populate_existing=True)
        )
        if current.status != "active" or current.primary_principal_id != primary:
            raise MemoryFailure("memory_share_target_unavailable")
    if not await entry_available(db, target_owner, target_entry):
        raise MemoryFailure("memory_share_target_unavailable")
    return (
        target_owner,
        source_link.link_id if source_link else None,
        target_link.link_id if target_link else None,
    )


def project_fields(body, fields):
    """Copy only explicit existing dictionary paths; do not infer or include a text summary."""
    projected = {}
    if not fields or len(fields) > 32 or len(set(fields)) != len(fields):
        raise MemoryFailure("memory_share_fields_invalid")
    for field in fields:
        path = field.split(".")
        if len(path) > 6 or path[0] not in {"text", "typed_data", "app_data"}:
            raise MemoryFailure("memory_share_fields_invalid")
        value = body
        for key in path:
            if not isinstance(value, dict) or key not in value:
                raise MemoryFailure("memory_share_fields_invalid")
            value = value[key]
        target = projected
        for key in path[:-1]:
            target = target.setdefault(key, {})
            if not isinstance(target, dict):
                raise MemoryFailure("memory_share_fields_invalid")
        target[path[-1]] = value
    return projected


async def grant_share(service, *, entry_id, expected_version, target_entry, fields, automatic):
    """Approve fields for the resolved entry, preserving source encryption and explicit link IDs."""
    await service.authorize()
    if service.project_id or target_entry == service.entry:
        raise MemoryFailure("memory_share_target_invalid")
    target_owner, source_link_id, target_link_id = await resolve_share_target(
        service, target_entry, lock=True
    )
    scope = await service.scope(None, create=True)
    row = await service.db.scalar(
        select(UserMemoryEntry)
        .where(
            UserMemoryEntry.entry_id == entry_id,
            UserMemoryEntry.scope_id == scope.scope_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.status != "active":
        raise MemoryFailure("memory_not_found")
    if row.version != expected_version:
        raise MemoryFailure("memory_version_conflict")
    project_fields(await service.read(row.body_uri), fields)
    grant = await service.db.scalar(
        select(UserMemoryShareGrant)
        .where(
            UserMemoryShareGrant.entry_id == entry_id,
            UserMemoryShareGrant.target_owner_user_id == target_owner,
            UserMemoryShareGrant.target_entry == target_entry,
        )
        .with_for_update()
    )
    if grant is None:
        grant = UserMemoryShareGrant(
            entry_id=entry_id,
            target_owner_user_id=target_owner,
            target_entry=target_entry,
            version=1,
        )
        service.db.add(grant)
    elif (
        grant.revoked_at is not None
        or grant.confirmed_entry_version != row.version
        or grant.allowed_fields_json != fields
        or grant.allow_automatic_use != automatic
        or grant.source_account_link_id != source_link_id
        or grant.account_link_id != target_link_id
    ):
        grant.version += 1
        await invalidate_share_disclosures(service, grant)
    grant.confirmed_entry_version, grant.allowed_fields_json = row.version, fields
    grant.allow_automatic_use, grant.revoked_at = automatic, None
    grant.source_account_link_id, grant.account_link_id = source_link_id, target_link_id
    await service.db.commit()
    return {"grant_id": grant.grant_id, "version": grant.version, "target_entry": target_entry}


async def read_shared(service, *, for_model, offset=0, limit=20, grant_id=None):
    """Return authorized fields; sensitive model use still needs recipient Run consent."""
    await service.authorize()
    if service.project_id or not (await service.settings())["enabled"]:
        return []
    if not await entry_available(service.db, service.owner, service.entry):
        return []
    rows = (
        await service.db.execute(
            select(UserMemoryShareGrant, UserMemoryEntry, UserMemoryScope)
            .join(UserMemoryEntry, UserMemoryEntry.entry_id == UserMemoryShareGrant.entry_id)
            .join(UserMemoryScope, UserMemoryScope.scope_id == UserMemoryEntry.scope_id)
            .where(
                UserMemoryShareGrant.target_owner_user_id == service.owner,
                UserMemoryShareGrant.target_entry == service.entry,
                UserMemoryShareGrant.revoked_at.is_(None),
                UserMemoryShareGrant.confirmed_entry_version == UserMemoryEntry.version,
                UserMemoryEntry.status == "active",
                UserMemoryScope.enabled.is_(True),
                UserMemoryScope.project_id.is_(None),
                *([UserMemoryShareGrant.grant_id == grant_id] if grant_id else []),
            )
            .order_by(UserMemoryShareGrant.grant_id)
            .offset(offset)
            .limit(min(limit, 50))
        )
    ).all()
    items = []
    for grant, row, scope in rows:
        if not await entry_available(service.db, scope.owner_user_id, scope.entry):
            continue
        origin = MemoryService(service.db, service.storage, scope.owner_user_id, scope.entry)
        try:
            current_target = await resolve_share_target(origin, service.entry)
        except MemoryFailure:
            continue
        if current_target != (service.owner, grant.source_account_link_id, grant.account_link_id):
            continue
        disclosed = await shared_disclosure_matches(service, row, grant) if for_model else False
        if for_model and not grant.allow_automatic_use and not disclosed:
            continue
        item = {
            "entry_id": row.entry_id,
            "version": row.version,
            "grant_id": grant.grant_id,
            "grant_version": grant.version,
            "source_entry": scope.entry,
            "sensitive": row.sensitive,
        }
        if for_model and row.sensitive and not disclosed:
            item["disclosure_required"] = True
        else:
            item.update(project_fields(await origin.read(row.body_uri), grant.allowed_fields_json))
        items.append(item)
    return items


async def shared_disclosure_matches(service, row, grant):
    """A past confirmation cannot authorize fields added to a sharing grant afterward."""
    if not service.run_id:
        return False
    record = await service.db.scalar(
        select(UserMemoryDisclosure)
        .where(
            UserMemoryDisclosure.entry_id == row.entry_id,
            UserMemoryDisclosure.entry_version == row.version,
            UserMemoryDisclosure.run_id == service.run_id,
            UserMemoryDisclosure.recipient_entry == service.entry,
            UserMemoryDisclosure.revoked_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )
    return bool(record and record.allowed_fields_json == grant.allowed_fields_json)


async def disclose_shared(service, *, grant_id, expected_version, key):
    """Confirm current projected fields for one owned live Run, without expanding field access."""
    from nexuspilot_api.features.memory.services.product_memory import memory_for_run
    from nexuspilot_api.models import LlmRun, RunStatus

    run = await service.db.scalar(
        select(LlmRun)
        .where(
            LlmRun.run_id == service.run_id,
            LlmRun.user_id == service.owner,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None or run.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
        raise MemoryFailure("memory_not_allowed")
    actual = await memory_for_run(service.db, service.storage, run.run_id)
    if actual.entry != service.entry:
        raise MemoryFailure("memory_not_allowed")
    grant = await service.db.scalar(
        select(UserMemoryShareGrant)
        .where(
            UserMemoryShareGrant.grant_id == grant_id,
            UserMemoryShareGrant.target_owner_user_id == service.owner,
            UserMemoryShareGrant.target_entry == service.entry,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if grant is None or grant.version != expected_version:
        raise MemoryFailure("memory_version_conflict")
    current = await read_shared(service, for_model=False, grant_id=grant_id)
    if not current:
        raise MemoryFailure("memory_not_found")
    row = await service.db.get(UserMemoryEntry, grant.entry_id, populate_existing=True)
    record = await service.db.scalar(
        select(UserMemoryDisclosure)
        .where(
            UserMemoryDisclosure.entry_id == row.entry_id,
            UserMemoryDisclosure.entry_version == row.version,
            UserMemoryDisclosure.run_id == run.run_id,
            UserMemoryDisclosure.recipient_entry == service.entry,
        )
        .with_for_update()
    )
    if record is None:
        record = UserMemoryDisclosure(
            entry_id=row.entry_id,
            entry_version=row.version,
            run_id=run.run_id,
            recipient_entry=service.entry,
        )
        service.db.add(record)
    record.allowed_fields_json = list(grant.allowed_fields_json)
    record.request_key, record.confirmed_at, record.revoked_at = key, utc_now(), None
    await service.db.commit()
    return {
        "grant_id": grant_id,
        "run_id": run.run_id,
        "entry_version": row.version,
        "grant_version": grant.version,
    }


async def shared_query_page(service, *, offset=0, limit=20, for_model=True):
    """Page the bounded grant list independently from local memories, including filtered slots."""
    rows = await service.db.scalars(
        select(UserMemoryShareGrant.grant_id)
        .where(
            UserMemoryShareGrant.target_owner_user_id == service.owner,
            UserMemoryShareGrant.target_entry == service.entry,
        )
        .order_by(UserMemoryShareGrant.grant_id)
        .offset(offset)
        .limit(limit + 1)
    )
    ids = list(rows)
    items = []
    for identity in ids[:limit]:
        items.extend(await read_shared(service, for_model=for_model, grant_id=identity))
    return {"items": items, "next_offset": offset + limit if len(ids) > limit else None}


async def revoke_share(service, grant_id, expected_version):
    """Only the source owner/entry revokes a grant, with version checking and repeat safety."""
    await service.authorize()
    scope = await service.scope(None, create=True)
    grant = await service.db.scalar(
        select(UserMemoryShareGrant)
        .join(UserMemoryEntry)
        .where(
            UserMemoryShareGrant.grant_id == grant_id,
            UserMemoryEntry.scope_id == scope.scope_id,
        )
        .with_for_update()
    )
    if grant is None:
        raise MemoryFailure("memory_not_found")
    if grant.version != expected_version and grant.revoked_at is None:
        raise MemoryFailure("memory_version_conflict")
    if grant.revoked_at is None:
        grant.revoked_at = utc_now()
        grant.version += 1
        await invalidate_share_disclosures(service, grant)
    await service.db.commit()
    return {"grant_id": grant_id, "version": grant.version, "revoked": True}


async def list_outgoing_shares(service, entry_id):
    """Restore source-owned decisions without reading or exposing another entry's memory."""
    await service.authorize()
    scope = await service.scope(None)
    row = await service.db.get(UserMemoryEntry, entry_id)
    if scope is None or row is None or row.scope_id != scope.scope_id:
        raise MemoryFailure("memory_not_found")
    grants = await service.db.scalars(
        select(UserMemoryShareGrant)
        .where(
            UserMemoryShareGrant.entry_id == entry_id,
        )
        .order_by(UserMemoryShareGrant.target_entry)
        .limit(20)
    )
    items = []
    for grant in grants:
        active = (
            grant.revoked_at is None
            and grant.confirmed_entry_version == row.version
            and row.status == "active"
            and scope.enabled
        )
        if active:
            try:
                active = await resolve_share_target(service, grant.target_entry) == (
                    grant.target_owner_user_id,
                    grant.source_account_link_id,
                    grant.account_link_id,
                )
            except MemoryFailure:
                active = False
        items.append(
            {
                "grant_id": grant.grant_id,
                "version": grant.version,
                "target_entry": grant.target_entry,
                "fields": grant.allowed_fields_json,
                "automatic": grant.allow_automatic_use,
                "active": active,
            }
        )
    return {"items": items}


async def invalidate_share_disclosures(service, grant):
    """A changed or revoked grant cannot regain old Run consent merely by restoring its fields."""
    await service.db.execute(
        update(UserMemoryDisclosure)
        .where(
            UserMemoryDisclosure.entry_id == grant.entry_id,
            UserMemoryDisclosure.recipient_entry == grant.target_entry,
            UserMemoryDisclosure.revoked_at.is_(None),
        )
        .values(revoked_at=utc_now())
    )

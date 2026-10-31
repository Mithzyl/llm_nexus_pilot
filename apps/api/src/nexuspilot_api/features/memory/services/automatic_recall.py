"""Resolve explicitly enabled Assistant-to-native recall without copying memory bodies."""

from nexuspilot_api.features.identity.models.account_linking import EcosystemAccountLink
from nexuspilot_api.features.memory.services.product_memory import MemoryFailure, MemoryService
from nexuspilot_api.features.memory.services.sharing import resolve_share_target


async def automatic_source(service):
    """Require a live association and the exact consent revision before reading Assistant memory."""
    if service.entry != "native" or not (await service.settings())["enabled"]:
        return None
    try:
        owner, _, link_id = await resolve_share_target(service, "personal_assistant")
    except MemoryFailure as error:
        if str(error) == "memory_share_target_unavailable":
            return None
        raise
    link = (
        await service.db.get(EcosystemAccountLink, link_id, populate_existing=True)
        if link_id
        else None
    )
    source = MemoryService(service.db, service.storage, owner, "personal_assistant")
    scope = await source.scope(None)
    expected = {
        "owner": service.owner,
        "link_id": link_id,
        "link_version": link.version if link else None,
    }
    if not scope or not scope.enabled or scope.native_use_grant != expected:
        return None
    return source


async def query_automatic(service, *, query="", offset=0, limit=10):
    """Return a bounded relevant page under explicit consent, not management access alone."""
    source = await automatic_source(service)
    if source is None:
        return {"items": [], "next_offset": None}
    page = await source.query(
        automatic=True, for_model=True, query=query, offset=offset, limit=limit
    )
    for item in page["items"]:
        item["source_entry"] = "personal_assistant"
    return page

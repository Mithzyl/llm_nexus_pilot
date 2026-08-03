"""Knowledge document, version, and retrieval controllers."""

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from nexuspilot_api.routers.common import (
    CursorCodecDependency,
    DatabaseSessionDependency,
    ObjectStorageDependency,
)
from nexuspilot_api.schemas.knowledge import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentRead,
    KnowledgeDocumentUpdate,
    KnowledgeRetrievalCreate,
    KnowledgeRetrievalRead,
    KnowledgeVersionCreate,
    KnowledgeVersionRead,
)
from nexuspilot_api.schemas.pagination import CursorPage
from nexuspilot_api.services.knowledge_service import (
    add_knowledge_version,
    create_knowledge_document,
    deactivate_knowledge_document,
    get_knowledge_document,
    retrieve_knowledge,
)

router = APIRouter(tags=["knowledge"])


@router.post(
    "/knowledge-documents",
    response_model=KnowledgeDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_knowledge_document(
    payload: KnowledgeDocumentCreate,
    db_session: DatabaseSessionDependency,
) -> KnowledgeDocumentRead:
    """Create one stable Knowledge document identity."""

    return await create_knowledge_document(db_session, payload)


@router.get("/knowledge-documents", response_model=CursorPage[KnowledgeDocumentRead])
async def get_knowledge_documents(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    db_session: DatabaseSessionDependency,
    cursor_codec: CursorCodecDependency,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorPage[KnowledgeDocumentRead]:
    """Page Knowledge documents for one validated owner."""

    from nexuspilot_api.services.knowledge_service import list_knowledge_documents

    return await list_knowledge_documents(
        db_session,
        user_id=user_id,
        codec=cursor_codec,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/knowledge-documents/{document_id}",
    response_model=KnowledgeDocumentRead,
)
async def get_knowledge_document_by_id(
    document_id: str,
    db_session: DatabaseSessionDependency,
) -> KnowledgeDocumentRead:
    """Return one Knowledge document."""

    return await get_knowledge_document(db_session, document_id)


@router.post(
    "/knowledge-documents/{document_id}/versions",
    response_model=KnowledgeVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_knowledge_version(
    document_id: str,
    payload: KnowledgeVersionCreate,
    response: Response,
    db_session: DatabaseSessionDependency,
    object_storage: ObjectStorageDependency,
) -> KnowledgeVersionRead:
    """Extract, chunk, and activate one new Knowledge version."""

    version = await add_knowledge_version(
        db_session, object_storage, document_id, payload
    )
    return version


@router.patch(
    "/knowledge-documents/{document_id}",
    response_model=KnowledgeDocumentRead,
)
async def patch_knowledge_document(
    document_id: str,
    payload: KnowledgeDocumentUpdate,
    db_session: DatabaseSessionDependency,
) -> KnowledgeDocumentRead:
    """Deactivate one document and its current version."""

    return await deactivate_knowledge_document(db_session, document_id, payload)


@router.post(
    "/knowledge-retrievals",
    response_model=KnowledgeRetrievalRead,
    status_code=status.HTTP_201_CREATED,
)
async def post_knowledge_retrieval(
    payload: KnowledgeRetrievalCreate,
    db_session: DatabaseSessionDependency,
) -> KnowledgeRetrievalRead:
    """Run one bounded owner-scoped Knowledge retrieval."""

    return await retrieve_knowledge(db_session, payload)

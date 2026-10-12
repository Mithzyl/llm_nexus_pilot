"""Private browser file API with principal, CSRF, bounded streaming and safe downloads."""

import asyncio
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.identity.dependencies import get_workspace_principal
from nexuspilot_api.features.identity.schemas.authentication import WorkspacePrincipalRead
from nexuspilot_api.features.identity.services.authentication_service import (
    require_owned_login_session,
    validate_authentication_origin,
    validate_login_session_csrf,
)
from nexuspilot_api.routers.common import DatabaseSessionDependency, ObjectStorageDependency
from nexuspilot_api.schemas.user_files import FileUploadCapabilities, UserFileCreate, UserFileRead
from nexuspilot_api.services.user_file_service import (
    begin_upload,
    delete_file,
    file_upload_capabilities,
    finish_upload,
    interrupt_upload,
    list_private_files,
    owned_file,
    read_verified_bytes,
    reserve_file,
)


def disable_file_cache(response: Response) -> None:
    """Prevent browsers and intermediaries from caching private file metadata."""

    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/files", tags=["files"], dependencies=[Depends(disable_file_cache)])


async def file_principal(
    request: Request,
    db: DatabaseSessionDependency,
    principal: Annotated[WorkspacePrincipalRead, Depends(get_workspace_principal)],
):
    """Require session-bound CSRF on every authenticated file mutation, even direct API calls."""

    if (
        request.method not in {"GET", "HEAD"}
        and principal.authentication_method == "browser_session"
    ):
        validate_authentication_origin(request.headers.get("origin"), get_settings())
        session = await require_owned_login_session(
            db, user_id=principal.user_id, login_session_id=principal.login_session_id
        )
        validate_login_session_csrf(
            login_session=session,
            csrf_cookie=request.cookies.get("nexuspilot_csrf"),
            csrf_header=request.headers.get("x-csrf-token"),
        )
    return principal


Principal = Annotated[WorkspacePrincipalRead, Depends(file_principal)]


@router.get("/capabilities", response_model=FileUploadCapabilities)
async def get_file_capabilities(principal: Principal):
    """Expose current format availability only to an authenticated/local workspace principal."""
    return await file_upload_capabilities()


@router.post("", response_model=UserFileRead, status_code=201)
async def post_file(
    payload: UserFileCreate,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    principal: Principal,
):
    """Persist a quota reservation before receiving file bytes; owner is never client supplied."""

    return await reserve_file(db, storage, principal.user_id, payload)


@router.get("", response_model=list[UserFileRead])
async def list_files(db: DatabaseSessionDependency, principal: Principal):
    """List at most 500 live private files so interrupted uploads can be found and removed."""

    return await list_private_files(db, principal.user_id)


@router.get("/{file_id}", response_model=UserFileRead)
async def get_file(file_id: str, db: DatabaseSessionDependency, principal: Principal):
    """Return private lifecycle facts after an owner-filtered lookup."""

    return await owned_file(db, file_id, principal.user_id)


@router.put("/{file_id}/content", response_model=UserFileRead)
async def put_file(
    file_id: str,
    request: Request,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    principal: Principal,
):
    """Accept one bounded raw stream; a ready retry does not reparse or rewrite its objects."""

    file = await begin_upload(db, file_id, principal.user_id)
    if file.status == "ready":
        return file
    try:
        content = bytearray()
        async with asyncio.timeout(60):
            async for chunk in request.stream():
                if len(content) + len(chunk) > file.size_bytes:
                    raise HTTPException(413, "文件超过声明大小。")
                content.extend(chunk)
        return await finish_upload(db, storage, file, bytes(content))
    except TimeoutError as exc:
        await interrupt_upload(db, file_id)
        raise HTTPException(408, "文件接收超时，请重试。") from exc
    except BaseException:
        # Cancellation also leaves an explicit failure; process crashes retain the expiry record.
        await interrupt_upload(db, file_id)
        raise


@router.get("/{file_id}/content")
async def download_file(
    file_id: str,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    principal: Principal,
):
    """Download verified bytes as attachment, never same-origin executable preview content."""

    file = await owned_file(db, file_id, principal.user_id)
    if file.status != "ready":
        raise HTTPException(409, "文件尚不可下载。")
    content = await read_verified_bytes(
        storage, file.storage_uri, file.size_bytes, file.content_hash
    )
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/{file_id}", status_code=204)
async def remove_file(
    file_id: str,
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    principal: Principal,
):
    """Revoke subsequent use and remove both objects; storage failure is retryable deletion."""

    file = await owned_file(db, file_id, principal.user_id)
    await delete_file(db, storage, file)

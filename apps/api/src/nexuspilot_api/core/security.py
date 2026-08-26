"""Simple API-key authentication for the phase-one service boundary."""

import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from nexuspilot_api.core.config import get_settings


async def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Authorize a request using a constant-time comparison against the configured key."""

    expected = get_settings().api_key
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


async def require_internal_api_key(
    x_internal_api_key: Annotated[
        str | None,
        Header(alias="X-Internal-API-Key"),
    ] = None,
) -> None:
    """Authorize internal audit reads with a key separate from the public API key."""

    expected = get_settings().internal_api_key.get_secret_value()
    if x_internal_api_key is None or not secrets.compare_digest(
        x_internal_api_key,
        expected,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing internal API key",
        )

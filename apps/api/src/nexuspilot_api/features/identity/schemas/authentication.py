"""Validated browser authentication requests and public principal projections."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.schemas.base import ApiModel


class BrowserLoginRequest(BaseModel):
    """Validate bounded local-password login input without normalizing the password."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1_024)
    device_name: str | None = Field(default=None, min_length=1, max_length=128)


class PasswordChangeRequest(BaseModel):
    """Validate the current and replacement password without logging either value."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1_024)
    new_password: str = Field(min_length=12, max_length=1_024)


class AuthenticatedPrincipalRead(BaseModel):
    """Expose only the stable final-user identity needed by browser clients."""

    user_id: str
    display_name: str
    authentication_method: Literal["browser_session"] = "browser_session"
    login_session_id: str
    scopes: list[str]


class UserLoginSessionRead(ApiModel):
    """Expose one revocable browser session without either session secret."""

    login_session_id: str
    device_name: str | None
    authenticated_at: datetime
    last_used_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None
    is_current: bool = False

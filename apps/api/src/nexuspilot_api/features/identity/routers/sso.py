"""Browser-approved codes and confidential App exchange; no user passwords cross applications."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.identity.services.authentication_service import (
    resolve_browser_principal,
)
from nexuspilot_api.features.identity.services.sso import (
    approved_client,
    complete_registration,
    exchange_code,
    issue_code,
)
from nexuspilot_api.routers.common import DatabaseSessionDependency

browser_router = APIRouter(tags=["application-login"])
client_router = APIRouter(prefix="/app", tags=["application-login-exchange"])


@browser_router.get("/me/application-login")
async def preview_application_login(
    application_id: str, redirect_uri: str, response: Response, db: DatabaseSessionDependency
):
    """Return only an approved destination for the user's explicit confirmation screen."""
    await approved_client(db, application_id, redirect_uri)
    response.headers["Cache-Control"] = "no-store"
    return {
        "application_id": application_id,
        "redirect_uri": redirect_uri,
        "name": {"personal_assistant": "个人助手", "retail": "零售助手"}.get(
            application_id, application_id
        ),
    }


class AuthorizationRequest(BaseModel):
    """Request explicit logged-in consent for one exact client callback and S256 challenge."""

    model_config = ConfigDict(extra="forbid")
    application_id: str = Field(min_length=1, max_length=64)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    code_challenge: str = Field(min_length=43, max_length=43)
    state: str = Field(min_length=16, max_length=256)


class CodeExchange(BaseModel):
    """Bound body parameters; the application identity is supplied by server credentials."""

    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=32, max_length=128)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    code_verifier: str = Field(min_length=43, max_length=128)


class RegistrationCompletion(BaseModel):
    """Complete only a user-authorized new App account, not a preexisting account merge."""

    model_config = ConfigDict(extra="forbid")
    registration_ticket: str = Field(min_length=32, max_length=128)
    external_user_id: str = Field(min_length=1, max_length=128)


async def authenticate_login_client(request: Request, db: DatabaseSessionDependency):
    """Authenticate a configured App without resolving a caller-supplied external user."""
    if request.cookies.get("nexuspilot_session"):
        raise HTTPException(400, "Browser and application credentials cannot be combined")
    app_id = request.headers.get("x-application-id", "")
    config = await approved_client(db, app_id)
    if not secrets.compare_digest(
        request.headers.get("authorization", "").encode(),
        ("Bearer " + config.inbound_key.get_secret_value()).encode(),
    ):
        raise HTTPException(401, "Invalid application credential")
    return app_id


@browser_router.post("/me/application-login")
async def authorize_application(
    payload: AuthorizationRequest,
    request: Request,
    response: Response,
    db: DatabaseSessionDependency,
):
    """Issue a code only after resource middleware checks the browser session and CSRF."""
    principal = await resolve_browser_principal(
        db,
        session_token=request.cookies.get("nexuspilot_session"),
        authorization_header=request.headers.get("authorization"),
        settings=get_settings(),
    )
    code = await issue_code(
        db,
        owner=principal.user_id,
        session_id=principal.login_session_id,
        app_id=payload.application_id,
        redirect_uri=payload.redirect_uri,
        challenge=payload.code_challenge,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"code": code, "state": payload.state, "redirect_uri": payload.redirect_uri}


@client_router.post("/exchange")
async def exchange_application_code(
    payload: CodeExchange,
    response: Response,
    db: DatabaseSessionDependency,
    application_id: Annotated[str, Depends(authenticate_login_client)],
):
    """Consume a code; App creates its own session from the returned identity."""
    response.headers["Cache-Control"] = "no-store"
    return await exchange_code(
        db,
        app_id=application_id,
        code=payload.code,
        redirect_uri=payload.redirect_uri,
        verifier=payload.code_verifier,
    )


@client_router.post("/complete-registration")
async def complete_application_registration(
    payload: RegistrationCompletion,
    response: Response,
    db: DatabaseSessionDependency,
    application_id: Annotated[str, Depends(authenticate_login_client)],
):
    """Use the exchange receipt to associate a new App user with the approved Nexus subject."""
    response.headers["Cache-Control"] = "no-store"
    return await complete_registration(
        db,
        app_id=application_id,
        ticket=payload.registration_ticket,
        external_user_id=payload.external_user_id,
    )

"""Static read-only App operations and private deployment configuration for the existing loop."""

import json
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.errors import ResourceNotFoundError
from nexuspilot_api.features.identity.models.applications import (
    Application,
    ApplicationUserMapping,
    RunApplicationContext,
)
from nexuspilot_api.models import LlmRun, User
from nexuspilot_api.services.tool_catalog import RegisteredTool


class SearchArguments(BaseModel):
    """Accept only a bounded business query, never target or user identity."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=1024)


class ProductArguments(BaseModel):
    """Identify one product; the business backend still checks its visibility."""

    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(min_length=1, max_length=128)


class CalendarArguments(BaseModel):
    """Bound a calendar query to an ordered date interval."""

    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date
    limit: int = Field(default=10, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def validate_interval(self):
        """Reject reversed or excessive intervals before the business request."""
        if not 0 <= (self.end_date - self.start_date).days <= 366:
            raise ValueError("Invalid calendar interval")
        return self


class ListArguments(BaseModel):
    """Request a bounded first page of task summaries."""

    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=10, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=1024)


class BusinessReadResult(BaseModel):
    """Require a version-one read envelope; item fields remain business-owned JSON."""

    model_config = ConfigDict(extra="forbid")
    items: list[dict] = Field(max_length=20)
    next_cursor: str | None = Field(default=None, max_length=1024)


def registered_read(name, description, arguments):
    """Create a trusted read-only version using the existing RegisteredTool type."""
    owner = "retail" if name.startswith("retail_") else "personal_assistant"
    return RegisteredTool(
        name=name,
        version="1.0.0",
        description=description,
        arguments=arguments,
        output=BusinessReadResult,
        required_capabilities=frozenset({"app_read"}),
        timeout_seconds=10,
        max_result_bytes=65536,
        execution_type="remote_http",
        owner_application_id=owner,
        canonical_name=owner + "." + name.removeprefix(owner + "_"),
    )


STATIC_APP_TOOLS = {
    "retail": (
        registered_read(
            "retail_search_products",
            "Search products visible to the current user.",
            SearchArguments,
        ),
        registered_read(
            "retail_get_product_details", "Read one visible product's details.", ProductArguments
        ),
        registered_read(
            "retail_get_product_availability",
            "Read product availability for the current user.",
            ProductArguments,
        ),
    ),
    "personal_assistant": (
        registered_read(
            "personal_assistant_list_calendar_events",
            "Read the user's calendar interval.",
            CalendarArguments,
        ),
        registered_read(
            "personal_assistant_list_tasks", "Read the user's task summaries.", ListArguments
        ),
        registered_read(
            "personal_assistant_search_notes", "Search the user's own notes.", SearchArguments
        ),
    ),
}


class ApplicationToolConfiguration(BaseModel):
    """Keep service authentication and exact operation paths out of model definitions."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    inbound_key: SecretStr = Field(min_length=32)
    service_token: SecretStr = Field(min_length=16)
    base_url: str
    operations: dict[str, str]
    allow_local_http: bool = False
    login_redirect_uris: list[str] = Field(default_factory=list, max_length=10)


def application_tool_configurations():
    """Load only administrator-selected static operations; fail closed without echoing secrets."""
    from nexuspilot_api.services.remote_tool_adapter import RemoteToolAdapter

    try:
        raw = json.loads(get_settings().app_tools_json.get_secret_value())
        configs = {}
        for app_id, value in raw.items():
            config = ApplicationToolConfiguration.model_validate(value)
            if config.inbound_key.get_secret_value().startswith("np_"):
                raise ValueError("Application credentials cannot use the personal key prefix")
            allowed = {t.name for t in STATIC_APP_TOOLS[app_id]}
            if not config.operations or not set(config.operations) <= allowed:
                raise ValueError("Unknown operation")
            if any(not path.startswith("/") or path.startswith("//") or ".." in path
                   or any(c in path for c in "?#\\\r\n") for path in config.operations.values()):
                raise ValueError("Invalid static operation path")
            if config.allow_local_http and get_settings().environment != "development":
                raise ValueError("Local HTTP requires development environment")
            RemoteToolAdapter(
                base_url=config.base_url,
                token=config.service_token.get_secret_value(),
                allow_local_http=config.allow_local_http,
            )
            configs[app_id] = config
        keys = [c.inbound_key.get_secret_value() for c in configs.values()]
        if len(set(keys)) != len(keys):
            raise ValueError("Applications require distinct credentials")
        return configs
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Invalid private App tool configuration") from None


async def authorized_application_context(db, run_id):
    """Resolve the persisted owner and current enabled app; absence denotes a native Run."""
    context = await db.get(RunApplicationContext, run_id, populate_existing=True)
    if context is None:
        return None
    mapping = await db.get(ApplicationUserMapping, context.mapping_id, populate_existing=True)
    run = await db.get(LlmRun, run_id, populate_existing=True)
    app = (
        await db.get(Application, mapping.application_id, populate_existing=True)
        if mapping
        else None
    )
    owner = (
        await db.get(User, mapping.pilot_principal_id, populate_existing=True) if mapping else None
    )
    if (
        mapping is None
        or not mapping.is_enabled
        or app is None
        or not app.is_enabled
        or owner is None
        or not owner.is_active
        or run is None
        or run.user_id != mapping.pilot_principal_id
    ):
        raise ResourceNotFoundError("Application execution")
    config = application_tool_configurations().get(mapping.application_id)
    if config is None:
        raise ResourceNotFoundError("Application execution")
    return context, mapping, config

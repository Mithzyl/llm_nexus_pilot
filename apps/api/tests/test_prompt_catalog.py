"""Prompt Template and Model Catalog behavior tests."""

import httpx

INTERNAL_HEADERS = {"X-Internal-API-Key": "test-internal-key-long-enough"}


async def create_prompt_template(
    client: httpx.AsyncClient,
    *,
    template_name: str,
    content_text: str,
    variable_schema: dict[str, str],
) -> httpx.Response:
    """Create one Prompt template through the internal management contract."""

    return await client.post(
        "/api/v1/internal/prompt-templates",
        headers=INTERNAL_HEADERS,
        json={
            "template_name": template_name,
            "content_text": content_text,
            "variable_schema": variable_schema,
            "created_by_actor_id": "prompt-test",
        },
    )


async def test_prompt_template_rejects_invalid_variable_contracts(
    client: httpx.AsyncClient,
) -> None:
    """Verify schema names, placeholder names, and supported value types agree."""

    missing_schema = await create_prompt_template(
        client,
        template_name="missing-schema",
        content_text="Hello {{ name }}",
        variable_schema={},
    )
    missing_placeholder = await create_prompt_template(
        client,
        template_name="missing-placeholder",
        content_text="Hello",
        variable_schema={"name": "string"},
    )
    unsupported_type = await create_prompt_template(
        client,
        template_name="unsupported-type",
        content_text="Hello {{ name }}",
        variable_schema={"name": "object"},
    )

    assert missing_schema.status_code == 422
    assert missing_placeholder.status_code == 422
    assert unsupported_type.status_code == 422


async def test_prompt_render_validates_and_formats_typed_variables(
    client: httpx.AsyncClient,
) -> None:
    """Verify rendering enforces declared scalar types and stable formatting."""

    created = await create_prompt_template(
        client,
        template_name="typed-prompt",
        content_text="{{ name }} has {{ count }} items; enabled={{ enabled }}",
        variable_schema={
            "name": "string",
            "count": "integer",
            "enabled": "boolean",
        },
    )
    assert created.status_code == 200

    rendered = await client.post(
        "/api/v1/prompt-renders",
        json={
            "template_name": "typed-prompt",
            "variables": {"name": "Nexus", "count": 3, "enabled": True},
        },
    )
    wrong_type = await client.post(
        "/api/v1/prompt-renders",
        json={
            "template_name": "typed-prompt",
            "variables": {"name": "Nexus", "count": "3", "enabled": True},
        },
    )

    assert rendered.status_code == 200
    assert rendered.json()["rendered_text"] == "Nexus has 3 items; enabled=true"
    assert wrong_type.status_code == 422


async def test_prompt_versions_remain_monotonic_after_active_version_rollback(
    client: httpx.AsyncClient,
) -> None:
    """Verify adding a version uses the latest immutable version, not current pointer."""

    created = await create_prompt_template(
        client,
        template_name="versioned-prompt",
        content_text="Version one",
        variable_schema={},
    )
    assert created.status_code == 200
    second = await client.post(
        "/api/v1/internal/prompt-templates/versioned-prompt/versions",
        headers=INTERNAL_HEADERS,
        json={
            "content_text": "Version two",
            "variable_schema": {},
            "created_by_actor_id": "prompt-test",
        },
    )
    assert second.status_code == 200
    rollback = await client.patch(
        "/api/v1/internal/prompt-templates/versioned-prompt/active-version",
        headers=INTERNAL_HEADERS,
        json={"version_number": 1, "actor_id": "prompt-test"},
    )
    assert rollback.status_code == 200

    third = await client.post(
        "/api/v1/internal/prompt-templates/versioned-prompt/versions",
        headers=INTERNAL_HEADERS,
        json={
            "content_text": "Version three",
            "variable_schema": {},
            "created_by_actor_id": "prompt-test",
        },
    )

    assert third.status_code == 200
    assert third.json()["current_version_number"] == 3
    assert third.json()["content_text"] == "Version three"


async def test_disabled_prompt_cannot_render(client: httpx.AsyncClient) -> None:
    """Verify an explicit Prompt status transition blocks subsequent rendering."""

    created = await create_prompt_template(
        client,
        template_name="disabled-prompt",
        content_text="Disabled later",
        variable_schema={},
    )
    assert created.status_code == 200
    disabled = await client.patch(
        "/api/v1/internal/prompt-templates/disabled-prompt/status",
        headers=INTERNAL_HEADERS,
        json={"status": "disabled", "actor_id": "prompt-test"},
    )
    rendered = await client.post(
        "/api/v1/prompt-renders",
        json={"template_name": "disabled-prompt", "variables": {}},
    )

    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert rendered.status_code == 409


async def test_prompt_options_expose_only_enabled_active_versions(
    client: httpx.AsyncClient,
) -> None:
    """Verify product selectors receive active schemas without Prompt source text."""

    enabled = await create_prompt_template(
        client,
        template_name="enabled-product-prompt",
        content_text="Explain {{ topic }} for {{ audience }}",
        variable_schema={"topic": "string", "audience": "string"},
    )
    disabled = await create_prompt_template(
        client,
        template_name="disabled-product-prompt",
        content_text="Do not expose this body",
        variable_schema={},
    )
    assert enabled.status_code == 200
    assert disabled.status_code == 200
    assert (
        await client.patch(
            "/api/v1/internal/prompt-templates/disabled-product-prompt/status",
            headers=INTERNAL_HEADERS,
            json={"status": "disabled", "actor_id": "prompt-test"},
        )
    ).status_code == 200

    response = await client.get("/api/v1/prompt-templates")

    assert response.status_code == 200
    assert response.json() == [
        {
            "template_name": "enabled-product-prompt",
            "purpose": None,
            "current_version_number": 1,
            "variable_schema": {"topic": "string", "audience": "string"},
        }
    ]
    assert "content_text" not in response.text


async def test_model_catalog_returns_latest_enabled_snapshot_by_creation_order(
    client: httpx.AsyncClient,
) -> None:
    """Verify non-lexical versions and disabled snapshots resolve deterministically."""

    created_entries = []
    for catalog_version in ["2", "10"]:
        response = await client.post(
            "/api/v1/internal/model-catalog-versions",
            headers=INTERNAL_HEADERS,
            json={
                "provider": "openai",
                "model": "catalog-model",
                "catalog_version": catalog_version,
                "source": "catalog-test",
                "context_window": 8_192,
                "tokenizer_name": "utf8_bytes_upper_bound",
                "tokenizer_version": "v1",
            },
        )
        assert response.status_code == 200
        created_entries.append(response.json())

    latest = await client.get(
        "/api/v1/model-capabilities",
        params={"provider": "openai", "model": "catalog-model"},
    )
    assert latest.status_code == 200
    assert latest.json()["catalog_version"] == "10"

    disabled = await client.patch(
        "/api/v1/internal/model-catalog-versions/"
        f"{created_entries[1]['catalog_version_id']}/status",
        headers=INTERNAL_HEADERS,
        json={"status": "disabled"},
    )
    fallback = await client.get(
        "/api/v1/model-capabilities",
        params={"provider": "openai", "model": "catalog-model"},
    )

    assert disabled.status_code == 200
    assert disabled.json()["known"] is False
    assert fallback.status_code == 200
    assert fallback.json()["catalog_version"] == "2"


async def test_registered_model_bootstrap_is_explicit_idempotent_and_capability_gated(
    client: httpx.AsyncClient,
) -> None:
    """Verify Registry models require explicit persistence and an enabled capability snapshot."""

    empty_directory = await client.get(
        "/api/v1/models", params={"requires_streaming": True}
    )
    first_bootstrap = await client.post(
        "/api/v1/internal/models/bootstrap",
        headers=INTERNAL_HEADERS,
    )
    second_bootstrap = await client.post(
        "/api/v1/internal/models/bootstrap",
        headers=INTERNAL_HEADERS,
    )
    unknown_capability = await client.get(
        "/api/v1/models", params={"requires_streaming": True}
    )

    capability = await client.post(
        "/api/v1/internal/model-catalog-versions",
        headers=INTERNAL_HEADERS,
        json={
            "provider": "openai",
            "model": "test-model",
            "catalog_version": "model-directory-v1",
            "source": "model-directory-test",
            "context_window": 65_536,
            "supports_streaming": True,
            "tokenizer_name": "utf8_bytes_upper_bound",
            "tokenizer_version": "v1",
        },
    )
    available_directory = await client.get(
        "/api/v1/models", params={"requires_streaming": True}
    )

    assert empty_directory.status_code == 200
    assert empty_directory.json() == []
    assert first_bootstrap.status_code == 200
    assert first_bootstrap.json() == {"created_count": 1, "existing_count": 0}
    assert second_bootstrap.status_code == 200
    assert second_bootstrap.json() == {"created_count": 0, "existing_count": 1}
    assert unknown_capability.status_code == 200
    assert unknown_capability.json()[0]["model_identifier"] == "test-model"
    assert unknown_capability.json()[0]["selectable"] is False
    assert unknown_capability.json()[0]["unavailable_reason"] == "capability_unknown"
    assert capability.status_code == 200
    assert available_directory.status_code == 200
    assert available_directory.json()[0]["selectable"] is True
    assert available_directory.json()[0]["unavailable_reason"] is None
    assert available_directory.json()[0]["catalog_version"] == "model-directory-v1"


async def test_registered_model_directory_distinguishes_disablement_and_registry_failures(
    client: httpx.AsyncClient,
) -> None:
    """Verify persisted models expose stable failure reasons without becoming selectable."""

    registered_responses = []
    for payload in [
        {
            "provider": "openai",
            "model_identifier": "not-allowlisted",
            "display_name": "Not allowlisted",
            "sort_order": 20,
        },
        {
            "provider": "deepseek",
            "model_identifier": "provider-unavailable",
            "display_name": "Provider unavailable",
            "sort_order": 10,
        },
    ]:
        response = await client.post(
            "/api/v1/internal/models",
            headers=INTERNAL_HEADERS,
            json=payload,
        )
        assert response.status_code == 200
        registered_responses.append(response.json())

    duplicate = await client.post(
        "/api/v1/internal/models",
        headers=INTERNAL_HEADERS,
        json={
            "provider": "openai",
            "model_identifier": "not-allowlisted",
            "display_name": "Duplicate",
        },
    )
    disabled = await client.patch(
        f"/api/v1/internal/models/{registered_responses[0]['model_id']}",
        headers=INTERNAL_HEADERS,
        json={"status": "disabled"},
    )
    invalid_clear = await client.patch(
        f"/api/v1/internal/models/{registered_responses[0]['model_id']}",
        headers=INTERNAL_HEADERS,
        json={"display_name": None},
    )
    directory = await client.get("/api/v1/models")

    assert duplicate.status_code == 409
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert invalid_clear.status_code == 422
    assert directory.status_code == 200
    assert [entry["model_identifier"] for entry in directory.json()] == [
        "provider-unavailable",
        "not-allowlisted",
    ]
    assert directory.json()[0]["unavailable_reason"] == "provider_unavailable"
    assert directory.json()[1]["unavailable_reason"] == "model_disabled"

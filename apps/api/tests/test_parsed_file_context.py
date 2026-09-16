"""Exercise unified file extraction at the trusted HTTP/Context boundary across providers."""

import json

import pytest
from nexuspilot_models.contracts import ProviderName
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy import select
from test_context_builds import INTERNAL_HEADERS
from test_native_file_context import prepare_native_fixture

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmModelAttempt


async def prepare_parsed_fixture(client, provider_name="deepseek"):
    """Use Run Artifact fixtures, never a production upload or a vision-capable model."""

    payload, provider, _, _ = await prepare_native_fixture(client, policy=False)
    if provider_name != "openai":
        catalog = await client.post(
            "/api/v1/internal/model-catalog-versions",
            headers=INTERNAL_HEADERS,
            json={
                "provider": provider_name,
                "model": "test-model",
                "catalog_version": "parsed",
                "source": "test",
                "context_window": 8192,
                "supports_vision": False,
                "tokenizer_name": "utf8_bytes_upper_bound",
                "tokenizer_version": "v1",
            },
        )
        assert catalog.status_code == 200, catalog.text
    artifact = (
        await client.post(
            f"/api/v1/runs/{payload['run_id']}/artifacts",
            data={"artifact_type": "file-input"},
            files={"file": ("采购.csv", "名称,金额\n班牌,1200\n设备,300\n".encode(), "text/csv")},
        )
    ).json()
    registry = ProviderRegistry()
    registry.register(
        ProviderName(provider_name), provider, allowed_models=frozenset({"test-model"})
    )
    app.dependency_overrides[get_provider_registry] = lambda: registry
    payload.update(
        provider=provider_name,
        context_source_references={
            "parsed_artifacts": [{"artifact_id": artifact["artifact_id"]}],
        },
    )
    return payload, provider, artifact


@pytest.mark.parametrize("provider_name", ["openai", "anthropic", "gemini", "deepseek"])
async def test_all_providers_receive_parsed_tables_not_native_files(
    client, test_database_session_factory, provider_name
):
    """Unified extraction requires no vision policy and persists usable source provenance."""

    payload, provider, artifact = await prepare_parsed_fixture(client, provider_name)
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 200, response.text
    request = provider.requests[0]
    assert not any(message.attachments for message in request.messages)
    assert request.native_input_capabilities is None
    source_message = next(m for m in request.messages if "parsed_file.v1" in m.content)
    snapshot = json.loads(source_message.content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert snapshot["source_hash"] == artifact["content_hash"]
    assert snapshot["blocks"][1]["cells"] == ["班牌", "1200"]
    assert snapshot["reading"]["has_more"] is False
    assert "memory://" not in source_message.content and "minio://" not in source_message.content
    async with test_database_session_factory() as session:
        attempt = await session.scalar(
            select(LlmModelAttempt).where(LlmModelAttempt.run_id == payload["run_id"])
        )
        context_id = attempt.context_build_id
    persisted = (await client.get(f"/api/v1/context-builds/{context_id}")).json()
    assert any(s["source_type"] == "parsed_artifact" for s in persisted["sources"])
    assert source_message.content in [m["content"] for m in persisted["messages"]]


async def test_selected_range_records_unread_content_without_claiming_full_coverage(client):
    """Explicit block selection preserves table row positions and continuation evidence."""

    payload, provider, artifact = await prepare_parsed_fixture(client)
    payload["context_source_references"]["parsed_artifacts"][0].update(
        block_offset=1, block_limit=1
    )
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 200, response.text
    source = next(m for m in provider.requests[0].messages if "parsed_file.v1" in m.content)
    snapshot = json.loads(source.content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert snapshot["reading"]["next_offset"] == 2
    assert snapshot["reading"]["total_blocks"] == 3
    assert snapshot["blocks"][0]["row_number"] == 2
    assert len(snapshot["blocks"]) == 1


@pytest.mark.parametrize("provider_name", ["openai", "anthropic", "gemini", "deepseek"])
async def test_office_result_positions_reach_all_providers_and_context_history(
    client, monkeypatch, provider_name
):
    """Keep legacy Office snapshots compatible across providers without inventing structure."""

    import hashlib

    from nexuspilot_api.infrastructure.office_text_extraction import OFFICE_MEDIA_TYPES
    from nexuspilot_api.schemas.file_parsing import ParsedFileBlock

    payload, provider, _ = await prepare_parsed_fixture(client, provider_name)
    content = b"PK\x03\x04protocol-fixture"
    artifact = (
        await client.post(
            f"/api/v1/runs/{payload['run_id']}/artifacts",
            data={"artifact_type": "file-input"},
            files={"file": ("示例.docx", content, OFFICE_MEDIA_TYPES[0])},
        )
    ).json()

    def extract(source, media_type):
        """Provide fixed executor evidence; real Office decoding is checked in Docker tests."""

        assert source == content
        return {
            "protocol": "office_parse.v1",
            "source_hash": hashlib.sha256(source).hexdigest(),
            "media_type": media_type,
            "parser_version": "office-v1/fixture",
            "status": "partial",
            "warnings": ["Images not extracted"],
            "blocks": [{"block_id": "block:1", "text": "正文", "paragraph_number": 1}],
        }

    monkeypatch.setattr(
        "nexuspilot_api.services.file_parsing_service.extract_office_content", extract
    )
    payload["context_source_references"] = {
        "parsed_artifacts": [{"artifact_id": artifact["artifact_id"]}]
    }
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 200, response.text
    source = next(m for m in provider.requests[0].messages if "parsed_file.v1" in m.content)
    snapshot = json.loads(source.content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert snapshot["blocks"][0]["paragraph_number"] == 1
    assert snapshot["blocks"][0]["line_start"] is None
    assert snapshot["warnings"] == ["Images not extracted"]
    assert "structure" not in snapshot
    # Historical document serialization orders blocks before page and warning metadata.
    legacy_fields = (
        "schema_version", "source_id", "source_hash", "filename", "media_type",
        "parser_name", "parser_version", "extraction_method", "status", "blocks",
        "total_pages", "uncovered_pages", "warnings",
    )
    document = {key: snapshot[key] for key in legacy_fields}
    # Context JSON sorts keys; restore the unchanged block schema's field order too.
    document["blocks"] = [ParsedFileBlock.model_validate(block).model_dump()
                          for block in snapshot["blocks"]]
    assert snapshot["document_hash"] == hashlib.sha256(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    assert not any(m.attachments for m in provider.requests[0].messages)


@pytest.mark.parametrize("failure", ["corrupt", "missing", "mime", "cross_run", "duplicate"])
async def test_invalid_file_references_fail_before_model_call(client, failure):
    """The parsing route does not bypass existing Run scope or trust object metadata blindly."""

    payload, provider, artifact = await prepare_parsed_fixture(client)
    storage = app.dependency_overrides[get_object_storage]()
    content, media_type = storage.objects[artifact["storage_uri"]]
    if failure == "missing":
        storage.objects.pop(artifact["storage_uri"])
    elif failure == "corrupt":
        storage.objects[artifact["storage_uri"]] = (b"x" * len(content), media_type)
    elif failure == "mime":
        storage.objects[artifact["storage_uri"]] = (content, "application/pdf")
    elif failure == "cross_run":
        other_payload, _, _ = await prepare_parsed_fixture_in_other_run(client, payload)
        payload.update(other_payload)
    else:
        payload["context_source_references"]["artifact_ids"] = [artifact["artifact_id"]]
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == (422 if failure == "duplicate" else 409), response.text
    assert not provider.requests


async def prepare_parsed_fixture_in_other_run(client, payload):
    """Create a same-owner different Run to verify scope is narrower than ownership alone."""

    run = (await client.get(f"/api/v1/runs/{payload['run_id']}")).json()
    turn = (
        await client.post(
            f"/api/v1/sessions/{run['session_id']}/turns",
            json={"user_id": "native-owner", "content_text": "second run"},
        )
    ).json()
    return (
        {"run_id": turn["run"]["run_id"], "current_user_message_id": turn["message"]["message_id"]},
        None,
        None,
    )


async def test_new_native_references_are_rejected_with_replacement_hint(client):
    """New contexts cannot bypass unified extraction through the retired native selector."""

    payload, provider, _, _ = await prepare_native_fixture(client)
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 409
    assert "parsed_artifacts" in response.text
    assert not provider.requests


async def test_parsed_snapshot_replays_but_changed_source_cannot_generate(
    client, test_database_session_factory
):
    """History preserves selected text while model dispatch revalidates the fixed original."""

    from nexuspilot_api.core.errors import ResourceConflictError
    from nexuspilot_api.schemas.context_builds import ContextBuildRead
    from nexuspilot_api.services.parsed_artifact_service import validate_parsed_context_sources

    payload, provider, artifact = await prepare_parsed_fixture(client)
    run = (await client.get(f"/api/v1/runs/{payload['run_id']}")).json()
    context_payload = {
        "user_id": "native-owner",
        "session_id": run["session_id"],
        "run_id": run["run_id"],
        "current_user_message_id": payload["current_user_message_id"],
        "provider": "deepseek",
        "model": "test-model",
        "source_references": payload["context_source_references"],
        "idempotency_key": "parsed-snapshot",
    }
    small = await client.post(
        "/api/v1/context-builds", json=context_payload | {"token_budget": 256}
    )
    assert small.status_code == 409
    created = await client.post("/api/v1/context-builds", json=context_payload)
    assert created.status_code == 201, created.text
    storage = app.dependency_overrides[get_object_storage]()
    content, media_type = storage.objects[artifact["storage_uri"]]
    storage.objects[artifact["storage_uri"]] = (b"x" * len(content), media_type)
    replay = await client.post("/api/v1/context-builds", json=context_payload)
    assert replay.status_code == 200
    assert replay.json()["messages"] == created.json()["messages"]
    assert replay.json()["sources"] == created.json()["sources"]
    async with test_database_session_factory() as session:
        with pytest.raises(ResourceConflictError, match="hash"):
            await validate_parsed_context_sources(
                session, storage, ContextBuildRead.model_validate(replay.json())
            )
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 409 and not provider.requests


async def test_large_extraction_requires_range_instead_of_overflowing_mysql_text(client):
    """Existing TEXT storage is not widened; oversized snapshots fail and ranges remain readable."""

    payload, provider, _ = await prepare_parsed_fixture(client)
    artifact = (
        await client.post(
            f"/api/v1/runs/{payload['run_id']}/artifacts",
            data={"artifact_type": "file-input"},
            files={"file": ("long.txt", b"line of text\n" * 1000, "text/plain")},
        )
    ).json()
    reference = {"artifact_id": artifact["artifact_id"]}
    payload["context_source_references"] = {"parsed_artifacts": [reference]}
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 409 and "range" in response.text
    assert not provider.requests
    reference.update(block_offset=950, block_limit=10)
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 200, response.text
    source = next(m for m in provider.requests[0].messages if "parsed_file.v1" in m.content)
    snapshot = json.loads(source.content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert snapshot["blocks"][0]["line_start"] == 951
    assert snapshot["reading"]["next_offset"] == 960


async def test_streaming_uses_same_parsed_input_as_generation(client):
    """The SSE path receives extracted content and produces a normal completed response."""

    from conftest import FakeProvider

    class StreamingFileProvider(FakeProvider):
        """Capture streaming model requests while retaining the existing deterministic events."""

        def __init__(self):
            """Record requests without contacting any external provider."""
            self.requests = []

        async def stream(self, request):
            """Capture input before returning the fixture's real SSE event sequence."""
            self.requests.append(request)
            async for event in super().stream(request):
                yield event

    payload, _, _ = await prepare_parsed_fixture(client)
    provider = StreamingFileProvider()
    registry = ProviderRegistry()
    registry.register(ProviderName.DEEPSEEK, provider, allowed_models=frozenset({"test-model"}))
    app.dependency_overrides[get_provider_registry] = lambda: registry
    response = await client.post("/api/v1/responses", json=payload | {"stream": True})
    assert response.status_code == 200, response.text
    assert "response.completed" in response.text and "streamed answer" in response.text
    assert any("parsed_file.v1" in m.content for m in provider.requests[0].messages)
    assert not any(m.attachments for m in provider.requests[0].messages)

"""Trusted native Artifact inputs use pinned Context evidence, never caller-provided URLs."""

import base64
import hashlib
import json

import httpx
import pytest
from nexuspilot_models.contracts import ProviderName
from nexuspilot_models.registry import ProviderRegistry
from test_context_builds import INTERNAL_HEADERS
from test_responses import CapturingContextProvider
from test_sessions import create_session, create_user

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app

PDF_BYTES = b"%PDF-1.7\nfixture document"
NATIVE_POLICY = {
    "media_types": ["application/pdf", "image/png"],
    "max_file_bytes": 1024,
    "max_request_bytes": 2048,
    "max_files": 2,
    "input_token_reserve_per_file": 1024,
}


async def prepare_native_fixture(client: httpx.AsyncClient, *, policy=True):
    """Create isolated Run ownership, a catalog and a trusted uploaded Artifact."""

    catalog = await client.post(
        "/api/v1/internal/model-catalog-versions",
        headers=INTERNAL_HEADERS,
        json={
            "provider": "openai",
            "model": "test-model",
            "catalog_version": "native-fixture",
            "source": "contract-test",
            "context_window": 8192,
            "supports_vision": True,
            "tokenizer_name": "utf8_bytes_upper_bound",
            "tokenizer_version": "v1",
            "capabilities_json": {"native_input": NATIVE_POLICY} if policy else {},
        },
    )
    assert catalog.status_code == 200, catalog.text
    await create_user(client, "native-owner")
    conversation = await create_session(client, "native-owner")
    turn = (
        await client.post(
            f"/api/v1/sessions/{conversation['session_id']}/turns",
            json={"user_id": "native-owner", "content_text": "Read the attached PDF"},
        )
    ).json()
    artifact = await client.post(
        f"/api/v1/runs/{turn['run']['run_id']}/artifacts",
        data={"artifact_type": "native-fixture"},
        files={"file": ("source.pdf", PDF_BYTES, "application/pdf")},
    )
    assert artifact.status_code == 201
    provider = CapturingContextProvider()
    registry = ProviderRegistry()
    registry.register(ProviderName.OPENAI, provider, allowed_models=frozenset({"test-model"}))
    app.dependency_overrides[get_provider_registry] = lambda: registry
    payload = {
        "run_id": turn["run"]["run_id"],
        "current_user_message_id": turn["message"]["message_id"],
        "provider": "openai",
        "model": "test-model",
        "input": "Read the attached PDF",
        "context_source_references": {"native_artifact_ids": [artifact.json()["artifact_id"]]},
        "idempotency_key": "native-response-fixture",
    }
    return payload, provider, artifact.json(), catalog.json()


async def test_historical_native_manifest_remains_readable_without_new_dispatch(
    client, test_database_session_factory
):
    """Historical manifests remain readable, but new native requests cannot call a provider."""

    payload, provider, artifact, catalog = await prepare_native_fixture(client)
    assert catalog["native_input"]["media_types"] == NATIVE_POLICY["media_types"]
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 409, response.text
    assert "parsed_artifacts" in response.text
    assert not provider.requests
    from nexuspilot_models.contracts import NativeInputCapabilities

    from nexuspilot_api.models import ContextSourceSelectionStatus, LlmContextSource
    from nexuspilot_api.services.native_file_input_service import read_native_artifact

    run = (await client.get(f"/api/v1/runs/{payload['run_id']}")).json()
    context = await client.post(
        "/api/v1/context-builds",
        json={
            "user_id": "native-owner",
            "session_id": run["session_id"],
            "run_id": run["run_id"],
            "current_user_message_id": payload["current_user_message_id"],
            "provider": "openai",
            "model": "test-model",
            "idempotency_key": "legacy-snapshot",
        },
    )
    assert context.status_code == 201, context.text
    context_id = context.json()["context_build_id"]
    async with test_database_session_factory() as session:
        manifest, attachment = await read_native_artifact(
            session,
            app.dependency_overrides[get_object_storage](),
            run_id=run["run_id"],
            artifact_id=artifact["artifact_id"],
            policy=NativeInputCapabilities.model_validate(NATIVE_POLICY),
        )
        # Construct an old stored source directly: production must not create new ones.
        session.add(
            LlmContextSource(
                context_build_id=context_id,
                source_type="native_artifact",
                source_id=artifact["artifact_id"],
                source_version=artifact["content_hash"],
                trust_level="external_untrusted",
                is_required=True,
                message_role="user",
                source_order=99,
                token_estimate=1024,
                selection_status=ContextSourceSelectionStatus.SELECTED,
                content_hash=hashlib.sha256(manifest.model_dump_json().encode()).hexdigest(),
                content_text=manifest.model_dump_json(),
            )
        )
        await session.commit()
    assert base64.b64decode(attachment.data_base64) == PDF_BYTES
    assert attachment.content_hash == artifact["content_hash"]
    evidence = await client.get(f"/api/v1/context-builds/{context_id}")
    native_source = next(
        s for s in evidence.json()["sources"] if s["source_type"] == "native_artifact"
    )
    assert native_source["source_version"] == artifact["content_hash"]
    assert native_source["token_estimate"] >= 1024
    assert attachment.data_base64 not in evidence.text
    assert "minio://" not in evidence.text and "memory://" not in evidence.text
    replay = await client.get(f"/api/v1/context-builds/{context_id}")
    assert replay.json() == evidence.json()


@pytest.mark.parametrize(
    "failure",
    ["unknown_policy", "cross_run", "same_owner_cross_run", "corrupt", "missing", "wrong_mime"],
)
async def test_native_artifact_failures_never_call_provider(client, failure):
    """Unknown capability, unauthorized scope and invalid objects fail before model costs."""

    payload, provider, artifact, _ = await prepare_native_fixture(
        client, policy=failure != "unknown_policy"
    )
    if failure in {"cross_run", "same_owner_cross_run"}:
        owner = "other-native-owner" if failure == "cross_run" else "native-owner"
        if failure == "cross_run":
            await create_user(client, owner)
        conversation = await create_session(client, owner)
        turn = (
            await client.post(
                f"/api/v1/sessions/{conversation['session_id']}/turns",
                json={"user_id": owner, "content_text": "read"},
            )
        ).json()
        payload.update(
            run_id=turn["run"]["run_id"], current_user_message_id=turn["message"]["message_id"]
        )
    if failure in {"corrupt", "missing", "wrong_mime"}:
        storage = app.dependency_overrides[get_object_storage]()
        if failure == "missing":
            storage.objects.pop(artifact["storage_uri"])
        elif failure == "corrupt":
            storage.objects[artifact["storage_uri"]] = (b"x" * len(PDF_BYTES), "application/pdf")
        else:
            storage.objects[artifact["storage_uri"]] = (PDF_BYTES, "image/png")
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 409, response.text
    assert not provider.requests


async def test_http_input_rejects_inline_bytes_and_urls(client):
    """Only verified artifact references may introduce native bytes at the HTTP boundary."""

    payload, provider, _, _ = await prepare_native_fixture(client)
    payload["input"] = [
        {
            "role": "user",
            "content": "read",
            "attachments": [
                {
                    "filename": "source.pdf",
                    "media_type": "application/pdf",
                    "data_base64": base64.b64encode(PDF_BYTES).decode(),
                    "content_hash": hashlib.sha256(PDF_BYTES).hexdigest(),
                }
            ],
        }
    ]
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 422
    assert not provider.requests


async def test_catalog_rejects_invalid_native_input_policy(client):
    """A vision flag alone is not a native policy; malformed policy must not be stored."""

    from nexuspilot_api.schemas.prompt_catalog import ModelCatalogVersionCreate

    with pytest.raises(ValueError):
        ModelCatalogVersionCreate(
            provider="openai",
            model="test",
            catalog_version="test",
            source="test",
            context_window=8192,
            tokenizer_name="utf8_bytes_upper_bound",
            tokenizer_version="v1",
            capabilities_json={"native_input": NATIVE_POLICY},
        )


async def test_legacy_native_integrity_and_empty_selector_hashes_remain_compatible(
    client, test_database_session_factory
):
    """Keep legacy integrity checks and historical hashes for empty file selectors."""

    from nexuspilot_models.contracts import NativeInputCapabilities

    from nexuspilot_api.core.errors import ResourceConflictError
    from nexuspilot_api.schemas.context_builds import ContextBuildCreate
    from nexuspilot_api.services.context_builder_service import _hash_runtime_request
    from nexuspilot_api.services.native_file_input_service import read_native_artifact

    payload, provider, artifact, _ = await prepare_native_fixture(client)
    context_payload = {
        "user_id": "native-owner",
        "provider": "openai",
        "model": "test-model",
        "run_id": payload["run_id"],
        "current_user_message_id": payload["current_user_message_id"],
        "source_references": payload["context_source_references"],
        "idempotency_key": "native-context-pinned",
    }
    run = (await client.get(f"/api/v1/runs/{payload['run_id']}")).json()
    context_payload["session_id"] = run["session_id"]
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as session:
        manifest, _ = await read_native_artifact(
            session,
            storage,
            run_id=payload["run_id"],
            artifact_id=artifact["artifact_id"],
            policy=NativeInputCapabilities.model_validate(NATIVE_POLICY),
        )
    storage.objects[artifact["storage_uri"]] = (b"x" * len(PDF_BYTES), "application/pdf")
    async with test_database_session_factory() as session:
        with pytest.raises(ResourceConflictError, match="integrity"):
            await read_native_artifact(
                session,
                storage,
                run_id=payload["run_id"],
                artifact_id=artifact["artifact_id"],
                policy=NativeInputCapabilities.model_validate(NATIVE_POLICY),
                pinned_manifest=manifest,
            )
    # A fresh Context also cannot turn the changed bytes into successful provider input.
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 409
    assert not provider.requests
    # Adding an empty optional native field must not invalidate legacy idempotency hashes.
    legacy = ContextBuildCreate.model_validate(context_payload | {"source_references": {}})
    old_fields = legacy.model_dump(mode="json", exclude={"idempotency_key"})
    old_fields["source_references"].pop("native_artifact_ids", None)
    old_fields["source_references"].pop("parsed_artifacts", None)
    old_hash = hashlib.sha256(
        json.dumps(old_fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    assert _hash_runtime_request(legacy) == old_hash

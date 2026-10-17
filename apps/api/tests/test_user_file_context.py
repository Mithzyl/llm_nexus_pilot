"""Verify actual private-upload binding and immutable parse reuse at the Responses boundary."""

import pytest
from test_parsed_file_context import prepare_parsed_fixture
from test_user_files import reserve_file


async def test_uploaded_file_is_bound_to_turn_and_automatically_enters_context(client, monkeypatch):
    """Saved upload manifests reach calls without unread text; deletion still revokes reuse."""

    first = await reserve_file(client)
    file_id = first.json()["file_id"]
    uploaded = await client.put(f"/api/v1/files/{file_id}/content", content=b"hello file")
    assert uploaded.status_code == 200, uploaded.text
    payload, provider, _ = await prepare_parsed_fixture(client)
    principal = (await client.get("/api/v1/auth/me")).json()
    conversation = (
        await client.post(
            "/api/v1/sessions",
            json={
                "user_id": principal["user_id"],
                "title": "File conversation",
            },
        )
    ).json()
    turn = await client.post(
        f"/api/v1/sessions/{conversation['session_id']}/turns",
        json={
            "user_id": principal["user_id"],
            "content_text": "Read this file",
            "file_ids": [file_id],
            "idempotency_key": "file-turn-001",
        },
    )
    assert turn.status_code == 201, turn.text
    assert turn.json()["message"]["metadata_json"]["files"][0]["file_id"] == file_id

    def reject_reparse(*_):
        """Fail if a model call reparses a file already validated during upload."""
        raise AssertionError("must reuse immutable parsed result")

    monkeypatch.setattr(
        "nexuspilot_api.services.parsed_artifact_service.parse_file_content", reject_reparse
    )
    payload.update(
        run_id=turn.json()["run"]["run_id"],
        current_user_message_id=turn.json()["message"]["message_id"],
        context_source_references={},
        input="Read this file",
    )
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 200, response.text
    assert all("hello file" not in message.content for message in provider.requests[-1].messages)
    assert any("file_manifest.v1" in message.content for message in provider.requests[-1].messages)

    async def other_principal(*_):
        """Represent another authenticated identity at the legacy resource authorization check."""
        from nexuspilot_api.features.identity.schemas.authentication import (
            LocalWorkspacePrincipalRead,
        )

        return LocalWorkspacePrincipalRead(
            user_id="different-owner",
            display_name="Other",
            authentication_method="local_deployment",
            scopes=[],
        )

    with monkeypatch.context() as private_access:
        private_access.setattr("legacy_file_access.get_workspace_principal", other_principal)
        assert (await client.get(f"/api/v1/artifacts/{file_id}/content")).status_code == 404
        assert (await client.post("/api/v1/responses", json=payload)).status_code == 404
        assert (
            await client.post(
                f"/api/v1/runs/{payload['run_id']}/agent-workflows",
                json={"run_id": "different-run"},
            )
        ).status_code == 404
    assert (await client.delete(f"/api/v1/files/{file_id}")).status_code == 204
    payload["idempotency_key"] = "deleted-file-response"
    assert (await client.post("/api/v1/responses", json=payload)).status_code == 409


@pytest.mark.parametrize("followup", [False, True])
async def test_agent_nodes_all_receive_bound_file_without_new_file_agent(
    client, test_database_session_factory, followup
):
    """All Agent nodes receive file metadata, not unread text, on first and subsequent turns."""
    from test_agent_workflows import (
        ScriptedAgentProvider,
        registered_agent_test_model,
        scripted_registry,
        workflow_payload,
    )
    from test_context_builds import create_model_capability

    from nexuspilot_api.core.dependencies import get_provider_registry
    from nexuspilot_api.main import app

    await create_model_capability(client, context_window=65536)
    async with test_database_session_factory() as db:
        db.add(registered_agent_test_model())
        await db.commit()
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    file = (await reserve_file(client)).json()
    assert (
        await client.put(f"/api/v1/files/{file['file_id']}/content", content=b"hello file")
    ).status_code == 200
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    turn = await client.post(
        f"/api/v1/sessions/{session['session_id']}/turns",
        json={"user_id": owner, "content_text": "Read this file", "file_ids": [file["file_id"]]},
    )
    assert turn.status_code == 201, turn.text
    if followup:
        turn = await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={"user_id": owner, "content_text": "Continue discussing the file"},
        )
        assert turn.status_code == 201, turn.text
    response = await client.post(
        f"/api/v1/runs/{turn.json()['run']['run_id']}/agent-workflows", json=workflow_payload()
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    assert len(provider.requests) >= 4
    assert all(
        any("file_manifest.v1" in message.content for message in request.messages)
        for request in provider.requests
    )
    assert all(
        "hello file" not in message.content
        for request in provider.requests
        for message in request.messages
    )

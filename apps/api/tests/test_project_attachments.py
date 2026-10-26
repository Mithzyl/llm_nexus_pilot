"""Verify selected project attachments and lazy followup reads with fixed Providers."""

import pytest
from test_context_builds import create_model_capability
from test_project_workspace import create_project
from test_user_files import reserve_file


async def saved_project_file(
    client, project, content=b"UNIQUE_FILE_TEXT_2718", filename="selected.txt"
):
    """Save an uploaded fixture into an independent project snapshot."""
    source = (await reserve_file(client, content, filename)).json()
    uploaded = await client.put(f"/api/v1/files/{source['file_id']}/content", content=content)
    assert uploaded.status_code == 200, uploaded.text
    saved = await client.post(
        f"/api/v1/projects/{project}/files",
        json={
            "file_id": source["file_id"],
            "filename": filename,
            "idempotency_key": f"save-{filename}",
        },
    )
    assert saved.status_code == 201, saved.text
    return source, saved.json()


async def project_session(client, project, key="project-session-attachment"):
    """Create a project-scoped conversation without starting execution."""
    response = await client.post(
        f"/api/v1/projects/{project}/sessions", json={"idempotency_key": key}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def prepare_attachment(client, project, saved, session, key="project-attachment-key"):
    """Request one selected snapshot, not the full project directory."""
    return await client.post(
        f"/api/v1/projects/{project}/files/{saved['project_file_id']}/attachments",
        json={
            "session_id": session["session_id"],
            "idempotency_key": key,
        },
    )


@pytest.mark.parametrize("followup", [False, True])
async def test_selected_project_copy_uses_existing_lazy_file_loop(
    resource_client, monkeypatch, followup
):
    """Offer only the selected copy; continued replies reuse its parse through existing tools."""
    from test_agent_workflows import scripted_registry
    from test_response_file_tools import QuickFileProvider

    from nexuspilot_api.core.dependencies import get_provider_registry
    from nexuspilot_api.main import app

    client = resource_client
    await create_model_capability(client, context_window=65536)
    project = await create_project(client)
    original, saved = await saved_project_file(client, project)
    await saved_project_file(client, project, b"UNSELECTED_PROJECT_SECRET", "not-selected.txt")
    session = await project_session(client, project)
    # A saved snapshot remains usable after explicit deletion of its original upload.
    assert (await client.delete(f"/api/v1/files/{original['file_id']}")).status_code == 204
    prepared = await prepare_attachment(client, project, saved, session)
    assert prepared.status_code == 201, prepared.text
    attachment = prepared.json()
    repeated = await prepare_attachment(client, project, saved, session)
    assert repeated.json()["file_id"] == attachment["file_id"]
    assert (
        attachment["parse_summary"]["project_attachment"]["project_file_id"]
        == saved["project_file_id"]
    )
    first = await client.post(
        f"/api/v1/sessions/{session['session_id']}/turns",
        json={
            "user_id": session["user_id"],
            "content_text": "Read selected file",
            "file_ids": [attachment["file_id"]],
            "idempotency_key": "selected-project-turn",
        },
    )
    assert first.status_code == 201, first.text
    turn = first.json()
    assert (
        turn["message"]["metadata_json"]["files"][0]["project_attachment"]["project_id"] == project
    )
    if followup:

        def reject_reparse(*args, **kwargs):
            """Followup must reuse the published parse, not fetch and parse the project again."""
            raise AssertionError("Unexpected reparse")

        monkeypatch.setattr(
            "nexuspilot_api.services.user_file_service.parse_file_content", reject_reparse
        )
        response = await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={
                "user_id": session["user_id"],
                "content_text": "Read it again",
            },
        )
        assert response.status_code == 201, response.text
        turn = response.json()
    provider = QuickFileProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        "/api/v1/responses",
        json={
            "run_id": turn["run"]["run_id"],
            "current_user_message_id": turn["message"]["message_id"],
            "provider": "openai",
            "model": "test-model",
            "input": "Read selected file",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["output_text"] == "File answer: UNIQUE_FILE_TEXT_2718"
    assert len(provider.requests) == 3
    assert all(
        "UNSELECTED_PROJECT_SECRET" not in m.content for r in provider.requests for m in r.messages
    )
    assert all("UNIQUE_FILE_TEXT_2718" not in m.content for m in provider.requests[0].messages)
    assert any("file_manifest.v1" in m.content for m in provider.requests[0].messages)


async def test_project_attachment_cannot_bind_another_conversation(resource_client):
    """Reject rebinding to another conversation, including attempts by the same owner."""
    client = resource_client
    project = await create_project(client)
    _, saved = await saved_project_file(client, project)
    session = await project_session(client, project)
    other = await project_session(client, project, "another-project-session")
    attachment = await prepare_attachment(client, project, saved, session)
    assert attachment.status_code == 201, attachment.text
    response = await client.post(
        f"/api/v1/sessions/{other['session_id']}/turns",
        json={
            "user_id": session["user_id"],
            "content_text": "Wrong conversation",
            "file_ids": [attachment.json()["file_id"]],
        },
    )
    assert response.status_code == 409


async def test_project_attachments_reject_foreign_users(
    resource_client, test_database_session_factory, monkeypatch
):
    """Foreign users cannot prepare snapshots or bind prepared files to their own conversation."""
    from test_authentication import login, provision_user

    from nexuspilot_api.core.config import get_settings

    monkeypatch.setenv("NEXUSPILOT_AUTHENTICATION_ENABLED", "true")
    get_settings.cache_clear()
    client = resource_client
    try:
        for owner in ("attach-a", "attach-b"):
            await provision_user(
                test_database_session_factory, user_id=owner, email=f"{owner}@example.test"
            )
        await login(client, email="attach-a@example.test")
        client.headers.update(
            {"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]}
        )
        project = await create_project(client)
        _, saved = await saved_project_file(client, project)
        session = await project_session(client, project)
        prepared = await prepare_attachment(client, project, saved, session)
        assert prepared.status_code == 201, prepared.text
        await login(client, email="attach-b@example.test")
        client.headers.update({"X-CSRF-Token": client.cookies["nexuspilot_csrf"]})
        other_project = await create_project(client)
        other_session = await project_session(client, other_project)
        assert (await prepare_attachment(client, project, saved, other_session)).status_code == 404
        assert (
            await prepare_attachment(client, other_project, saved, other_session)
        ).status_code == 404
        response = await client.post(
            f"/api/v1/sessions/{other_session['session_id']}/turns",
            json={
                "user_id": "attach-b",
                "content_text": "Attempt foreign attachment",
                "file_ids": [prepared.json()["file_id"]],
            },
        )
        assert response.status_code == 404, response.text
    finally:
        get_settings.cache_clear()


async def test_project_attachment_agent_reads_and_reviews_selected_copy(
    resource_client, test_database_session_factory
):
    """The existing general Agent reads a selected snapshot and reviewer supplementary blocks."""
    from test_agent_workflows import registered_agent_test_model, scripted_registry
    from test_general_agent import FileReadingProvider, general_payload

    from nexuspilot_api.core.dependencies import get_provider_registry
    from nexuspilot_api.main import app

    client = resource_client
    await create_model_capability(client, context_window=131072)
    # General Agent bindings require an enabled model registration in addition to capabilities.
    async with test_database_session_factory() as db:
        db.add(registered_agent_test_model())
        await db.commit()
    project = await create_project(client)
    _, saved = await saved_project_file(client, project, b"ORIGINAL_ALPHA\nORIGINAL_BETA\n")
    session = await project_session(client, project)
    prepared = await prepare_attachment(client, project, saved, session)
    assert prepared.status_code == 201, prepared.text
    turn = await client.post(
        f"/api/v1/sessions/{session['session_id']}/turns",
        json={
            "user_id": session["user_id"],
            "content_text": "Compare selected lines",
            "file_ids": [prepared.json()["file_id"]],
        },
    )
    assert turn.status_code == 201, turn.text
    provider = FileReadingProvider(prepared.json()["file_id"])
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = await client.post(
        f"/api/v1/runs/{turn.json()['run']['run_id']}/agent-workflows",
        json=general_payload("always"),
    )
    assert result.status_code == 201, result.text
    assert result.json()["status"] == "completed", result.text
    assert provider.review_read
    assert all("ORIGINAL_ALPHA" not in m.content for m in provider.requests[0].messages)

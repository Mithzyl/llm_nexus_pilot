"""Real local parser + HTTP/context checks with synthetic fixtures and no paid model.

Requires NEXUSPILOT_TEST_FILE_RUNTIME=1, FILE_RUNTIME_TEST_IMAGE and a directory
in NEXUSPILOT_TEST_FILE_FIXTURES from deployments/verification/create_file_fixtures.py.
SQL uses the existing isolated SQLite fixture and object storage is in memory;
this test does not claim MySQL/MinIO or browser acceptance.
"""

import os
import tempfile
import threading
from pathlib import Path

import pytest
from test_agent_workflows import (
    ScriptedAgentProvider,
    registered_agent_test_model,
    scripted_registry,
    workflow_payload,
)
from test_context_builds import create_model_capability
from test_conversation_file_context import assert_same_context
from test_office_runtime_executor import executor as executor
from test_responses import CapturingContextProvider
from test_user_files import reserve_file

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app

pytestmark = pytest.mark.skipif(
    os.environ.get("NEXUSPILOT_TEST_FILE_RUNTIME") != "1"
    or not os.environ.get("NEXUSPILOT_TEST_FILE_FIXTURES"),
    reason="Requires explicit local Docker and synthetic file fixtures",
)


@pytest.fixture
def file_executor(executor, monkeypatch):  # noqa: F811 -- imported pytest fixture
    """Run a dedicated local socket with the pinned test image, then clean it up."""

    image = executor.resolve_image(os.environ.get("FILE_RUNTIME_TEST_IMAGE", executor.IMAGE_TAG))
    with tempfile.TemporaryDirectory(prefix="fc-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "parse.sock")
        with executor.FileRuntimeServer(path, image) as server:
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            monkeypatch.setattr(get_settings(), "file_runtime_socket", path)
            try:
                yield image
            finally:
                server.shutdown()
                worker.join(timeout=130)
                assert not worker.is_alive()


@pytest.mark.parametrize("extension", ["docx", "xlsx", "pptx", "pdf"])
@pytest.mark.parametrize("mode", ["responses", "agent"])
async def test_real_file_upload_first_turn_followup_and_persisted_context(
    resource_client, test_database_session_factory, file_executor, extension, mode, monkeypatch,
):
    """Read real extracted text on both turns and reload exact snapshots in both modes."""

    client = resource_client
    directory = Path(os.environ["NEXUSPILOT_TEST_FILE_FIXTURES"])
    content = (directory / f"sample.{extension}").read_bytes()
    await create_model_capability(client, context_window=65536)
    async with test_database_session_factory() as db:
        db.add(registered_agent_test_model())
        await db.commit()
    provider = CapturingContextProvider() if mode == "responses" else ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    reserved = await reserve_file(client, content, f"sample.{extension}")
    assert reserved.status_code == 201, reserved.text
    file_id = reserved.json()["file_id"]
    uploaded = await client.put(f"/api/v1/files/{file_id}/content", content=content)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["status"] == "ready"
    assert (await client.get(f"/api/v1/files/{file_id}/content")).content == content
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()

    def no_reparse(*args):
        """Uploaded fixed extraction must be reused, not recomputed by each model node."""
        raise AssertionError("Unexpected parse after successful upload")

    monkeypatch.setattr("nexuspilot_api.services.parsed_artifact_service.parse_file_content",
                        no_reparse)
    for number in range(2):
        turn = await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={"user_id": owner, "content_text": "Read the file",
                  **({"file_ids": [file_id]} if number == 0 else {})},
        )
        assert turn.status_code == 201, turn.text
        run_id = turn.json()["run"]["run_id"]
        before = len(provider.requests)
        if mode == "responses":
            response = await client.post("/api/v1/responses", json={
                "run_id": run_id, "current_user_message_id": turn.json()["message"]["message_id"],
                "provider": "openai", "model": "test-model", "input": "Read the file",
            })
            assert response.status_code == 200, response.text
        else:
            response = await client.post(f"/api/v1/runs/{run_id}/agent-workflows",
                                         json=workflow_payload(idempotency_key=f"file-context-{number}"))
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "completed", response.text
        requests = provider.requests[before:]
        assert requests
        assert all(any("FILE_RUNTIME_271828" in message.content
                       and "中文文件读取测试" in message.content
                       for message in request.messages) for request in requests)
        run = (await client.get(f"/api/v1/runs/{run_id}")).json()
        for attempt in run["attempts"]:
            context_id = attempt["context_build_id"]
            context = (await client.get(f"/api/v1/context-builds/{context_id}")).json()
            assert any(source["source_id"] == file_id
                       and source["source_type"] == "parsed_artifact"
                       and source["content_hash"] and source["source_version"]
                       for source in context["sources"])
            assert file_executor in str(context)
            reloaded = await client.get(f"/api/v1/context-builds/{context_id}")
            assert_same_context(reloaded.json(), context)


async def test_real_pdf_partial_pages_and_encrypted_upload_failure(resource_client, file_executor):
    """Preserve blank-page coverage while rejecting encrypted source bytes before ready."""

    from nexuspilot_api.infrastructure.pdf_text_extraction import extract_pdf_pages

    directory = Path(os.environ["NEXUSPILOT_TEST_FILE_FIXTURES"])
    pages, _ = extract_pdf_pages((directory / "sample.pdf").read_bytes())
    assert len(pages) == 3 and not pages[1].strip()
    assert "中文文件读取测试" in pages[0] and "第三页文本" in pages[2]
    content = (directory / "encrypted.pdf").read_bytes()
    file = (await reserve_file(resource_client, content, "encrypted.pdf")).json()
    reply = await resource_client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
    assert reply.status_code == 422, reply.text
    metadata = (await resource_client.get(f"/api/v1/files/{file['file_id']}")).json()
    assert metadata["status"] == "failed"

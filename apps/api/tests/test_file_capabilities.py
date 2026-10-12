"""Check authenticated parser availability and reject unavailable uploads before reservation."""

import pytest
from test_user_files import reserve_file

from nexuspilot_api.core.config import get_settings


async def test_missing_executor_excludes_office_without_disabling_text(
    resource_client, monkeypatch
):
    """An unconfigured parser must not be advertised and must not create failed file records."""
    client = resource_client
    monkeypatch.setattr(get_settings(), "file_runtime_socket", None)
    response = await client.get("/api/v1/files/capabilities")
    assert response.status_code == 200, response.text
    result = response.json()
    assert ".txt" in result["accepted_extensions"]
    assert ".docx" not in result["accepted_extensions"]
    assert ".pdf" not in result["accepted_extensions"]
    assert result["executor_status"] == "not_configured"
    before = (await client.get("/api/v1/files")).json()
    rejected = await reserve_file(client, b"PK fixture", "notes.docx")
    assert rejected.status_code == 503, rejected.text
    assert "尚未启用" in rejected.json()["detail"]
    assert (await client.get("/api/v1/files")).json() == before
    assert (await reserve_file(client)).status_code == 201


@pytest.mark.parametrize("extension", ["docx", "xlsx", "pptx", "pdf"])
async def test_unreachable_executor_rejects_new_reservation(
    resource_client, monkeypatch, tmp_path, extension
):
    """A configured but absent socket is not evidence of usable parsing and exposes no path."""
    client = resource_client
    path = str(tmp_path / "missing.sock")
    monkeypatch.setattr(get_settings(), "file_runtime_socket", path)
    response = await client.get("/api/v1/files/capabilities")
    assert response.json()["executor_status"] == "unavailable"
    assert path not in response.text
    rejected = await reserve_file(client, b"fixture", f"notes.{extension}")
    assert rejected.status_code == 503
    assert path not in rejected.text


async def test_capabilities_require_login_when_authentication_is_enabled(
    resource_client, monkeypatch
):
    """Public parser metadata must still follow the workspace authentication policy."""
    monkeypatch.setattr(get_settings(), "authentication_enabled", True)
    reply = await resource_client.get("/api/v1/files/capabilities")
    assert reply.status_code == 401

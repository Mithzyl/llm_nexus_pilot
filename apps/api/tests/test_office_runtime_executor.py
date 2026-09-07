"""Test socket transport, package rejection and optional actual Docker Office execution.

Set NEXUSPILOT_TEST_FILE_RUNTIME=1 after building the image to run the real
container test. No production documents, database services or Providers are used.
"""

import hashlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path

import pytest

from nexuspilot_api.core.config import Settings, get_settings
from nexuspilot_api.infrastructure.office_text_extraction import (
    OFFICE_MEDIA_TYPES,
    OfficeExtractionError,
    extract_office_content,
)
from nexuspilot_api.schemas.file_parsing import FileParseRequest
from nexuspilot_api.services.file_parsing_service import parse_file_content

RUNTIME_DIRECTORY = Path(__file__).resolve().parents[3] / "deployments" / "file-runtime"


@pytest.fixture
def executor(monkeypatch):
    """Load administrator runtime code without importing Docker or Office libraries into API."""

    monkeypatch.syspath_prepend(str(RUNTIME_DIRECTORY))
    spec = importlib.util.spec_from_file_location(
        "office_executor", RUNTIME_DIRECTORY / "serve_runtime.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def word_fixture(extra_entries=None) -> bytes:
    """Create an independent minimal OOXML fixture using only ZIP/XML, not the parser library."""

    entries = {
        "[Content_Types].xml": """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        "_rels/.rels": """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
Target="word/document.xml"/>
</Relationships>""",
        "word/document.xml": """<w:document
xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>中文 Office 文件</w:t></w:r></w:p></w:body></w:document>""",
    }
    entries.update(extra_entries or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


@pytest.mark.parametrize("invalid", ["path", "binary", "entity", "ratio", "mismatch"])
def test_invalid_packages_rejected_before_library_load(monkeypatch, invalid):
    """Reject ZIP traversal, active content, entities, expansion bombs and format mismatch."""

    spec = importlib.util.spec_from_file_location(
        "office_parser", RUNTIME_DIRECTORY / "parse_office.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    additions = {
        "path": {"../escaped.xml": "bad"},
        "binary": {"word/vbaProject.bin": "macro"},
        "entity": {
            "word/extra.xml": '<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><x>&a;</x>'
        },
        "ratio": {"word/extra.xml": "a" * 100000},
        "mismatch": {},
    }
    content = word_fixture(additions[invalid])
    media_type = OFFICE_MEDIA_TYPES[1] if invalid == "mismatch" else OFFICE_MEDIA_TYPES[0]
    with pytest.raises(ValueError):
        module.validate_package(content, media_type)


@pytest.mark.parametrize("reply", [{"error": "file_executor_busy"}, [], {"error": "unknown"}])
def test_socket_response_errors_are_not_success_or_host_fallback(monkeypatch, reply):
    """Exercise actual Unix framing and safe errors against a tiny local protocol peer."""

    with tempfile.TemporaryDirectory(prefix="of-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "p.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(path)
            server.listen(1)

            def respond():
                """Read one bounded synthetic request and return a controlled protocol failure."""

                connection, _ = server.accept()
                with connection, connection.makefile("rb") as incoming:
                    incoming.readline(1_500_001)
                    connection.sendall(json.dumps(reply).encode() + b"\n")

            worker = threading.Thread(target=respond)
            worker.start()
            monkeypatch.setattr(get_settings(), "file_runtime_socket", path)
            try:
                with pytest.raises(OfficeExtractionError):
                    extract_office_content(b"PK\x03\x04test", OFFICE_MEDIA_TYPES[0])
            finally:
                worker.join(timeout=5)
            assert not worker.is_alive()


def test_executor_refuses_public_directory_and_existing_socket(executor):
    """Socket authorization is enforced by filesystem ownership/modes, never a public TCP port."""

    with tempfile.TemporaryDirectory(prefix="of-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "p.sock")
        os.chmod(temporary, 0o777)
        with pytest.raises(ValueError, match="directory"):
            executor.FileRuntimeServer(path, "unused")
        os.chmod(temporary, 0o700)
        Path(path).write_text("do not replace")
        with pytest.raises(ValueError, match="exists"):
            executor.FileRuntimeServer(path, "unused")
        assert Path(path).read_text() == "do not replace"


def test_opaque_printer_settings_do_not_reject_normal_office_documents():
    """OOXML printer settings are opaque metadata, not executable macros."""

    spec = importlib.util.spec_from_file_location(
        "office_parser", RUNTIME_DIRECTORY / "parse_office.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_package(
        word_fixture({"word/printerSettings/printerSettings1.bin": b"opaque settings"}),
        OFFICE_MEDIA_TYPES[0],
    )


def test_socket_configuration_rejects_relative_paths():
    """Configuration must select a local absolute path rather than a caller-controlled URL."""

    with pytest.raises(ValueError, match="absolute"):
        Settings(_env_file=None, file_runtime_socket="relative.sock")


@pytest.mark.skipif(
    os.environ.get("NEXUSPILOT_TEST_FILE_RUNTIME") != "1",
    reason="Requires explicit real Docker permission and a built Linux file image",
)
def test_real_office_socket_container_and_context_blocks(executor, monkeypatch):
    """Run a source fixture through actual socket, Docker, parser and API result conversion."""

    image_id = executor.resolve_image(os.environ.get("FILE_RUNTIME_TEST_IMAGE", executor.IMAGE_TAG))
    with tempfile.TemporaryDirectory(prefix="of-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "p.sock")
        with executor.FileRuntimeServer(path, image_id) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            monkeypatch.setattr(get_settings(), "file_runtime_socket", path)
            content = word_fixture()
            request = FileParseRequest(
                source_id="00000000-0000-0000-0000-000000000001",
                source_hash=hashlib.sha256(content).hexdigest(),
                filename="示例.docx",
                media_type=OFFICE_MEDIA_TYPES[0],
            )
            try:
                document = parse_file_content(request, content)
                assert document.blocks[0].text == "中文 Office 文件"
                assert document.blocks[0].paragraph_number == 1
                assert image_id in document.parser_version
                with pytest.raises(OfficeExtractionError, match="corrupt"):
                    extract_office_content(b"not a document", OFFICE_MEDIA_TYPES[0])
            finally:
                server.shutdown()
                thread.join(timeout=130)
        assert not Path(path).exists()


def test_timed_out_container_is_removed(executor, monkeypatch):
    """If Docker cannot create in time, remove only the newly generated request container."""

    calls = []

    def run(arguments, **kwargs):
        """Emulate a timeout after Docker may already have created the container."""

        calls.append(arguments)
        if arguments[1] == "create":
            raise subprocess.TimeoutExpired(arguments, 30)
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(executor.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        executor.parse_in_container(b"{}\n", "sha256:" + "a" * 64)
    created = calls[0]
    name = created[created.index("--name") + 1]
    assert calls[-1] == ["docker", "rm", "--force", name]
    assert "--network" in created and created[created.index("--network") + 1] == "none"
    assert not {"--volume", "--mount", "--env", "--env-file"} & set(created)

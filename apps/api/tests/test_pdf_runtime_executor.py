"""Verify PDF transport uses the dedicated executor, never an API-local parser."""

import base64
import hashlib
import json
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.infrastructure.pdf_text_extraction import PdfExtractionError, extract_pdf_pages


def test_pdf_requires_configured_executor_without_host_fallback(monkeypatch):
    """Missing executor configuration must fail before any local subprocess can start."""

    def forbidden(*args, **kwargs):
        """Reject attempts to start Poppler or an OS sandbox in the API process."""
        pytest.fail("PDF attempted an API-local subprocess")

    monkeypatch.setattr(get_settings(), "file_runtime_socket", None)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(PdfExtractionError, match="尚未启用"):
        extract_pdf_pages(b"%PDF-1.7\nfixture")


@pytest.mark.parametrize("failure", [None, "hash", "protocol", "pages", "version", "busy"])
def test_pdf_socket_checks_identity_structure_and_safe_failures(monkeypatch, failure):
    """An actual Unix peer returns pages only when format, source and metadata match."""

    source = b"%PDF-1.7\nfixture"
    received = []
    report = {
        "protocol": "file_parse.v1", "media_type": "application/pdf",
        "source_hash": hashlib.sha256(source).hexdigest(),
        "parser_version": "pdf-v1/poppler-sha256:fixture/sha256:image",
        "pages": ["中文第一页", "", "third page"],
    }
    if failure == "hash":
        report["source_hash"] = "0" * 64
    elif failure == "protocol":
        report["protocol"] = "office_parse.v1"
    elif failure == "pages":
        report["pages"] = [42]
    elif failure == "version":
        report["parser_version"] = ""
    elif failure == "busy":
        report = {"error": "file_executor_busy"}
    with tempfile.TemporaryDirectory(prefix="pdf-", dir="/tmp") as temporary:
        path = str(Path(temporary) / "p.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.settimeout(3)
            server.bind(path)
            server.listen(1)

            def respond():
                """Read exactly the bounded request and return this test's synthetic result."""
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    return
                with connection, connection.makefile("rb") as incoming:
                    received.append(json.loads(incoming.readline(1_500_001)))
                    connection.sendall(json.dumps(report).encode() + b"\n")

            worker = threading.Thread(target=respond)
            worker.start()
            monkeypatch.setattr(get_settings(), "file_runtime_socket", path)
            try:
                if failure:
                    with pytest.raises(PdfExtractionError):
                        extract_pdf_pages(source)
                else:
                    assert extract_pdf_pages(source) == (report["pages"], report["parser_version"])
            finally:
                worker.join(timeout=5)
    assert received == [{
        "protocol": "file_parse.v1", "media_type": "application/pdf",
        "content_base64": base64.b64encode(source).decode(),
    }]

"""Verify Office structure evidence independently of extracted text and parser libraries."""

import hashlib
import json

import pytest

from nexuspilot_api.schemas.file_parsing import FileParseRequest, SlideSelection
from nexuspilot_api.services.file_parsing_service import (
    FileParsingError,
    get_file_outline,
    parse_file_content,
    select_file_blocks,
)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def extracted_document(monkeypatch, media_type, structure):
    """Convert a source-bound executor report, retaining old reports when structure is absent."""

    content = b"PK\x03\x04fixture"
    request = FileParseRequest(
        source_id="00000000-0000-0000-0000-000000000001",
        source_hash=hashlib.sha256(content).hexdigest(),
        filename="document",
        media_type=media_type,
    )
    report = {
        "protocol": "office_parse.v1",
        "source_hash": request.source_hash,
        "media_type": media_type,
        "parser_version": "office-v2/test",
        "status": "partial",
        "warnings": ["Images not interpreted"],
        "blocks": [
            {
                "block_id": "block:1",
                "text": "正文",
                **(
                    {
                        "kind": "table_row",
                        "sheet_name": "数据",
                        "row_number": 1,
                        "cell_range": "A1:A1",
                        "cells": ["正文"],
                    }
                    if media_type == XLSX
                    else {"slide_number": 1}
                ),
            }
        ],
    }
    if structure is not None:
        report["structure"] = structure
    monkeypatch.setattr(
        "nexuspilot_api.services.file_parsing_service.extract_office_content",
        lambda *_: report,
    )
    return parse_file_content(request, content)


def test_outline_keeps_empty_hidden_worksheets_in_source_order(monkeypatch):
    """An empty worksheet is a real source location, not missing from the workbook."""

    structure = {
        "kind": "worksheets",
        "entries": [
            {"name": "空表", "visibility": "hidden", "block_count": 0},
            {"name": "数据", "visibility": "visible", "block_count": 1},
        ],
    }
    document = extracted_document(monkeypatch, XLSX, structure)
    outline = get_file_outline(document)
    assert outline["sheets"] == ["空表", "数据"]
    assert outline["structure"] == structure
    assert outline["structure_complete"] is True
    assert document.status == "partial"
    assert len(document.blocks) == 1


def test_outline_keeps_slides_without_text_and_rejects_out_of_bounds_selection(monkeypatch):
    """A known slide without extracted text is neither an absent slide nor readable content."""

    structure = {
        "kind": "slides",
        "entries": [
            {"number": 1, "block_count": 1},
            {"number": 2, "block_count": 0},
        ],
    }
    document = extracted_document(monkeypatch, PPTX, structure)
    assert get_file_outline(document)["slides"] == [1, 2]
    assert get_file_outline(document)["structure"] == structure
    with pytest.raises(FileParsingError, match="no readable"):
        select_file_blocks(document, SlideSelection(kind="slides", slide_start=2, slide_end=2))
    with pytest.raises(FileParsingError, match="exceeds known"):
        select_file_blocks(document, SlideSelection(kind="slides", slide_start=1, slide_end=3))


def test_legacy_outline_does_not_claim_complete_structure(monkeypatch):
    """Old executor results remain readable without guessing unextracted source locations."""

    outline = get_file_outline(extracted_document(monkeypatch, PPTX, None))
    assert outline["slides"] == [1]
    assert outline["structure_complete"] is False
    assert outline["structure"] is None


@pytest.mark.parametrize(
    "structure",
    [
        {"kind": "slides", "entries": [{"number": 1, "block_count": 1}]},
        {"kind": "worksheets", "entries": []},
        {
            "kind": "worksheets",
            "entries": [{"name": "其他", "visibility": "visible", "block_count": 1}],
        },
        {
            "kind": "worksheets",
            "entries": [{"name": "数据", "visibility": "visible", "block_count": 0}],
        },
        {
            "kind": "worksheets",
            "entries": [{"name": "数据", "visibility": "visible", "block_count": 1}] * 2,
        },
        {
            "kind": "worksheets",
            "entries": [{"name": "数据", "visibility": "invalid", "block_count": 1}],
        },
    ],
)
def test_rejects_inconsistent_executor_structure(monkeypatch, structure):
    """Reject wrong formats, missing locations, false counts and duplicate directory entries."""

    with pytest.raises(FileParsingError):
        extracted_document(monkeypatch, XLSX, structure)


def test_rejects_noncontiguous_slide_directory(monkeypatch):
    """A complete slide inventory cannot silently omit slide two."""

    with pytest.raises(FileParsingError):
        extracted_document(
            monkeypatch,
            PPTX,
            {
                "kind": "slides",
                "entries": [
                    {"number": 1, "block_count": 1},
                    {"number": 3, "block_count": 0},
                ],
            },
        )


async def test_selected_file_context_keeps_complete_inventory(client, monkeypatch):
    """A selected sheet's model context retains empty-sheet evidence and the original inventory."""

    from test_parsed_file_context import prepare_parsed_fixture

    payload, provider, _ = await prepare_parsed_fixture(client)
    structure = {
        "kind": "worksheets",
        "entries": [
            {"name": "数据", "visibility": "visible", "block_count": 1},
            {"name": "空表", "visibility": "hidden", "block_count": 0},
        ],
    }
    extracted_document(monkeypatch, XLSX, structure)
    artifact = await client.post(
        f"/api/v1/runs/{payload['run_id']}/artifacts",
        data={"artifact_type": "file-input"},
        files={"file": ("目录.xlsx", b"PK\x03\x04fixture", XLSX)},
    )
    assert artifact.status_code == 201, artifact.text
    payload["context_source_references"] = {
        "parsed_artifacts": [
            {
                "artifact_id": artifact.json()["artifact_id"],
                "selection": {"kind": "worksheet", "sheet_name": "数据"},
            }
        ]
    }
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 200, response.text
    source = next(m for m in provider.requests[0].messages if "parsed_file.v1" in m.content)
    snapshot = json.loads(source.content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert snapshot["structure"] == structure
    assert snapshot["status"] == "partial"
    assert len(snapshot["blocks"]) == 1

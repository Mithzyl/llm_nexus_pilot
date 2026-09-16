"""Verify native file range selection without upload services, agents or parser processes."""

import hashlib
import json

import pytest
from pydantic import ValidationError
from test_parsed_file_context import prepare_parsed_fixture

from nexuspilot_api.schemas.context_builds import ContextBuildCreate
from nexuspilot_api.schemas.file_parsing import (
    ParsedArtifactReference,
    ParsedFileBlock,
    ParsedFileDocument,
)
from nexuspilot_api.services.context_builder_service import _hash_runtime_request
from nexuspilot_api.services.file_parsing_service import FileParsingError

ARTIFACT_ID = "00000000-0000-0000-0000-000000000001"
WORD_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SHEET_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SLIDE_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def document_fixture(media_type, blocks, **metadata):
    """Build immutable parser evidence with original positions rather than invoke dependencies."""

    return ParsedFileDocument(
        source_id=ARTIFACT_ID,
        source_hash="1" * 64,
        filename="fixture",
        media_type=media_type,
        parser_name="fixture",
        parser_version="1",
        status="partial",
        warnings=["Images were not extracted"],
        blocks=[ParsedFileBlock.model_validate(block) for block in blocks],
        **metadata,
    )


def selected_blocks(document, selection):
    """Exercise the same typed selector consumed by the Context Artifact service."""

    from nexuspilot_api.services.file_parsing_service import select_file_blocks

    reference = ParsedArtifactReference(artifact_id=ARTIFACT_ID, selection=selection)
    return select_file_blocks(document, reference.selection)


@pytest.mark.parametrize(
    "selection",
    [
        {"kind": "pdf_pages", "page_start": 0, "page_end": 1},
        {"kind": "pdf_pages", "page_start": 3, "page_end": 1},
        {"kind": "pdf_pages", "page_start": 1, "page_end": 1, "sheet_name": "x"},
        {"kind": "word_table", "table_number": 0},
        {"kind": "worksheet", "sheet_name": ""},
        {"kind": "worksheet", "sheet_name": "x", "cell_range": "C3:A1"},
        {"kind": "worksheet", "sheet_name": "x", "cell_range": "A0:B2"},
        {"kind": "worksheet", "sheet_name": "x", "cell_range": "XFE1"},
        {"kind": "worksheet", "sheet_name": "x", "cell_range": "A1048577"},
        {"kind": "worksheet", "sheet_name": "x", "cell_range": "y!A1"},
        {"kind": "slides", "slide_start": 2, "slide_end": 1},
        {"kind": "unknown"},
    ],
)
def test_invalid_or_mixed_native_ranges_are_rejected(selection):
    """Reject contradictory, unbounded or cross-format ranges before reading any source."""

    with pytest.raises(ValidationError):
        ParsedArtifactReference(artifact_id=ARTIFACT_ID, selection=selection)


def test_pdf_range_preserves_locations_and_uncovered_page_evidence():
    """Select real PDF pages without rewriting line numbers or hiding missing extraction."""

    document = document_fixture(
        "application/pdf",
        [
            {"block_id": "p1", "text": "first", "page_number": 1, "line_start": 1},
            {"block_id": "p3", "text": "third", "page_number": 3, "line_start": 1},
        ],
        total_pages=3,
        uncovered_pages=[2],
    )
    blocks = selected_blocks(document, {"kind": "pdf_pages", "page_start": 2, "page_end": 3})
    assert [(b.block_id, b.page_number, b.line_start) for b in blocks] == [("p3", 3, 1)]
    assert document.status == "partial" and document.uncovered_pages == [2]
    for start, end in [(2, 2), (1, 4)]:
        with pytest.raises(FileParsingError):
            selected_blocks(document, {"kind": "pdf_pages", "page_start": start, "page_end": end})


def test_word_table_and_powerpoint_slide_selection_do_not_mix_locations():
    """Identical row numbers in unrelated tables or slides never join the selected material."""

    word = document_fixture(
        WORD_TYPE,
        [
            {"block_id": "p", "text": "intro", "paragraph_number": 1},
            {"block_id": "t1", "text": "left", "kind": "table_row", "table_number": 1},
            {"block_id": "t2", "text": "right", "kind": "table_row", "table_number": 2},
        ],
    )
    assert [
        b.block_id for b in selected_blocks(word, {"kind": "word_table", "table_number": 2})
    ] == ["t2"]
    slides = document_fixture(
        SLIDE_TYPE,
        [
            {"block_id": "s1", "text": "first", "slide_number": 1},
            {"block_id": "s2", "text": "second", "slide_number": 2},
            {"block_id": "n2", "text": "notes", "slide_number": 2, "is_notes": True},
        ],
    )
    assert [
        b.block_id
        for b in selected_blocks(slides, {"kind": "slides", "slide_start": 2, "slide_end": 2})
    ] == ["s2", "n2"]
    with pytest.raises(FileParsingError, match="format"):
        selected_blocks(word, {"kind": "slides", "slide_start": 1, "slide_end": 1})
    with pytest.raises(FileParsingError, match="no readable"):
        selected_blocks(word, {"kind": "word_table", "table_number": 9})


def workbook_fixture():
    """Keep two worksheets and all formula metadata to expose inconsistent column slicing."""

    row = {
        "kind": "table_row",
        "row_number": 1,
        "cell_range": "A1:C1",
        "cells": ["", "0012", ""],
        "text": '["", "0012", ""]',
        "cell_formulas": ["=1+1", None, "=2+2"],
        "cell_types": ["f", "s", "f"],
        "cell_number_formats": ["General", "@", "0.00"],
        "missing_formula_values": ["A1", "C1"],
        "merged_ranges": ["A1:B1", "D1:E1"],
        "hidden": True,
    }
    return document_fixture(
        SHEET_TYPE,
        [
            row | {"block_id": "other", "sheet_name": "Other"},
            row | {"block_id": "chosen", "sheet_name": "预算"},
            {
                "block_id": "row2",
                "kind": "table_row",
                "text": '["next"]',
                "sheet_name": "预算",
                "row_number": 2,
                "cell_range": "A2:A2",
                "cells": ["next"],
            },
        ],
    )


def test_worksheet_cell_selection_crops_all_metadata_without_mutating_source():
    """Return exact selected values and formula positions, preserving the original document."""

    document = workbook_fixture()
    before = document.model_dump_json()
    blocks = selected_blocks(
        document, {"kind": "worksheet", "sheet_name": "预算", "cell_range": "B1:C1"}
    )
    assert len(blocks) == 1
    row = blocks[0]
    assert row.block_id == "chosen" and row.sheet_name == "预算"
    assert row.cell_range == "B1:C1" and row.row_number == 1
    assert row.cells == ["0012", ""] and json.loads(row.text) == row.cells
    assert row.cell_formulas == [None, "=2+2"]
    assert row.cell_types == ["s", "f"] and row.cell_number_formats == ["@", "0.00"]
    assert row.missing_formula_values == ["C1"] and row.merged_ranges == ["A1:B1"]
    assert row.hidden is True and row.line_start is None
    assert document.model_dump_json() == before
    whole_sheet = selected_blocks(document, {"kind": "worksheet", "sheet_name": "预算"})
    assert [b.block_id for b in whole_sheet] == ["chosen", "row2"]


def test_worksheet_empty_ranges_and_misaligned_metadata_fail_clearly():
    """A missing worksheet or unusable range must not silently return another table or cells."""

    for selection in [
        {"kind": "worksheet", "sheet_name": "missing"},
        {"kind": "worksheet", "sheet_name": "预算", "cell_range": "D1:E1"},
    ]:
        with pytest.raises(FileParsingError, match="no readable"):
            selected_blocks(workbook_fixture(), selection)
    document = workbook_fixture()
    document.blocks[1].cell_types = ["s"]
    with pytest.raises(FileParsingError, match="metadata"):
        selected_blocks(document, {"kind": "worksheet", "sheet_name": "预算", "cell_range": "B1"})


def test_no_native_selection_retains_historical_context_request_hash():
    """Adding an optional selector cannot invalidate saved idempotency hashes for old requests."""

    payload = ContextBuildCreate(
        user_id="fixture",
        session_id=ARTIFACT_ID,
        provider="deepseek",
        model="test",
        run_id=ARTIFACT_ID,
        current_user_message_id=ARTIFACT_ID,
        idempotency_key="test-hash",
        source_references={"parsed_artifacts": [{"artifact_id": ARTIFACT_ID}]},
    )
    old_request = payload.model_dump(mode="json", exclude={"idempotency_key"})
    old_request["source_references"].pop("native_artifact_ids")
    old_request["source_references"]["parsed_artifacts"][0].pop("selection", None)
    expected = hashlib.sha256(
        json.dumps(old_request, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    assert _hash_runtime_request(payload) == expected


def test_equivalent_cell_coordinates_have_one_canonical_selection():
    """Case and single-cell syntax cannot create conflicting identities for the same range."""

    first = ParsedArtifactReference(
        artifact_id=ARTIFACT_ID,
        selection={"kind": "worksheet", "sheet_name": "预算", "cell_range": "b2"},
    )
    second = ParsedArtifactReference(
        artifact_id=ARTIFACT_ID,
        selection={"kind": "worksheet", "sheet_name": "预算", "cell_range": "B2:B2"},
    )
    assert first == second and first.selection.cell_range == "B2:B2"


async def test_native_selection_reaches_context_with_range_identity_and_replay(client, monkeypatch):
    """HTTP requests pin selected pages, keep missing-page facts and reject changed-key replay."""

    payload, provider, _ = await prepare_parsed_fixture(client)
    monkeypatch.setattr(
        "nexuspilot_api.services.file_parsing_service.extract_pdf_pages",
        lambda _: (["page one", "", "page three"], "fixture"),
    )
    artifact = (
        await client.post(
            f"/api/v1/runs/{payload['run_id']}/artifacts",
            data={"artifact_type": "file-input"},
            files={"file": ("document.pdf", b"%PDF-1.7\nfixture", "application/pdf")},
        )
    ).json()
    reference = {
        "artifact_id": artifact["artifact_id"],
        "selection": {"kind": "pdf_pages", "page_start": 1, "page_end": 1},
    }
    payload["context_source_references"] = {"parsed_artifacts": [reference]}
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 200, response.text
    message = next(m for m in provider.requests[0].messages if "parsed_file.v1" in m.content)
    snapshot = json.loads(message.content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert snapshot["selection"] == reference["selection"]
    assert snapshot["status"] == "partial" and snapshot["uncovered_pages"] == [2]
    assert [b["page_number"] for b in snapshot["blocks"]] == [1]
    assert snapshot["reading"]["document_total_blocks"] == 2
    run = (await client.get(f"/api/v1/runs/{payload['run_id']}")).json()
    context_payload = {
        "user_id": "native-owner",
        "session_id": run["session_id"],
        "run_id": payload["run_id"],
        "current_user_message_id": payload["current_user_message_id"],
        "provider": "deepseek",
        "model": "test-model",
        "idempotency_key": "native-range-test",
        "source_references": payload["context_source_references"],
    }
    first = await client.post("/api/v1/context-builds", json=context_payload)
    assert first.status_code == 201, first.text
    assert (await client.post("/api/v1/context-builds", json=context_payload)).status_code == 200
    reference["selection"]["page_end"] = 2
    assert (await client.post("/api/v1/context-builds", json=context_payload)).status_code == 409
    second = await client.post(
        "/api/v1/context-builds", json=context_payload | {"idempotency_key": "native-range-second"}
    )
    assert second.status_code == 201, second.text
    versions = []
    for built in (first.json(), second.json()):
        source = next(s for s in built["sources"] if s["source_type"] == "parsed_artifact")
        versions.append(source["source_version"])
    assert versions[0] != versions[1]


async def test_native_selection_wrong_format_does_not_invoke_provider(client):
    """A valid PDF selector on a CSV Artifact fails at source matching before any model call."""

    payload, provider, _ = await prepare_parsed_fixture(client)
    payload["context_source_references"]["parsed_artifacts"][0]["selection"] = {
        "kind": "pdf_pages",
        "page_start": 1,
        "page_end": 1,
    }
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 409, response.text
    assert not provider.requests


@pytest.mark.parametrize(
    "media_type,selection,fixture_blocks,expected_ids",
    [
        (
            WORD_TYPE,
            {"kind": "word_table", "table_number": 2},
            [
                {"block_id": "other", "kind": "table_row", "text": "other", "table_number": 1},
                {"block_id": "chosen", "kind": "table_row", "text": "chosen", "table_number": 2},
            ],
            ["chosen"],
        ),
        (
            SHEET_TYPE,
            {"kind": "worksheet", "sheet_name": "预算", "cell_range": "B1:C1"},
            [
                {
                    "block_id": "chosen",
                    "kind": "table_row",
                    "text": '["hidden","002","3"]',
                    "sheet_name": "预算",
                    "row_number": 1,
                    "cell_range": "A1:C1",
                    "cells": ["hidden", "002", "3"],
                },
                {
                    "block_id": "other",
                    "kind": "table_row",
                    "text": '["not selected"]',
                    "sheet_name": "别的表",
                    "row_number": 1,
                    "cell_range": "A1:A1",
                    "cells": ["not selected"],
                },
            ],
            ["chosen"],
        ),
        (
            SLIDE_TYPE,
            {"kind": "slides", "slide_start": 2, "slide_end": 2},
            [
                {"block_id": "other", "text": "other", "slide_number": 1},
                {"block_id": "chosen", "text": "chosen", "slide_number": 2},
            ],
            ["chosen"],
        ),
    ],
)
async def test_office_native_ranges_reach_model_without_other_tables_or_slides(
    client, monkeypatch, media_type, selection, fixture_blocks, expected_ids
):
    """Run each Office selector through trusted storage, extraction conversion and Context."""

    payload, provider, _ = await prepare_parsed_fixture(client)
    content = b"PK\x03\x04office-selection"
    artifact = (
        await client.post(
            f"/api/v1/runs/{payload['run_id']}/artifacts",
            data={"artifact_type": "file-input"},
            files={"file": ("office-fixture", content, media_type)},
        )
    ).json()

    def extract(source, declared_type):
        """Supply static parser evidence while actual process parsing remains separately tested."""

        assert source == content and declared_type == media_type
        return {
            "protocol": "office_parse.v1",
            "source_hash": hashlib.sha256(source).hexdigest(),
            "media_type": declared_type,
            "parser_version": "test-office",
            "status": "partial",
            "warnings": ["Graphics not extracted"],
            "blocks": fixture_blocks,
        }

    monkeypatch.setattr(
        "nexuspilot_api.services.file_parsing_service.extract_office_content", extract
    )
    payload["context_source_references"] = {
        "parsed_artifacts": [{"artifact_id": artifact["artifact_id"], "selection": selection}]
    }
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 200, response.text
    message = next(m for m in provider.requests[0].messages if "parsed_file.v1" in m.content)
    snapshot = json.loads(message.content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert [block["block_id"] for block in snapshot["blocks"]] == expected_ids
    assert snapshot["warnings"] == ["Graphics not extracted"] and snapshot["status"] == "partial"
    if selection["kind"] == "worksheet":
        assert snapshot["blocks"][0]["cells"] == ["002", "3"]
        assert snapshot["blocks"][0]["cell_range"] == "B1:C1"


async def test_native_range_pagination_is_relative_to_selected_pages(client, monkeypatch):
    """Paginate selected blocks while recording the full-document size separately."""

    payload, provider, _ = await prepare_parsed_fixture(client)
    monkeypatch.setattr(
        "nexuspilot_api.services.file_parsing_service.extract_pdf_pages",
        lambda _: (["first page", "first line\nsecond line\nthird line"], "fixture"),
    )
    artifact = (
        await client.post(
            f"/api/v1/runs/{payload['run_id']}/artifacts",
            data={"artifact_type": "file-input"},
            files={"file": ("pages.pdf", b"%PDF-1.7\nfixture", "application/pdf")},
        )
    ).json()
    payload["context_source_references"] = {
        "parsed_artifacts": [
            {
                "artifact_id": artifact["artifact_id"],
                "selection": {"kind": "pdf_pages", "page_start": 2, "page_end": 2},
                "block_offset": 1,
                "block_limit": 1,
            }
        ]
    }
    response = await client.post("/api/v1/responses", json=payload)
    assert response.status_code == 200, response.text
    message = next(m for m in provider.requests[0].messages if "parsed_file.v1" in m.content)
    snapshot = json.loads(message.content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert len(snapshot["blocks"]) == 1 and snapshot["blocks"][0]["line_start"] == 2
    assert snapshot["blocks"][0]["page_number"] == 2
    assert snapshot["reading"] == {
        "offset": 1,
        "total_blocks": 3,
        "next_offset": 2,
        "has_more": True,
        "document_total_blocks": 4,
    }

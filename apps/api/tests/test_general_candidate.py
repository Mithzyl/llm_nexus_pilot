"""Backend candidate segmentation and read coverage, without model inference or paid calls."""

import pytest
from pydantic import ValidationError

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import GeneralCandidateOutput
from nexuspilot_api.features.agent_runtime.services.general_candidate_service import (
    build_candidate_units,
    build_read_coverage,
)


def test_general_saved_answer_does_not_relax_legacy_model_or_public_message_input():
    """Only backend-generated Assistant prose bypasses the legacy 16k input field cap."""
    from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
        FinalSynthesisModelOutput,
        FinalSynthesisOutput,
    )
    from nexuspilot_api.schemas.sessions import GeneratedAssistantMessageCreate, MessageCreate

    body = "文" * 40000
    with pytest.raises(ValidationError):
        FinalSynthesisModelOutput(final_text=body)
    with pytest.raises(ValidationError):
        MessageCreate(role="assistant", content_text=body)
    with pytest.raises(ValidationError):
        GeneratedAssistantMessageCreate(role="user", content_text=body)
    assert GeneratedAssistantMessageCreate(content_text=body).content_text == body
    result = FinalSynthesisOutput(final_text=body, source_agent_run_ids=[],
        source_handoff_ids=[], source_artifact_ids=[], source_evaluation_ids=[])
    assert result.final_text == body


def test_units_keep_exact_unicode_positions_and_do_not_parse_code_as_citations():
    """Keep prose/list/code units at original offsets; code examples do not issue references."""
    text = (
        "# 标题\r\n\r\n第一段 [S1]。\r\n\r\n- 第二段 [S2]\r\n- 未引用。\r\n\r\n"
        "```py\r\nx = '[S999]'\r\n```\r\n"
    )
    units = build_candidate_units(text, {"S1", "S2"})
    assert [unit.kind for unit in units] == ["heading", "prose", "prose", "prose", "code"]
    assert [unit.unit_id for unit in units] == ["u1", "u2", "u3", "u4", "u5"]
    assert [unit.source_aliases for unit in units] == [[], ["S1"], ["S2"], [], []]
    assert units[3].citation_status == "missing"
    for unit in units:
        assert text[unit.start_char : unit.end_char] == unit.text
        assert unit.content_verified is False


def test_unknown_and_inline_code_references_are_not_claimed_as_valid():
    """Flag only real prose citations, while repeated aliases remain a single association."""
    unit = build_candidate_units("Use `[S999]` as syntax; fact [S1] [S1] [S8].", {"S1"})[0]
    assert unit.source_aliases == ["S1", "S8"]
    assert unit.unknown_source_aliases == ["S8"]
    assert unit.citation_status == "unknown"
    assert (
        build_candidate_units("General explanation.", set())[0].citation_status == "not_applicable"
    )


def test_coverage_deduplicates_actual_returns_and_separates_file_snapshots():
    """Only actual tool blocks count, not search scope or model declarations of full reading."""

    def source(parsed_hash, blocks):
        """Represent an already authorized, snapshot-verified resolver result."""
        return {
            "reference": {
                "file_id": "file",
                "source_hash": "raw",
                "parsed_hash": parsed_hash,
                "parser_version": "parser",
            },
            "filename": "report.pdf",
            "parsed_document_block_count": 4,
            "blocks": [{"block_id": block} for block in blocks],
            "warnings": ["OCR unavailable"],
            "extraction_status": "partial",
            "read_scope": {"search_scope": "entire_document"},
        }

    coverage = build_read_coverage(
        {"S1": source("a", ["1", "2"]), "S2": source("a", ["2", "3"]), "S3": source("b", ["4"])}
    )
    assert len(coverage) == 2
    assert coverage[0].returned_block_ids == ["1", "2", "3"]
    assert coverage[0].unreturned_block_count == 1
    assert not coverage[0].all_parsed_blocks_returned
    assert coverage[1].unreturned_block_count == 3
    assert coverage[0].warnings == ["OCR unavailable"]
    assert coverage[0].model_understanding_verified is False


def test_historical_candidate_is_readable_without_inventing_units():
    """Leave v1 source-only records readable and distinguish missing segmentation from v2."""
    old = GeneralCandidateOutput.model_validate(
        {"schema_version": "general_candidate.v1", "text": "old"}
    )
    assert old.units == [] and old.read_coverage == []


def test_candidate_rejects_wrong_offsets_and_unrepresented_text():
    """Versioned storage validation rejects a unit pointing at different or incomplete prose."""
    text = "第一段。\n\nSecond paragraph."
    units = build_candidate_units(text, set())
    valid = GeneralCandidateOutput(schema_version="general_candidate.v2", text=text, units=units)
    damaged = valid.model_dump()
    damaged["units"][0]["end_char"] += 1
    with pytest.raises(ValidationError, match="original text span"):
        GeneralCandidateOutput.model_validate(damaged)
    damaged = valid.model_dump()
    damaged["units"].pop()
    with pytest.raises(ValidationError, match="omit trailing"):
        GeneralCandidateOutput.model_validate(damaged)

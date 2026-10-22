"""Small reviewer decisions must cover the exact requested candidate units and issued sources."""

import json

import pytest
from pydantic import ValidationError

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import GeneralReviewOutput
from nexuspilot_api.features.agent_runtime.services.general_review_service import (
    ReviewDecisionError,
    assemble_review,
    parse_review_decisions,
)


def decision(unit_id, verdict="supported", sources=None):
    """Build one model-authored decision with no fabricated platform identifiers."""
    return {
        "unit_id": unit_id,
        "verdict": verdict,
        "feedback": "Checked",
        "source_aliases": sources or [],
    }


@pytest.mark.parametrize("sources", [[], ["S2"], ["S1", "S2"]])
def test_supported_cited_unit_requires_its_actual_candidate_aliases(sources):
    """Approval must identify the exact cited scope, not omit it or silently substitute another."""
    with pytest.raises(ReviewDecisionError) as failure:
        parse_review_decisions(
            json.dumps({"decisions": [decision("u1", sources=sources)]}),
            ["u1"], {"S1", "S2"}, candidate_sources={"u1": ["S1"]},
        )
    assert failure.value.pending_unit_ids == ["u1"]


@pytest.mark.parametrize("verdict,sources", [
    ("supported", ["S1"]), ("revise", ["S2"]), ("unconfirmed", []),
])
def test_review_can_approve_matching_scope_or_request_citation_change(verdict, sources):
    """A different supporting excerpt can request revision, never silently approve old prose."""
    result = parse_review_decisions(
        json.dumps({"decisions": [decision("u1", verdict, sources)]}),
        ["u1"], {"S1", "S2"}, candidate_sources={"u1": ["S1"]},
    )
    assert result[0].verdict == verdict


def test_partial_review_keeps_valid_decisions_for_targeted_repair():
    """Missing and invalid references require correction without redoing accepted units."""
    with pytest.raises(ReviewDecisionError) as failure:
        parse_review_decisions(
            json.dumps({"decisions": [decision("u1"), decision("u2", sources=["S9"])]}),
            ["u1", "u2", "u3"],
            {"S1"},
        )
    assert list(failure.value.accepted) == ["u1"]
    assert failure.value.pending_unit_ids == ["u2", "u3"]


@pytest.mark.parametrize("items", [[decision("u1"), decision("u1")], [decision("u99")]])
def test_duplicate_or_foreign_unit_decisions_cannot_pass(items):
    """Reject ambiguous or other-candidate identities even when verdicts say supported."""
    with pytest.raises(ReviewDecisionError):
        parse_review_decisions(json.dumps({"decisions": items}), ["u1"], set())


def test_review_summary_is_assembled_from_every_unit_not_model_global_claim():
    """One revise decision takes precedence; every accepted decision remains in the record."""
    parsed = parse_review_decisions(
        json.dumps({"decisions": [decision("u2", "revise"), decision("u1")]}), ["u1", "u2"], set()
    )
    review = assemble_review("candidate-id", parsed, {})
    assert review.verdict == "revise"
    assert [d.unit_id for d in review.decisions] == ["u1", "u2"]
    assert review.candidate_node_id == "candidate-id"
    assert review.schema_version == "general_review.v2"


def test_old_overall_review_is_not_accepted_as_unit_review():
    """Historical records stay readable elsewhere, but cannot satisfy a new per-unit request."""
    with pytest.raises(ReviewDecisionError):
        parse_review_decisions('{"verdict":"supported","feedback":"all good"}', ["u1"], set())


def test_persisted_review_cannot_claim_support_over_a_revision_decision():
    """Validate stored aggregate consistency and keep old whole-candidate reviews readable."""
    old = GeneralReviewOutput(verdict="supported", feedback="Historical review")
    assert old.schema_version == "general_review.v1" and old.decisions == []
    reviewed = parse_review_decisions(
        json.dumps({"decisions": [decision("u1", "revise")]}), ["u1"], set()
    )
    record = assemble_review("candidate-id", reviewed, {}).model_dump()
    record["verdict"] = "supported"
    with pytest.raises(ValidationError, match="Aggregate verdict"):
        GeneralReviewOutput.model_validate(record)

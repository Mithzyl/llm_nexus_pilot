"""Validate compact per-unit review replies; assemble platform records without model rewrites."""

import json

from pydantic import ValidationError

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    GeneralReviewOutput,
    GeneralUnitReview,
)

REVIEW_BATCH_SIZE = 6
REVIEW_INSTRUCTIONS = (
    "Compare each requested review_unit with the complete candidate and original sources. "
    "Check omissions, contradictions, unsupported certainty and unread/extraction limits. "
    "Each source alias denotes exactly its returned blocks, not the whole file or all "
    "blocks seen by another agent. For a cited unit marked supported, source_aliases must "
    "explicitly match that unit's cited aliases, and those exact excerpts must support it. "
    "If only a different excerpt supports the claim, use revise and identify the correct "
    "alias so the main Agent changes its prose. Never approve an incorrect citation "
    "merely because the claim is true elsewhere in the file. "
    "For platform input/attempt claims, candidate_input_evidence contains the authorized "
    "first page of the candidate's fixed requests. Use these platform records directly; "
    "task_input_coverage summarizes the whole fixed set rather than only this page; "
    "unknown_request_count means totals are incomplete, not zero. "
    "they are evidence even without file source aliases. Counts mean prepared input, "
    "not provider receipt or model comprehension. An earlier tool query and the candidate "
    "snapshot can have different request counts because later requests were added. "
    "Compare matching attempt IDs and observation times; a larger later count alone "
    "does not disprove a claim about an earlier query. If next_offset is non-null and more "
    "records are needed, read_task_input_snapshot is already loaded: "
    "use candidate_input_snapshot or an explicitly related snapshot. They identify fixed "
    "request sets for the named tasks. get_task_input_coverage is your own task, NOT the "
    "candidate, and is unavailable for this review. Do not reject another task's attempt "
    "because it is absent from your own requests. File source aliases cannot prove "
    "platform request counts. If the relevant snapshot is unavailable, mark unconfirmed. "
    "You may read more using the supplied tools. Prefer submit_review to submit exactly this "
    "batch's decisions; make it the only tool call in that response. No prose JSON is needed "
    "after a valid submission. If not using that tool, the compatible output is only JSON: "
    '{"decisions":[{"unit_id":"u1","verdict":"supported|revise|unconfirmed",'
    '"feedback":"short reason or required change","source_aliases":["S1"]}]}. '
    "Return exactly one decision for each review_unit, no other units or global verdict. "
    "Use revise when changes are needed, even for otherwise correct content. "
    "Use unconfirmed when support cannot be established. Headings or non-file advice can "
    "have empty sources; never invent file support. A correct structural heading may be "
    "supported with feedback explaining that it is only a heading. Never return not_applicable "
    "as a verdict. Cite only supplied source_alias values. "
    "Keep each feedback at most 512 characters. Do not reproduce platform evidence IDs."
)


class ReviewDecisionError(ValueError):
    """Preserve valid unit decisions and identify the subset requiring one local repair."""

    def __init__(self, message, expected, accepted=None):
        """Keep ordered pending IDs; ambiguous envelopes require the entire batch again."""
        super().__init__(message)
        self.accepted = accepted or {}
        self.pending_unit_ids = [unit_id for unit_id in expected if unit_id not in self.accepted]
        if not self.pending_unit_ids:
            self.accepted = {}
            self.pending_unit_ids = list(expected)


def parse_review_decisions(text, expected_unit_ids, known_aliases, *, candidate_sources=None):
    """Validate decisions and explicit approval scope without inferring semantic support."""
    expected = list(expected_unit_ids)
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as error:
        raise ReviewDecisionError("Review response must be JSON", expected) from error
    if (
        not isinstance(data, dict)
        or set(data) != {"decisions"}
        or not isinstance(data["decisions"], list)
    ):
        raise ReviewDecisionError("Review response requires only a decisions list", expected)
    accepted = {}
    seen = set()
    errors = []
    for item in data["decisions"]:
        unit_id = item.get("unit_id") if isinstance(item, dict) else None
        if not isinstance(unit_id, str) or unit_id not in expected:
            raise ReviewDecisionError("Review contains an unrequested unit", expected)
        if unit_id in seen:
            accepted.pop(unit_id, None)
            errors.append(f"Duplicate decision for {unit_id}")
            continue
        seen.add(unit_id)
        try:
            decision = GeneralUnitReview.model_validate(item)
            if len(set(decision.source_aliases)) != len(decision.source_aliases):
                raise ValueError("Duplicate source aliases")
            if not set(decision.source_aliases).issubset(known_aliases):
                raise ValueError("Unknown source aliases")
            cited = set((candidate_sources or {}).get(unit_id, []))
            if (decision.verdict == "supported" and cited
                    and set(decision.source_aliases) != cited):
                raise ValueError(
                    "Supported cited unit must explicitly confirm exactly its cited aliases; "
                    "use revise if the citation must change"
                )
        except (ValidationError, ValueError) as error:
            errors.append(f"Invalid decision for {unit_id}: {error}")
            continue
        accepted[unit_id] = decision
    missing = [identity for identity in expected if identity not in accepted]
    if errors or missing:
        raise ReviewDecisionError(
            "; ".join(errors + [f"Missing valid decisions: {missing}"]), expected, accepted
        )
    return [accepted[identity] for identity in expected]


def assemble_review(candidate_node_id, decisions, sources):
    """Compute overall handling from every decision, retaining full reasons and source mapping."""
    verdicts = {item.verdict for item in decisions}
    verdict = (
        "revise"
        if "revise" in verdicts
        else "unconfirmed"
        if "unconfirmed" in verdicts
        else "supported"
    )
    actionable = [item for item in decisions if item.verdict != "supported"]
    feedback = "\n".join(f"{item.unit_id}: {item.feedback}" for item in actionable)
    cited = {alias for item in decisions for alias in item.source_aliases}
    return GeneralReviewOutput(
        schema_version="general_review.v2",
        candidate_node_id=candidate_node_id,
        verdict=verdict,
        feedback=feedback or f"All {len(decisions)} candidate units reviewed.",
        decisions=decisions,
        sources={alias: sources[alias] for alias in sorted(cited)},
    )

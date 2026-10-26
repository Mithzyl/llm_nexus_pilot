"""Versioned model-authored claims; source truth is resolved by the platform, not these fields."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FileEvidenceReference(BaseModel):
    """Pin file or web read blocks; retain the legacy type name and default file semantics."""

    model_config = ConfigDict(extra="forbid")
    tool_call_id: str = Field(min_length=36, max_length=36)
    result_id: str = Field(min_length=1, max_length=64)
    source_kind: Literal["file", "web"] = "file"
    file_id: str | None = Field(default=None, min_length=36, max_length=36)
    web_result_id: str | None = Field(default=None, min_length=36, max_length=36)
    source_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    parsed_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    parser_version: str | None = Field(default=None, min_length=1, max_length=512)
    block_ids: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_source_identity(self):
        """Require existing file checks, but web references use only tool identity and excerpts."""
        if self.source_kind == "file" and (
            not self.file_id
            or self.web_result_id
            or not self.source_hash
            or not self.parsed_hash
            or not self.parser_version
        ):
            raise ValueError("File identity required")
        if self.source_kind == "web" and (
            self.web_result_id != self.tool_call_id
            or self.file_id
            or self.source_hash
            or self.parsed_hash
            or self.parser_version
        ):
            raise ValueError("Web tool result identity required; file hashes are not applicable")
        return self


class WorkerClaim(BaseModel):
    """Present an assertion for review, never an automatic declaration of verified truth."""

    model_config = ConfigDict(extra="forbid")
    claim_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    text: str = Field(min_length=1, max_length=4000)
    source_type: Literal["file", "general_knowledge", "inference"] = "file"
    kind: Literal["assertion", "absence"] = "assertion"
    scope: Literal["read_blocks", "parsed_document"] = "read_blocks"
    evidence_refs: list[FileEvidenceReference] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_file_sources(self):
        """Refuse file assertions without references or disguised document-wide inferences."""
        if (self.source_type == "file" or self.kind == "absence") and not self.evidence_refs:
            raise ValueError("File and absence claims require evidence references")
        return self


class UnresolvedEvidenceItem(BaseModel):
    """Describe uncertainty and its actual inspected scope without claiming full-file absence."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[FileEvidenceReference] = Field(default_factory=list, max_length=20)
    requires_more_reading: bool = True


class WorkerEvidenceBundle(BaseModel):
    """Carry claims and unresolved sources unchanged across a v2 Handoff."""

    model_config = ConfigDict(extra="forbid")
    claims: list[WorkerClaim] = Field(max_length=50)
    unresolved_items: list[UnresolvedEvidenceItem] = Field(default_factory=list, max_length=50)
    hypotheses: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_claim_keys(self):
        """Keep reviewer assessments unambiguously associated with a single claim."""
        keys = [claim.claim_key for claim in self.claims]
        if len(keys) != len(set(keys)):
            raise ValueError("Claim keys must be unique within a Worker result")
        return self


class ClaimAssessment(BaseModel):
    """Record a reviewer's content judgment separately from source integrity checks."""

    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=36, max_length=36)
    claim_key: str = Field(min_length=1, max_length=64)
    verdict: Literal["supported", "contradicted", "insufficient_evidence"]
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[FileEvidenceReference] = Field(default_factory=list, max_length=20)

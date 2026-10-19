"""Assemble source-linked candidate units and factual tool coverage without another model call."""

import re

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    GeneralCandidateUnit,
    GeneralReadCoverage,
)


def build_candidate_units(text: str, known_aliases: set[str]) -> list[GeneralCandidateUnit]:
    """Keep exact Unicode spans; paragraph/list grouping does not infer atomic claims.

    Fenced and inline code are not interpreted as source citations. Uncited prose is
    annotated, not silently attributed to a nearby paragraph's references.
    """
    units = []
    start = None
    end = 0
    kind = "prose"
    fence = None

    def flush():
        """Append one trimmed original span without rewriting punctuation or content."""
        nonlocal start
        if start is None:
            return
        raw = text[start:end]
        left = start + len(raw) - len(raw.lstrip())
        right = end - (len(raw) - len(raw.rstrip()))
        if left < right:
            body = text[left:right]
            citation_text = re.sub(r"(`+).*?\1", "", body) if kind == "prose" else ""
            aliases = list(dict.fromkeys(re.findall(r"\[(S[0-9]+)\]", citation_text)))
            unknown = [alias for alias in aliases if alias not in known_aliases]
            status = (
                "unknown"
                if unknown
                else "present"
                if aliases
                else "missing"
                if kind == "prose" and known_aliases
                else "not_applicable"
            )
            units.append(
                GeneralCandidateUnit(
                    unit_id=f"u{len(units) + 1}",
                    kind=kind,
                    start_char=left,
                    end_char=right,
                    text=body,
                    source_aliases=aliases,
                    unknown_source_aliases=unknown,
                    citation_status=status,
                )
            )
        start = None

    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if fence:
            end = offset + len(line)
            if re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + ",}", stripped):
                flush()
                fence = None
            offset += len(line)
            continue
        opening = re.match(r"^\s*(`{3,}|~{3,})", line)
        heading = bool(re.match(r"^\s{0,3}#{1,6}\s", line))
        item = bool(re.match(r"^\s*(?:[-+*]|[0-9]+[.)])\s", line))
        if not stripped or opening or heading or item:
            flush()
        if stripped:
            if start is None:
                start = offset
                kind = "code" if opening else "heading" if heading else "prose"
            end = offset + len(line)
            if opening:
                fence = opening.group(1)
            elif heading:
                flush()
        offset += len(line)
    flush()
    return units


def build_read_coverage(originals: dict) -> list[GeneralReadCoverage]:
    """Union authorized returned blocks by exact snapshot, keeping extraction limitations.

    Search scope is deliberately ignored: searching the entire document is not
    returning its entire text. This is not model-input or comprehension coverage.
    """
    grouped = {}
    for alias, source in originals.items():
        ref = source["reference"]
        key = tuple(
            ref[name] for name in ("file_id", "source_hash", "parsed_hash", "parser_version")
        )
        group = grouped.setdefault(key, {"source": source, "aliases": [], "blocks": {}})
        group["aliases"].append(alias)
        group["blocks"].update({block["block_id"]: None for block in source["blocks"]})
    coverage = []
    for (file_id, source_hash, parsed_hash, parser_version), group in grouped.items():
        source = group["source"]
        total = source["parsed_document_block_count"]
        blocks = list(group["blocks"])
        if len(blocks) > total:
            raise ValueError("Returned blocks exceed the verified parse inventory")
        coverage.append(
            GeneralReadCoverage(
                file_id=file_id,
                filename=source["filename"],
                source_hash=source_hash,
                parsed_hash=parsed_hash,
                parser_version=parser_version,
                source_aliases=group["aliases"],
                total_parsed_blocks=total,
                returned_block_ids=blocks,
                unreturned_block_count=total - len(blocks),
                all_parsed_blocks_returned=bool(total) and len(blocks) == total,
                extraction_status=source["extraction_status"],
                warnings=source["warnings"],
            )
        )
    return coverage

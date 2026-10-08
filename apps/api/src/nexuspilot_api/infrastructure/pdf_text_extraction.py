"""Adapt dedicated executor PDF pages without API-local subprocesses or fallback."""

from nexuspilot_api.infrastructure.file_runtime_client import FileRuntimeError, request_file_parse


class PdfExtractionError(ValueError):
    """Report safe PDF transport, identity, encryption or extraction failures."""


def extract_pdf_pages(content: bytes) -> tuple[list[str], str]:
    """Read at most 200 bounded pages, retaining empty pages for partial coverage."""

    try:
        report = request_file_parse(content, "application/pdf", protocol="file_parse.v1")
        pages = report.get("pages")
        if (
            not isinstance(pages, list) or not 1 <= len(pages) <= 200
            or not all(isinstance(page, str) for page in pages)
            or sum(len(page.encode("utf-8")) for page in pages) > 1024 * 1024
            or not any(page.strip() for page in pages)
        ):
            raise PdfExtractionError("PDF executor returned invalid or empty pages")
        return pages, report["parser_version"]
    except (FileRuntimeError, UnicodeError) as exc:
        raise PdfExtractionError(str(exc) if isinstance(exc, FileRuntimeError)
                                 else "PDF executor returned invalid text") from exc

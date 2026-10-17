"""Extract embedded PDF text only inside the offline disposable file-runtime container.

This module is called by the fixed protocol dispatcher, never by the API. Its
Poppler children inherit the container's filesystem, network and resource limits.
"""

import hashlib
import os
import stat
import subprocess
import tempfile
from pathlib import Path


def read_output_text(path: Path, *, maximum_bytes: int) -> str:
    """Read bounded regular output without following parser-created symbolic or hard links."""

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
            or metadata.st_size > maximum_bytes
        ):
            raise ValueError("PDF output is not a bounded private regular file")
        content = bytearray()
        while chunk := os.read(descriptor, min(65536, maximum_bytes + 1 - len(content))):
            content.extend(chunk)
            if len(content) > maximum_bytes:
                raise ValueError("PDF output exceeds the allowed size")
        return content.decode("utf-8", errors="strict")
    finally:
        os.close(descriptor)


def run_pdf_command(directory: Path, arguments: list[str]) -> str:
    """Execute fixed Poppler argv with private descriptors, bounded metadata and a deadline."""

    with tempfile.TemporaryFile(dir=directory) as output:
        subprocess.run(
            arguments, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.DEVNULL, cwd=directory,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(directory)},
            timeout=20, check=True,
        )
        output.seek(0)
        metadata = output.read(65537)
        if len(metadata) > 65536:
            raise ValueError("PDF metadata exceeds the allowed size")
        return metadata.decode("utf-8", errors="strict")


def extract_pages(content: bytes) -> tuple[list[str], str]:
    """Extract up to 200 pages from at most 20 MiB; retain text-output and timeout limits."""

    if not content.startswith(b"%PDF-") or not 0 < len(content) <= 20 * 1024 * 1024:
        raise ValueError("Unsupported PDF source")
    with tempfile.TemporaryDirectory(prefix="pdf-") as temporary:
        directory = Path(temporary)
        source, output = directory / "source.pdf", directory / "content.txt"
        source.write_bytes(content)
        metadata = run_pdf_command(directory, ["/usr/bin/pdfinfo", str(source)])
        fields = dict(line.split(":", 1) for line in metadata.splitlines() if ":" in line)
        if fields.get("Encrypted", "").strip().lower() != "no":
            raise ValueError("Encrypted PDFs are not supported")
        count = int(fields["Pages"].strip())
        if not 1 <= count <= 200:
            raise ValueError("PDF page count exceeds the allowed range")
        run_pdf_command(directory, [
            "/usr/bin/pdftotext", "-layout", "-enc", "UTF-8", str(source), str(output),
        ])
        pages = read_output_text(output, maximum_bytes=1024 * 1024).split("\f")
        if pages and pages[-1] == "":
            pages.pop()
        if len(pages) != count or not any(page.strip() for page in pages):
            raise ValueError("PDF page coverage is invalid or contains no embedded text")
        with Path("/usr/bin/pdftotext").open("rb") as binary:
            version = hashlib.file_digest(binary, "sha256").hexdigest()
        return pages, f"pdf-v1/poppler-sha256:{version}"

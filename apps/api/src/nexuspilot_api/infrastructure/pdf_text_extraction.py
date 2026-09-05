"""Extract embedded PDF text with preinstalled Poppler inside an OS sandbox.

No network, OCR, user scripts or host fallback. Wall/CPU/output/memory bounds
apply to a short-lived child; only a task-owned temporary directory is writable.
"""

import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class PdfExtractionError(ValueError):
    """Report a safe PDF dependency, isolation, encryption or extraction failure."""


# Runs in a new isolated Python process, not preexec_fn in the API's worker thread.
_LIMITED_EXEC = (
    "import os,resource,sys\n"
    "limits=[(resource.RLIMIT_CPU,20),(resource.RLIMIT_FSIZE,4194304),"
    "(resource.RLIMIT_NOFILE,64),(resource.RLIMIT_CORE,0)]\n"
    "if sys.platform=='linux': limits.append((resource.RLIMIT_AS,536870912))\n"
    "for kind,limit in limits:\n"
    " hard=resource.getrlimit(kind)[1]\n"
    " cap=limit if hard==resource.RLIM_INFINITY else min(limit,hard)\n"
    " resource.setrlimit(kind,(cap,hard))\n"
    " resource.setrlimit(kind,(cap,cap))\n"
    "os.execv(sys.argv[1],sys.argv[1:])"
)


def _sandbox_command(directory: Path, command: list[str]) -> list[str]:
    """Build an argument-vector-only sandbox; missing isolation always raises, never falls back."""

    if sys.platform == "darwin" and (sandbox := shutil.which("sandbox-exec")):
        profile = (
            "(version 1)(deny default)(allow process-exec)(allow sysctl-read)"
            '(allow file-map-executable (subpath "/System") (subpath "/usr")'
            ' (subpath "/Library/Frameworks") (subpath "/opt/homebrew"))'
            "(allow file-read-metadata)"
            '(allow file-read* (literal "/") (subpath "/System") (subpath "/usr")'
            ' (subpath "/bin")'
            ' (subpath "/Library/Frameworks") (subpath "/opt/homebrew/Cellar")'
            ' (subpath "/opt/homebrew/lib") (subpath "/opt/homebrew/opt")'
            ' (subpath "/opt/homebrew/bin")'
            ' (subpath "/private/var/db/dyld") (literal "/dev/null")'
            ' (literal "/dev/urandom") (literal "/dev/random")'
            ' (subpath (param "WORK")))'
            '(allow file-write* (subpath (param "WORK")) (literal "/dev/null"))'
        )
        return [sandbox, "-D", f"WORK={directory}", "-p", profile, *command]
    if sys.platform == "linux" and (sandbox := shutil.which("bwrap")):
        arguments = [sandbox, "--unshare-all", "--die-with-parent", "--new-session"]
        for runtime_path in ("/usr", "/lib", "/lib64"):
            if Path(runtime_path).exists():
                arguments.extend(["--ro-bind", runtime_path, runtime_path])
        return [
            *arguments,
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(directory),
            str(directory),
            "--chdir",
            str(directory),
            *command,
        ]
    raise PdfExtractionError("PDF isolation unavailable; sandbox-exec or bubblewrap is required")


def _run_pdf_command(directory: Path, arguments: list[str]) -> str:
    """Run one bounded child with sanitized environment and safe errors, returning small stdout."""

    command = _sandbox_command(
        directory, [str(Path(sys.executable).resolve()), "-I", "-c", _LIMITED_EXEC, *arguments]
    )
    try:
        # Keep metadata on private open descriptors. A previous parser must not
        # redirect the parent's next open/truncate through a planted symlink.
        with tempfile.TemporaryFile(dir=directory) as stdout, tempfile.TemporaryFile(
            dir=directory
        ) as stderr:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                cwd=directory,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(directory)},
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 25
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        raise PdfExtractionError("PDF extraction timed out")
                    # Darwin does not enforce RLIMIT_AS/DATA portably. Inspect only this
                    # child PID; the sandbox denies forking and all network access.
                    if sys.platform == "darwin":
                        usage = subprocess.run(
                            ["/bin/ps", "-o", "rss=", "-p", str(process.pid)],
                            capture_output=True,
                            timeout=2,
                            check=False,
                        )
                        resident_kib = usage.stdout.strip()
                        if resident_kib.isdigit() and int(resident_kib) > 512 * 1024:
                            raise PdfExtractionError("PDF memory usage exceeds the allowed limit")
                        if not resident_kib.isdigit() and process.poll() is None:
                            raise PdfExtractionError("PDF memory monitoring is unavailable")
                    time.sleep(0.02)
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=5)
            if process.returncode:
                raise PdfExtractionError(
                    "PDF extraction failed: isolation unavailable, encrypted or damaged document"
                )
            stdout.seek(0)
            metadata = stdout.read(65537)
            if len(metadata) > 65536:
                raise PdfExtractionError("PDF command metadata exceeds the allowed size")
            return metadata.decode("utf-8", errors="strict")
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise PdfExtractionError("PDF extraction unavailable or timed out") from exc


def _read_output_text(path: Path, *, maximum_bytes: int) -> str:
    """Read bounded regular output without following parser-created symbolic or hard links."""

    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > maximum_bytes
        ):
            raise PdfExtractionError("PDF output is not a bounded private regular file")
        content = bytearray()
        while chunk := os.read(descriptor, min(65536, maximum_bytes + 1 - len(content))):
            content.extend(chunk)
            if len(content) > maximum_bytes:
                raise PdfExtractionError("PDF output exceeds the allowed size")
        return content.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise PdfExtractionError("PDF output is unavailable or invalid") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def extract_pdf_pages(content: bytes) -> tuple[list[str], str]:
    """Extract at most 200 pages of embedded text; empty pages remain visible for coverage."""

    pdfinfo, pdftotext = shutil.which("pdfinfo"), shutil.which("pdftotext")
    if not pdfinfo or not pdftotext:
        raise PdfExtractionError("PDF extraction unavailable; preinstalled Poppler is required")
    with tempfile.TemporaryDirectory(prefix="nexuspilot-pdf-") as temporary_directory:
        directory = Path(temporary_directory).resolve()
        source_path, text_path = directory / "source.pdf", directory / "content.txt"
        source_path.write_bytes(content)
        # Reject encrypted files even when the library can open them with an empty password.
        metadata = _run_pdf_command(directory, [pdfinfo, str(source_path)])
        fields = dict(line.split(":", 1) for line in metadata.splitlines() if ":" in line)
        if fields.get("Encrypted", "").strip().lower() != "no":
            raise PdfExtractionError("Encrypted PDFs are not supported")
        try:
            page_count = int(fields["Pages"].strip())
        except (KeyError, ValueError) as exc:
            raise PdfExtractionError("PDF page count is unavailable") from exc
        if not 1 <= page_count <= 200:
            raise PdfExtractionError("PDF page count exceeds the allowed range")
        _run_pdf_command(
            directory, [pdftotext, "-layout", "-enc", "UTF-8", str(source_path), str(text_path)]
        )
        pages = _read_output_text(text_path, maximum_bytes=1024 * 1024).split("\f")
        if pages and pages[-1] == "":
            pages.pop()
        if len(pages) != page_count:
            raise PdfExtractionError("PDF extracted pages do not match source page count")
        # Hash the installed parser executable as version evidence without a second unbounded run.
        import hashlib

        with Path(pdftotext).open("rb") as binary:
            version = hashlib.file_digest(binary, "sha256").hexdigest()
        return pages, f"poppler-sha256:{version}"

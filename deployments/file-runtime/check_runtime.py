"""Verify the preinstalled file runtime with synthetic documents, offline in Docker.

Run via ``make file-runtime-check``. This is an environment acceptance check,
not the product file parser: it neither reads user documents nor contacts APIs.
Any failed check exits nonzero; JSON reports each completed check separately.
"""

import importlib.metadata
import json
import os
import socket
import subprocess
import tempfile
from pathlib import Path


def check_isolation(directory: Path) -> dict:
    """Probe required Linux runtime restrictions and private temporary writes."""

    if os.getuid() != 65532 or os.getgid() != 65532:
        raise RuntimeError("Runtime must use the dedicated non-root user")
    process_status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    if int(process_status["CapEff"].strip(), 16) != 0:
        raise RuntimeError("Runtime retains Linux capabilities")
    if process_status["NoNewPrivs"].strip() != "1":
        raise RuntimeError("Privilege escalation is not disabled")
    mounts = Path("/proc/mounts").read_text().splitlines()
    root = next(line.split() for line in mounts if line.split()[1] == "/")
    if "ro" not in root[3].split(","):
        raise RuntimeError("Root filesystem is not read-only")
    temporary = next(line.split() for line in mounts if line.split()[1] == "/tmp")
    if not {"noexec", "nosuid", "nodev"}.issubset(temporary[3].split(",")):
        raise RuntimeError("Temporary filesystem restrictions are missing")
    for route in Path("/proc/net/route").read_text().splitlines()[1:]:
        if route.split()[0] != "lo":
            raise RuntimeError("Runtime has a non-loopback IPv4 route")
    with socket.socket() as connection:
        connection.settimeout(1)
        try:
            connection.connect(("198.51.100.1", 443))
        except OSError:
            pass
        else:
            raise RuntimeError("Unexpected external network access")
    for forbidden in ("/var/run/docker.sock", "/app/apps/api/.env"):
        if Path(forbidden).exists():
            raise RuntimeError(
                "Runtime exposes an application secret path or Docker socket"
            )
    if any(name.startswith("NEXUSPILOT_") for name in os.environ):
        raise RuntimeError("Runtime inherited application configuration")
    (directory / "temporary.txt").write_text("private task output", encoding="utf-8")
    for cgroup_file in ("memory.max", "pids.max", "cpu.max"):
        configured = Path("/sys/fs/cgroup", cgroup_file).read_text().strip()
        if configured.split()[0] == "max":
            raise RuntimeError(f"Missing resource bound: {cgroup_file}")
    return {
        "non_root": True,
        "root_read_only": True,
        "external_network_unavailable": True,
        "capabilities_empty": True,
        "temporary_write": True,
        "resources_bounded": True,
    }


def check_word(directory: Path) -> dict:
    """Round-trip a Chinese DOCX heading, paragraph and merged table using the installed library."""

    from docx import Document

    path = directory / "sample.docx"
    document = Document()
    document.add_heading("文件解析测试", level=1)
    document.add_paragraph("中文正文与 English text")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "合并标题"
    table.cell(1, 0).text = "金额"
    table.cell(1, 1).text = "123.45"
    document.save(path)
    loaded = Document(path)
    if [paragraph.text for paragraph in loaded.paragraphs] != [
        "文件解析测试",
        "中文正文与 English text",
    ] or loaded.tables[0].cell(1, 1).text != "123.45":
        raise RuntimeError("DOCX text or table round-trip failed")
    if loaded.tables[0].cell(0, 0).text != loaded.tables[0].cell(0, 1).text:
        raise RuntimeError("DOCX merged cell round-trip failed")
    return {"paragraphs": 2, "tables": 1, "unicode": True}


def check_excel(directory: Path) -> dict:
    """Verify XLSX sheets, merged cells and formulas without calculating or refreshing links."""

    from openpyxl import Workbook, load_workbook

    path = directory / "sample.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "数据"
    worksheet.append(["编号", "金额"])
    worksheet.append(["00123", 12.5])
    worksheet["B3"] = "=SUM(B2:B2)"
    worksheet.merge_cells("D1:E1")
    worksheet["D1"] = "合并标题"
    workbook.save(path)
    workbook.close()
    loaded = load_workbook(path, data_only=False, keep_links=False)
    cached = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        if (
            loaded["数据"]["A2"].value != "00123"
            or loaded["数据"]["B3"].value != "=SUM(B2:B2)"
        ):
            raise RuntimeError("XLSX identifier or formula changed")
        if "D1:E1" not in loaded["数据"].merged_cells:
            raise RuntimeError("XLSX merged range was lost")
        if cached["数据"]["B3"].value is not None:
            raise RuntimeError("Missing cached formula result was fabricated")
    finally:
        loaded.close()
        cached.close()
    return {"sheets": 1, "formula_preserved": True, "formula_executed": False}


def check_powerpoint(directory: Path) -> dict:
    """Verify PPTX slide text, table cells and speaker notes using a synthetic presentation."""

    from pptx import Presentation
    from pptx.util import Inches

    path = directory / "sample.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(0, 0, Inches(3), Inches(1)).text = "幻灯片正文"
    table = slide.shapes.add_table(1, 2, 0, Inches(2), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "测试"
    slide.notes_slide.notes_text_frame.text = "演讲者备注"
    presentation.save(path)
    loaded = Presentation(path).slides[0]
    if (
        loaded.shapes[0].text != "幻灯片正文"
        or loaded.shapes[1].table.cell(0, 1).text != "测试"
    ):
        raise RuntimeError("PPTX text or table round-trip failed")
    if loaded.notes_slide.notes_text_frame.text != "演讲者备注":
        raise RuntimeError("PPTX notes were lost")
    return {"slides": 1, "tables": 1, "notes": True}


def check_pdf(directory: Path) -> dict:
    """Build a tiny valid text PDF and exercise the preinstalled Poppler binaries offline."""

    text_stream = b"BT /F1 12 Tf 40 160 Td (File runtime PDF check) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(text_stream)).encode()
        + b" >>\nstream\n"
        + text_stream
        + b"\nendstream",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(content)
    content.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010} 00000 n \n".encode())
    content.extend(
        f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    )
    source, output = directory / "sample.pdf", directory / "sample.txt"
    source.write_bytes(content)
    metadata = subprocess.run(
        ["pdfinfo", str(source)], check=True, capture_output=True, timeout=10
    )
    subprocess.run(
        ["pdftotext", "-layout", str(source), str(output)],
        check=True,
        capture_output=True,
        timeout=10,
    )
    if (
        b"Pages:" not in metadata.stdout
        or "File runtime PDF check" not in output.read_text()
    ):
        raise RuntimeError("Poppler failed to extract the PDF fixture")
    version = subprocess.run(
        ["pdftotext", "-v"], check=True, capture_output=True, timeout=10
    )
    return {
        "pages": 1,
        "embedded_text": True,
        "version": version.stderr.decode().splitlines()[0],
    }


def main() -> int:
    """Run all independent checks and return a nonzero exit code for any failed requirement."""

    report = {
        "checks": {},
        "python_packages": {
            distribution.metadata["Name"]: distribution.version
            for distribution in importlib.metadata.distributions()
        },
    }
    with tempfile.TemporaryDirectory(prefix="file-runtime-check-") as temporary:
        for name, check in (
            ("isolation", check_isolation),
            ("docx", check_word),
            ("xlsx", check_excel),
            ("pptx", check_powerpoint),
            ("pdf", check_pdf),
        ):
            try:
                details = check(Path(temporary))
                report["checks"][name] = {"status": "passed", "details": details}
            except Exception as error:
                # This acceptance command contains only synthetic data, no user file contents.
                report["checks"][name] = {"status": "failed", "error": str(error)}
    passed = all(check["status"] == "passed" for check in report["checks"].values())
    report["status"] = "passed" if passed else "failed"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

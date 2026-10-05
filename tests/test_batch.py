"""The batch layer: finding PDFs, naming outputs, and the command line itself."""

import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest
from conftest import ROOT, make_page, save

import batch_watermark as bw


def pdf(path: Path) -> Path:
    doc = pymupdf.open()
    make_page(doc)
    return save(doc, path)


@pytest.fixture
def pack(tmp_path):
    root = tmp_path / "Tender pack"
    pdf(root / "Cover.pdf")
    pdf(root / "UPPER.PDF")
    pdf(root / "drawings" / "Plan.pdf")
    (root / "notes.docx").write_text("not a pdf")
    (root / "._Cover.pdf").write_bytes(b"\x00\x05\x16\x07 AppleDouble")
    (root / ".DS_Store").write_bytes(b"\x00")
    pdf(root / "old _X_Watermarked" / "Cover_watermarked.pdf")
    return root


def test_find_pdfs_is_case_insensitive_and_skips_junk(pack):
    found = [p.relative_to(pack).as_posix() for p in bw.find_pdfs(pack, bw.FOLDER_SUFFIX)]
    assert sorted(found) == ["Cover.pdf", "UPPER.PDF", "drawings/Plan.pdf"]
    assert bw.count_ignored(pack, bw.FOLDER_SUFFIX) == 1


def test_find_pdfs_works_inside_a_folder_that_looks_like_output(tmp_path):
    """Only folders *below* the chosen one are treated as previous output."""
    inner = tmp_path / "Archive_Watermarked" / "Pack"
    pdf(inner / "a.pdf")
    assert len(bw.find_pdfs(inner, bw.FOLDER_SUFFIX)) == 1


def test_same_named_sources_never_share_an_output(tmp_path):
    a, b = tmp_path / "A" / "Docs", tmp_path / "B" / "Docs"
    fa, fb = pdf(tmp_path / "A" / "report.pdf"), pdf(tmp_path / "B" / "report.pdf")
    pdf(a / "x.pdf")
    pdf(b / "x.pdf")
    out = tmp_path / "out"
    folder_pdfs = {f: bw.find_pdfs(f, bw.FOLDER_SUFFIX) for f in (a, b)}
    jobs = bw.build_jobs("DRAFT", folder_pdfs, [fa, fb], out, bw.FOLDER_SUFFIX, False)
    destinations = [str(j[1]).casefold() for j in jobs]
    assert len(destinations) == len(set(destinations)) == 4


@pytest.mark.parametrize("text,expected", [
    ("CONFIDENTIAL - Acme", "CONFIDENTIAL - Acme"),
    ('a/b:c*d?"e"', "a-b-c-d--e"),
    ("Page {page} of {pages}", "Page of"),
    ("...", "Watermarked"),
])
def test_slugify(text, expected):
    assert bw.slugify(text) == expected


def test_slugify_caps_length():
    assert len(bw.slugify("x" * 500)) <= 80


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path rules")
def test_long_path_handles_network_shares():
    assert bw.long_path(r"\\server\share\pack\a.pdf") == r"\\?\UNC\server\share\pack\a.pdf"
    assert bw.long_path(r"C:\x\a.pdf") == r"\\?\C:\x\a.pdf"


def run_cli(*args, cwd):
    # A pipe on Windows uses the legacy code page - exactly the setting that
    # used to crash on names like "Łukasz".
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    return subprocess.run([sys.executable, str(ROOT / "batch_watermark.py"), *args],
                          cwd=cwd, capture_output=True, env=env, timeout=120)


def test_cli_end_to_end_with_several_recipients(pack, tmp_path):
    result = run_cli(str(pack), "--company", "Łukasz Wójcik", "株式会社", cwd=tmp_path)
    assert result.returncode == 0, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
    outputs = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*_watermarked.pdf")
                     if "old _X" not in p.as_posix())
    assert len(outputs) == 6
    assert any("CONFIDENTIAL - Łukasz Wójcik_Watermarked/drawings/Plan_watermarked.pdf" in o for o in outputs)
    text = pymupdf.open(next(tmp_path.rglob("Tender pack _CONFIDENTIAL - 株式会社_Watermarked/Cover_watermarked.pdf")))[0].get_text()
    assert "CONFIDENTIAL - 株式会社" in text


def test_cli_dry_run_writes_nothing(pack, tmp_path):
    before = set(tmp_path.rglob("*"))
    result = run_cli(str(pack), "--text", "DRAFT", "--dry-run", cwd=tmp_path)
    assert result.returncode == 0
    assert b"Dry run complete" in result.stdout
    assert set(tmp_path.rglob("*")) == before


def test_cli_reports_bad_files_and_carries_on(pack, tmp_path):
    (pack / "broken.pdf").write_bytes(b"%PDF-1.4 broken")
    result = run_cli(str(pack), "--text", "DRAFT", cwd=tmp_path)
    out = result.stdout.decode(errors="replace")
    assert result.returncode == 1
    assert "ERR broken.pdf" in out
    assert "3 succeeded, 1 failed" in out


def test_cli_rejects_bad_colour(pack, tmp_path):
    result = run_cli(str(pack), "--text", "X", "--color", "blurple", cwd=tmp_path)
    assert result.returncode == 2
    assert b"not a colour" in result.stderr


def test_cli_custom_prefix(pack, tmp_path):
    result = run_cli(str(pack), "--company", "Acme", "--prefix", "FOR REVIEW", "--dry-run", cwd=tmp_path)
    assert result.returncode == 0
    assert b'["FOR REVIEW - Acme"]' in result.stdout
    result = run_cli(str(pack), "--company", "Acme", "--prefix", "", "--dry-run", cwd=tmp_path)
    assert b'["Acme"]' in result.stdout


def test_cli_position_and_font_flags(pack, tmp_path):
    result = run_cli(str(pack), "--text", "X", "--position", "top-right", "--no-bold", "--italic", "--dry-run", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    result = run_cli(str(pack), "--text", "X", "--position", "0.2,1.5", cwd=tmp_path)
    assert result.returncode == 2 and b"between 0 and 1" in result.stderr
    result = run_cli(str(pack), "--text", "X", "--font", "Definitely Not A Font", cwd=tmp_path)
    assert result.returncode == 2 and b"no font called" in result.stderr

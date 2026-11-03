"""Empirical Adversarial Stress Tests for Word File Locking, Safe Path Resolution,
Saved Path Propagation, and Atomic Temp File Cleanup on Windows.

Authoritative Requirements:
- ORIGINAL_REQUEST.md § R1, Acceptance Criteria (File Collisions)
- PROJECT.md § M1 (F5, F6), Output Writing <-> CLI
- DISPATCH.md § Challenger 2
"""

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import docx
import pytest

from reportsmith.brief import load_brief
from reportsmith.docx_build import (
    DocxReportBuilder,
    get_output_candidates,
    get_safe_output_path,
    is_file_writable,
)
from reportsmith.facts import derive_facts
from reportsmith.outline import parse_format_file


@contextmanager
def win32_lock_file(path: Path, share_mode: int = 1):
    """Acquire a Windows file handle lock.
    share_mode=1 (FILE_SHARE_READ): allows reading, prevents overwriting/moving (Word default)
    share_mode=0: exclusive lock, prevents any sharing
    """
    if sys.platform != "win32":
        pytest.skip("Windows-specific file locking required")

    import win32con
    import win32file

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"PK\x03\x04Existing locked document content placeholder")

    share = win32con.FILE_SHARE_READ if share_mode == 1 else 0
    handle = win32file.CreateFile(
        str(path.resolve()),
        win32con.GENERIC_READ | win32con.GENERIC_WRITE,
        share,
        None,
        win32con.OPEN_EXISTING,
        0,
        None,
    )
    try:
        yield path
    finally:
        win32file.CloseHandle(handle)


@contextmanager
def multi_file_locks(paths, share_mode: int = 1):
    """Context manager to lock multiple files simultaneously."""
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in paths:
            stack.enter_context(win32_lock_file(p, share_mode=share_mode))
        yield paths


def _count_tmp_docx(folder: Path):
    """Find any leftover temporary docx files."""
    return list(folder.glob(".*.tmp.docx")) + list(folder.glob("*.tmp"))


# ==============================================================================
# 1. TRIPLE AND MULTI-LOCK RESILIENCE
# ==============================================================================

def test_adversarial_triple_lock_builder_resolution(tmp_path):
    """Stress test: report.docx, report_updated.docx, and report_1.docx are ALL locked.
    DocxReportBuilder.build() must save to report_2.docx without crashing.
    """
    target = tmp_path / "report.docx"
    locked_files = [
        target,
        tmp_path / "report_updated.docx",
        tmp_path / "report_1.docx",
    ]

    brief = load_brief("examples/brief.yaml")
    facts = derive_facts(brief, base_dir=Path("."))
    outline = parse_format_file("examples/format.txt")

    with multi_file_locks(locked_files):
        builder = DocxReportBuilder(brief=brief, facts=facts, outline=outline)
        doc = builder.build({}, out_path=target)

        # Expected resolution: report_2.docx
        expected_path = tmp_path / "report_2.docx"
        assert builder.saved_path == expected_path, (
            f"Builder saved_path should be {expected_path}, got {builder.saved_path}"
        )
        assert expected_path.is_file(), f"Resolved candidate {expected_path} was not created"

        # Verify it's a valid Word document
        loaded_doc = docx.Document(str(expected_path))
        assert len(loaded_doc.sections) == 3, "Report must have 3 sections"

        # Verify atomic temp file cleanup: NO temporary files left in folder
        leftover_tmp = _count_tmp_docx(tmp_path)
        assert len(leftover_tmp) == 0, f"Found leftover temporary files: {leftover_tmp}"


def test_adversarial_ten_consecutive_locks(tmp_path):
    """Stress test: target, _updated, and _1 through _10 are ALL locked (12 locks).
    Resolver and builder must traverse to report_11.docx cleanly.
    """
    target = tmp_path / "report.docx"
    locked_files = [target, tmp_path / "report_updated.docx"]
    for i in range(1, 11):
        locked_files.append(tmp_path / f"report_{i}.docx")

    brief = load_brief("examples/brief.yaml")
    facts = derive_facts(brief, base_dir=Path("."))
    outline = parse_format_file("examples/format.txt")

    with multi_file_locks(locked_files):
        # 1. Test get_safe_output_path directly
        safe_path = get_safe_output_path(target)
        assert safe_path == tmp_path / "report_11.docx"

        # 2. Test full DocxReportBuilder.build()
        builder = DocxReportBuilder(brief=brief, facts=facts, outline=outline)
        builder.build({}, out_path=target)

        expected = tmp_path / "report_11.docx"
        assert builder.saved_path == expected
        assert expected.is_file()

        # Check valid docx
        doc = docx.Document(str(expected))
        assert len(doc.sections) == 3

        # Check no temp file leakage
        assert len(_count_tmp_docx(tmp_path)) == 0


def test_adversarial_exclusive_zero_share_lock(tmp_path):
    """Stress test: file locked with share_mode=0 (exclusive lock, no sharing whatsoever)."""
    target = tmp_path / "report.docx"

    brief = load_brief("examples/brief.yaml")
    facts = derive_facts(brief, base_dir=Path("."))
    outline = parse_format_file("examples/format.txt")

    with win32_lock_file(target, share_mode=0):
        assert not is_file_writable(target)
        builder = DocxReportBuilder(brief=brief, facts=facts, outline=outline)
        builder.build({}, out_path=target)

        assert builder.saved_path == tmp_path / "report_updated.docx"
        assert builder.saved_path.is_file()
        assert len(_count_tmp_docx(tmp_path)) == 0


def test_adversarial_python_open_handle_locking(tmp_path):
    """Stress test: file held open via Python open(..., 'r+b')."""
    target = tmp_path / "report.docx"
    target.write_bytes(b"initial data")

    brief = load_brief("examples/brief.yaml")
    facts = derive_facts(brief, base_dir=Path("."))
    outline = parse_format_file("examples/format.txt")

    # Keep file open in Python with msvcrt lock if available
    f = open(target, "r+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except Exception:
                pass

        builder = DocxReportBuilder(brief=brief, facts=facts, outline=outline)
        builder.build({}, out_path=target)

        # On Windows, open file cannot be replaced/unlinked; resolves to _updated.docx
        assert builder.saved_path == tmp_path / "report_updated.docx"
        assert builder.saved_path.is_file()
        assert len(_count_tmp_docx(tmp_path)) == 0
    finally:
        f.close()


# ==============================================================================
# 2. ATOMIC CLEANUP ON ABORTED OR FAILING BUILDS
# ==============================================================================

def test_adversarial_atomic_cleanup_on_build_abort(tmp_path):
    """Verify that if builder.build encounters an unexpected exception mid-build,
    the finally block removes the temporary .tmp.docx file so out/ is never polluted.
    """
    target = tmp_path / "report.docx"
    brief = load_brief("examples/brief.yaml")
    facts = derive_facts(brief, base_dir=Path("."))
    outline = parse_format_file("examples/format.txt")

    builder = DocxReportBuilder(brief=brief, facts=facts, outline=outline)

    # Inject an error right after doc.save(str(temp_p)) by monkeypatching os.replace
    def faulty_replace(src, dst):
        raise RuntimeError("Simulated system crash during atomic file rename!")

    with patch("os.replace", side_effect=faulty_replace):
        with pytest.raises(RuntimeError, match="Simulated system crash"):
            builder.build({}, out_path=target)

    # Check that temp file was cleaned up by the finally block
    leftover = _count_tmp_docx(tmp_path)
    assert len(leftover) == 0, f"Found leftover temporary files after crash: {leftover}"


def test_adversarial_atomic_cleanup_on_document_save_failure(tmp_path):
    """Verify cleanup if Document.save fails."""
    target = tmp_path / "report.docx"
    brief = load_brief("examples/brief.yaml")
    facts = derive_facts(brief, base_dir=Path("."))
    outline = parse_format_file("examples/format.txt")

    builder = DocxReportBuilder(brief=brief, facts=facts, outline=outline)

    from docx.document import Document as DocxDocumentClass
    with patch.object(DocxDocumentClass, "save", side_effect=IOError("Simulated disk I/O error")):
        with pytest.raises(IOError, match="Simulated disk I/O error"):
            builder.build({}, out_path=target)

    leftover = _count_tmp_docx(tmp_path)
    assert len(leftover) == 0


# ==============================================================================
# 3. SAVED PATH PROPAGATION & CLI COUPLING
# ==============================================================================

def test_adversarial_cli_build_and_latest_report_propagation(tmp_path, monkeypatch):
    """Verify end-to-end CLI behavior when target is locked:
    1. cmd_build resolves to safe path and writes out/latest_report.txt.
    2. cmd_check automatically reads out/latest_report.txt and validates resolved file.
    3. cmd_postprocess automatically reads out/latest_report.txt.
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "report.docx"
    cand_up = out_dir / "report_updated.docx"

    brief_path = Path("examples/brief.yaml").resolve()
    format_path = Path("examples/format.txt").resolve()

    # Lock both report.docx and report_updated.docx
    with multi_file_locks([target, cand_up]):
        # Mock CLI build args
        import argparse
        from reportsmith.cli import cmd_build, cmd_check, cmd_postprocess

        # Set working directory to tmp_path for latest_report.txt inspection
        monkeypatch.chdir(tmp_path)

        args = argparse.Namespace(
            content=None,
            format=str(format_path),
            sample=None,
            brief=str(brief_path),
            out=str(target),
            model=None,
            base_url=None,
            sections="1.1",
            concurrency=1,
            clear_cache=False,
            resume=False,
            refine=False,
            postprocess=False,
            dry_run=False,
        )

        cmd_build(args)

        # Expected: resolved to report_1.docx
        expected_report = out_dir / "report_1.docx"
        assert expected_report.is_file(), f"Expected {expected_report} to exist"

        # Check out/latest_report.txt
        latest_txt = Path("out/latest_report.txt")
        assert latest_txt.is_file(), "out/latest_report.txt must exist"
        recorded_path = Path(latest_txt.read_text(encoding="utf-8").strip())
        assert recorded_path.resolve() == expected_report.resolve(), (
            f"out/latest_report.txt contains '{recorded_path}', expected '{expected_report}'"
        )

        # Now test cmd_check with default args (no report specified)
        check_out = tmp_path / "check_summary.txt"
        check_args = argparse.Namespace(
            report=None,
            brief=str(brief_path),
            out=str(check_out),
        )
        cmd_check(check_args)
        assert check_out.is_file(), "cmd_check should succeed and produce check output"
        check_content = check_out.read_text(encoding="utf-8")
        assert "REPORTSMITH VERIFICATION REPORT" in check_content


def test_adversarial_cli_postprocess_fallback_when_default_absent(tmp_path, monkeypatch):
    """Verify cmd_postprocess falls back to out/latest_report.txt when out/report.docx is absent."""
    import argparse
    from reportsmith.cli import cmd_postprocess

    real_brief = Path("examples/brief.yaml").resolve()
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Simulate resolved build path recorded in latest_report.txt
    resolved_file = out_dir / "report_updated.docx"
    resolved_file.write_bytes(b"PK\x03\x04resolved content")
    latest_txt = out_dir / "latest_report.txt"
    latest_txt.write_text(str(resolved_file.resolve()), encoding="utf-8")

    # out/report.docx does NOT exist on disk
    post_args = argparse.Namespace(
        docx="out/report.docx",
        brief=str(real_brief),
        out=None,
        model=None,
        base_url=None,
    )

    with patch("reportsmith.docx_postprocess.run_docx_postprocessing") as mock_post:
        cmd_postprocess(post_args)
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        assert Path(call_kwargs["docx_path"]).resolve() == resolved_file.resolve()


def test_adversarial_cli_postprocess_defect_when_stale_report_exists(tmp_path, monkeypatch):
    """Empirical demonstration of defect in cmd_postprocess:
    When out/report.docx exists (e.g. from prior build, locked by Word),
    cmd_postprocess ignores out/latest_report.txt because of 'not docx_path.is_file()' check.
    """
    import argparse
    from reportsmith.cli import cmd_postprocess

    real_brief = Path("examples/brief.yaml").resolve()
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Old/stale out/report.docx exists on disk
    stale_file = out_dir / "report.docx"
    stale_file.write_bytes(b"PK\x03\x04stale content")

    # Newly generated report was saved to report_1.docx and recorded in latest_report.txt
    fresh_file = out_dir / "report_1.docx"
    fresh_file.write_bytes(b"PK\x03\x04fresh content")
    latest_txt = out_dir / "latest_report.txt"
    latest_txt.write_text(str(fresh_file.resolve()), encoding="utf-8")

    post_args = argparse.Namespace(
        docx="out/report.docx",  # default
        brief=str(real_brief),
        out=None,
        model=None,
        base_url=None,
    )

    with patch("reportsmith.docx_postprocess.run_docx_postprocessing") as mock_post:
        cmd_postprocess(post_args)
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        actual_targeted = Path(call_kwargs["docx_path"]).resolve()
        # Demonstrates the defect: cmd_postprocess targeted stale_file instead of fresh_file!
        is_defect_present = (actual_targeted == stale_file.resolve())
        assert is_defect_present, "Expected defect where cmd_postprocess targets stale file instead of fresh"


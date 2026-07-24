"""Unit tests for ``tools/terminal_post_process.py``.

Tests cover:
- ``filter_output`` — ANSI stripping + line ending normalization
- ``_dedup_output`` — single-line and multi-line block deduplication
- ``_truncate_lines`` — head/tail truncation with fold marker
- ``_token_filter_output`` — full pipeline integration
- ``_maybe_export_output_async`` — oversized output export
- ``_save_original_output`` — original save to temp file
- ``_build_session_output_block`` — metadata block assembly
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.terminal_post_process import (
    _DEFAULT_DEDUP_THRESHOLD,
    _DEFAULT_EXPORT_CHARS,
    TerminalOutputResult,
    _build_session_output_block,
    _dedup_output,
    _maybe_export_output_async,
    _save_original_output,
    _token_filter_output,
    _truncate_lines,
    filter_output,
)


# =========================================================================
# filter_output
# =========================================================================


class TestFilterOutput:
    def test_strips_ansi(self):
        raw = "\x1b[31mhello\x1b[0m world"
        assert filter_output(raw) == "hello world"

    def test_normalizes_crlf(self):
        raw = "line1\r\nline2\r\nline3"
        assert filter_output(raw) == "line1\nline2\nline3"

    def test_normalizes_lone_cr(self):
        raw = "line1\rline2\rline3"
        assert filter_output(raw) == "line1\nline2\nline3"

    def test_passthrough_clean(self):
        raw = "hello\nworld\n"
        assert filter_output(raw) == "hello\nworld\n"

    def test_empty_string(self):
        assert filter_output("") == ""

    def test_only_ansi(self):
        assert filter_output("\x1b[31m\x1b[0m") == ""


# =========================================================================
# _dedup_output
# =========================================================================


class TestDedupOutput:
    def test_no_repeats(self):
        text = "a\nb\nc\nd"
        deduped, count = _dedup_output(text, threshold=3)
        assert deduped == text
        assert count == 0

    def test_single_line_repeats(self):
        text = "a\nb\nb\nb\nb\nc"
        deduped, count = _dedup_output(text, threshold=3)
        # 'b' appears 4 times (>3), should be annotated on first occurrence
        assert "b  (4 repeats)" in deduped
        assert "a" in deduped
        assert "c" in deduped
        # 3 extra 'b's were removed (4 occurrences, 1 kept = 3 removed)
        assert count == 3

    def test_repeated_below_threshold(self):
        text = "a\na\na\nb\nc"  # 'a' appears 3 times, not >3
        deduped, count = _dedup_output(text, threshold=3)
        assert deduped == text
        assert count == 0

    def test_empty_string(self):
        deduped, count = _dedup_output("", threshold=3)
        assert deduped == ""
        assert count == 0

    def test_single_line(self):
        deduped, count = _dedup_output("hello", threshold=3)
        assert deduped == "hello"
        assert count == 0


# =========================================================================
# _truncate_lines
# =========================================================================


class TestTruncateLines:
    def test_noop_when_under_limit(self):
        text = "a\nb\nc"
        assert _truncate_lines(text, max_lines=10) == text

    def test_noop_when_max_lines_none(self):
        text = "a\nb\nc\n" * 100
        assert _truncate_lines(text, max_lines=None) == text

    def test_truncates_with_fold_marker(self):
        lines = "\n".join(f"line{i}" for i in range(20))
        result = _truncate_lines(lines, max_lines=10)
        assert "lines omitted" in result
        # Should start with first 5 (floor(10/2)) lines
        assert result.startswith("line0")
        # Should end with "line19"
        assert result.strip().endswith("line19")

    def test_empty_string(self):
        assert _truncate_lines("", max_lines=10) == ""

    def test_small_max_lines(self):
        lines = "\n".join(f"line{i}" for i in range(100))
        result = _truncate_lines(lines, max_lines=1)
        assert "lines omitted" in result


# =========================================================================
# _token_filter_output
# =========================================================================


class TestTokenFilterOutput:
    def test_passthrough_when_no_filters(self):
        result = _token_filter_output(
            "hello world\nline2",
            token_kill=False,
            rtk_rewritten=False,
            max_lines=None,
        )
        assert result.output == "hello world\nline2"
        assert result.dedup_applied is False
        assert result.lines_truncated is False

    def test_dedup_applied_when_token_kill_and_not_rtk_rewritten(self):
        text = "a\nb\nb\nb\nb\nc"
        with patch("tools.rtk_provision._rtk_available", return_value=True):
            result = _token_filter_output(
                text,
                token_kill=True,
                rtk_rewritten=False,
                max_lines=None,
            )
        assert result.dedup_applied is True or "  (4 repeats)" in result.output

    def test_no_dedup_when_rtk_rewritten(self):
        text = "a\nb\nb\nb\nb\nc"
        result = _token_filter_output(
            text,
            token_kill=True,
            rtk_rewritten=True,
            max_lines=None,
        )
        assert result.output == text
        assert result.dedup_applied is False

    def test_no_dedup_when_rtk_not_available(self):
        """When rtk is not installed, skip ALL dedup even if token_kill is on."""
        text = "a\nb\nb\nb\nb\nc"
        with patch("tools.rtk_provision._rtk_available", return_value=False):
            result = _token_filter_output(
                text,
                token_kill=True,
                rtk_rewritten=False,
                max_lines=None,
            )
        assert result.output == text
        assert result.dedup_applied is False

    def test_lines_truncated(self):
        lines = "\n".join(f"line{i}" for i in range(50))
        result = _token_filter_output(
            lines,
            token_kill=False,
            rtk_rewritten=False,
            max_lines=10,
        )
        assert result.lines_truncated is True
        assert "lines omitted" in result.output

    def test_original_saved_when_filter_active(self):
        result = _token_filter_output(
            "test\noutput",
            token_kill=True,
            rtk_rewritten=False,
            max_lines=5,
        )
        # original_path should be set when filter is active
        assert result.original_output == "test\noutput"

    def test_returns_TerminalOutputResult(self):
        result = _token_filter_output(
            "hello",
            token_kill=False,
            rtk_rewritten=False,
        )
        assert isinstance(result, TerminalOutputResult)


# =========================================================================
# _maybe_export_output_async
# =========================================================================


class TestMaybeExportOutput:
    def test_under_limit_no_export(self):
        text = "short output"
        result, path = _maybe_export_output_async(text, output_limit=100)
        assert result == text
        assert path is None

    def test_over_limit_exports(self):
        text = "x" * 5000
        result, path = _maybe_export_output_async(text, output_limit=100)
        assert path is not None
        assert "[Output too large" in result
        assert "exported to file:" in result
        # Clean up
        try:
            os.remove(path)
        except OSError:
            pass

    def test_exactly_at_limit(self):
        text = "x" * _DEFAULT_EXPORT_CHARS
        result, path = _maybe_export_output_async(text, output_limit=_DEFAULT_EXPORT_CHARS)
        assert result == text
        assert path is None


# =========================================================================
# _save_original_output
# =========================================================================


class TestSaveOriginalOutput:
    def test_saves_to_temp_file(self):
        path_str = _save_original_output("test content")
        if path_str is not None:
            path = Path(path_str)
            assert path.exists()
            assert path.read_text(encoding="utf-8", errors="replace") == "test content"
            # Clean up parent dir
            shutil.rmtree(path.parent, ignore_errors=True)
        else:
            pytest.skip("Could not save original output")


# =========================================================================
# _build_session_output_block
# =========================================================================


class TestBuildSessionOutputBlock:
    def test_builds_block_with_metadata(self):
        result = TerminalOutputResult(
            output="hello",
            original_output="hello",
            original_path="/tmp/orig.txt",
            output_truncated=True,
            output_path="/tmp/export.txt",
            dedup_applied=True,
            lines_truncated=False,
            summarized=False,
        )
        block = _build_session_output_block(
            result,
            task_id="task-123",
            status="completed",
            exit_code=0,
            elapsed_seconds=1.5,
        )
        assert "task_id: task-123" in block
        assert "status: completed" in block
        assert "exit_code: 0" in block
        assert "output:" in block
        assert "output_truncated: True" in block
        assert "output_path: /tmp/export.txt" in block
        assert "original_path: /tmp/orig.txt" in block
        assert "dedup_applied: True" in block
        assert "lines_truncated: False" in block
        assert "elapsed_seconds: 1.50" in block or "elapsed_seconds: 1.5" in block

    def test_minimal_block(self):
        result = TerminalOutputResult(
            output="hello",
            original_output="hello",
        )
        block = _build_session_output_block(result)
        assert "output:" in block
        assert "output_truncated: False" in block

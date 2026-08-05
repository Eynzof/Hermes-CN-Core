"""Tests for terminal truncation spill + metadata (deferred retrieval)."""

import json
import os
import sys
from pathlib import Path

import pytest

from tools.terminal_tool import terminal_tool


# ``python3`` on Windows is often the Microsoft-Store app-execution-alias stub
# ("Python was not found...", exit 9009) rather than a real interpreter. Use
# the active interpreter explicitly so these tests run on the fork's Windows
# host. ``& <quoted-path>`` (PowerShell call operator) is required because the
# terminal wrapper runs the command via ``Invoke-Expression`` and a bare
# quoted string literal cannot start a command there.
_PY = "& '" + sys.executable.replace("'", "''") + "'"


@pytest.fixture
def small_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    import tools.tool_output_limits as lim
    monkeypatch.setattr(lim, "_cached_limits", {
        "max_bytes": 2000, "max_lines": 2000, "max_line_length": 2000,
    })
    return tmp_path


class TestTruncationSpill:
    def test_truncated_output_has_metadata_and_spill(self, small_cap):
        r = json.loads(terminal_tool(
            f"{_PY} -c \"print('marker_head'); [print(f'row_{{i}}', 'x'*80) for i in range(200)]; print('marker_tail')\"",
            task_id="t-spill-1", token_kill=False))
        assert r["exit_code"] == 0
        assert "OUTPUT TRUNCATED" in r["output"]
        assert r["output_total_chars"] > 2000
        p = Path(r["full_output_path"])
        assert p.exists()
        full = p.read_text()
        assert "marker_head" in full and "marker_tail" in full
        # The spill contains rows that were cut from the visible window.
        assert "row_100 " in full
        assert "read_file" in r["truncation_note"]

    def test_small_output_has_no_metadata(self, small_cap):
        r = json.loads(terminal_tool("echo tiny", task_id="t-spill-2"))
        assert r["exit_code"] == 0
        assert "full_output_path" not in r
        assert "output_total_chars" not in r

    def test_spill_is_redacted(self, small_cap):
        r = json.loads(terminal_tool(
            f"{_PY} -c \"print('sk-proj-' + 'a1B2c3D4e5F6g7H8i9J0' * 3); [print('pad', 'y'*90) for i in range(200)]\"",
            task_id="t-spill-3", token_kill=False))
        p = Path(r["full_output_path"])
        full = p.read_text()
        assert "a1B2c3D4e5F6g7H8i9J0a1B2c3D4e5F6g7H8i9J0" not in full

    def test_old_spills_cleaned(self, small_cap, tmp_path):
        spill_dir = tmp_path / ".hermes" / "cache" / "terminal-output"
        spill_dir.mkdir(parents=True, exist_ok=True)
        stale = spill_dir / "out-1-2-dead.log"
        stale.write_text("old")
        os.utime(stale, (1, 1))
        json.loads(terminal_tool(
            f"{_PY} -c \"[print('z'*90) for i in range(200)]\"", task_id="t-spill-4", token_kill=False))
        assert not stale.exists()

    def test_failed_command_still_gets_spill(self, small_cap):
        r = json.loads(terminal_tool(
            f"{_PY} -c \"[print('e'*90) for i in range(200)]; import sys; sys.exit(3)\"",
            task_id="t-spill-5", token_kill=False))
        assert r["exit_code"] == 3
        assert Path(r["full_output_path"]).exists()

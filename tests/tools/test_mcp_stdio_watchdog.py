"""Contract tests for the direct POSIX stdio MCP child watchdog."""

import os
import sys

import pytest

from tools import mcp_stdio_watchdog, mcp_tool


def test_is_orphaned_is_false_while_direct_parent_is_unchanged():
    original_ppid = 1234

    assert mcp_stdio_watchdog._is_orphaned(
        original_ppid,
        getppid=lambda: original_ppid,
    ) is False


def test_is_orphaned_is_true_after_direct_parent_changes():
    assert mcp_stdio_watchdog._is_orphaned(
        1234,
        getppid=lambda: 5678,
    ) is True


@pytest.mark.skipif(os.name != "posix", reason="watchdog wrapping is POSIX-only")
def test_wrap_command_uses_stable_parent_pid_and_preserves_command_tail():
    parent_pid = os.getpid()
    command = "/opt/hermes/bin/mcp-server"
    command_args = ["--label", "value with spaces", "--", "literal-tail"]

    wrapped_command, wrapped_args = mcp_tool._wrap_command_with_watchdog(
        command,
        command_args,
    )

    assert wrapped_command == sys.executable
    assert wrapped_args == [
        os.path.join(os.path.dirname(mcp_tool.__file__), "mcp_stdio_watchdog.py"),
        "--ppid",
        str(parent_pid),
        "--",
        command,
        *command_args,
    ]
    assert "--create-time" not in wrapped_args


@pytest.mark.skipif(os.name != "posix", reason="watchdog wrapping is POSIX-only")
def test_wrap_command_skips_watchdog_under_frozen_runtime(monkeypatch):
    """PyInstaller-frozen CN portable runtime: sys.executable is the CLI
    binary, so the watchdog wrapper must be skipped (spawning it with the
    watchdog script would run `hermes mcp_stdio_watchdog.py` → argparse
    "invalid choice")."""
    monkeypatch.setattr(mcp_tool.sys, "frozen", True, raising=False)
    command = "/opt/hermes/bin/mcp-server"
    command_args = ["--flag"]

    wrapped_command, wrapped_args = mcp_tool._wrap_command_with_watchdog(
        command,
        command_args,
    )

    # Watchdog skipped: command + args pass through unchanged.
    assert wrapped_command == command
    assert wrapped_args == command_args


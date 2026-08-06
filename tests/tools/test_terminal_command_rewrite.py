"""Regression tests for RTK shell command rewriting."""

from unittest.mock import patch

import pytest

from tools.terminal_command_rewrite import (
    _maybe_rewrite_shell_command_with_rtk,
    _split_shell_words,
)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status 2>&1", ["git", "status", "2>", "&", "1"]),
        ("git status 2>/dev/null", ["git", "status", "2>/dev/null"]),
        ("git status || echo failed", ["git", "status", "||", "echo", "failed"]),
        ("git status && python -V", ["git", "status", "&&", "python", "-V"]),
        ("git status |& tail -5", ["git", "status", "|&", "tail", "-5"]),
    ],
)
def test_split_shell_words_consumes_metacharacters(command, expected):
    assert _split_shell_words(command) == expected


def test_rewrite_handles_stderr_redirects_without_hanging():
    command = "git status 2>&1 || python -V 2>&1"

    with patch("tools.terminal_command_rewrite._rtk_available", return_value=True):
        rewritten, changed = _maybe_rewrite_shell_command_with_rtk(
            command, token_kill=True
        )

    assert changed is True
    assert rewritten == "rtk git status 2>&1 || rtk python -V 2>&1"


def test_codex_probe_with_redirects_returns_unchanged():
    command = (
        "which codex 2>/dev/null || "
        "npx --yes @openai/codex --version 2>&1 || echo NOT_FOUND"
    )

    with patch("tools.terminal_command_rewrite._rtk_available", return_value=True):
        rewritten, changed = _maybe_rewrite_shell_command_with_rtk(
            command, token_kill=True
        )

    assert changed is False
    assert rewritten == command

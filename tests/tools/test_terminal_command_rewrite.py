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


def test_rewrite_stderr_redirect_compound_skipped_for_safety():
    # Multi-segment commands (top-level `||`) are NOT rtk-wrapped: rtk's
    # output is not guaranteed to end with a newline, so a wrapped segment
    # followed by more output glues lines together and misleads the model.
    # The command must reach the shell unchanged (and must not hang on the
    # `2>&1` redirect tokens while parsing).
    command = "git status 2>&1 || python -V 2>&1"

    with patch("tools.terminal_command_rewrite._rtk_available", return_value=True):
        rewritten, changed = _maybe_rewrite_shell_command_with_rtk(
            command, token_kill=True
        )

    assert changed is False
    assert rewritten == command


def test_rewrite_known_single_command():
    # Single known commands are still rtk-wrapped (the safety guard must
    # not over-block simple commands).
    with patch("tools.terminal_command_rewrite._rtk_available", return_value=True):
        rewritten, changed = _maybe_rewrite_shell_command_with_rtk(
            "git status", token_kill=True
        )

    assert changed is True
    assert rewritten == "rtk git status"


def test_rewrite_find_not_wrapped():
    # Standard find usage (compound predicates) must run the real find(1);
    # rtk's find wrapper refuses `-not`/`-exec` with a hard error.
    command = "find . -name '*.cpp' -not -path './build/*'"

    with patch("tools.terminal_command_rewrite._rtk_available", return_value=True):
        rewritten, changed = _maybe_rewrite_shell_command_with_rtk(
            command, token_kill=True
        )

    assert changed is False
    assert rewritten == command


def test_rewrite_compound_command_skipped_for_safety():
    # Multi-segment commands (top-level `&&`) are NOT wrapped: rtk's output
    # is not guaranteed to end with a newline, so a wrapped segment followed
    # by more output glues lines together (e.g. `git status --short; echo x`
    # -> ` M f.txtx`) and misleads the model.  The local dedup pipeline is
    # faithful, so it handles these instead.
    command = "git status && cargo test"

    with patch("tools.terminal_command_rewrite._rtk_available", return_value=True):
        rewritten, changed = _maybe_rewrite_shell_command_with_rtk(
            command, token_kill=True
        )

    assert changed is False
    assert rewritten == command


def test_rewrite_semicolon_segments_skipped_for_safety():
    command = "git status; cargo test"

    with patch("tools.terminal_command_rewrite._rtk_available", return_value=True):
        rewritten, changed = _maybe_rewrite_shell_command_with_rtk(
            command, token_kill=True
        )

    assert changed is False
    assert rewritten == command


def test_rewrite_pipe_segments_skipped_for_safety():
    command = "git status || cargo test"

    with patch("tools.terminal_command_rewrite._rtk_available", return_value=True):
        rewritten, changed = _maybe_rewrite_shell_command_with_rtk(
            command, token_kill=True
        )

    assert changed is False
    assert rewritten == command


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

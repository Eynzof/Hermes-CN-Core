"""Shell command rewriting — prepend ``rtk`` to known commands for token reduction.

When ``token_kill=True``, this module rewrites shell commands so that known
long-running / high-output commands are piped through ``rtk``, the reasoning
toolkit binary that natively collapses repeated output lines.

Usage::

    from tools.terminal_command_rewrite import _maybe_rewrite_shell_command_with_rtk

    cmd, rewritten = _maybe_rewrite_shell_command_with_rtk(
        "git log --oneline -100", token_kill=True
    )
    # cmd == "rtk git log --oneline -100", rewritten == True
"""

from __future__ import annotations

from tools.rtk_provision import _rtk_available

# ---------------------------------------------------------------------------
# Known commands that benefit from rtk's deduplication
# ---------------------------------------------------------------------------

_RTK_KNOWN_COMMANDS: frozenset[str] = frozenset({
    "git",
    "cargo",
    "pytest",
    "npm",
    "pnpm",
    "yarn",
    "docker",
    "kubectl",
    "ls",
    "grep",
    "rg",
    "find",
    "cat",
    "head",
    "tail",
    "python",
    "python3",
    "pip",
    "pip3",
    "go",
    "rustc",
    "make",
    "cmake",
    "curl",
    "wget",
    "ps",
    "df",
    "du",
    "netstat",
    "ss",
    "systemctl",
    "journalctl",
    # PowerShell cmdlets (Windows)
    "Get-ChildItem",
    "Get-Content",
    "Select-String",
    "Get-Process",
    "Get-Service",
    "Get-EventLog",
    "Write-Output",
    "ForEach-Object",
    "Where-Object",
})


# ---------------------------------------------------------------------------
# Shell segment helpers
# ---------------------------------------------------------------------------


def _is_quote_char(ch: str) -> bool:
    """Return True if *ch* is a quote character."""
    return ch in ("'", '"', "`")


def _read_shell_word(command: str, start: int) -> tuple[str, int]:
    """Read one shell token preserving quotes/escapes, starting at *start*.

    Returns (token_text, next_position).  This mirrors the internal
    ``_read_shell_token`` from ``terminal_tool.py``.
    """
    i = start
    n = len(command)
    if i >= n:
        return "", i

    ch = command[i]
    # Quoted string
    if ch in ("'", '"'):
        quote = ch
        i += 1
        while i < n and command[i] != quote:
            if command[i] == "\\" and i + 1 < n:
                i += 2  # skip escaped char
            else:
                i += 1
        if i < n:
            i += 1  # skip closing quote
        return command[start:i], i

    # Backtick command substitution
    if ch == "`":
        i += 1
        depth = 1
        while i < n and depth > 0:
            if command[i] == "`":
                depth -= 1
            elif command[i] == "\\" and i + 1 < n:
                i += 2
                continue
            i += 1
        return command[start:i], i

    # Regular token (unquoted)
    while i < n and not command[i].isspace() and command[i] not in (";", "&", "|", "(", ")"):
        if command[i] == "\\" and i + 1 < n:
            i += 2
        elif command[i] in ("'", '"', "`"):
            # Nested quote inside unquoted token
            token, i = _read_shell_word(command, i)
        else:
            i += 1
    return command[start:i], i


def _split_shell_segments(command: str) -> list[str]:
    """Split a shell command into segments at ``;``, ``&&``, ``||``, ``|``.

    Respects quotes, escapes, and subshells. Returns a list of segment
    strings (the operators are NOT included in the segments).
    """
    segments: list[str] = []
    i = 0
    n = len(command)
    seg_start = 0

    while i < n:
        ch = command[i]

        # Skip whitespace
        if ch.isspace():
            i += 1
            continue

        # Handle quotes
        if _is_quote_char(ch):
            _, i = _read_shell_word(command, i)
            continue

        # Handle subshell $(...)
        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            depth = 1
            i += 2
            while i < n and depth > 0:
                if command[i] == "(":
                    depth += 1
                elif command[i] == ")":
                    depth -= 1
                elif command[i] in ("'", '"', "`"):
                    _, i = _read_shell_word(command, i)
                    continue
                i += 1
            continue

        # Handle pipeline/conditional separators
        if ch == ";":
            segment = command[seg_start:i].strip()
            if segment:
                segments.append(segment)
            i += 1
            seg_start = i
            continue

        if ch == "&" and i + 1 < n and command[i + 1] == "&":
            segment = command[seg_start:i].strip()
            if segment:
                segments.append(segment)
            i += 2
            seg_start = i
            continue

        if ch == "|":
            # Check for || or |&
            if i + 1 < n and command[i + 1] == "|":
                segment = command[seg_start:i].strip()
                if segment:
                    segments.append(segment)
                i += 2
                seg_start = i
                continue
            # Single pipe (pipeline) — also split here because rtk wraps
            # the whole segement, but piping through rtk doesn't make sense
            # for piped commands.
            segment = command[seg_start:i].strip()
            if segment:
                segments.append(segment)
            i += 1
            seg_start = i
            continue

        i += 1

    # Last segment
    segment = command[seg_start:].strip()
    if segment:
        segments.append(segment)

    return segments


# ---------------------------------------------------------------------------
# Command rewriting
# ---------------------------------------------------------------------------


def _rewrite_shell_segment(segment: str) -> str:
    """Rewrite a single shell segment to use ``rtk``.

    Finds the first real executable token (skipping env assignments, ``sudo``,
    etc.). If the token matches ``_RTK_KNOWN_COMMANDS``, prepend ``rtk``.

    Skips segments already starting with ``rtk`` or ``rtk.exe``.
    Respects ``RTK_DISABLED=1`` prefix — leaves untouched.
    """
    trimmed = segment.lstrip()
    if not trimmed:
        return segment

    # Already starts with rtk — skip
    if trimmed.startswith("rtk ") or trimmed == "rtk" or trimmed.startswith("rtk.exe ") or trimmed == "rtk.exe":
        return segment

    # Respect RTK_DISABLED=1 environment variable prefix
    if trimmed.startswith("RTK_DISABLED=1"):
        return segment

    words = trimmed.split()
    if not words:
        return segment

    # Skip env assignments (KEY=VALUE) and 'sudo' to find the real command
    idx = 0
    while idx < len(words):
        word = words[idx]
        if "=" in word and not word.startswith("-") and idx > 0 if "=" in word.split("=", 1)[0] else False:
            # KEY=VALUE assignment — skip
            idx += 1
            continue
        if word == "sudo":
            idx += 1
            continue
        break

    if idx >= len(words):
        return segment

    cmd_token = words[idx]
    # Normalize: strip .exe suffix for comparison
    cmd_name = cmd_token[:-4] if cmd_token.lower().endswith(".exe") else cmd_token

    if cmd_name in _RTK_KNOWN_COMMANDS:
        return "rtk " + segment

    return segment


def _maybe_rewrite_shell_command_with_rtk(
    command: str,
    token_kill: bool,
    exclude_read: bool = False,
) -> tuple[str, bool]:
    """Entry point for command rewriting.

    If ``token_kill`` is disabled, ``rtk`` is not available, or the command
    already starts with ``rtk``, returns ``(command, False)`` unchanged.

    Otherwise splits into segments, rewrites each known command segment, and
    returns ``(rewritten_command, True)``.

    Args:
        command: The original shell command string.
        token_kill: Whether token-kill rewriting is enabled.
        exclude_read: If True, prevents rewriting of the ``read`` builtin
            (not in our known-commands set so this is a no-op today).

    Returns:
        A tuple of (rewritten_command, was_rewritten).
    """
    if not token_kill:
        return command, False

    if not _rtk_available():
        return command, False

    if not command or not command.strip():
        return command, False

    trimmed = command.strip()

    # Already starts with rtk — skip
    if trimmed.startswith("rtk ") or trimmed == "rtk" or trimmed.startswith("rtk.exe ") or trimmed == "rtk.exe":
        return command, False

    segments = _split_shell_segments(command)
    if not segments:
        return command, False

    rewritten_segments = [_rewrite_shell_segment(seg) for seg in segments]

    if rewritten_segments == [command]:
        # Single segment, no rewrite happened
        if rewritten_segments[0] == command.strip():
            return command, False

    # Re-join with the original whitespace delimiters
    # We need to reconstruct the command from the original string,
    # replacing each segment with its rewritten version.
    # Simple approach: just join with the same separators.
    import re

    # Find all separators in the original command
    separators = re.findall(r"(?:&&|\|\||\||;)", command)
    result_parts = []
    sep_idx = 0
    for seg in rewritten_segments:
        result_parts.append(seg)
        if sep_idx < len(separators):
            result_parts.append(separators[sep_idx])
            sep_idx += 1

    rewritten = " ".join(result_parts)

    if rewritten == command:
        return command, False

    return rewritten, True

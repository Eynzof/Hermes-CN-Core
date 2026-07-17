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

import functools
import platform
import re as _re
import shutil

from tools.rtk_provision import _rtk_available


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

_IS_WINDOWS: bool = platform.system() == "Windows"

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
})


# ---------------------------------------------------------------------------
# Shell segment helpers
# ---------------------------------------------------------------------------


def _is_quote_char(ch: str) -> bool:
    """Return True if *ch* is a quote character."""
    return ch in ("'", '"', "`")


def _find_ansi_c_end(command: str, start: int) -> int:
    """Return the index AFTER the closing ``'`` of a ``$'...'`` region."""
    i = start
    n = len(command)
    while i < n:
        c = command[i]
        if c == "\\" and i + 1 < n:
            i += 2
        elif c == "'":
            return i + 1
        else:
            i += 1
    return -1


def _find_matching_paren(command: str, open_pos: int) -> int:
    """Return the index of the ``)`` matching the ``(`` at ``command[open_pos]``.

    Respects quotes, ``$'...'``, and nested ``$(...)`` so that ``)``
    inside those constructs are not confused with the matching paren.
    """
    assert command[open_pos] == "("
    depth = 1
    i = open_pos + 1
    n = len(command)
    while i < n:
        c = command[i]
        if c == "'":
            end = command.find("'", i + 1)
            if end == -1:
                return -1
            i = end + 1
        elif c == '"':
            end = command.find('"', i + 1)
            if end == -1:
                return -1
            i = end + 1
        elif c == "`":
            end = command.find("`", i + 1)
            if end == -1:
                return -1
            i = end + 1
        elif c == "$" and i + 1 < n and command[i + 1] == "'":
            end = _find_ansi_c_end(command, i + 2)
            if end == -1:
                return -1
            i = end
        elif c == "$" and i + 1 < n and command[i + 1] == "(":
            depth += 1
            i += 2
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
            i += 1
        else:
            i += 1
    return -1


def _read_shell_word(command: str, start: int) -> tuple[str, int]:
    """Read one shell token preserving quotes/escapes, starting at *start*.

    Handles single quotes ``'...'``, double quotes ``"..."`` (with escapes),
    ANSI-C quoting ``$'...'``, backtick command substitution `` `...` ``,
    and command substitution ``$(...)``.  Returns ``(token_text, next_position)``.
    """
    i = start
    n = len(command)
    if i >= n:
        return "", i

    ch = command[i]

    # ANSI-C quoting  $'...'
    if ch == "$" and i + 1 < n and command[i + 1] == "'":
        end = _find_ansi_c_end(command, i + 2)
        if end == -1:
            return command[i:], n  # unterminated
        return command[i:end], end

    # Single or double quoted string
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

    # Command substitution  $(...)  at word start
    if ch == "$" and i + 1 < n and command[i + 1] == "(":
        end = _find_matching_paren(command, i + 1)
        if end == -1:
            return command[i:], n  # unterminated
        return command[i : end + 1], end + 1

    # Regular token (unquoted) — stop at whitespace or shell metacharacters.
    # Shell metacharacters that separate tokens: ; & | ( )
    while i < n and not command[i].isspace() and command[i] not in (";", "&", "|", "(", ")"):
        c = command[i]
        if c == "\\" and i + 1 < n:
            i += 2
        elif c == "$" and i + 1 < n and command[i + 1] == "'":
            end = _find_ansi_c_end(command, i + 2)
            if end == -1:
                i = n
                break
            i = end
        elif c == "$" and i + 1 < n and command[i + 1] == "(":
            # Command substitution embedded in a word (e.g., foo$(bar))
            end = _find_matching_paren(command, i + 1)
            if end == -1:
                i = n
                break
            i = end + 1
        elif c in ("'", '"', "`"):
            # Nested quote inside unquoted token
            token, i = _read_shell_word(command, i)
        else:
            i += 1
    return command[start:i], i


def _split_shell_segments(command: str) -> list[str]:
    """Split a shell command into segments at ``;``, ``&&``, ``||``, ``|``, and ``|&``.

    Respects quotes, escapes, ``$'...'``, and ``$(...)`` subshells so that
    separators inside them do not create spurious segments.
    Returns a list of segment strings (the operators are NOT included).
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

        # Handle ANSI-C quoting $'...'
        if ch == "$" and i + 1 < n and command[i + 1] == "'":
            end = _find_ansi_c_end(command, i + 2)
            if end != -1:
                i = end
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
                elif command[i] == "$" and i + 1 < n and command[i + 1] == "'":
                    end = _find_ansi_c_end(command, i + 2)
                    if end != -1:
                        i = end
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
            # Check for || (chain OR) or |& (pipe stderr)
            if i + 1 < n and command[i + 1] == "|":
                segment = command[seg_start:i].strip()
                if segment:
                    segments.append(segment)
                i += 2
                seg_start = i
                continue
            if i + 1 < n and command[i + 1] == "&":
                # |& — pipe stderr (bash 4+)
                segment = command[seg_start:i].strip()
                if segment:
                    segments.append(segment)
                i += 2
                seg_start = i
                continue
            # Single pipe (pipeline)
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


@functools.lru_cache(maxsize=128)
def _cmd_is_executable(name: str) -> bool:
    """Check if *name* resolves to a standalone executable on this system.

    On Windows, PowerShell cmdlets (``Select-String``, ``Where-Object``, etc.)
    are NOT standalone executables — ``shutil.which`` returns ``None`` for them.
    On POSIX, everything is treated as potentially executable.
    """
    return shutil.which(name) is not None


def _split_shell_words(command: str) -> list[str]:
    """Split a shell command string into words, respecting quotes and escapes.

    Unlike ``str.split()``, this preserves quoted arguments with spaces
    as single tokens (e.g. ``--format="%s %an"`` stays as one word).
    """
    words: list[str] = []
    i = 0
    n = len(command)
    while i < n:
        # Skip whitespace
        while i < n and command[i].isspace():
            i += 1
        if i >= n:
            break
        word, i = _read_shell_word(command, i)
        if word:
            words.append(word)
    return words


def _rewrite_shell_segment(segment: str, *, _is_piped_output: bool = False) -> str:
    """Rewrite a single shell segment to use ``rtk``.

    Finds the first real executable token (skipping env assignments, ``sudo``,
    etc.). If the token matches ``_RTK_KNOWN_COMMANDS``, prepend ``rtk``.

    Skips segments already starting with ``rtk`` or ``rtk.exe``.
    Respects ``RTK_DISABLED=1`` prefix — leaves untouched.

    Args:
        segment: The shell command segment to rewrite.
        _is_piped_output: If True, this segment's stdout is piped to another
            command, so wrapping with ``rtk`` would corrupt the data flow
            (deduplication happens before the next command sees the output).
            Internal parameter — only set by ``_maybe_rewrite_shell_command_with_rtk``.
    """
    trimmed = segment.lstrip()
    if not trimmed:
        return segment

    # Don't wrap segments whose output goes to another command via pipe
    if _is_piped_output:
        return segment

    # Already starts with rtk — skip
    if trimmed.startswith("rtk ") or trimmed == "rtk" or trimmed.startswith("rtk.exe ") or trimmed == "rtk.exe":
        return segment

    # Respect RTK_DISABLED=1 environment variable prefix
    if trimmed.startswith("RTK_DISABLED=1"):
        return segment

    words = _split_shell_words(trimmed)
    if not words:
        return segment

    # Skip env assignments (KEY=VALUE) and prefix commands
    # to find the real executable token.
    # Env assignment must be a valid identifier followed by =.
    _ENV_ASSIGN_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    _PREFIX_SKIP = frozenset({"sudo", "time", "nohup", "nice"})

    idx = 0
    while idx < len(words):
        word = words[idx]
        if _ENV_ASSIGN_RE.match(word):
            idx += 1
            continue
        if word in _PREFIX_SKIP:
            idx += 1
            continue
        break

    if idx >= len(words):
        return segment

    cmd_token = words[idx]
    # Normalize: strip .exe suffix for comparison
    cmd_name = cmd_token[:-4] if cmd_token.lower().endswith(".exe") else cmd_token

    if cmd_name not in _RTK_KNOWN_COMMANDS:
        return segment

    # On Windows, verify the command is actually a standalone executable.
    # PowerShell cmdlets (Select-String, Where-Object, etc.) are NOT
    # standalone executables — rtk would fail to launch them.
    if _IS_WINDOWS and not _cmd_is_executable(cmd_name):
        return segment

    return "rtk " + segment


def _find_separators_quote_aware(command: str) -> list[str]:
    """Find all ``;``, ``&&``, ``||``, ``|``, ``|&`` separators in *command*.

    Respects quotes, ``$'...'``, and ``$(...)`` subshells so that
    separators inside them are NOT matched (unlike a naive regex).
    """
    separators: list[str] = []
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]

        # Skip whitespace
        if ch.isspace():
            i += 1
            continue

        # Skip quoted regions
        if ch in ("'", '"', "`"):
            _, i = _read_shell_word(command, i)
            continue

        # Skip ANSI-C quoting
        if ch == "$" and i + 1 < n and command[i + 1] == "'":
            end = _find_ansi_c_end(command, i + 2)
            if end != -1:
                i = end
                continue

        # Skip $() subshell
        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            end = _find_matching_paren(command, i + 1)
            if end != -1:
                i = end + 1
                continue

        # Detect separators
        if ch == ";":
            separators.append(";")
            i += 1
            continue

        if ch == "&" and i + 1 < n and command[i + 1] == "&":
            separators.append("&&")
            i += 2
            continue

        if ch == "|":
            if i + 1 < n and command[i + 1] == "|":
                separators.append("||")
                i += 2
                continue
            if i + 1 < n and command[i + 1] == "&":
                separators.append("|&")
                i += 2
                continue
            separators.append("|")
            i += 1
            continue

        i += 1

    return separators


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

    # Find all separators using a quote-aware scanner (not a blind regex).
    separators = _find_separators_quote_aware(command)

    # Determine which segments have their stdout piped to another command.
    # When segment[i] is followed by a ``|`` or ``|&`` separator, its output
    # goes directly into the next command — wrapping with ``rtk`` would
    # deduplicate the data BEFORE the next command processes it,
    # corrupting the pipeline (e.g. ``git log | grep fix`` would miss
    # commits that rtk collapsed).
    piped_output_indices: set[int] = set()
    for i, sep in enumerate(separators):
        if sep in ("|", "|&"):
            piped_output_indices.add(i)

    rewritten_segments = [
        _rewrite_shell_segment(seg, _is_piped_output=(i in piped_output_indices))
        for i, seg in enumerate(segments)
    ]

    if rewritten_segments == [command]:
        # Single segment, no rewrite happened
        if rewritten_segments[0] == command.strip():
            return command, False

    # Re-join with the original whitespace delimiters
    # replacing each segment with its rewritten version.
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

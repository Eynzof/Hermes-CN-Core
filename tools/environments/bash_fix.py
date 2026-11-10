"""Windows Git Bash compatibility fixes for selected native POSIX commands.

Git for Windows ships a substantial POSIX userland, but a few command names
commonly emitted for Linux or macOS are absent even though an equivalent is
already available.  This module rewrites only verified, behaviorally compatible
command words.  It does not install software and deliberately leaves commands
without a faithful equivalent untouched.

Windows-style backslash paths (``D:\\repo\\src``, ``\\\\server\\share``,
``~\\Desktop``, ``.\\build``) — whether used as arguments, redirection targets,
or as the command word itself (``C:\\tools\\rg.exe``) — are rewritten to the
forward-slash spellings Git Bash understands, and the cmd.exe-only
``cd /d <path>`` form loses its flag
(``cd`` accepts a single argument in Bash).  Git Bash virtual POSIX absolute
paths are rewritten to the native spellings native Windows executables can
resolve: ``/tmp/x`` becomes the real Windows temp directory (``/tmp`` in Git
Bash is a virtual mount, so a native tool would otherwise read it as
``<current-drive>:\\tmp\\x``) and ``/c/x``/``/d/x`` become ``C:/x``/``D:/x``.
This makes a POSIX-style command behave the same under Git Bash and native
POSIX bash.  A redundant leading shell
invocation — ``bash cd /c/dev/x && ...`` or ``bash -c 'cd C:\\x && rev'`` —
is unwrapped (and the ``-c`` inline script is scanned for fallbacks and
paths) because the Bash tool already runs the whole string via bash, so
``bash cd ...`` would otherwise try to open ``cd`` as a script file and fail.
Under an active command wrapper (``env``/``nohup``/``timeout``/...) the shell
word is an operand of that wrapper, so ``bash -c '<script>'`` keeps its shape
and only the inline script is fixed in place.

Command wrappers whose operand is itself a command are scanned as command
contexts so missing POSIX commands behind them get their Git Bash fallback:
``timeout`` (its one DURATION operand is consumed first), ``stdbuf``, ``nice``,
and ``xargs`` (bundled Git Bash executables that exec their operand), plus the
fallback wrappers ``gtimeout`` and ``watch`` (which also record their own
fallback definition; ``watch`` re-runs its command through ``eval "$*"`` in
the same shell, matching procps ``watch``'s ``sh -c`` behavior).  Fallback
definitions are exported (``export -f``) so nested shells — the standalone
runner scripts and ``bash -c`` operands — inherit them.

Rewrites are conservative: the
unquoted word must look unambiguously like a Windows path, so quoted data,
tool-level escape sequences, short ambiguous words such as ``a\\nb``, and
single-segment relative paths such as ``foo\\bar`` are preserved byte-for-byte.
Words whose normalized form needs it (spaces, ``&``, ``;``, ...) are emitted
inside double quotes; glob metacharacters stay unquoted so ``D:/x/*.txt`` still
performs pathname expansion.

The scanner is shell-aware: quoted text, comments, heredoc and here-string
bodies, assignments, case patterns, and ordinary arguments are data, not
commands.  Nested command substitutions and process substitutions are scanned
as their own command contexts.

Unquoted ``nul``/``NUL`` output redirection targets are rewritten to
``/dev/null`` (Git Bash would otherwise quietly create a real file named
``nul``), and command names with no faithful Windows Git Bash equivalent
are recorded in ``BashFix.unsupported`` together with the reason — for
those the command text is left byte-for-byte unchanged so the caller can
explain the failure instead of running a guaranteed ``command not found``.

This module is Windows-only by design: :func:`fix_bash_command` returns
non-Windows input byte-for-byte unchanged, mirroring the call-site guard
in ``tools/environments/local.py``.
"""

# The scanner implementation lives in the `shell_compat` package (one module per
# responsibility).  This file is the stable facade: it holds no scanner logic,
# re-exports the historical module-level surface of the single-file scanner
# verbatim, and keeps the Windows-platform gate at the app layer.  The native
# runtime (`runtime_py`) acceleration of the upstream port is intentionally
# absent here: this project ships the pure-Python translator only.

from __future__ import annotations

import sys

from agent.re_compat import re  # noqa: F401  (historical module surface)

from tools.environments.shell_compat import bash_core as _bc
from tools.environments.shell_compat import fallback_data as _fd

# Re-export the historical surface from the implementation package.
_REV_PERL = _fd._REV_PERL
_NATIVE_DELEGATE = _fd._NATIVE_DELEGATE
_STUB_AWARE_FALLBACKS = _fd._STUB_AWARE_FALLBACKS
_PGREP_PS_NAME = _fd._PGREP_PS_NAME
_PGREP_PS_FULL = _fd._PGREP_PS_FULL
_PKILL_PS_NAME = _fd._PKILL_PS_NAME
_PKILL_PS_FULL = _fd._PKILL_PS_FULL
_ZIP_PS = _fd._ZIP_PS
_TREE_PERL = _fd._TREE_PERL
_POWERSHELL_PASTE = _fd._POWERSHELL_PASTE
_TASKLIST_PS = _fd._TASKLIST_PS
_TASKKILL_PS = _fd._TASKKILL_PS
_SYSTEMINFO_PS = _fd._SYSTEMINFO_PS
_KILLALL_PS = _fd._KILLALL_PS
_PIDOF_PS = _fd._PIDOF_PS
_COLUMN_PERL = _fd._COLUMN_PERL
_FALLBACK_BODIES = _fd._FALLBACK_BODIES
_FALLBACKS = _fd._FALLBACKS
_UNSUPPORTED_BODIES = _fd._UNSUPPORTED_BODIES
_FALLBACK_COMMAND_WRAPPERS = _fd._FALLBACK_COMMAND_WRAPPERS
_fallback_definition = _fd._fallback_definition
_single_quote = _fd._single_quote
_wrapper_runner = _fd._wrapper_runner

_ASSIGNMENT_RE = _bc._ASSIGNMENT_RE
_NAME_RE = _bc._NAME_RE
_COMMAND_START_KEYWORDS = _bc._COMMAND_START_KEYWORDS
_COMMAND_END_KEYWORDS = _bc._COMMAND_END_KEYWORDS
_LIST_KEYWORDS = _bc._LIST_KEYWORDS
_COMMAND_WRAPPERS = _bc._COMMAND_WRAPPERS
_WRAPPER_OPERAND_COUNTS = _bc._WRAPPER_OPERAND_COUNTS
_SAME_SHELL_WRAPPERS = _bc._SAME_SHELL_WRAPPERS
_SHELL_WRAPPERS = _bc._SHELL_WRAPPERS
_SHELL_C_OPTIONS = _bc._SHELL_C_OPTIONS
_WRAPPER_OPTIONS_WITH_VALUE = _bc._WRAPPER_OPTIONS_WITH_VALUE
_WRAPPER_PATH_OPTIONS = _bc._WRAPPER_PATH_OPTIONS
_WRAPPER_PATH_OPTION_LONG = _bc._WRAPPER_PATH_OPTION_LONG
_OPERATOR_CHARS = _bc._OPERATOR_CHARS
_REDIRECTION_START = _bc._REDIRECTION_START
_WORD_END_CHARS = _bc._WORD_END_CHARS
_MAX_NESTING_DEPTH = _bc._MAX_NESTING_DEPTH
_PATH_DRIVE_RE = _bc._PATH_DRIVE_RE
_PATH_SEGMENT_RE = _bc._PATH_SEGMENT_RE
_PATH_SAFE_CHARS = _bc._PATH_SAFE_CHARS
_ESCAPED_LITERAL_CHARS = _bc._ESCAPED_LITERAL_CHARS
_windows_temp_dir = _bc._windows_temp_dir
BashFix = _bc.BashFix
bash_compatibility_prelude = _bc.bash_compatibility_prelude
_read_shell_control_operator = _bc._read_shell_control_operator
_apply_heredoc_operator_move = _bc._apply_heredoc_operator_move
_fix_heredoc_trailing_operators = _bc._fix_heredoc_trailing_operators
_HEREDOC_TRAILING_OPERATORS = _bc._HEREDOC_TRAILING_OPERATORS
_process_unquoted = _bc._process_unquoted

_Wrapper = _bc._BashWrapper
_HereDoc = _bc._BashHereDoc
_Scanner = _bc._BashFixScanner

def fix_bash_command(command: str) -> BashFix:
    """Rewrite selected native POSIX commands for Windows Git Bash.

    Non-Windows input is always returned byte-for-byte unchanged.  On Windows,
    only literal command words with verified equivalents are changed; unknown
    or semantically ambiguous commands are left for Bash to handle normally,
    and names with no faithful equivalent are reported through
    :attr:`BashFix.unsupported` without touching the command text.  The scan
    runs through the `shell_compat` translator package (the lossless tolerant
    IR + rule modules); this thin wrapper preserves the historical public
    surface and the explicit win32 gate at the call-site layer.  The package's
    own :func:`fix_bash_command` is the ungated kernel, reached on the Windows
    path only.
    """
    if sys.platform != "win32" or not command:
        return BashFix(command)
    # The translator is shell-aware and linear: it exits without allocating
    # generated shell code when nothing changes, and repairs misplaced heredoc
    # control operators internally.
    return _bc.fix_bash_command(command)

__all__ = [
    "BashFix",
    "_ASSIGNMENT_RE",
    "_COLUMN_PERL",
    "_COMMAND_END_KEYWORDS",
    "_COMMAND_START_KEYWORDS",
    "_COMMAND_WRAPPERS",
    "_ESCAPED_LITERAL_CHARS",
    "_FALLBACKS",
    "_FALLBACK_BODIES",
    "_FALLBACK_COMMAND_WRAPPERS",
    "_HEREDOC_TRAILING_OPERATORS",
    "_HereDoc",
    "_KILLALL_PS",
    "_LIST_KEYWORDS",
    "_MAX_NESTING_DEPTH",
    "_NAME_RE",
    "_NATIVE_DELEGATE",
    "_OPERATOR_CHARS",
    "_PATH_DRIVE_RE",
    "_PATH_SAFE_CHARS",
    "_PATH_SEGMENT_RE",
    "_PGREP_PS_FULL",
    "_PGREP_PS_NAME",
    "_PIDOF_PS",
    "_PKILL_PS_FULL",
    "_PKILL_PS_NAME",
    "_POWERSHELL_PASTE",
    "_REDIRECTION_START",
    "_REV_PERL",
    "_SAME_SHELL_WRAPPERS",
    "_SHELL_C_OPTIONS",
    "_SHELL_WRAPPERS",
    "_STUB_AWARE_FALLBACKS",
    "_SYSTEMINFO_PS",
    "_Scanner",
    "_TASKKILL_PS",
    "_TASKLIST_PS",
    "_TREE_PERL",
    "_UNSUPPORTED_BODIES",
    "_WORD_END_CHARS",
    "_WRAPPER_OPERAND_COUNTS",
    "_WRAPPER_OPTIONS_WITH_VALUE",
    "_WRAPPER_PATH_OPTIONS",
    "_WRAPPER_PATH_OPTION_LONG",
    "_Wrapper",
    "_ZIP_PS",
    "_apply_heredoc_operator_move",
    "_fallback_definition",
    "_fix_heredoc_trailing_operators",
    "_process_unquoted",
    "_read_shell_control_operator",
    "_single_quote",
    "_windows_temp_dir",
    "_wrapper_runner",
    "bash_compatibility_prelude",
    "fix_bash_command",
    "re",
]

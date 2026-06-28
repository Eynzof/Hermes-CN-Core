"""Shared ANSI color utilities for Hermes CLI modules."""

import os
import sys


def should_use_color() -> bool:
    """Return True when colored output is appropriate.

    Precedence: NO_COLOR / TERM=dumb disable color unconditionally
    (https://no-color.org/); then an explicit force flag (FORCE_COLOR /
    CLICOLOR_FORCE) enables it even when stdout is not a TTY; otherwise fall
    back to the TTY check.

    The force flags matter for the desktop: the Tauri terminal feature spawns
    Hermes with FORCE_COLOR=1 / CLICOLOR_FORCE=1 set (see Hermes-CN-Desktop
    src/commands/terminal.rs build_terminal_env). Without honoring them here,
    those vars were silently ignored and the in-app / external terminal showed
    monochrome output. See FORK_NOTES P-032.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    force = os.environ.get("FORCE_COLOR")
    if force is not None and force != "0":
        return True
    if os.environ.get("CLICOLOR_FORCE") == "1":
        return True
    if not sys.stdout.isatty():
        return False
    return True


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


def color(text: str, *codes) -> str:
    """Apply color codes to text (only when color output is appropriate)."""
    if not should_use_color():
        return text
    return "".join(codes) + text + Colors.RESET

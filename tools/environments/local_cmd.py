"""Opt-in cmd.exe routing and startup-script encoding for the local environment."""

import base64
import gzip
import os
import re

_SIMPLE_COMMAND_PATTERNS = (
    "dir", "echo", "type", "copy", "move", "del", "erase",
    "mkdir", "md", "rmdir", "rd", "cd", "chdir", "cls",
    "ver", "whoami", "hostname", "where",
)

_SIMPLE_COMMAND_RE = re.compile(
    r"^(?:" + "|".join(_SIMPLE_COMMAND_PATTERNS) + r")\b",
    re.IGNORECASE,
)

_CMD_METACHAR_RE = re.compile(r"""[|&<>;`$%()'\"^!*?\[\]{}\n\r]""")

_CMD_STATEFUL_RE = re.compile(
    r"^(?:cd|chdir|pushd|popd|set|setx|start|call|exit)\b", re.IGNORECASE
)


def is_simple_command(command: str) -> bool:
    """True when *command* looks like a bare cmd.exe-compatible builtin.

    Coarse classifier from the P-042 plan: matches on the leading verb only
    (``dir``/``echo``/``type``/``copy``/``move``/``del``/``mkdir``/``rmdir``/
    ``cd`` …).  Callers that actually *route* to cmd.exe must additionally pass
    :func:`_cmd_fast_path_eligible`, which rejects metacharacters and stateful
    builtins so behaviour can't diverge from PowerShell.
    """
    return bool(command and _SIMPLE_COMMAND_RE.match(command.strip()))


def _cmd_fast_path_eligible(command: str) -> bool:
    """Strict gate: *command* is a bare builtin AND safe to run via cmd.exe.

    Requires :func:`is_simple_command`, no shell metacharacters (so quoting /
    redirection / chaining / globbing can't be reinterpreted), and no cwd/env-
    mutating builtin (a one-shot ``cmd /c`` couldn't persist the change the
    tracked session expects).  Everything it rejects simply falls through to the
    unchanged PowerShell path — safety over coverage.
    """
    if not command:
        return False
    stripped = command.strip()
    if not is_simple_command(stripped):
        return False
    if _CMD_METACHAR_RE.search(stripped):
        return False
    if _CMD_STATEFUL_RE.match(stripped):
        return False
    return True


def _resolve_cmd_fast_path(shell_type: str) -> bool:
    """True when the opt-in cmd.exe fast path is enabled (Windows + PowerShell).

    Canonical setting ``terminal.cmd_fast_path`` in config.yaml; the internal
    ``HERMES_CMD_FAST_PATH`` env var bridges it and wins when set.  Default OFF
    — the persistent-session fast path is faster and preserves PowerShell
    semantics, so cmd.exe routing is only for deployments that opt in.
    """
    from tools.environments.local import _IS_WINDOWS
    if not _IS_WINDOWS or shell_type not in ("powershell", "pwsh"):
        return False
    override = os.environ.get("HERMES_CMD_FAST_PATH")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        return bool((cfg.get("terminal") or {}).get("cmd_fast_path", False))
    except Exception:
        return False


def _decode_cmd_output(data: bytes) -> str:
    """Decode cmd.exe output: UTF-8 first, then the system ANSI code page.

    cmd builtins emit text in the console/OEM code page (cp936/GBK on a Chinese
    Windows), so a hard UTF-8 decode would mojibake CJK.  Mirrors the file-read
    fallback in file_operations (``_decode_file_bytes``).
    """
    from tools.environments.local import _IS_WINDOWS
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if _IS_WINDOWS:
        try:
            return data.decode("mbcs", errors="replace")
        except (LookupError, ValueError):
            pass
    return data.decode("utf-8", errors="replace")


def _encode_startup_script(script: str) -> str:
    """Encode a multi-line startup script as a self-decoding one-liner.

    Long multi-line scripts passed through the Windows command line to MSYS2
    bash get corrupted (argv quoting heuristics).  A single-line base64+gzip
    payload contains only safe ASCII characters and sidesteps every quoting
    issue; the receiving shell decodes and evals it.

    Ported from kimix ``bash_tool._encode_startup_script`` (stdlib
    ``base64``/``gzip`` replace kimix's ``pybase64`` — identical output).
    This is one third of the interactive-Git-Bash bootstrap set, together
    with :func:`bash_compatibility_prelude` (bash_fix.py) and
    :func:`_with_msystem_neutralized`.
    """
    payload = base64.b64encode(gzip.compress(script.encode("utf-8"))).decode("ascii")
    return "eval \"$(printf '%s' '" + payload + "' | base64 -d | gzip -d)\""

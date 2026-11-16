"""Terminal mode selection and existing background-session continuation."""

import json
import logging
from typing import Optional
from tools.registry import tool_error

logger = logging.getLogger("tools.terminal_tool")

def _normalize_terminal_mode(
    mode: Optional[str],
    interactive: bool,
    background: bool,
) -> tuple[bool, bool]:
    """Normalize a kimi-style ``mode`` value into (background, interactive).

    ``"run"``/``"execute"``/``"foreground"``/``"fg"`` → foreground.
    ``"background"``/``"bg"``/``"async"``/``"detach"`` → background.
    ``"interactive"``/``"repl"``/``"shell"`` → background + interactive.

    Unknown modes fall back to the explicit ``background``/``interactive``
    flags (LLM-friendly: never hard-fail on a synonym).
    """
    if not mode:
        return background, interactive
    m = str(mode).strip().lower()
    if m in ("run", "execute", "foreground", "fg"):
        return False, interactive
    if m in ("background", "bg", "async", "detach"):
        return True, interactive
    if m in ("interactive", "repl", "shell"):
        return True, True
    logger.warning(
        "Unknown terminal mode %r — falling back to explicit flags "
        "(background=%s, interactive=%s)",
        mode, background, interactive,
    )
    return background, interactive


def _interactive_shell_command() -> str:
    """Return the shell command that starts a persistent interactive session.

    Used when ``terminal(mode='interactive')`` is called without an explicit
    command — the model can also pass its own REPL (e.g. ``python``).
    """
    import sys

    if sys.platform == "win32":
        try:
            from tools.environments.local import _resolve_shell

            shell_type, _ = _resolve_shell()
        except Exception:
            shell_type = "powershell"
        if shell_type == "bash":
            return "bash -i"
        if shell_type == "pwsh":
            return "pwsh -NoLogo -NoExit"
        return "powershell -NoLogo -NoExit"
    return "bash -i"


def _continue_background_session(
    process_id: str,
    command: str,
    timeout: int,
    wait_for_pattern: Optional[str] = None,
    inactivity_timeout: Optional[int] = None,
) -> str:
    """Send ``command`` to an existing background process's stdin and wait for
    its new output — kimi-style ``Bash(task_id=...)`` session continuation.

    Returns a JSON string (registry wait result, redacted).
    """
    try:
        from tools.process_registry import _redact_process_result, process_registry

        proc = process_registry.get(process_id)
        if proc is None:
            return tool_error(
                f"No background process with session_id {process_id}. "
                "Start one with terminal(background=true) (or mode='interactive')."
            )
        if proc.exited:
            return tool_error(
                f"Process {process_id} already exited "
                f"(exit_code={proc.exit_code}, reason={proc.completion_reason}). "
                "Start a new one with terminal(background=true)."
            )

        baseline = len(proc.output_buffer or "")
        write_result = process_registry.submit_stdin(process_id, command)
        if write_result.get("status") != "ok":
            return json.dumps(write_result, ensure_ascii=False)

        wait_result = process_registry.wait(
            process_id,
            timeout=timeout,
            pattern=wait_for_pattern,
            inactivity_timeout=inactivity_timeout,
            since_chars=baseline,
        )
        return json.dumps(_redact_process_result(wait_result), ensure_ascii=False)
    except Exception as e:
        logger.error("Session continuation failed: %s", e, exc_info=True)
        return json.dumps({
            "output": "",
            "exit_code": -1,
            "error": f"Session continuation failed: {e}",
            "status": "error",
        }, ensure_ascii=False)

"""Process-local, typed conversation backend for the Dashboard bridge.

The backend owns the dangerous parts (agent construction, session-context
binding, tool/memory/background-review suppression, interrupt lifecycle) and
exposes only ``submit`` / ``discard`` / ``readiness`` to the pipe layer. It
never touches the legacy JSON-RPC dispatcher, never serializes credentials,
and keeps the bounded user/assistant history in memory for the life of the
connection only.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, ContextManager

from hermes_cli.dashboard_bridge.runtime import ConversationRuntime

_log = logging.getLogger(__name__)

AgentFactory = Callable[..., Any]
Emit = Callable[[str, dict], None]

MAX_HISTORY_MESSAGES = 32


class _BridgeFailClosed(RuntimeError):
    """Raised by fail-closed callbacks when a forbidden surface is touched."""


def _fail(surface: str):
    def _raise(*_args, **_kwargs):
        raise _BridgeFailClosed(
            f"dashboard bridge refuses {surface}: tools, actions, approvals, "
            "clarify, secret, sudo and non-text surfaces are disabled"
        )

    return _raise


def bridge_agent_factory(correlation_id: str):
    """Construct a restricted agent through ``tui_gateway.server._make_agent``.

    The bridge never calls the legacy JSON-RPC dispatcher: construction goes
    through the same seam the TUI/Desktop use, but with an empty toolset,
    memory and background review disabled, and every non-text callback
    replaced by a fail-closed raise.
    """
    from tui_gateway.server import _make_agent

    return _make_agent(
        sid="dashboard-bridge",
        key=correlation_id,
        session_id=correlation_id,
        enabled_toolsets_override=[],
        skip_memory_override=True,
        skip_background_review_override=True,
        callback_overrides={
            "tool_calls_committed_callback": _fail("tool_calls_committed"),
            "tool_start_callback": _fail("tool_start"),
            "tool_complete_callback": _fail("tool_complete"),
            "tool_progress_callback": _fail("tool_progress"),
            "tool_gen_callback": _fail("tool_gen"),
            "clarify_callback": _fail("clarify"),
            "read_terminal_callback": _fail("read_terminal"),
            "read_preview_callback": _fail("read_preview"),
            "read_window_below_callback": _fail("read_window_below"),
            "status_callback": lambda *a, **k: None,
            "notice_callback": lambda *a, **k: None,
            "notice_clear_callback": lambda *a, **k: None,
            "thinking_callback": lambda *a, **k: None,
            "reasoning_callback": lambda *a, **k: None,
            "reaction_callback": lambda *a, **k: None,
            "interim_assistant_callback": lambda *a, **k: None,
        },
    )


@contextmanager
def _dashboard_session_context(session_key: str, cwd: str) -> ContextManager[Any]:
    """Bind the gateway session context for the turn thread and clear it after."""
    tokens: list[Any] = []
    try:
        from tui_gateway.server import _set_session_context

        tokens = _set_session_context(
            session_key,
            cwd=cwd,
            ui_session_id="dashboard-bridge",
        )
    except Exception:
        tokens = []
    try:
        yield
    finally:
        if tokens:
            try:
                from tui_gateway.server import _clear_session_context

                _clear_session_context(tokens)
            except Exception:
                pass


class DashboardConversationBackend:
    """One backend per pipe connection; at most one in-flight conversation."""

    def __init__(
        self,
        agent_factory: AgentFactory,
        *,
        session_key: str = "dashboard-bridge",
        cwd: str | None = None,
        max_history_messages: int = MAX_HISTORY_MESSAGES,
    ) -> None:
        self._session_key = session_key
        self._cwd = cwd or str(Path.cwd())
        self._runtime = ConversationRuntime(
            agent_factory,
            context_factory=lambda: _dashboard_session_context(
                self._session_key, self._cwd
            ),
            history_limit=max_history_messages,
        )

    def readiness(self) -> dict:
        return {"status": "ready"}

    def submit(self, input_text: str, correlation_id: str, emit: Emit) -> bool:
        return self._runtime.submit(input_text, correlation_id, emit)

    def discard(self, correlation_id: str) -> bool:
        return self._runtime.discard(correlation_id)

    def in_flight(self, correlation_id: str | None = None) -> bool:
        return self._runtime.in_flight(correlation_id)

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._runtime.wait_idle(timeout)

    def close(self) -> None:
        self._runtime.close()

    @property
    def history(self) -> list[dict[str, str]]:
        return self._runtime.history
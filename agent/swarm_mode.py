"""Swarm mode state tracker for the AgentSwarm system.

Thread-safe via ``contextvars`` (matching the ``tools/approval.py`` pattern).
"""

from contextvars import ContextVar
from enum import Enum
from typing import Optional


class SwarmTrigger(Enum):
    """How swarm mode was entered."""

    MANUAL = "manual"  # /swarm on — persists across turns
    TASK = "task"      # /swarm <prompt> — auto-exits after one turn
    TOOL = "tool"      # agent_swarm tool call — auto-exits after the turn


class SwarmMode:
    """Per-agent swarm mode tracker.

    Thread-safe via ``ContextVar`` so concurrent subagents each see their
    parent's swarm state correctly without cross-talk.

    Usage::

        mode = SwarmMode()
        mode.enter(SwarmTrigger.MANUAL)
        assert mode.is_active
        mode.exit()
        assert not mode.is_active
    """

    _active_trigger: ContextVar[Optional[SwarmTrigger]] = ContextVar(
        "swarm_trigger", default=None
    )

    # ── lifecycle ──────────────────────────────────────────────────────

    def enter(self, trigger: SwarmTrigger) -> None:
        """Enter swarm mode with the given *trigger*.

        If already in swarm mode this is a no-op (nested mode is not
        supported).
        """
        if self._active_trigger.get() is not None:
            return
        self._active_trigger.set(trigger)

    def exit(self) -> None:
        """Exit swarm mode.  Safe to call when not active."""
        trigger = self._active_trigger.get()
        self._active_trigger.set(None)
        if trigger is not None and trigger != SwarmTrigger.TOOL:
            self._inject_exit_reminder()

    # ── queries ────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """True when swarm mode is currently active."""
        return self._active_trigger.get() is not None

    @property
    def trigger(self) -> Optional[SwarmTrigger]:
        """The trigger that activated swarm mode, or ``None``."""
        return self._active_trigger.get()

    @property
    def should_auto_exit(self) -> bool:
        """True when the mode should be exited at end of the current turn."""
        t = self._active_trigger.get()
        return t in (SwarmTrigger.TASK, SwarmTrigger.TOOL)

    # ── reminder injection ────────────────────────────────────────────

    def _inject_enter_reminder(self) -> None:
        """Instruct the model to use agent_swarm for parallel execution.

        The reminder text is loaded from the bundled markdown file.
        This should be injected as a system-style user message so the
        model sees the swarm-mode guidance on every turn while active.
        """
        try:
            import os
            _here = os.path.dirname(__file__)
            _path = os.path.join(_here, "swarm-mode-enter-reminder.md")
            if os.path.exists(_path):
                with open(_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except Exception:
            pass
        return None

    def _inject_exit_reminder(self) -> None:
        """Instruct the model to return to standard operation."""
        try:
            import os
            _here = os.path.dirname(__file__)
            _path = os.path.join(_here, "swarm-mode-exit-reminder.md")
            if os.path.exists(_path):
                with open(_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except Exception:
            pass
        return None

    # ── convenience helpers ────────────────────────────────────────────

    def trigger_name(self) -> str:
        """Human-readable name of the current trigger (or 'inactive')."""
        t = self._active_trigger.get()
        return t.value if t else "inactive"

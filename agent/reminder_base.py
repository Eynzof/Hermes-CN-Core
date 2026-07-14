"""Base abstractions for ephemeral reminders injected at API-call time.

Reminders are short-lived guidance notes (system hints, out-of-band user
steers, etc.) that are appended to the *current turn's user message copy*
before it is sent to the model. They are never persisted to the message
history, so the upstream prompt-cache prefix stays stable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional


ReminderTarget = Literal["user_message"]


class Reminder:
    """A single ephemeral guidance note to inject before an LLM call.

    Attributes:
        type: Identifier string (e.g. "compact_reminder", "steer").
        content: Plain text content to append to the user message.
        target: Where the reminder should be injected. Currently only
            ``"user_message"`` is supported.
        metadata: Optional provider-specific metadata.
    """

    __slots__ = ("type", "content", "target", "metadata")

    def __init__(
        self,
        type: str,
        content: str,
        target: ReminderTarget = "user_message",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.type = type
        self.content = content
        self.target = target
        self.metadata = metadata or {}


class ReminderProvider(ABC):
    """Base class for any provider that emits ephemeral reminders.

    Called every API-call iteration. Providers handle their own throttling
    and state management.
    """

    @abstractmethod
    def get_reminders(
        self,
        agent: Any,
        api_call_count: int,
    ) -> List[Reminder]:
        """Return reminders to inject before this API call.

        Args:
            agent: The AIAgent instance (access context_compressor, config, etc.).
            api_call_count: The current step/iteration number (1-based).

        Returns:
            A (possibly empty) list of :class:`Reminder` objects.
        """
        ...

    def on_context_compacted(self, agent: Any) -> None:
        """Called after context compression completes.

        Override to reset throttling state so the reminder can fire again
        after compaction resets context usage. Default is a no-op.
        """
        return None

    def on_turn_start(self, agent: Any) -> None:
        """Called at the start of a new user turn. Default no-op."""
        return None

    def on_turn_end(self, agent: Any) -> None:
        """Called at the end of a user turn. Default no-op."""
        return None

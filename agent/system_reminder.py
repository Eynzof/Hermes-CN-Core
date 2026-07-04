"""ABC for system-reminder providers that inject ephemeral hints before each LLM step."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, List


class SystemReminder:
    """A single system-reminder to inject before the next LLM call.

    Attributes:
        type: Identifier string (e.g. "compact_reminder").
        content: Plain text content to append to the user message.
    """
    __slots__ = ("type", "content")

    def __init__(self, type: str, content: str) -> None:
        self.type = type
        self.content = content


class SystemReminderProvider(ABC):
    """Base class for providers that inject system reminders.

    Called every API-call iteration. Providers handle their own throttling.
    """

    @abstractmethod
    def get_reminders(
        self,
        agent: Any,           # AIAgent instance
        api_call_count: int,  # current step number (1-based)
    ) -> List[SystemReminder]:
        """Return reminders to inject before this API call.

        Args:
            agent: The AIAgent instance (access context_compressor, config, etc.).
            api_call_count: The current step/iteration number (1-based).

        Returns:
            A (possibly empty) list of SystemReminder objects.
        """
        ...

    def on_context_compacted(self, agent: Any) -> None:
        """Called after context compression completes.

        Override to reset throttling state so the reminder fires again
        after compaction resets context usage. Default is a no-op.
        """
        return None

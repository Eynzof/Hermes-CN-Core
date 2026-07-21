"""ABC for system-reminder providers that inject ephemeral hints before each LLM step."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional

from agent.reminder_base import Reminder, ReminderProvider


class SystemReminder(Reminder):
    """A single system-reminder to inject before the next LLM call.

    Inherits ``__slots__`` from :class:`Reminder`; adding an empty
    ``__slots__`` tuple keeps instances dict-free.
    """

    __slots__ = ()

    def __init__(
        self,
        type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            type=type,
            content=content,
            target="user_message",
            metadata=metadata,
        )


class SystemReminderProvider(ReminderProvider):
    """Base class for providers that inject system reminders.

    Called every API-call iteration. Providers handle their own throttling.
    """

    @abstractmethod
    def get_reminders(
        self,
        agent: Any,
        api_call_count: int,
    ) -> List[SystemReminder]:
        """Return reminders to inject before this API call.

        Args:
            agent: The AIAgent instance (access context_compressor, config, etc.).
            api_call_count: The current step/iteration number (1-based).

        Returns:
            A (possibly empty) list of :class:`SystemReminder` objects.
        """
        ...

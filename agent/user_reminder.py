"""User-reminder providers for out-of-band user messages (e.g. /steer)."""

from __future__ import annotations

from abc import abstractmethod
from threading import Lock
from typing import Any, List, Optional

from agent.reminder_base import Reminder, ReminderProvider


class UserReminder(Reminder):
    """A single out-of-band user reminder to inject before the next LLM call."""

    __slots__ = ()

    def __init__(
        self,
        type: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        super().__init__(
            type=type,
            content=content,
            target="user_message",
            metadata=metadata,
        )


class UserReminderProvider(ReminderProvider):
    """Provider for out-of-band user messages."""

    @abstractmethod
    def get_reminders(
        self,
        agent: Any,
        api_call_count: int,
    ) -> List[UserReminder]: ...


class SteerUserReminderProvider(UserReminderProvider):
    """Thread-safe queue for /steer input.

    Multiple concurrent :meth:`push` calls are serialized; all texts are
    preserved and joined in FIFO order with newlines. The queue is drained
    by :meth:`get_reminders`, which is called once per API call.
    """

    def __init__(self, max_length: Optional[int] = 4000) -> None:
        self._lock = Lock()
        self._items: List[str] = []
        self._max_length = max_length or 0

    def push(self, text: str, source: str = "cli") -> bool:
        """Append a steer text to the queue.

        Args:
            text: The steer text. Empty or whitespace-only strings are ignored.
            source: Optional source label (e.g. "cli", "gateway").

        Returns:
            True if the text was accepted, False if it was empty/whitespace.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        if self._max_length and len(cleaned) > self._max_length:
            cleaned = cleaned[: self._max_length]
        with self._lock:
            self._items.append(cleaned)
        return True

    def get_reminders(
        self,
        agent: Any,
        api_call_count: int,
    ) -> List[UserReminder]:
        """Drain the queue and return a single :class:`UserReminder`."""
        with self._lock:
            if not self._items:
                return []
            content = "\n".join(self._items)
            self._items = []
        return [
            UserReminder(
                type="steer",
                content=content,
                metadata={"source": "user"},
            )
        ]

    def peek(self) -> Optional[str]:
        """Read pending steer text without clearing the queue."""
        with self._lock:
            return "\n".join(self._items) if self._items else None

    def clear(self) -> None:
        """Drop all pending steer text."""
        with self._lock:
            self._items = []

    def __bool__(self) -> bool:
        """True when there is pending steer text."""
        with self._lock:
            return bool(self._items)

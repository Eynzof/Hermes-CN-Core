"""Unified registry for ephemeral reminder providers.

Both system reminders (e.g. compact reminders) and out-of-band user steers
are collected here and injected into the current turn's user message copy
at API-call time.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from agent.reminder_base import Reminder, ReminderProvider
from agent.system_reminder import SystemReminderProvider
from agent.user_reminder import SteerUserReminderProvider, UserReminderProvider

logger = logging.getLogger(__name__)


class ReminderRegistry:
    """Collects ephemeral reminders from system and user providers."""

    def __init__(self) -> None:
        self._system_providers: List[SystemReminderProvider] = []
        self._user_providers: List[UserReminderProvider] = []

    def register_system_provider(self, provider: SystemReminderProvider) -> None:
        """Register a system-reminder provider."""
        self._system_providers.append(provider)

    def register_user_provider(self, provider: UserReminderProvider) -> None:
        """Register a user-reminder provider."""
        self._user_providers.append(provider)

    def get_reminders(self, agent: Any, api_call_count: int) -> List[Reminder]:
        """Collect all reminders, system first, then user.

        Exceptions from individual providers are isolated so one broken
        provider cannot crash the API call.
        """
        reminders: List[Reminder] = []
        for provider in self._system_providers:
            try:
                reminders.extend(provider.get_reminders(agent, api_call_count))
            except Exception:
                logger.warning(
                    "SystemReminderProvider %s failed",
                    type(provider).__name__,
                    exc_info=True,
                )
        for provider in self._user_providers:
            try:
                reminders.extend(provider.get_reminders(agent, api_call_count))
            except Exception:
                logger.warning(
                    "UserReminderProvider %s failed",
                    type(provider).__name__,
                    exc_info=True,
                )
        return reminders

    def steer(self, text: str) -> bool:
        """Convenience: push text to the first SteerUserReminderProvider.

        Returns True if the text was accepted, False if no steer provider is
        registered or the text was empty.
        """
        for provider in self._user_providers:
            if isinstance(provider, SteerUserReminderProvider):
                return provider.push(text)
        return False

    def drain_user_reminders(self, agent: Any) -> Optional[str]:
        """Drain all user providers and return joined text.

        Used at turn end to surface any steer that arrived after the final
        assistant response (so the caller can deliver it as the next user
        turn instead of silently dropping it).
        """
        pieces: List[str] = []
        for provider in self._user_providers:
            try:
                reminders = provider.get_reminders(agent, 0)
            except Exception:
                logger.warning(
                    "UserReminderProvider %s failed during drain",
                    type(provider).__name__,
                    exc_info=True,
                )
                continue
            for reminder in reminders:
                if reminder.content:
                    pieces.append(reminder.content)
        if not pieces:
            return None
        return "\n".join(pieces)

    def clear_user_reminders(self) -> None:
        """Clear all user providers that support ``clear()``.

        Returns the drained text if any provider had pending content, otherwise
        ``None``.
        """
        for provider in self._user_providers:
            if hasattr(provider, "clear"):
                try:
                    provider.clear()
                except Exception:
                    logger.warning(
                        "Failed to clear %s",
                        type(provider).__name__,
                        exc_info=True,
                    )

    def on_context_compacted(self, agent: Any) -> None:
        """Fan out context-compaction hook to all providers."""
        for provider in self._system_providers + self._user_providers:
            try:
                provider.on_context_compacted(agent)
            except Exception:
                logger.warning(
                    "%s on_context_compacted failed",
                    type(provider).__name__,
                    exc_info=True,
                )

    def on_turn_start(self, agent: Any) -> None:
        """Fan out turn-start hook to all providers."""
        for provider in self._system_providers + self._user_providers:
            try:
                provider.on_turn_start(agent)
            except Exception:
                logger.warning(
                    "%s on_turn_start failed",
                    type(provider).__name__,
                    exc_info=True,
                )

    def on_turn_end(self, agent: Any) -> None:
        """Fan out turn-end hook to all providers."""
        for provider in self._system_providers + self._user_providers:
            try:
                provider.on_turn_end(agent)
            except Exception:
                logger.warning(
                    "%s on_turn_end failed",
                    type(provider).__name__,
                    exc_info=True,
                )

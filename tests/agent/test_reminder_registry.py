"""Tests for agent/reminder_registry.py."""

from __future__ import annotations

from typing import Any, List

import pytest

from agent.reminder_base import Reminder
from agent.reminder_registry import ReminderRegistry
from agent.system_reminder import SystemReminder, SystemReminderProvider
from agent.user_reminder import (
    SteerUserReminderProvider,
    UserReminder,
    UserReminderProvider,
)


class _SystemProvider(SystemReminderProvider):
    def __init__(self, reminders: List[SystemReminder]):
        self._reminders = reminders

    def get_reminders(self, agent: Any, api_call_count: int) -> List[SystemReminder]:
        return list(self._reminders)


class _UserProvider(UserReminderProvider):
    def __init__(self, reminders: List[UserReminder]):
        self._reminders = reminders

    def get_reminders(self, agent: Any, api_call_count: int) -> List[UserReminder]:
        return list(self._reminders)


class TestReminderRegistry:
    def test_empty_registry_returns_empty_list(self):
        registry = ReminderRegistry()
        assert registry.get_reminders(None, 1) == []

    def test_collects_system_then_user_reminders(self):
        registry = ReminderRegistry()
        registry.register_system_provider(
            _SystemProvider([SystemReminder(type="sys", content="s1")])
        )
        registry.register_user_provider(
            _UserProvider([UserReminder(type="usr", content="u1")])
        )
        reminders = registry.get_reminders(None, 1)
        assert [r.type for r in reminders] == ["sys", "usr"]

    def test_exception_in_one_provider_does_not_crash_collection(self):
        class _BadSystem(SystemReminderProvider):
            def get_reminders(
                self, agent: Any, api_call_count: int
            ) -> List[SystemReminder]:
                raise ValueError("boom")

        registry = ReminderRegistry()
        registry.register_system_provider(_BadSystem())
        registry.register_system_provider(
            _SystemProvider([SystemReminder(type="good", content="ok")])
        )
        reminders = registry.get_reminders(None, 1)
        assert len(reminders) == 1
        assert reminders[0].type == "good"

    def test_steer_pushes_to_steer_provider(self):
        registry = ReminderRegistry()
        steer = SteerUserReminderProvider()
        registry.register_user_provider(steer)
        assert registry.steer("focus") is True
        assert steer.peek() == "focus"

    def test_steer_returns_false_when_no_steer_provider(self):
        registry = ReminderRegistry()
        registry.register_user_provider(_UserProvider([]))
        assert registry.steer("focus") is False

    def test_steer_returns_false_for_empty_text(self):
        registry = ReminderRegistry()
        registry.register_user_provider(SteerUserReminderProvider())
        assert registry.steer("") is False
        assert registry.steer("   ") is False

    def test_clear_user_reminders_clears_steer_provider(self):
        registry = ReminderRegistry()
        steer = SteerUserReminderProvider()
        registry.register_user_provider(steer)
        registry.steer("note")
        registry.clear_user_reminders()
        assert steer.peek() is None

    def test_clear_user_reminders_skips_providers_without_clear(self):
        registry = ReminderRegistry()
        registry.register_user_provider(_UserProvider([]))
        # Should not raise
        registry.clear_user_reminders()

    def test_has_pending_steer_returns_false_when_empty(self):
        registry = ReminderRegistry()
        assert registry.has_pending_steer() is False

    def test_has_pending_steer_returns_true_when_items_exist(self):
        registry = ReminderRegistry()
        steer = SteerUserReminderProvider()
        registry.register_user_provider(steer)
        assert registry.has_pending_steer() is False
        registry.steer("hello")
        assert registry.has_pending_steer() is True

    def test_has_pending_steer_returns_false_after_drain(self):
        registry = ReminderRegistry()
        steer = SteerUserReminderProvider()
        registry.register_user_provider(steer)
        registry.steer("hello")
        registry.drain_user_reminders(None)
        assert registry.has_pending_steer() is False

    def test_has_pending_steer_false_when_no_steer_provider(self):
        registry = ReminderRegistry()
        registry.register_user_provider(_UserProvider([]))
        assert registry.has_pending_steer() is False

    def test_drain_user_reminders_joins_and_clears_providers(self):
        registry = ReminderRegistry()
        steer = SteerUserReminderProvider()
        registry.register_user_provider(steer)
        registry.steer("first")
        registry.steer("second")
        drained = registry.drain_user_reminders(None)
        assert drained == "first\nsecond"
        assert steer.peek() is None
        assert registry.drain_user_reminders(None) is None

    def test_drain_user_reminders_exception_isolated(self):
        class _BadUser(UserReminderProvider):
            def get_reminders(
                self, agent: Any, api_call_count: int
            ) -> List[UserReminder]:
                raise ValueError("boom")

        registry = ReminderRegistry()
        registry.register_user_provider(_BadUser())
        steer = SteerUserReminderProvider()
        registry.register_user_provider(steer)
        registry.steer("survive")
        assert registry.drain_user_reminders(None) == "survive"

    def test_lifecycle_hooks_fan_out(self):
        class _TrackingProvider(SystemReminderProvider):
            def __init__(self):
                self.calls: List[str] = []

            def get_reminders(
                self, agent: Any, api_call_count: int
            ) -> List[SystemReminder]:
                return []

            def on_context_compacted(self, agent: Any) -> None:
                self.calls.append("compacted")

            def on_turn_start(self, agent: Any) -> None:
                self.calls.append("start")

            def on_turn_end(self, agent: Any) -> None:
                self.calls.append("end")

        p = _TrackingProvider()
        registry = ReminderRegistry()
        registry.register_system_provider(p)
        registry.on_context_compacted(None)
        registry.on_turn_start(None)
        registry.on_turn_end(None)
        assert p.calls == ["compacted", "start", "end"]

    def test_lifecycle_hook_exception_isolated(self):
        class _Bad(SystemReminderProvider):
            def get_reminders(
                self, agent: Any, api_call_count: int
            ) -> List[SystemReminder]:
                return []

            def on_turn_start(self, agent: Any) -> None:
                raise RuntimeError("boom")

        registry = ReminderRegistry()
        registry.register_system_provider(_Bad())
        # Should not raise
        registry.on_turn_start(None)

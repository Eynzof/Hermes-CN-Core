"""Tests for agent/system_reminder.py — SystemReminder + SystemReminderProvider."""

from __future__ import annotations

from abc import ABC
from typing import Any, List

import pytest

from agent.reminder_base import Reminder, ReminderProvider
from agent.system_reminder import SystemReminder, SystemReminderProvider


class TestSystemReminder:
    def test_attributes(self):
        r = SystemReminder(type="t", content="c")
        assert r.type == "t"
        assert r.content == "c"

    def test_inherits_from_reminder(self):
        r = SystemReminder(type="t", content="c")
        assert isinstance(r, Reminder)

    def test_target_is_user_message(self):
        r = SystemReminder(type="t", content="c")
        assert r.target == "user_message"

    def test_metadata_optional(self):
        r = SystemReminder(type="t", content="c", metadata={"k": "v"})
        assert r.metadata == {"k": "v"}

    def test_slots_inherited_from_base(self):
        r = SystemReminder(type="t", content="c")
        with pytest.raises(AttributeError):
            r.__dict__
        assert "type" in SystemReminder.__slots__ or "type" in Reminder.__slots__
        assert "content" in SystemReminder.__slots__ or "content" in Reminder.__slots__


class TestSystemReminderProvider:
    def test_cannot_be_instantiated_directly(self):
        with pytest.raises(TypeError):
            SystemReminderProvider()

    def test_concrete_provider_must_implement_get_reminders(self):
        with pytest.raises(TypeError):

            class _Missing(SystemReminderProvider):
                pass

            _Missing()

    def test_get_reminders_signature(self):
        class _Good(SystemReminderProvider):
            def get_reminders(
                self, agent: Any, api_call_count: int
            ) -> List[SystemReminder]:
                return []

        instance = _Good()
        assert instance.get_reminders(None, 1) == []

    def test_inherits_from_reminder_provider(self):
        assert issubclass(SystemReminderProvider, ReminderProvider)
        assert issubclass(SystemReminderProvider, ABC)

    def test_on_context_compacted_default_noop(self):
        class _NoOverride(SystemReminderProvider):
            def get_reminders(
                self, agent: Any, api_call_count: int
            ) -> List[SystemReminder]:
                return []

        instance = _NoOverride()
        instance.on_context_compacted(None)  # should not raise

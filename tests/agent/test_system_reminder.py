"""Tests for agent/system_reminder.py — SystemReminder dataclass + SystemReminderProvider ABC."""

from __future__ import annotations

from abc import ABC
from typing import Any, List

import pytest

from agent.system_reminder import SystemReminder, SystemReminderProvider


# ── SystemReminder dataclass ──────────────────────────────────────────


class TestSystemReminder:
    def test_attributes(self):
        r = SystemReminder(type="t", content="c")
        assert r.type == "t"
        assert r.content == "c"

    def test_slots(self):
        r = SystemReminder(type="t", content="c")
        assert SystemReminder.__slots__ == ("type", "content")
        with pytest.raises(AttributeError):
            r.__dict__


# ── SystemReminderProvider ABC ────────────────────────────────────────


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
        """Subclass with valid get_reminders signature instantiates without error."""

        class _Good(SystemReminderProvider):
            def get_reminders(self, agent: Any, api_call_count: int) -> List[SystemReminder]:
                return []

        instance = _Good()
        assert instance.get_reminders(None, 1) == []

    def test_on_context_compacted_default_noop(self):
        """Subclass that does not override on_context_compacted — calling it does not raise."""

        class _NoOverride(SystemReminderProvider):
            def get_reminders(self, agent: Any, api_call_count: int) -> List[SystemReminder]:
                return []

        instance = _NoOverride()
        instance.on_context_compacted(None)  # should not raise

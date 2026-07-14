"""Tests for agent/reminder_base.py."""

from __future__ import annotations

from abc import ABC
from typing import Any, List

import pytest

from agent.reminder_base import Reminder, ReminderProvider


class TestReminder:
    def test_stores_attributes(self):
        r = Reminder(type="t", content="c")
        assert r.type == "t"
        assert r.content == "c"
        assert r.target == "user_message"
        assert r.metadata == {}

    def test_metadata_defaults_to_empty_dict(self):
        r = Reminder(type="t", content="c")
        assert r.metadata == {}

    def test_metadata_optional(self):
        r = Reminder(type="t", content="c", metadata={"k": "v"})
        assert r.metadata == {"k": "v"}

    def test_target_can_be_set(self):
        r = Reminder(type="t", content="c", target="user_message")
        assert r.target == "user_message"

    def test_slots(self):
        r = Reminder(type="t", content="c")
        with pytest.raises(AttributeError):
            r.__dict__


class TestReminderProvider:
    def test_cannot_be_instantiated_directly(self):
        with pytest.raises(TypeError):
            ReminderProvider()

    def test_concrete_provider_must_implement_get_reminders(self):
        with pytest.raises(TypeError):

            class _Missing(ReminderProvider):
                pass

            _Missing()

    def test_valid_subclass_instantiates(self):
        class _Good(ReminderProvider):
            def get_reminders(self, agent: Any, api_call_count: int) -> List[Reminder]:
                return []

        instance = _Good()
        assert instance.get_reminders(None, 1) == []

    def test_default_lifecycle_hooks_are_no_ops(self):
        class _NoOverride(ReminderProvider):
            def get_reminders(self, agent: Any, api_call_count: int) -> List[Reminder]:
                return []

        instance = _NoOverride()
        assert instance.on_context_compacted(None) is None
        assert instance.on_turn_start(None) is None
        assert instance.on_turn_end(None) is None

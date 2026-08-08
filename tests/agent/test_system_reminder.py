"""Tests for agent/system_reminder.py — SystemReminder + SystemReminderProvider."""

from __future__ import annotations

from abc import ABC
from typing import Any, List

import pytest

from agent.reminder_base import Reminder, ReminderProvider
from agent.system_reminder import SystemReminder, SystemReminderProvider

class TestSystemReminderProvider:
    def test_cannot_be_instantiated_directly(self):
        with pytest.raises(TypeError):
            SystemReminderProvider()

    def test_concrete_provider_must_implement_get_reminders(self):
        with pytest.raises(TypeError):

            class _Missing(SystemReminderProvider):
                pass

            _Missing()


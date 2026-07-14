"""Tests for agent/user_reminder.py."""

from __future__ import annotations

import threading

import pytest

from agent.reminder_base import Reminder
from agent.user_reminder import (
    SteerUserReminderProvider,
    UserReminder,
    UserReminderProvider,
)


class TestUserReminder:
    def test_inherits_from_reminder(self):
        r = UserReminder(type="steer", content="hello")
        assert isinstance(r, Reminder)

    def test_target_is_user_message(self):
        r = UserReminder(type="steer", content="hello")
        assert r.target == "user_message"

    def test_metadata_optional(self):
        r = UserReminder(type="steer", content="hello", metadata={"source": "cli"})
        assert r.metadata == {"source": "cli"}


class TestUserReminderProvider:
    def test_cannot_be_instantiated_directly(self):
        with pytest.raises(TypeError):
            UserReminderProvider()

    def test_get_reminders_signature(self):
        from typing import Any, List

        class _P(UserReminderProvider):
            def get_reminders(
                self, agent: Any, api_call_count: int
            ) -> List[UserReminder]:
                return []

        assert _P().get_reminders(None, 0) == []


class TestSteerUserReminderProvider:
    def test_push_accepts_non_empty_text(self):
        p = SteerUserReminderProvider()
        assert p.push("hello") is True
        assert p.peek() == "hello"

    def test_push_rejects_empty_text(self):
        p = SteerUserReminderProvider()
        assert p.push("") is False
        assert p.push("   ") is False
        assert p.push(None) is False  # type: ignore[arg-type]

    def test_push_strips_whitespace(self):
        p = SteerUserReminderProvider()
        assert p.push("  hello  ") is True
        assert p.peek() == "hello"

    def test_push_enforces_max_length(self):
        p = SteerUserReminderProvider(max_length=5)
        assert p.push("hello world") is True
        assert p.peek() == "hello"

    def test_push_no_max_length(self):
        p = SteerUserReminderProvider(max_length=0)
        long_text = "x" * 10000
        assert p.push(long_text) is True
        assert p.peek() == long_text

    def test_push_none_max_length_treated_as_unlimited(self):
        p = SteerUserReminderProvider(max_length=None)
        long_text = "x" * 10000
        assert p.push(long_text) is True
        assert p.peek() == long_text

    def test_multiple_pushes_joined_fifo_with_newlines(self):
        p = SteerUserReminderProvider()
        p.push("first")
        p.push("second")
        p.push("third")
        assert p.peek() == "first\nsecond\nthird"

    def test_get_reminders_returns_user_reminder(self):
        p = SteerUserReminderProvider()
        p.push("hello")
        reminders = p.get_reminders(None, 1)
        assert len(reminders) == 1
        assert reminders[0].type == "steer"
        assert reminders[0].content == "hello"
        assert reminders[0].target == "user_message"
        assert reminders[0].metadata.get("source") == "user"

    def test_get_reminders_clears_queue(self):
        p = SteerUserReminderProvider()
        p.push("hello")
        p.get_reminders(None, 1)
        assert p.peek() is None
        assert not p

    def test_peek_reads_without_clearing(self):
        p = SteerUserReminderProvider()
        p.push("hello")
        assert p.peek() == "hello"
        assert p.peek() == "hello"
        assert bool(p) is True

    def test_clear_drops_pending_items(self):
        p = SteerUserReminderProvider()
        p.push("hello")
        p.clear()
        assert p.peek() is None
        assert not p

    def test_bool_false_when_empty(self):
        p = SteerUserReminderProvider()
        assert not p
        p.push("x")
        assert p

    def test_thread_safety(self):
        p = SteerUserReminderProvider()
        items = [f"item-{i}" for i in range(500)]
        errors = []

        def push_item(text: str):
            try:
                p.push(text)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=push_item, args=(text,)) for text in items]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        result = p.get_reminders(None, 1)
        assert len(result) == 1
        received = result[0].content.splitlines()
        assert len(received) == len(items)
        assert set(received) == set(items)

    def test_get_reminders_is_atomic_against_push(self):
        p = SteerUserReminderProvider()
        p.push("first")
        results = []

        def drain():
            results.append(p.get_reminders(None, 1))

        def push_many():
            for i in range(1000):
                p.push(f"extra-{i}")

        t1 = threading.Thread(target=drain)
        t2 = threading.Thread(target=push_many)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        drained = [r.content for batch in results for r in batch]
        remaining = p.peek()
        if remaining:
            all_text = drained[0].splitlines() + remaining.splitlines()
        else:
            all_text = drained[0].splitlines() if drained else []
        assert len(all_text) >= 1
        assert "first" in all_text

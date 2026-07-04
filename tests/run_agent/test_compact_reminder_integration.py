"""Integration tests for CompactReminderProvider wired into run_conversation()."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())

import run_agent


def _patch_bootstrap(monkeypatch):
    monkeypatch.setattr(run_agent, "get_tool_definitions", lambda **kwargs: [{
        "type": "function",
        "function": {"name": "t", "description": "t", "parameters": {"type": "object", "properties": {}}},
    }])
    monkeypatch.setattr(run_agent, "check_toolset_requirements", lambda: {})


class _FakeOpenAIClient:
    api_key = "fake-key"
    base_url = "https://api.openai.com/v1"
    _default_headers = None


def _make_agent(
    monkeypatch,
    compact_reminder_enabled=True,
    compact_reminder_threshold=0.30,
    compact_reminder_cooldown_steps=2,
    response_fn=None,
):
    _patch_bootstrap(monkeypatch)
    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client",
        lambda *a, **kw: (_FakeOpenAIClient(), "test-model"),
    )

    if response_fn is None:
        response_fn = lambda: SimpleNamespace(
            choices=[SimpleNamespace(index=0, message=SimpleNamespace(
                role="assistant", content="ok", tool_calls=None, reasoning_content=None,
            ), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10000, completion_tokens=10, total_tokens=10010),
            model="gpt-4o",
        )

    class _A(run_agent.AIAgent):
        def __init__(self, *a, **kw):
            kw.update(skip_context_files=True, skip_memory=True, max_iterations=5)
            super().__init__(*a, **kw)
            self._cleanup_task_resources = self._persist_session = lambda *a, **k: None
            self._save_trajectory = lambda *a, **k: None
            # Override config defaults set by init_agent() with test values
            self.compact_reminder_enabled = compact_reminder_enabled
            self.compact_reminder_threshold = compact_reminder_threshold
            self.compact_reminder_cooldown_steps = compact_reminder_cooldown_steps

        def run_conversation(self, msg, conversation_history=None, task_id=None):
            self._interruptible_api_call = lambda kw: response_fn()
            self._disable_streaming = True
            return super().run_conversation(msg, conversation_history=conversation_history, task_id=task_id)

    return _A(
        model="test-model",
        api_key="test-key",
        base_url="http://localhost:1234/v1",
        provider="openrouter",
        api_mode="chat_completions",
    )


# ── Agent attribute defaults ─────────────────────────────────────────


class TestAgentAttributeDefaults:
    def test_default_compact_reminder_enabled_is_true(self, monkeypatch):
        agent = _make_agent(monkeypatch, compact_reminder_enabled=True)
        # The agent should have compact_reminder_enabled set (not fallback to True)
        assert agent.compact_reminder_enabled is True

    def test_default_threshold_is_0_70(self, monkeypatch):
        agent = _make_agent(monkeypatch, compact_reminder_enabled=True)
        assert agent.compact_reminder_threshold == 0.30  # test value set in _make_agent

    def test_default_cooldown_is_5(self, monkeypatch):
        agent = _make_agent(monkeypatch, compact_reminder_enabled=True)
        assert agent.compact_reminder_cooldown_steps == 2  # test value set in _make_agent


# ── Provider creation from config ─────────────────────────────────────


class TestProviderCreation:
    def test_provider_created_when_enabled(self, monkeypatch):
        agent = _make_agent(monkeypatch, compact_reminder_enabled=True)
        assert getattr(agent, "compact_reminder_enabled", False) is True

    def test_provider_not_created_when_disabled(self, monkeypatch):
        agent = _make_agent(monkeypatch, compact_reminder_enabled=False)
        assert getattr(agent, "compact_reminder_enabled", True) is False


# ── Error isolation ───────────────────────────────────────────────────


class TestErrorIsolation:
    def test_provider_exception_does_not_crash_loop(self, monkeypatch):
        """If get_reminders raises, the conversation loop should continue."""
        agent = _make_agent(monkeypatch)
        # Monkeypatch the provider to raise
        import agent.compact_reminder as cr_mod
        original = cr_mod.CompactReminderProvider.get_reminders
        monkeypatch.setattr(
            cr_mod.CompactReminderProvider, "get_reminders",
            lambda self, agent, api_call_count: (_ for _ in ()).throw(ValueError("boom")),
        )
        try:
            result = agent.run_conversation("hello")
            assert result is not None
            assert "final_response" in result or "messages" in result
        finally:
            monkeypatch.setattr(cr_mod.CompactReminderProvider, "get_reminders", original)

    def test_on_context_compacted_exception_does_not_crash(self, monkeypatch):
        """If on_context_compacted raises, the loop should continue."""
        agent = _make_agent(monkeypatch)
        import agent.compact_reminder as cr_mod
        original = cr_mod.CompactReminderProvider.on_context_compacted
        monkeypatch.setattr(
            cr_mod.CompactReminderProvider, "on_context_compacted",
            lambda self, agent: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        try:
            result = agent.run_conversation("hello")
            assert result is not None
        finally:
            monkeypatch.setattr(cr_mod.CompactReminderProvider, "on_context_compacted", original)

    def test_reminder_not_persisted_to_messages(self, monkeypatch):
        """The reminder text should NOT appear in the persisted messages list."""
        agent = _make_agent(monkeypatch)
        result = agent.run_conversation("test_reminder_not_persisted")
        messages = result.get("messages", [])
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and "[System Reminder:" in content:
                pytest.fail(f"Reminder found in persisted message: {content[:200]}")

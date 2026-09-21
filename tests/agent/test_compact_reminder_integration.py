"""Integration tests for CompactReminderProvider wired into run_conversation()."""
from __future__ import annotations


import copy
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
    capture=None,
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
            def _api_call(api_kwargs):
                if capture is not None:
                    capture.append(copy.deepcopy(api_kwargs))
                return response_fn()

            self._interruptible_api_call = _api_call
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

# ── Provider creation from config ─────────────────────────────────────

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


# ── System-reminder collection wiring (P-059) ─────────────────────────

class TestSystemReminderWiring:
    def test_reminder_reaches_the_wire_user_row_only(self, monkeypatch):
        """The registry's system reminders are appended to the current turn's user row in
        the API request copy — and never to the persisted transcript (the prompt-cache
        prefix must stay byte-stable and the reminder must not become durable history)."""
        import agent.compact_reminder as cr_mod

        monkeypatch.setattr(
            cr_mod.CompactReminderProvider, "get_reminders",
            lambda self, agent, api_call_count: [
                cr_mod.SystemReminder(type="compact_reminder", content="probe reminder")
            ],
        )
        captured = []
        agent = _make_agent(monkeypatch, capture=captured)
        result = agent.run_conversation("hello")

        assert captured, "no API request was captured"
        wire_users = [
            m for m in captured[0]["messages"]
            if isinstance(m, dict) and m.get("role") == "user"
        ]
        assert any(
            "[compact_reminder] probe reminder" in str(m.get("content", ""))
            for m in wire_users
        ), f"reminder missing from the wire user row: {wire_users!r}"
        for msg in result.get("messages", []):
            assert "[compact_reminder]" not in str(msg.get("content", ""))


class TestCompactionNotification:
    def test_on_context_compacted_fires_after_post_tool_compaction(self, monkeypatch):
        """A committed post-tool compaction notifies the reminder registry, so a provider's
        throttle (CompactReminderProvider) is reset against the compacted transcript."""
        import agent.turn_preflight as tp

        notified = []

        class _Registry:
            def on_context_compacted(self, agent):
                notified.append(agent)

        class _Compressor:
            last_prompt_tokens = 30_000
            threshold_tokens = 20_000
            awaiting_real_usage_after_compression = False

            def should_compress(self, tokens):
                return True

        compacted = [{"role": "user", "content": "[CONTEXT COMPACTION] summary"}]
        agent = SimpleNamespace(
            context_compressor=_Compressor(),
            compression_enabled=True,
            _usage_anchor=None,
            _reminder_registry=_Registry(),
            _safe_print=lambda *a, **k: None,
            _compress_context=lambda messages, system_message, **kw: (compacted, system_message),
        )
        monkeypatch.setattr(tp, "ensure_compression_feasibility_checked", lambda agent, tokens: None)
        monkeypatch.setattr(tp, "_clear_overflow_warn", lambda agent: None)
        monkeypatch.setattr(tp, "compression_skipped_due_to_lock", lambda agent: False)
        monkeypatch.setattr(
            tp, "conversation_history_after_compression",
            lambda agent, messages, history: list(compacted),
        )

        verdict = tp.compress_after_tool_results(
            agent, messages=[{"role": "user", "content": "hi"}], system_message="sys",
            user_message="hi", active_system_prompt="sys", conversation_history=[],
            compression_attempts=0, max_compression_attempts=3, effective_task_id="t",
            final_response=None, turn_exit_reason="unknown",
        )
        assert verdict.messages is compacted, "compaction result was not adopted"
        assert notified == [agent], "on_context_compacted was not fired after the compaction"

"""Tests for AIAgent.steer() — mid-run user message injection.

/steer lets the user add a note to the agent's current turn without
interrupting. The note is appended to the current turn's user message copy
on the next API call, so the model sees it in its natural ``user`` role.
The persisted message history is not mutated, preserving the prompt cache.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

import pytest

from agent.prompt_builder import STEER_CHANNEL_NOTE
from agent.reminder_base import Reminder
from agent.reminder_registry import ReminderRegistry
from agent.user_reminder import SteerUserReminderProvider
from run_agent import AIAgent


def _bare_agent() -> AIAgent:
    """Build an AIAgent without running __init__, then install the unified
    reminder registry with a steer provider — matches the object.__new__ stub
    pattern used elsewhere in the test suite.
    """
    agent = object.__new__(AIAgent)
    agent._reminder_registry = ReminderRegistry()
    agent._steer_provider = SteerUserReminderProvider()
    agent._reminder_registry.register_user_provider(agent._steer_provider)
    return agent


def _inject_user_copy(
    agent: AIAgent,
    user_message: Dict[str, Any],
    api_call_count: int = 1,
) -> str:
    """Mimic the user-message injection block in conversation_loop.py.

    Only system reminders are injected — steer is injected into tool
    results instead (see :func:`_inject_steer_into_tool_results`).
    """
    injections: List[str] = []
    registry = getattr(agent, "_reminder_registry", None)
    if registry is not None:
        for reminder in registry.get_system_reminders(agent, api_call_count):
            injections.append(f"[{reminder.type}] {reminder.content}")
    base = user_message.get("content", "")
    if isinstance(base, str) and injections:
        return base + "\n\n" + "\n\n".join(injections)
    return base


def _inject_steer_into_tool_results(
    agent: AIAgent,
    messages: List[Dict[str, Any]],
    api_call_count: int = 1,
) -> None:
    """Mimic the post-tool-execution steer injection in conversation_loop.py."""
    registry = getattr(agent, "_reminder_registry", None)
    if registry is not None and registry.has_pending_steer():
        # Only drain steer if there's a tool result message to inject into.
        # If no tool results exist (text-only response), steer stays pending
        # and is caught by turn_finalizer.py's drain_user_reminders() as
        # result["pending_steer"].
        _last_tool_msg = None
        for msg in reversed(messages):
            if msg.get("role") == "tool":
                _last_tool_msg = msg
                break
        if _last_tool_msg is None:
            return  # No tool result — leave steer in the queue
        for reminder in registry.get_user_reminders(agent, api_call_count):
            steer_text = f"[{reminder.type}] {reminder.content}"
            if isinstance(_last_tool_msg.get("content"), str):
                _last_tool_msg["content"] += f"\n\n{steer_text}"


class TestSteerAcceptance:
    def test_accepts_non_empty_text(self):
        agent = _bare_agent()
        assert agent.steer("go ahead and check the logs") is True
        assert agent._steer_provider.peek() == "go ahead and check the logs"
        assert agent._pending_steer == "go ahead and check the logs"

    def test_rejects_empty_string(self):
        agent = _bare_agent()
        assert agent.steer("") is False
        assert agent._steer_provider.peek() is None

    def test_rejects_whitespace_only(self):
        agent = _bare_agent()
        assert agent.steer("   \n\t  ") is False
        assert agent._steer_provider.peek() is None

    def test_rejects_none(self):
        agent = _bare_agent()
        assert agent.steer(None) is False  # type: ignore[arg-type]
        assert agent._steer_provider.peek() is None

    def test_strips_surrounding_whitespace(self):
        agent = _bare_agent()
        assert agent.steer("  hello world  \n") is True
        assert agent._steer_provider.peek() == "hello world"

    def test_concatenates_multiple_steers_with_newlines(self):
        agent = _bare_agent()
        agent.steer("first note")
        agent.steer("second note")
        agent.steer("third note")
        assert agent._steer_provider.peek() == "first note\nsecond note\nthird note"


class TestSteerDrain:
    def test_drain_returns_and_clears(self):
        agent = _bare_agent()
        agent.steer("hello")
        assert agent._drain_pending_steer() == "hello"
        assert agent._steer_provider.peek() is None

    def test_drain_on_empty_returns_none(self):
        agent = _bare_agent()
        assert agent._drain_pending_steer() is None


class TestSteerUserMessageInjection:
    def test_steer_no_longer_appears_in_user_message_copy(self):
        """Steer is now injected into tool results, not the user message."""
        agent = _bare_agent()
        agent.steer("please also check auth.log")
        user_msg = {"role": "user", "content": "what's in /var/log?"}
        api_content = _inject_user_copy(agent, user_msg, api_call_count=1)
        assert "what's in /var/log?" in api_content
        assert "[steer]" not in api_content
        # Steer is still pending because it wasn't drained by user message
        assert agent._steer_provider.peek() == "please also check auth.log"

    def test_steer_no_longer_lands_in_user_message(self):
        """Steer is no longer appended to the user message."""
        agent = _bare_agent()
        agent.steer("focus on error handling")
        user_msg = {"role": "user", "content": "run the tests"}
        api_content = _inject_user_copy(agent, user_msg, api_call_count=1)
        assert api_content == "run the tests"
        assert agent._steer_provider.peek() == "focus on error handling"

    def test_no_injection_when_no_steer_pending(self):
        agent = _bare_agent()
        user_msg = {"role": "user", "content": "hello"}
        api_content = _inject_user_copy(agent, user_msg, api_call_count=1)
        assert api_content == "hello"

    def test_persisted_user_message_is_unchanged(self):
        """Only the api_msg copy is augmented; the original user message is not."""
        agent = _bare_agent()
        agent.steer("extra note")
        user_msg = {"role": "user", "content": "original request"}
        _inject_user_copy(agent, user_msg, api_call_count=1)
        assert user_msg["content"] == "original request"


class TestSteerToolResultInjection:
    def test_steer_is_injected_into_tool_results(self):
        """Steer is now injected into the last tool result message after
        tool execution, so the LLM sees it on the very next API call."""
        agent = _bare_agent()
        agent.steer("please check auth.log")
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "content": "output", "tool_call_id": "a"},
        ]
        _inject_steer_into_tool_results(agent, messages, api_call_count=1)
        assert "[steer] please check auth.log" in messages[-1]["content"]
        assert agent._steer_provider.peek() is None

    def test_steer_appended_to_last_tool_result(self):
        """When multiple tool results exist, steer is appended to the LAST one."""
        agent = _bare_agent()
        agent.steer("update summary")
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
            {"role": "tool", "content": "file content", "tool_call_id": "a"},
            {"role": "tool", "content": "search results", "tool_call_id": "b"},
        ]
        _inject_steer_into_tool_results(agent, messages, api_call_count=1)
        assert messages[-1]["content"] == "search results\n\n[steer] update summary"
        # Earlier tool result is unchanged
        assert messages[-2]["content"] == "file content"

    def test_no_injection_when_no_tool_results(self):
        """If there are no tool result messages, steer stays pending."""
        agent = _bare_agent()
        agent.steer("hello")
        messages = [
            {"role": "assistant", "content": "text response"},
        ]
        _inject_steer_into_tool_results(agent, messages, api_call_count=1)
        assert agent._steer_provider.peek() == "hello"

    def test_apply_pending_steer_to_tool_results_is_no_op(self):
        """The old tool-result injection path is deprecated and does nothing."""
        agent = _bare_agent()
        agent.steer("should not land here")
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "content": "output", "tool_call_id": "a"},
        ]
        agent._apply_pending_steer_to_tool_results(messages, num_tool_msgs=1)
        assert messages[-1]["content"] == "output"
        # The steer is still pending in the registry.
        assert agent._steer_provider.peek() == "should not land here"


class TestSteerThreadSafety:
    def test_concurrent_steer_calls_preserve_all_text(self):
        agent = _bare_agent()
        N = 200

        def worker(idx: int) -> None:
            agent.steer(f"note-{idx}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        text = agent._drain_pending_steer()
        assert text is not None
        lines = text.split("\n")
        assert len(lines) == N
        assert set(lines) == {f"note-{i}" for i in range(N)}


class TestSteerClearedOnInterrupt:
    def test_clear_interrupt_drops_pending_steer(self):
        """A hard interrupt supersedes any pending steer."""
        agent = _bare_agent()
        agent._interrupt_requested = True
        agent._interrupt_message = None
        agent._interrupt_thread_signal_pending = False
        agent._execution_thread_id = None
        agent._tool_worker_threads = None
        agent._tool_worker_threads_lock = None

        agent.steer("will be dropped")
        assert agent._steer_provider.peek() == "will be dropped"

        agent.clear_interrupt()
        assert agent._steer_provider.peek() is None


class TestSteerToolResultDrain:
    """Steers sent during an API call are delivered on the very next API call
    via the tool-result injection path."""

    def test_steer_before_first_tool_call_lands_in_tool_result(self):
        agent = _bare_agent()
        agent.steer("early steer")
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "content": "tool output", "tool_call_id": "a"},
        ]
        _inject_steer_into_tool_results(agent, messages, api_call_count=1)
        assert "[steer] early steer" in messages[-1]["content"]
        assert agent._steer_provider.peek() is None

    def test_steer_between_calls_lands_in_tool_result(self):
        agent = _bare_agent()
        agent.steer("change approach")
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "content": "tool output", "tool_call_id": "a"},
        ]
        _inject_steer_into_tool_results(agent, messages, api_call_count=2)
        assert "[steer] change approach" in messages[-1]["content"]


class TestSteerChannelNote:
    def test_system_prompt_note_describes_user_message_injection(self):
        assert "[steer]" in STEER_CHANNEL_NOTE
        assert "user message" in STEER_CHANNEL_NOTE.lower()

    def test_system_prompt_note_no_longer_references_old_marker(self):
        from agent.prompt_builder import STEER_CHANNEL_NOTE

        assert "OUT-OF-BAND USER MESSAGE" not in STEER_CHANNEL_NOTE
        assert "[/OUT-OF-BAND USER MESSAGE]" not in STEER_CHANNEL_NOTE


class TestSteerCommandRegistry:
    def test_steer_in_command_registry(self):
        """The /steer slash command must be registered so it reaches all
        platforms (CLI, gateway, TUI autocomplete, Telegram/Slack menus).
        """
        from hermes_cli.commands import resolve_command

        cmd = resolve_command("steer")
        assert cmd is not None
        assert cmd.name == "steer"
        assert cmd.category == "Session"
        assert cmd.args_hint == "<prompt>"

    def test_steer_in_bypass_set(self):
        """When the agent is running, /steer MUST bypass the Level-1
        base-adapter queue so it reaches the gateway runner's /steer
        handler. Otherwise it would be queued as user text and only
        delivered at turn end — defeating the whole point.
        """
        from hermes_cli.commands import (
            ACTIVE_SESSION_BYPASS_COMMANDS,
            should_bypass_active_session,
        )

        assert "steer" in ACTIVE_SESSION_BYPASS_COMMANDS
        assert should_bypass_active_session("steer") is True


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

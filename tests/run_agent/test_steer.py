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

from agent.prompt_builder import (
    STEER_CHANNEL_NOTE,
    STEER_MARKER_OPEN,
    format_steer_marker,
)
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
    agent._pending_steer = None
    agent._pending_steer_lock = threading.Lock()
    agent._pending_redirect = None
    agent._pending_redirect_lock = threading.Lock()
    agent._model_request_active = threading.Event()
    agent._executing_tools = False
    agent._execution_thread_id = None
    agent._interrupt_thread_signal_pending = False
    agent._interrupt_requested = False
    agent._interrupt_message = None
    agent._active_children = []
    agent._active_children_lock = threading.Lock()
    agent._tool_worker_threads = None
    agent._tool_worker_threads_lock = None
    agent._current_streamed_assistant_text = ""
    agent._stream_needs_break = False
    agent._strip_think_blocks = lambda content: content
    agent.quiet_mode = True
    agent.api_mode = "chat_completions"
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
            steer_text = f"User injection prompt: {reminder.content}"
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



class TestActiveTurnRedirect:
    def test_rejects_when_no_turn_is_active(self):
        agent = _bare_agent()
        assert agent.redirect("change course") is False
        assert agent._pending_redirect is None

    def test_cancels_only_an_active_model_request(self):
        agent = _bare_agent()
        agent._model_request_active.set()

        assert agent.redirect("use Postgres") is True
        assert agent._pending_redirect == "use Postgres"
        assert agent._interrupt_requested is True
        assert agent._interrupt_message is None

    def test_multiple_redirects_preserve_message_boundaries(self):
        agent = _bare_agent()
        agent._model_request_active.set()

        assert agent.redirect("first correction") is True
        assert agent.redirect("second correction") is True
        assert agent._pending_redirect == (
            "first correction\n\n"
            "[Additional user correction]\n"
            "second correction"
        )

    def test_hard_interrupt_wins_over_new_redirect(self):
        agent = _bare_agent()
        agent._model_request_active.set()
        agent._interrupt_requested = True

        assert agent.redirect("too late") is False
        assert agent._pending_redirect is None

    def test_reasoning_deltas_are_display_only(self):
        """Streamed reasoning must never accumulate into replayable transcript
        state — an assistant checkpoint that inlines chain-of-thought trips
        Anthropic's output classifier and permanently bricks the session
        (deterministic empty-response storms on every replay)."""
        agent = _bare_agent()
        seen = []
        agent.reasoning_callback = seen.append

        agent._fire_reasoning_delta("visible provider thinking")

        # Displayed to the surface, but never checkpointed anywhere.
        assert seen == ["visible provider thinking"]
        assert not getattr(agent, "_current_streamed_reasoning_text", "")

    def test_response_completion_before_redirect_lock_rejects_correction(self):
        agent = _bare_agent()
        agent._model_request_active.set()
        started = threading.Event()
        outcome = {}

        def redirect():
            started.set()
            outcome["accepted"] = agent.redirect("late correction")

        with agent._pending_redirect_lock:
            worker = threading.Thread(target=redirect)
            worker.start()
            assert started.wait(timeout=1)
            # Mirrors conversation_loop clearing the request-active marker
            # under this same lock before redirect can commit its slot.
            agent._model_request_active.clear()
        worker.join(timeout=1)

        assert outcome["accepted"] is False
        assert agent._pending_redirect is None

    def test_hard_stop_wins_concurrent_redirect(self):
        agent = _bare_agent()
        agent._model_request_active.set()
        start = threading.Barrier(3)
        outcome = {}

        def redirect():
            start.wait()
            outcome["redirect"] = agent.redirect("change course")

        def hard_stop():
            start.wait()
            agent.interrupt("stop requested")

        redirect_thread = threading.Thread(target=redirect)
        stop_thread = threading.Thread(target=hard_stop)
        redirect_thread.start()
        stop_thread.start()
        start.wait()
        redirect_thread.join(timeout=1)
        stop_thread.join(timeout=1)

        assert redirect_thread.is_alive() is False
        assert stop_thread.is_alive() is False
        assert agent._interrupt_requested is True
        assert agent._interrupt_message == "stop requested"
        assert agent._pending_redirect is None

    def test_codex_app_server_hard_stop_reaches_native_session(self):
        agent = _bare_agent()
        calls = []
        agent.api_mode = "codex_app_server"
        agent._codex_session = type(
            "_CodexSession",
            (),
            {"request_interrupt": lambda self: calls.append("interrupt")},
        )()

        agent.interrupt()

        assert calls == ["interrupt"]


    def test_redirect_during_tool_execution_uses_safe_steer_boundary(self):
        agent = _bare_agent()
        agent._executing_tools = True

        assert agent.redirect("also check migrations") is True
        assert agent._pending_redirect is None
        assert agent._pending_steer == "also check migrations"
        assert agent._interrupt_requested is False


class TestActiveTurnRedirectCheckpoint:
    def test_assistant_tail_puts_correction_last(self):
        from agent.conversation_loop import _apply_active_turn_redirect

        agent = _bare_agent()
        agent._current_streamed_assistant_text = "Visible draft."
        messages = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": "committed assistant item"},
        ]

        _apply_active_turn_redirect(agent, messages, "Use Postgres instead.")

        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Use Postgres instead."
        assert sum(1 for m in messages if m["role"] == "assistant") == 1
        # Scaffolding is provider-replay text, carried in the sidecar so the
        # model still sees the interrupted context — never in the transcript.
        replayed = messages[-1]["api_content"]
        assert "Visible draft." in replayed
        assert "Context from the interrupted assistant response" in replayed
        assert replayed.endswith("Use Postgres instead.")

    def test_scaffolding_never_lands_in_transcript_content(self):
        """The checkpoint machinery is for the MODEL, not the transcript.

        Persisting ``[This response was interrupted by a user correction.]``
        into an assistant row's ``content`` or ``api_content`` painted raw
        scaffolding as the model's own prior reply (#81841). Scaffold bytes
        ride only in the *user correction's* ``api_content``; assistant
        placeholders stay clean (or ``display_kind="hidden"`` when empty).
        """
        from agent.conversation_loop import _apply_active_turn_redirect

        scaffolding = (
            "[This response was interrupted by a user correction.]",
            "Visible response before the interruption:",
            "[Context from the interrupted assistant response]",
        )

        for tail_role in ("tool", "assistant"):
            for streamed in ("Partial reply on screen.", ""):
                agent = _bare_agent()
                agent._current_streamed_assistant_text = streamed
                messages = [{"role": "user", "content": "start"}]
                if tail_role == "assistant":
                    messages.append({"role": "assistant", "content": "committed"})
                else:
                    messages.append(
                        {"role": "assistant", "tool_calls": [{"id": "a"}]}
                    )
                    messages.append(
                        {"role": "tool", "content": "out", "tool_call_id": "a"}
                    )

                _apply_active_turn_redirect(agent, messages, "New direction.")

                for msg in messages:
                    if msg.get("role") == "assistant":
                        # Scaffold must never live on an assistant row at all
                        # — content OR api_content (API replay substitutes the
                        # sidecar back into content).
                        blob = (
                            str(msg.get("content") or "")
                            + str(msg.get("api_content") or "")
                        )
                        for marker in scaffolding:
                            assert marker not in blob, (
                                f"scaffolding leaked into assistant row "
                                f"(tail={tail_role}, streamed={bool(streamed)}): "
                                f"{blob!r}"
                            )
                    if msg.get("display_kind") == "hidden":
                        continue  # dropped by every transcript surface
                    content = str(msg.get("content", ""))
                    for marker in scaffolding:
                        assert marker not in content, (
                            f"scaffolding leaked into visible content "
                            f"(tail={tail_role}, streamed={bool(streamed)}): {content!r}"
                        )

                # The user's correction is always shown verbatim.
                assert messages[-1]["content"] == "New direction."
                # ...and the model still receives the interrupted context,
                # but only via the user correction's api_content sidecar.
                replayed = messages[-1].get("api_content") or ""
                assert "[This response was interrupted by a user correction.]" in replayed
                if streamed:
                    assert streamed in replayed

    def test_checkpoint_never_replays_chain_of_thought(self):
        """Raw CoT serialized into checkpoint content reads to Anthropic's
        output classifier as reasoning-injection; because the checkpoint is
        persisted and replayed on every later call, one redirect during a
        thinking phase permanently bricked sessions with deterministic
        empty-response storms (July 2026). Reasoning must never appear in
        replayable content — in either the assistant-checkpoint or the
        merged-user-correction shape."""
        from agent.conversation_loop import _apply_active_turn_redirect

        for tail_role in ("user", "assistant"):
            agent = _bare_agent()
            # Simulate a surface having displayed reasoning this turn.
            agent._current_streamed_reasoning_text = "SECRET chain of thought."
            agent._current_streamed_assistant_text = "Visible draft."
            messages = [{"role": "user", "content": "start"}]
            if tail_role == "assistant":
                messages.append({"role": "assistant", "content": "committed"})

            _apply_active_turn_redirect(agent, messages, "Change course.")

            # Check BOTH the transcript content and the replayed sidecar —
            # the sidecar is what actually reaches the provider.
            serialized = "".join(
                str(m.get("content", "")) + str(m.get("api_content") or "")
                for m in messages
            )
            assert "SECRET chain of thought." not in serialized
            assert "Reasoning shown before the interruption" not in serialized
            assert "Visible draft." in serialized

    def test_checkpoint_omits_reasoning_label_when_nothing_visible(self):
        from agent.conversation_loop import _apply_active_turn_redirect

        agent = _bare_agent()
        agent._current_streamed_reasoning_text = "thinking only, no text yet"
        messages = [{"role": "user", "content": "start"}]

        _apply_active_turn_redirect(agent, messages, "New direction.")

        placeholder = messages[-2]
        correction = messages[-1]
        # Nothing was on screen: empty hidden placeholder for alternation;
        # scaffold rides only on the user correction's api_content.
        assert placeholder["role"] == "assistant"
        assert placeholder["display_kind"] == "hidden"
        assert placeholder.get("content") == ""
        # Neutral provider-replay payload (#88955): keeps the row out of the
        # re-heal sanitizer loop; the interrupt scaffold is still never here.
        assert placeholder.get("api_content") == "[response interrupted]"
        assert correction["content"] == "New direction."
        assert (
            "[This response was interrupted by a user correction.]"
            in correction["api_content"]
        )

    def test_tool_tail_scaffold_never_on_assistant_api_content(self):
        """#81841: mid-tool steer must not put the interrupt scaffold on the
        placeholder assistant row (that is what the model echoed)."""
        from agent.conversation_loop import _apply_active_turn_redirect

        agent = _bare_agent()
        messages = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "content": "out", "tool_call_id": "a"},
        ]

        _apply_active_turn_redirect(agent, messages, "Stop and do X instead.")

        placeholder = messages[-2]
        correction = messages[-1]
        assert placeholder["role"] == "assistant"
        assert placeholder.get("display_kind") == "hidden"
        assert placeholder.get("content") == ""
        # Neutral provider-replay payload (#88955), NOT the interrupt scaffold.
        assert placeholder.get("api_content") == "[response interrupted]"
        assert correction["role"] == "user"
        assert correction["content"] == "Stop and do X instead."
        assert correction["api_content"].startswith(
            "[Context from the interrupted assistant response]\n"
            "[This response was interrupted by a user correction.]"
        )


    def test_redirect_during_tool_execution_uses_safe_steer_boundary(self):
        agent = _bare_agent()
        agent._executing_tools = True

        assert agent.redirect("also check migrations") is True
        assert agent._pending_redirect is None
        assert agent._pending_steer == "also check migrations"
        assert agent._interrupt_requested is False


class TestActiveTurnRedirectCheckpoint:
    def test_assistant_tail_puts_correction_last(self):
        from agent.conversation_loop import _apply_active_turn_redirect

        agent = _bare_agent()
        agent._current_streamed_assistant_text = "Visible draft."
        messages = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": "committed assistant item"},
        ]

        _apply_active_turn_redirect(agent, messages, "Use Postgres instead.")

        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Use Postgres instead."
        assert sum(1 for m in messages if m["role"] == "assistant") == 1
        # Scaffolding is provider-replay text, carried in the sidecar so the
        # model still sees the interrupted context — never in the transcript.
        replayed = messages[-1]["api_content"]
        assert "Visible draft." in replayed
        assert "Context from the interrupted assistant response" in replayed
        assert replayed.endswith("Use Postgres instead.")

    def test_scaffolding_never_lands_in_transcript_content(self):
        """The checkpoint machinery is for the MODEL, not the transcript.

        Persisting ``[This response was interrupted by a user correction.]``
        into ``content`` painted raw scaffolding as an assistant bubble on
        every reload. It must ride in ``api_content`` (replayed to the
        provider) while ``content`` stays clean, or be marked
        ``display_kind="hidden"`` when there is no clean form at all.
        """
        from agent.conversation_loop import _apply_active_turn_redirect

        scaffolding = (
            "[This response was interrupted by a user correction.]",
            "Visible response before the interruption:",
            "[Context from the interrupted assistant response]",
        )

        for tail_role in ("tool", "assistant"):
            for streamed in ("Partial reply on screen.", ""):
                agent = _bare_agent()
                agent._current_streamed_assistant_text = streamed
                messages = [{"role": "user", "content": "start"}]
                if tail_role == "assistant":
                    messages.append({"role": "assistant", "content": "committed"})
                else:
                    messages.append(
                        {"role": "assistant", "tool_calls": [{"id": "a"}]}
                    )
                    messages.append(
                        {"role": "tool", "content": "out", "tool_call_id": "a"}
                    )

                _apply_active_turn_redirect(agent, messages, "New direction.")

                for msg in messages:
                    if msg.get("display_kind") == "hidden":
                        continue  # dropped by every transcript surface
                    content = str(msg.get("content", ""))
                    for marker in scaffolding:
                        assert marker not in content, (
                            f"scaffolding leaked into visible content "
                            f"(tail={tail_role}, streamed={bool(streamed)}): {content!r}"
                        )

                # The user's correction is always shown verbatim.
                assert messages[-1]["content"] == "New direction."
                # ...and the model still receives the interrupted context.
                replayed = "".join(
                    str(m.get("api_content") or m.get("content", "")) for m in messages
                )
                assert "[This response was interrupted by a user correction.]" in replayed
                if streamed:
                    assert streamed in replayed

    def test_checkpoint_never_replays_chain_of_thought(self):
        """Raw CoT serialized into checkpoint content reads to Anthropic's
        output classifier as reasoning-injection; because the checkpoint is
        persisted and replayed on every later call, one redirect during a
        thinking phase permanently bricked sessions with deterministic
        empty-response storms (July 2026). Reasoning must never appear in
        replayable content — in either the assistant-checkpoint or the
        merged-user-correction shape."""
        from agent.conversation_loop import _apply_active_turn_redirect

        for tail_role in ("user", "assistant"):
            agent = _bare_agent()
            # Simulate a surface having displayed reasoning this turn.
            agent._current_streamed_reasoning_text = "SECRET chain of thought."
            agent._current_streamed_assistant_text = "Visible draft."
            messages = [{"role": "user", "content": "start"}]
            if tail_role == "assistant":
                messages.append({"role": "assistant", "content": "committed"})

            _apply_active_turn_redirect(agent, messages, "Change course.")

            # Check BOTH the transcript content and the replayed sidecar —
            # the sidecar is what actually reaches the provider.
            serialized = "".join(
                str(m.get("content", "")) + str(m.get("api_content") or "")
                for m in messages
            )
            assert "SECRET chain of thought." not in serialized
            assert "Reasoning shown before the interruption" not in serialized
            assert "Visible draft." in serialized

    def test_checkpoint_omits_reasoning_label_when_nothing_visible(self):
        from agent.conversation_loop import _apply_active_turn_redirect

        agent = _bare_agent()
        agent._current_streamed_reasoning_text = "thinking only, no text yet"
        messages = [{"role": "user", "content": "start"}]

        _apply_active_turn_redirect(agent, messages, "New direction.")

        checkpoint_row = messages[-2]
        correction = messages[-1]
        # Nothing was on screen, so the row exists only for the model: hidden
        # from every transcript surface.
        assert checkpoint_row["display_kind"] == "hidden"
        # Upstream #81841: the scaffold is replayed via the CORRECTION's
        # api_content sidecar — never on the placeholder assistant row (an
        # assistant row inlining the scaffold makes the model echo it).
        assert "api_content" not in checkpoint_row
        assert (
            "This response was interrupted by a user correction."
            not in (checkpoint_row.get("content") or "")
        )
        assert correction["content"] == "New direction."
class TestEmptyHiddenAssistantRehealRegression:
    """#88955: a no-visible-text redirect persisted an empty
    ``display_kind="hidden"`` assistant placeholder that the pre-call sanitizer
    re-healed on every later call (wire copy only, so the loop never converged).
    The placeholder must carry a neutral provider-replay ``api_content`` so the
    historical API projection fills ``content`` and the sanitizer stops
    touching the row — while the durable transcript stays hidden and empty."""

    def test_active_turn_redirect_hidden_placeholder_has_provider_replay_payload(self):
        from agent.conversation_loop import _apply_active_turn_redirect

        agent = _bare_agent()
        agent._current_streamed_assistant_text = ""
        messages = [{"role": "user", "content": "start"}]

        _apply_active_turn_redirect(agent, messages, "Use Postgres instead.")

        placeholder = messages[-2]
        correction = messages[-1]
        assert placeholder["role"] == "assistant"
        assert placeholder["content"] == ""
        assert placeholder["display_kind"] == "hidden"
        assert placeholder["api_content"] == "[response interrupted]"
        # The user correction keeps clean text in content and the interruption
        # context only in its own api_content sidecar.
        assert correction["role"] == "user"
        assert correction["content"] == "Use Postgres instead."
        assert (
            "[This response was interrupted by a user correction.]"
            in correction["api_content"]
        )
        # #81841: the interrupt scaffold must never reach assistant content or
        # api_content (API replay substitutes api_content back into content).
        assert (
            "[This response was interrupted by a user correction.]"
            not in str(placeholder.get("content") or "")
            + str(placeholder.get("api_content") or "")
        )

    def test_hidden_redirect_placeholder_does_not_reheal_on_repeated_projection(self):
        from agent.agent_runtime_helpers import (
            _msg_has_payload,
            repair_empty_non_final_messages,
        )
        from agent.conversation_loop import _apply_active_turn_redirect

        agent = _bare_agent()
        agent._current_streamed_assistant_text = ""
        messages = [{"role": "user", "content": "start"}]
        _apply_active_turn_redirect(agent, messages, "Do X instead.")
        durable = list(messages)

        def project(rows):
            """Mirror the real send-time projection (conversation_loop.py):
            api_content -> content for historical user/assistant rows, and the
            display/row bookkeeping stripped from every outgoing copy."""
            out = []
            for msg in rows:
                api_msg = dict(msg)
                _api_content = api_msg.pop("api_content", None)
                api_msg.pop("display_kind", None)
                api_msg.pop("display_metadata", None)
                api_msg.pop("_row_id", None)
                if (
                    isinstance(_api_content, str)
                    and _api_content
                    and msg.get("role") in ("user", "assistant")
                ):
                    api_msg["content"] = _api_content
                out.append(api_msg)
            return out

        for _pass in range(2):
            projected = project(durable)
            hidden_assistant = next(
                m for m in projected if m.get("role") == "assistant"
            )
            # The provider replay sidecar was projected into content, so the
            # row already carries payload and the sanitizer has nothing to heal.
            assert _msg_has_payload(hidden_assistant) is True
            assert hidden_assistant["content"] == "[response interrupted]"
            assert "display_kind" not in hidden_assistant
            assert "api_content" not in hidden_assistant

            healed = repair_empty_non_final_messages(projected)
            healed_assistant = next(
                m for m in healed if m.get("role") == "assistant"
            )
            assert healed_assistant["content"] == "[response interrupted]"
            assert "display_kind" not in healed_assistant
            assert "api_content" not in healed_assistant
            # Durable transcript is never mutated by projection or sanitizer.
            assert durable == messages
            assert durable[1]["content"] == ""
            assert durable[1]["display_kind"] == "hidden"
            assert durable[1]["api_content"] == "[response interrupted]"

        # #81841 scaffold never appears on the assistant wire.
        assert (
            "[This response was interrupted by a user correction.]"
            not in healed_assistant["content"]
        )

    def test_empty_non_final_sanitizer_still_repairs_unmarked_empty_assistant(self):
        """Control: a genuinely empty non-final assistant with no provider-replay
        sidecar is still healed — the fix must not disable the generic net."""
        from agent.agent_runtime_helpers import repair_empty_non_final_messages

        rows = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": "", "display_kind": "hidden"},
            {"role": "user", "content": "correction"},
        ]
        healed = repair_empty_non_final_messages(rows)
        assistant = next(m for m in healed if m.get("role") == "assistant")
        assert assistant["content"] == "[response interrupted]"
        # The durable list is not mutated (wire-copy-only design).
        assert rows[1]["content"] == ""


class TestSteerInjection:
    def test_appends_to_last_tool_result(self):
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
        assert "User injection prompt: please check auth.log" in messages[-1]["content"]
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
        assert messages[-1]["content"] == "search results\n\nUser injection prompt: update summary"
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

    def test_apply_pending_steer_to_tool_results_forwards_to_helper(self):
        """The method forwards to the real marker injection (restored from
        upstream) — the steer lands on the last tool result and is drained."""
        agent = _bare_agent()
        agent.steer("should land here")
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "content": "output", "tool_call_id": "a"},
        ]
        agent._apply_pending_steer_to_tool_results(messages, num_tool_msgs=1)
        assert STEER_MARKER_OPEN in messages[-1]["content"]
        assert "should land here" in messages[-1]["content"]
        # The steer was drained by the injection.
        assert agent._steer_provider.peek() is None

    def test_marker_labels_text_as_out_of_band_user_message(self):
        """The injection marker must attribute the appended text to the user
        via the explicit out-of-band marker (which the system prompt tells the
        model to trust) — otherwise the model reads it as untrusted tool output
        and refuses it as suspected prompt injection.  Cache-safe: it only
        rewrites existing tool content, never the message-role sequence.
        """
        agent = _bare_agent()
        agent.steer("stop after next step")
        messages = [{"role": "tool", "content": "x", "tool_call_id": "1"}]
        agent._apply_pending_steer_to_tool_results(messages, num_tool_msgs=1)
        content = messages[-1]["content"]
        assert STEER_MARKER_OPEN in content
        assert "stop after next step" in content

    def test_multimodal_content_list_preserved(self):
        """Anthropic-style list content should be preserved, with the steer
        appended as a text block."""
        agent = _bare_agent()
        agent.steer("extra note")
        original_blocks = [{"type": "text", "text": "existing output"}]
        messages = [
            {"role": "tool", "content": list(original_blocks), "tool_call_id": "1"}
        ]
        agent._apply_pending_steer_to_tool_results(messages, num_tool_msgs=1)
        new_content = messages[-1]["content"]
        assert isinstance(new_content, list)
        assert len(new_content) == 2
        assert new_content[0] == {"type": "text", "text": "existing output"}
        assert new_content[1]["type"] == "text"
        assert "extra note" in new_content[1]["text"]



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
        agent._pending_redirect = "also drop this"
        assert agent._pending_steer == "will be dropped"

        agent.clear_interrupt()
        assert agent._steer_provider.peek() is None
        assert agent._pending_steer is None
        assert agent._pending_redirect is None


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
        assert "User injection prompt: early steer" in messages[-1]["content"]
        assert agent._steer_provider.peek() is None

    def test_steer_between_calls_lands_in_tool_result(self):
        agent = _bare_agent()
        agent.steer("change approach")
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "content": "tool output", "tool_call_id": "a"},
        ]
        _inject_steer_into_tool_results(agent, messages, api_call_count=2)
        assert "User injection prompt: change approach" in messages[-1]["content"]


class TestSteerChannelNote:
    def test_system_prompt_note_describes_user_message_injection(self):
        assert "injection prompt" in STEER_CHANNEL_NOTE
        assert "user message" in STEER_CHANNEL_NOTE.lower()

    def test_system_prompt_note_no_longer_references_old_marker(self):
        from agent.prompt_builder import STEER_CHANNEL_NOTE

        assert "OUT-OF-BAND USER MESSAGE" not in STEER_CHANNEL_NOTE
        assert "[/OUT-OF-BAND USER MESSAGE]" not in STEER_CHANNEL_NOTE

    def test_system_prompt_scopes_freshness_to_unanswered_marker(self):
        """A delivered marker remains in immutable history on later API calls.

        The freshness contract lives in TWO places and this test pins the
        split (#95681 diet): the MARKER carries its own replay rule at
        delivery time ("delivered once at this position", "not a new
        delivery when replayed"), while the prompt note keeps only the
        summary clause scoping action to the latest tool results. The
        detailed only-if-no-later-assistant-message teaching moved out of
        the prompt because the marker already says it on every delivery.
        """
        from agent.prompt_builder import STEER_CHANNEL_NOTE

        assert "latest tool results" in STEER_CHANNEL_NOTE
        assert "history" in STEER_CHANNEL_NOTE

        emitted = format_steer_marker("deploy once")
        assert "delivered once at this position" in emitted
        assert "not a new delivery when replayed" in emitted

    def test_marker_no_longer_uses_the_distrusted_label(self):
        """Regression: the bare 'User guidance:' line read as tool content and
        got refused as injection — it must not come back."""
        assert "User guidance:" not in format_steer_marker("hi")


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


class TestLegacyHiddenPlaceholderWireSubstitution:
    """Projection-side half of #88955: rows persisted BEFORE the writer-side
    ``api_content`` stamp are ``content=""`` + ``display_kind="hidden"`` with
    no sidecar. The send-time projection must give the WIRE copy the neutral
    ``[response interrupted]`` payload so legacy sessions converge instead of
    re-healing forever — while the durable row stays hidden and empty."""

    def _loop_agent(self):
        from unittest.mock import MagicMock, patch

        from run_agent import AIAgent

        with (
            patch("run_agent.get_tool_definitions", return_value=[]),
            patch("run_agent.check_toolset_requirements", return_value={}),
            patch("run_agent.OpenAI"),
        ):
            agent = AIAgent(
                api_key="test-key-1234567890",
                base_url="https://openrouter.ai/api/v1",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        agent.client = MagicMock()
        agent._cached_system_prompt = "You are helpful."
        agent._use_prompt_caching = False
        agent.tool_delay = 0
        agent.compression_enabled = False
        agent.save_trajectories = False
        return agent

    def test_legacy_empty_hidden_assistant_row_gets_neutral_wire_payload(self):
        """The projection itself must fill the row — the sanitizer must have
        NOTHING left to heal (its per-turn warning spam IS the bug)."""
        from unittest.mock import patch

        import agent.agent_runtime_helpers as _arh

        from tests.run_agent.test_run_agent import _mock_response

        agent = self._loop_agent()
        agent.client.chat.completions.create.side_effect = [
            _mock_response(content="ok", finish_reason="stop"),
        ]
        sanitizer_inputs = []
        _real_repair = _arh.repair_empty_non_final_messages

        def _spy_repair(messages, *a, **k):
            sanitizer_inputs.append(
                [
                    (m.get("role"), m.get("content"))
                    for m in messages
                    if isinstance(m, dict)
                ]
            )
            return _real_repair(messages, *a, **k)
        # Legacy pre-fix row: no api_content sidecar.
        history = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": "", "display_kind": "hidden"},
            {"role": "user", "content": "correction", "finish_reason": "stop"},
            {"role": "assistant", "content": "earlier reply", "finish_reason": "stop"},
        ]

        with (
            patch.object(agent, "_flush_messages_to_session_db"),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(
                _arh, "repair_empty_non_final_messages", side_effect=_spy_repair
            ),
        ):
            agent.run_conversation("next question", conversation_history=history)

        # Precondition: the sanitizer actually ran on this call path.
        assert sanitizer_inputs, "sanitizer was never invoked — test is vacuous"
        # The projection already filled the legacy row BEFORE sanitization:
        # every assistant row the sanitizer saw carried payload, so it healed 0.
        for snapshot in sanitizer_inputs:
            for role, content in snapshot:
                if role == "assistant":
                    assert (content or "").strip(), (
                        "sanitizer still received an empty assistant row — "
                        "the re-heal loop is back (#88955)"
                    )

        wire = agent.client.chat.completions.create.call_args.kwargs["messages"]
        wire_assistants = [m for m in wire if m.get("role") == "assistant"]
        legacy = wire_assistants[0]
        # Substituted on the wire by the projection (not the sanitizer):
        assert legacy["content"] == "[response interrupted]"
        assert "display_kind" not in legacy
        # #81841: never the interrupt scaffold.
        assert "[This response was interrupted" not in legacy["content"]
        # Durable history untouched.
        assert history[1]["content"] == ""
        assert history[1]["display_kind"] == "hidden"
        assert "api_content" not in history[1]

    def test_hidden_row_with_tool_calls_or_text_is_not_touched(self):
        from agent.conversation_loop import _clone_message_for_send  # noqa: F401
        from unittest.mock import patch

        from tests.run_agent.test_run_agent import _mock_response

        agent = self._loop_agent()
        agent.client.chat.completions.create.side_effect = [
            _mock_response(content="ok", finish_reason="stop"),
        ]
        history = [
            {"role": "user", "content": "start"},
            {
                "role": "assistant",
                "content": "visible text",
                "display_kind": "hidden",
                "finish_reason": "stop",
            },
            {"role": "user", "content": "more"},
            {"role": "assistant", "content": "reply", "finish_reason": "stop"},
        ]

        with (
            patch.object(agent, "_flush_messages_to_session_db"),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            agent.run_conversation("next", conversation_history=history)

        wire = agent.client.chat.completions.create.call_args.kwargs["messages"]
        wire_assistants = [m for m in wire if m.get("role") == "assistant"]
        assert wire_assistants[0]["content"] == "visible text"

"""Backend-specific integration tests for /steer user-message injection.

For each backend, we run a two-iteration tool-call conversation and assert:
  * A steer sent during the first API call reaches the second request.
  * The steer is delivered as a STANDALONE ``role: user`` row (marker-labeled
    ``User injection prompt:``) right after the newest tool result — never
    smeared onto the already-persisted tool row, which append-only persistence
    never rewrites and replay would then diverge from the live request bytes.
  * The original user turn's text is preserved in the persisted transcript.
  * The system prompt is byte-identical across iterations.
"""
from __future__ import annotations


import copy
import json
import sys
import threading
import types
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason="Windows baseline: path/subprocess operations fail")

sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())

import run_agent


def _patch_bootstrap(monkeypatch):
    monkeypatch.setattr(
        run_agent,
        "get_tool_definitions",
        lambda **kwargs: [
            {
                "type": "function",
                "function": {
                    "name": "t",
                    "description": "t",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    monkeypatch.setattr(run_agent, "check_toolset_requirements", lambda: {})
    monkeypatch.setattr(
        run_agent, "handle_function_call", lambda *a, **k: '{"ok": true}'
    )


def _tool_call_response(
    api_mode: str = "chat_completions", tool_call_id: str = "tc_1"
) -> SimpleNamespace:
    if api_mode == "anthropic_messages":
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    id=tool_call_id,
                    name="t",
                    input={},
                )
            ],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            model="test-model",
        )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                message=SimpleNamespace(
                    role="assistant",
                    content="",
                    tool_calls=[
                        SimpleNamespace(
                            id=tool_call_id,
                            type="function",
                            function=SimpleNamespace(name="t", arguments="{}"),
                        )
                    ],
                    reasoning_content=None,
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        model="test-model",
    )


def _final_response(api_mode: str = "chat_completions") -> SimpleNamespace:
    if api_mode == "anthropic_messages":
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="done")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=20, output_tokens=5),
            model="test-model",
        )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                message=SimpleNamespace(
                    role="assistant",
                    content="done",
                    tool_calls=None,
                    reasoning_content=None,
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=5, total_tokens=25),
        model="test-model",
    )


def _make_agent(monkeypatch, provider: str, api_mode: str, model: str):
    _patch_bootstrap(monkeypatch)

    class _FakeOpenAIClient:
        api_key = "fake-key"
        base_url = "https://api.openai.com/v1"
        _default_headers = None

    # Route client resolution to a fake so no real network/auth is attempted.
    monkeypatch.setattr(
        "agent.auxiliary_client.resolve_provider_client",
        lambda *a, **kw: (_FakeOpenAIClient(), model),
    )

    class _A(run_agent.AIAgent):
        def __init__(self, *a, **kw):
            kw.update(skip_context_files=True, skip_memory=True, max_iterations=5)
            super().__init__(*a, **kw)
            self._cleanup_task_resources = self._persist_session = lambda *a, **k: None
            self._save_trajectory = lambda *a, **k: None
            self._captured_calls: List[Dict[str, Any]] = []
            self._response_sequence = [
                _tool_call_response(api_mode=api_mode),
                _final_response(api_mode=api_mode),
            ]
            self._response_index = 0

        def run_conversation(self, msg, conversation_history=None, task_id=None):
            self._disable_streaming = True
            return super().run_conversation(
                msg, conversation_history=conversation_history, task_id=task_id
            )

        def _interruptible_api_call(self, api_kwargs: dict):
            self._captured_calls.append(copy.deepcopy(api_kwargs))
            response = self._response_sequence[self._response_index]
            self._response_index += 1
            return response

    return _A(
        model=model,
        api_key="test-key",
        base_url="http://localhost:1234/v1",
        provider=provider,
        api_mode=api_mode,
    )


def _find_message(messages: List[Dict[str, Any]], role: str) -> Dict[str, Any]:
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == role:
            return msg
    raise AssertionError(f"no {role!r} message found in {messages!r}")


def _system_content(api_kwargs: Dict[str, Any]) -> str:
    """Return the system prompt content however the backend passes it."""
    system = api_kwargs.get("system")
    if system:
        if isinstance(system, str):
            return system
        if isinstance(system, list):
            return "\n".join(str(item) for item in system)
    messages = api_kwargs.get("messages", [])
    sys_msg = next(
        (m for m in messages if isinstance(m, dict) and m.get("role") == "system"), None
    )
    if sys_msg:
        return str(sys_msg.get("content", ""))
    return ""


def _tool_result_texts(message: Dict[str, Any]) -> List[str]:
    """Text carried by a message's tool-result payloads, across backend shapes.

    OpenAI-compatible backends use ``role="tool"`` rows (their whole content is
    tool output); Anthropic-style backends carry ``tool_result`` blocks inside
    ``role="user"`` rows.
    """
    content = message.get("content", "")
    if message.get("role") == "tool":
        return [content if isinstance(content, str) else json.dumps(content)]
    if isinstance(content, list):
        return [
            str(block.get("text", "") or block.get("content", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
    return []


def _all_text(message: Dict[str, Any]) -> str:
    """Every visible text of a wire message, string or block content."""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "") or block.get("content", "")))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


@pytest.mark.parametrize(
    "provider, api_mode, model",
    [
        ("openai", "chat_completions", "gpt-4o"),
        ("anthropic", "anthropic_messages", "claude-opus-4"),
        ("google", "chat_completions", "gemini-2.0"),
        ("openrouter", "chat_completions", "openrouter/auto"),
    ],
)
class TestSteerBackends:
    def test_steer_delivered_as_standalone_user_row(
        self, monkeypatch, provider: str, api_mode: str, model: str
    ):
        agent = _make_agent(monkeypatch, provider, api_mode, model)

        api_started = threading.Event()
        steer_submitted = threading.Event()
        steer_results: List[bool] = []
        original_api_call = agent._interruptible_api_call

        def coordinated_api_call(api_kwargs: dict):
            if agent._response_index == 0:
                api_started.set()
                assert steer_submitted.wait(timeout=5), "steer was not submitted"
            return original_api_call(api_kwargs)

        agent._interruptible_api_call = coordinated_api_call

        def steer_in_thread():
            assert api_started.wait(timeout=5), "first API call did not start"
            try:
                steer_results.append(agent.steer("focus on error handling"))
            finally:
                steer_submitted.set()

        steer_thread = threading.Thread(target=steer_in_thread)
        steer_thread.start()
        result = agent.run_conversation("run tool t")
        steer_thread.join(timeout=5)

        assert result is not None
        assert "final_response" in result
        assert steer_results == [True]

        # Two API calls: tool-call request and final response request.
        assert len(agent._captured_calls) == 2
        first_kwargs, second_kwargs = agent._captured_calls

        # First call has no steer yet.
        first_user = _find_message(first_kwargs["messages"], "user")
        assert "[steer]" not in str(first_user.get("content", ""))

        # Second call — the original user text is preserved (may be wrapped as a
        # list of content parts for certain providers like Anthropic).
        second_user = _find_message(second_kwargs["messages"], "user")
        assert "run tool t" in _all_text(second_user)

        # The steer is delivered exactly once, as its own marker-labeled user row
        # after the newest tool result (never inside a tool result payload).
        _steer_marker = "User injection prompt: focus on error handling"
        _deliveries = [
            _m for _m in second_kwargs["messages"]
            if isinstance(_m, dict) and _steer_marker in _all_text(_m)
        ]
        assert len(_deliveries) == 1, f"expected exactly one steer delivery, got {_deliveries!r}"
        assert "OUT-OF-BAND USER MESSAGE" in _all_text(_deliveries[0])
        for _m in second_kwargs["messages"]:
            if not isinstance(_m, dict):
                continue
            assert not any(
                "focus on error handling" in _text for _text in _tool_result_texts(_m)
            ), "steer smeared onto a tool result payload instead of its own user row"

        # The persisted messages list keeps the original user text as its first
        # user row (the steer row is appended after it).
        persisted_user = _find_message(result["messages"], "user")
        assert persisted_user["content"] == "run tool t"
        assert "[steer]" not in str(persisted_user.get("content", ""))

        # System prompt is stable across both calls.
        assert _system_content(first_kwargs) == _system_content(second_kwargs)

    def test_steer_does_not_displace_the_user_turn(
        self, monkeypatch, provider: str, api_mode: str, model: str
    ):
        agent = _make_agent(monkeypatch, provider, api_mode, model)
        agent.steer("change approach")
        result = agent.run_conversation("run tool t")

        assert len(agent._captured_calls) == 2
        second_kwargs = agent._captured_calls[1]
        _messages = second_kwargs["messages"]
        _user_rows = [
            m for m in _messages if isinstance(m, dict) and m.get("role") == "user"
        ]

        # The user's own request survives verbatim: the steer is delivered beside
        # it (its own user row, or — on Anthropic, which carries tool results in
        # user rows — an extra text block next to the tool result), never as a
        # rewrite of the turn that started the run.
        assert _user_rows, "no user row in the second request"
        assert "run tool t" in _all_text(_user_rows[0])
        assert any("change approach" in _all_text(m) for m in _user_rows)
        # …and only the user channel carries it: a steer smeared onto a tool
        # payload would diverge from the append-only persisted row on replay.
        for _m in _messages:
            if isinstance(_m, dict):
                assert not any(
                    "change approach" in _text for _text in _tool_result_texts(_m)
                ), "steer smeared onto a tool result payload"
        # It reached the model as a real delivery, not as a pending leftover.
        assert not result.get("pending_steer")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

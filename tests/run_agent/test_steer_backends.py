"""Backend-specific integration tests for /steer user-message injection.

For each backend, we run a two-iteration tool-call conversation and assert:
  * A steer sent during the first API call appears in the user message copy
    of the second request.
  * The steer is labeled with ``[steer]``.
  * No synthetic ``role: user`` message is inserted mid-turn.
  * The persisted ``messages`` list is unchanged (only the api copy is augmented).
  * The system prompt is byte-identical across iterations.
"""

from __future__ import annotations

import copy
import sys
import types
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

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
    def test_steer_injected_into_tool_results(
        self, monkeypatch, provider: str, api_mode: str, model: str
    ):
        agent = _make_agent(monkeypatch, provider, api_mode, model)

        def steer_in_thread():
            # Wait until the first API call is definitely in flight.
            while len(agent._captured_calls) < 1:
                pass
            agent.steer("focus on error handling")

        steer_thread = __import__("threading").Thread(target=steer_in_thread)
        steer_thread.start()
        result = agent.run_conversation("run tool t")
        steer_thread.join(timeout=5)

        assert result is not None
        assert "final_response" in result

        # Two API calls: tool-call request and final response request.
        assert len(agent._captured_calls) == 2
        first_kwargs, second_kwargs = agent._captured_calls

        # First call has no steer yet.
        first_user = _find_message(first_kwargs["messages"], "user")
        assert "[steer]" not in str(first_user.get("content", ""))

        # Second call — steer is in the tool result, NOT the user message.
        second_user = _find_message(second_kwargs["messages"], "user")
        second_user_content_str = str(second_user.get("content", ""))
        assert "[steer]" not in second_user_content_str
        # The original user text is preserved (may be wrapped as a list of
        # content parts for certain providers like Anthropic).
        assert "run tool t" in second_user_content_str

        # Second call — steer is injected into the last tool result message.
        # For OpenAI-compatible backends, tool results have role="tool".
        # For Anthropic, tool results are converted to role="user" with
        # content blocks of type "tool_result" — search for steer text
        # in those blocks too.
        _found_steer = False
        for _m in second_kwargs["messages"]:
            if isinstance(_m, dict):
                _content = _m.get("content", "")
                if isinstance(_content, str) and "[steer] focus on error handling" in _content:
                    _found_steer = True
                    break
                if isinstance(_content, list):
                    for _block in _content:
                        if isinstance(_block, dict) and _block.get("type") == "tool_result":
                            _block_text = str(_block.get("text", "") or _block.get("content", ""))
                            if "[steer] focus on error handling" in _block_text:
                                _found_steer = True
                                break
                if _found_steer:
                    break
        assert _found_steer, "[steer] not found in any tool result message"

        # The persisted messages list keeps the original user text.
        persisted_user = _find_message(result["messages"], "user")
        assert persisted_user["content"] == "run tool t"
        assert "[steer]" not in str(persisted_user.get("content", ""))

        # System prompt is stable across both calls.
        assert _system_content(first_kwargs) == _system_content(second_kwargs)

    def test_no_synthetic_user_message_inserted(
        self, monkeypatch, provider: str, api_mode: str, model: str
    ):
        agent = _make_agent(monkeypatch, provider, api_mode, model)
        agent.steer("change approach")
        result = agent.run_conversation("run tool t")

        assert len(agent._captured_calls) == 2
        second_kwargs = agent._captured_calls[1]

        def _is_original_user_message(m: Dict[str, Any]) -> bool:
            if not isinstance(m, dict) or m.get("role") != "user":
                return False
            content = m.get("content")
            if isinstance(content, list):
                return not any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content
                )
            return True

        user_messages = [
            m for m in second_kwargs["messages"] if _is_original_user_message(m)
        ]
        # Exactly one original-turn user message in the second request — it is
        # augmented in-place. No extra synthetic user message was inserted.
        assert len(user_messages) == 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

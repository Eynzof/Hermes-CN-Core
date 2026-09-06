"""Real end-to-end reasoning tests against the DeepSeek API.

These tests verify that the OpenAI chat-completions and Responses (codex)
transports correctly enable reasoning effort and accept/return thinking
blocks, and that the Anthropic transport builds the correct ``thinking``
parameter — all against the live DeepSeek API
(``https://api.deepseek.com``), which exposes both an OpenAI-compatible
``/v1/chat/completions`` surface (with ``reasoning_content``) and an
OpenAI-compatible ``/v1/responses`` surface (with ``reasoning`` output
items).

The DeepSeek API key is read from the ``DEEPSEEK_API_KEY`` environment
variable and is NEVER hardcoded in this file (it is a secret). Tests are
skipped automatically when the env var is absent so CI stays closed.

Run locally with::

    DEEPSEEK_API_KEY=sk-... python -m pytest \\
        tests/agent/transports/test_reasoning_e2e_deepseek.py -s -ra
"""

from __future__ import annotations

import os
import types
from typing import Any

import pytest

# Secret is read from the environment only — never embedded in source.
_DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
_REASONING_MODEL = "deepseek-v4-flash"  # V4 family has thinking ON by default

# Skip the whole module when the secret isn't present — CI stays closed and
# we never hardcode the key.
pytestmark = pytest.mark.skipif(
    not _DEEPSEEK_API_KEY,
    reason="DEEPSEEK_API_KEY env var not set; live reasoning E2E tests skipped.",
)


def _make_client() -> Any:
    """Build a real OpenAI SDK client pointed at the DeepSeek endpoint."""
    from openai import OpenAI

    return OpenAI(
        api_key=_DEEPSEEK_API_KEY,
        base_url=_DEEPSEEK_BASE_URL,
        timeout=60.0,
    )


# ── Chat Completions transport ──────────────────────────────────────────────


class TestChatCompletionsReasoningE2E:
    """Live DeepSeek /v1/chat/completions calls through the transport layer."""

    def test_nonstream_returns_reasoning_and_text_blocks(self):
        """Non-streaming chat-completions call returns BOTH a thinking block
        (``reasoning_content``) and a text block (``content``).

        This exercises:
          - DeepSeekProfile.build_api_kwargs_extras() emits
            ``reasoning_effort`` + ``extra_body.thinking={"type":"enabled"}``.
          - The transport's normalize_response() captures
            ``reasoning_content`` into provider_data and ``content`` into the
            NormalizedResponse.content.
        """
        from providers import get_provider_profile

        from agent.transports import get_transport
        import agent.transports.chat_completions  # noqa: F401

        profile = get_provider_profile("deepseek")
        transport = get_transport("chat_completions")

        messages = [
            {"role": "user", "content": "What is 17 * 23? Think briefly, then give the answer."},
        ]
        kwargs = transport.build_kwargs(
            model=_REASONING_MODEL,
            messages=messages,
            tools=[],
            reasoning_config={"enabled": True, "effort": "medium"},
            supports_reasoning=True,
            provider_profile=profile,
            provider_name="deepseek",
            base_url=profile.base_url,
            max_tokens_param_fn=lambda n: {"max_tokens": n},
            max_tokens=2048,
        )

        # The profile must enable thinking for V4 models.
        assert kwargs["extra_body"]["thinking"] == {"type": "enabled"}
        assert kwargs["reasoning_effort"] == "medium"

        client = _make_client()
        # The transport returns SDK-ready kwargs; invoke the SDK directly with them.
        # ``extra_body`` is an SDK kwarg the transport already separated out.
        raw = client.chat.completions.create(
            model=kwargs["model"],
            messages=kwargs["messages"],
            reasoning_effort=kwargs.get("reasoning_effort"),
            extra_body=kwargs.get("extra_body", {}),
            max_tokens=kwargs.get("max_tokens", 2048),
            stream=False,
        )
        assert transport.validate_response(raw), "transport rejected the response"

        normalized = transport.normalize_response(raw)
        # ── Thinking block present ───────────────────────────────────────
        rc = (normalized.provider_data or {}).get("reasoning_content")
        assert isinstance(rc, str) and rc.strip(), (
            "expected non-empty reasoning_content (thinking block) in provider_data; "
            f"got provider_data={normalized.provider_data!r}"
        )
        # ── Text block present ───────────────────────────────────────────
        assert isinstance(normalized.content, str) and normalized.content.strip(), (
            f"expected non-empty content (text block); got content={normalized.content!r}"
        )
        # The numeric answer should appear in the text block.
        assert "391" in normalized.content, (
            f"expected '391' in the text block; got {normalized.content!r}"
        )

    def test_stream_yields_reasoning_then_text_deltas(self):
        """Streaming chat-completions emits reasoning_content deltas followed
        by content deltas — both must be non-empty."""
        from providers import get_provider_profile

        from agent.transports import get_transport
        import agent.transports.chat_completions  # noqa: F401

        profile = get_provider_profile("deepseek")
        transport = get_transport("chat_completions")

        messages = [
            {"role": "user", "content": "What is 8 + 5? Think, then answer."},
        ]
        kwargs = transport.build_kwargs(
            model=_REASONING_MODEL,
            messages=messages,
            tools=[],
            reasoning_config={"enabled": True, "effort": "low"},
            supports_reasoning=True,
            provider_profile=profile,
            provider_name="deepseek",
            base_url=profile.base_url,
            max_tokens_param_fn=lambda n: {"max_tokens": n},
            max_tokens=1024,
        )

        client = _make_client()
        stream = client.chat.completions.create(
            model=kwargs["model"],
            messages=kwargs["messages"],
            reasoning_effort=kwargs.get("reasoning_effort"),
            extra_body=kwargs.get("extra_body", {}),
            max_tokens=kwargs.get("max_tokens", 1024),
            stream=True,
        )

        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            rc = getattr(delta, "reasoning_content", None)
            if isinstance(rc, str) and rc:
                reasoning_parts.append(rc)
            c = getattr(delta, "content", None)
            if isinstance(c, str) and c:
                content_parts.append(c)

        reasoning_text = "".join(reasoning_parts).strip()
        content_text = "".join(content_parts).strip()
        assert reasoning_text, "stream produced no reasoning_content (thinking block)"
        assert content_text, "stream produced no content (text block)"
        assert "13" in content_text, f"expected '13' in text; got {content_text!r}"

    def test_auto_enable_reasoning_from_history_does_not_400(self):
        """A resumed history carrying ``reasoning_content`` with NO explicit
        reasoning_config must not 400 on DeepSeek (kosong #1616 parity).

        The transport's ``_auto_enable_reasoning_from_history`` synthesizes a
        ``medium`` default so the strict gateway stays happy. We verify by
        replaying an assistant turn that contains ``reasoning_content`` and
        sending the synthesized kwargs to the live API.
        """
        from providers import get_provider_profile

        from agent.transports import get_transport
        import agent.transports.chat_completions  # noqa: F401

        profile = get_provider_profile("deepseek")
        transport = get_transport("chat_completions")

        # History that carries reasoning_content from a prior thinking turn.
        messages = [
            {"role": "user", "content": "What is 2 + 2? Think, then answer."},
            {
                "role": "assistant",
                "content": "4",
                "reasoning_content": "2 plus 2 equals 4.",
            },
            {"role": "user", "content": "Now what is 3 + 3?"},
        ]
        # Deliberately pass reasoning_config=None to exercise the auto-enable.
        kwargs = transport.build_kwargs(
            model=_REASONING_MODEL,
            messages=messages,
            tools=[],
            reasoning_config=None,
            supports_reasoning=True,
            provider_profile=profile,
            provider_name="deepseek",
            base_url=profile.base_url,
            max_tokens_param_fn=lambda n: {"max_tokens": n},
            max_tokens=1024,
        )
        # Auto-enable synthesized a medium config → profile emitted
        # reasoning_effort=medium and thinking enabled.
        assert kwargs["reasoning_effort"] == "medium", (
            "auto-enable should have synthesized reasoning_effort=medium; "
            f"got kwargs={kwargs!r}"
        )
        assert kwargs["extra_body"]["thinking"] == {"type": "enabled"}

        client = _make_client()
        # This must NOT raise (no 400 from the strict gateway).
        raw = client.chat.completions.create(
            model=kwargs["model"],
            messages=kwargs["messages"],
            reasoning_effort=kwargs.get("reasoning_effort"),
            extra_body=kwargs.get("extra_body", {}),
            max_tokens=kwargs.get("max_tokens", 1024),
            stream=False,
        )
        assert transport.validate_response(raw)
        normalized = transport.normalize_response(raw)
        assert (normalized.provider_data or {}).get("reasoning_content"), (
            "follow-up thinking turn should still carry reasoning_content"
        )
        assert normalized.content and "6" in normalized.content, (
            f"expected '6' in follow-up text; got {normalized.content!r}"
        )


# ── Responses (codex) transport ────────────────────────────────────────────


class TestResponsesReasoningE2E:
    """Live DeepSeek /v1/responses calls through the Responses transport."""

    def test_responses_returns_reasoning_and_message_items(self):
        """The Responses transport emits ``reasoning`` + ``include``
        kwargs; the live DeepSeek /v1/responses endpoint returns a
        ``reasoning`` output item (thinking block) and a ``message`` output
        item (text block)."""
        from agent.transports import get_transport
        import agent.transports.codex  # noqa: F401

        transport = get_transport("codex_responses")

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is 9 * 9? Think, then answer."},
        ]
        kwargs = transport.build_kwargs(
            model=_REASONING_MODEL,
            messages=messages,
            tools=[],
            reasoning_config={"enabled": True, "effort": "medium"},
            base_url="https://api.deepseek.com/v1",
            session_id="e2e-test-responses",
            max_tokens=1024,
        )
        # The Responses transport must request reasoning.
        assert "reasoning" in kwargs, "Responses kwargs missing 'reasoning'"
        assert kwargs["reasoning"]["effort"] == "medium"
        assert kwargs["store"] is False

        client = _make_client()
        raw = client.responses.create(**kwargs)
        assert transport.validate_response(raw), "transport rejected the response"

        normalized = transport.normalize_response(raw)
        # ── Reasoning (thinking) block present ───────────────────────────
        provider_data = normalized.provider_data or {}
        assert provider_data.get("codex_reasoning_items"), (
            "expected codex_reasoning_items (thinking block); "
            f"got provider_data={provider_data!r}"
        )
        # ── Text block present ───────────────────────────────────────────
        assert isinstance(normalized.content, str) and normalized.content.strip(), (
            f"expected non-empty content (text block); got {normalized.content!r}"
        )
        assert "81" in normalized.content, (
            f"expected '81' in the text block; got {normalized.content!r}"
        )

    def test_responses_reasoning_disabled_omits_reasoning_kwarg(self):
        """When reasoning is explicitly disabled, the transport omits the
        ``reasoning`` kwarg so the *client* doesn't request thinking.

        Note: DeepSeek's V4 family thinks by default server-side, so the
        response may still carry reasoning items even when the client omits
        ``reasoning``. The contract under test is that the TRANSPORT does not
        send a ``reasoning`` field when the user disabled thinking — and that
        the live call still succeeds (no 400). We do NOT assert the model
        stays silent, because that is a server-side default, not a transport
        contract.
        """
        from agent.transports import get_transport
        import agent.transports.codex  # noqa: F401

        transport = get_transport("codex_responses")
        messages = [{"role": "user", "content": "Say hi."}]
        kwargs = transport.build_kwargs(
            model=_REASONING_MODEL,
            messages=messages,
            tools=[],
            reasoning_config={"enabled": False},
            base_url="https://api.deepseek.com/v1",
            session_id="e2e-test-responses-disabled",
            max_tokens=256,
        )
        assert "reasoning" not in kwargs, (
            "reasoning should be omitted when explicitly disabled"
        )
        # The live call must still succeed (no HTTP 400) without the kwarg.
        client = _make_client()
        raw = client.responses.create(**kwargs)
        assert transport.validate_response(raw)
        normalized = transport.normalize_response(raw)
        # Text block is always expected.
        assert normalized.content and normalized.content.strip(), (
            "expected text content even with reasoning disabled"
        )


# ── Anthropic transport (kwargs correctness — DeepSeek has no /v1/messages) ─


class TestAnthropicThinkingKwargs:
    """DeepSeek does not expose an Anthropic-compatible ``/v1/messages``
    endpoint (it 404s), so we verify the Anthropic transport builds the
    correct ``thinking`` parameter shape for a reasoning-config-aware model.
    This is a kwargs-correctness test, not a live call — but it guards the
    contract that a Claude/Kimi reasoning model receives a ``thinking`` block
    when reasoning_config is enabled.
    """

    def test_anthropic_emits_adaptive_thinking_for_claude(self):
        """Claude 4.6+ adaptive-thinking models get
        ``thinking={"type":"adaptive","display":"summarized"}`` +
        ``output_config.effort``."""
        from agent.transports import get_transport
        import agent.transports.anthropic  # noqa: F401

        transport = get_transport("anthropic_messages")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Think and answer: what is 7*6?"},
        ]
        kwargs = transport.build_kwargs(
            model="claude-opus-4-6",
            messages=messages,
            tools=[],
            reasoning_config={"enabled": True, "effort": "high"},
            max_tokens=4096,
        )
        assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert kwargs["output_config"]["effort"] == "high"

    def test_anthropic_emits_manual_thinking_for_legacy_claude(self):
        """Legacy (pre-4.6) Claude models get manual
        ``thinking={"type":"enabled","budget_tokens":N}`` + temperature=1."""
        from agent.transports import get_transport
        import agent.transports.anthropic  # noqa: F401

        transport = get_transport("anthropic_messages")
        messages = [{"role": "user", "content": "Think and answer: 7*6?"}]
        kwargs = transport.build_kwargs(
            model="claude-3-7-sonnet-20250219",
            messages=messages,
            tools=[],
            reasoning_config={"enabled": True, "effort": "high"},
            max_tokens=4096,
        )
        assert kwargs["thinking"]["type"] == "enabled"
        assert kwargs["thinking"]["budget_tokens"] > 0
        assert kwargs["temperature"] == 1

    def test_anthropic_omits_thinking_when_disabled(self):
        """reasoning_config={"enabled": False} must NOT add a thinking kwarg."""
        from agent.transports import get_transport
        import agent.transports.anthropic  # noqa: F401

        transport = get_transport("anthropic_messages")
        messages = [{"role": "user", "content": "hi"}]
        kwargs = transport.build_kwargs(
            model="claude-opus-4-6",
            messages=messages,
            tools=[],
            reasoning_config={"enabled": False},
            max_tokens=2048,
        )
        assert "thinking" not in kwargs
        assert "output_config" not in kwargs

    def test_anthropic_omits_thinking_when_no_config(self):
        """reasoning_config=None must NOT add a thinking kwarg (no forced thinking)."""
        from agent.transports import get_transport
        import agent.transports.anthropic  # noqa: F401

        transport = get_transport("anthropic_messages")
        messages = [{"role": "user", "content": "hi"}]
        kwargs = transport.build_kwargs(
            model="claude-opus-4-6",
            messages=messages,
            tools=[],
            reasoning_config=None,
            max_tokens=2048,
        )
        assert "thinking" not in kwargs
        assert "output_config" not in kwargs

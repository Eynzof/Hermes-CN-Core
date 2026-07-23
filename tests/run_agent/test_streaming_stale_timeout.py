"""Streaming stale-stream detector: abort the live transport and give up
within a bounded time instead of hanging forever.

Regression coverage for the long-session desktop hang (P-022): when an
Anthropic streaming connection went silent (half-open socket, no FIN), the
old detector only aborted the *OpenAI* request client — never the Anthropic
client that actually owned the stream — then reset its own timer and looped,
so ``interruptible_streaming_api_call`` blocked forever. The UI elapsed timer
kept ticking while nothing happened.

These tests assert:
  * the Anthropic client is built with TCP keepalive socket options, and
  * a wedged Anthropic stream is aborted (on the Anthropic client) and the
    turn surfaces a ``TimeoutError`` in bounded time rather than hanging.
"""

from __future__ import annotations

import threading
import time

import pytest


# ── TCP keepalive wiring ─────────────────────────────────────────────────────


def test_keepalive_socket_options_enables_keepalive():
    import socket

    from agent.httpx_clients import keepalive_socket_options

    opts = keepalive_socket_options()
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in opts
    # On Linux the per-socket tuning knobs should be present too.
    if hasattr(socket, "TCP_KEEPIDLE"):
        keys = {(fam, opt) for (fam, opt, _val) in opts}
        assert (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE) in keys
        assert (socket.IPPROTO_TCP, socket.TCP_KEEPINTVL) in keys
        assert (socket.IPPROTO_TCP, socket.TCP_KEEPCNT) in keys


def test_anthropic_client_built_with_keepalive(monkeypatch):
    """build_anthropic_client must forward keepalive socket_options to the
    underlying httpx client so the kernel can detect a dead connection."""
    import agent.httpx_clients as httpx_clients
    from agent import anthropic_adapter

    captured = {}

    def _fake_build_httpx_client(*, base_url=None, timeout=None, headers=None,
                                 socket_options=None):
        import httpx
        captured["socket_options"] = socket_options
        # Must be a real httpx.Client — the Anthropic SDK type-checks http_client.
        return httpx.Client()

    monkeypatch.setattr(httpx_clients, "build_httpx_client", _fake_build_httpx_client)
    # The adapter imports the symbol lazily inside the function, so patching the
    # source module is enough.
    client = anthropic_adapter.build_anthropic_client("sk-ant-dummy", None, timeout=30.0)
    assert client is not None
    assert captured["socket_options"], "anthropic http_client got no keepalive options"
    import socket
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in captured["socket_options"]


# ── wedged Anthropic stream → bounded TimeoutError ───────────────────────────


class _WedgedAnthropicStream:
    """A streaming context manager whose iterator blocks ~forever — models a
    half-open provider socket where the worker's ``recv`` never returns and
    ``shutdown()`` from the detector thread fails to unblock it (worst case)."""

    response = None

    def __init__(self, released: threading.Event):
        self._released = released

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        # Block until the test releases us; in production this is an
        # unbounded recv() on a dead socket.
        self._released.wait(timeout=30)
        return iter(())

    def get_final_message(self):
        return None


def _make_streaming_agent():
    """Bare AIAgent (via __new__) with just the attrs/stubs the streaming
    Anthropic path touches."""
    from run_agent import AIAgent

    agent = AIAgent.__new__(AIAgent)
    agent._interrupt_requested = False
    agent.quiet_mode = True
    agent.log_prefix = ""
    agent.api_mode = "anthropic_messages"
    agent.provider = "anthropic"
    agent.model = "claude-sonnet-4-5"
    agent.base_url = "https://api.anthropic.com"
    agent._base_url = agent.base_url
    agent._disable_streaming = False
    agent._current_streamed_assistant_text = ""

    # Anthropic stream client — only ``.messages.stream`` is exercised.
    released = threading.Event()

    class _Messages:
        def stream(self, **kwargs):
            return _WedgedAnthropicStream(released)

    class _AnthropicClient:
        def __init__(self):
            self.messages = _Messages()

        def close(self):
            pass

    agent._anthropic_client = _AnthropicClient()
    agent._released_event = released

    # Stubs for the side-effect methods the detector / call path invoke.
    abort_calls = {"n": 0, "clients": []}

    def _force_close(client):
        abort_calls["n"] += 1
        abort_calls["clients"].append(client)
        return 1

    agent._force_close_tcp_sockets = _force_close
    # v0.19 streams through a request-local Anthropic client so the watchdog
    # can abort sockets without closing the shared SDK client from a stranger
    # thread.  Reuse the fake stream owner here while preserving that contract.
    agent._create_request_anthropic_client = (
        lambda *, reason: agent._anthropic_client
    )
    agent._abort_request_anthropic_client = (
        lambda client, *, reason: _force_close(client)
    )
    agent._close_request_anthropic_client = (
        lambda client, *, reason: client.close()
    )
    agent._rebuild_anthropic_client = lambda: None
    agent._try_refresh_anthropic_client_credentials = lambda: True
    agent._touch_activity = lambda *a, **k: None
    agent._emit_status = lambda *a, **k: None
    agent._buffer_status = lambda *a, **k: None
    agent._stream_diag_init = lambda: {}
    agent._stream_diag_capture_response = lambda *a, **k: None
    agent._fire_first_delta = lambda *a, **k: None
    agent._fire_stream_delta = lambda *a, **k: None
    agent._fire_reasoning_delta = lambda *a, **k: None
    agent._fire_tool_gen_started = lambda *a, **k: None
    agent._abort_calls = abort_calls
    return agent


def test_streaming_stale_anthropic_aborts_and_times_out(monkeypatch, tmp_path):
    """A wedged Anthropic stream must surface a TimeoutError in bounded time
    and must abort the *Anthropic* client (not silently loop forever)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    # Tiny knobs so the bounded escalation completes in ~seconds.
    monkeypatch.setenv("HERMES_STREAM_STALE_TIMEOUT", "0.4")
    monkeypatch.setenv("HERMES_STREAM_STALE_KILL_GRACE", "0.4")
    monkeypatch.setenv("HERMES_STREAM_STALE_MAX_KILLS", "2")
    monkeypatch.setenv("HERMES_STREAM_RETRIES", "0")

    from agent.chat_completion_helpers import interruptible_streaming_api_call

    agent = _make_streaming_agent()
    api_kwargs = {
        "model": "claude-sonnet-4-5",
        "messages": [{"role": "user", "content": "hi"}],
    }

    t0 = time.time()
    try:
        with pytest.raises(TimeoutError):
            interruptible_streaming_api_call(agent, api_kwargs)
        elapsed = time.time() - t0
        # Must be bounded — not the old infinite hang. Generous upper bound to
        # avoid flakiness on slow CI: stale(0.4) + 2 * grace(0.4) + slack.
        assert elapsed < 15, f"stale recovery took {elapsed:.1f}s (expected bounded)"
        # The Anthropic client (the stream owner) was aborted at least once.
        assert agent._abort_calls["n"] >= 1
        assert agent._abort_calls["clients"][0] is agent._anthropic_client
    finally:
        # Release the abandoned daemon worker so it can unwind.
        agent._released_event.set()

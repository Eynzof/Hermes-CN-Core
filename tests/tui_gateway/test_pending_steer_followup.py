"""P-023: the gateway turn-runner must deliver a leftover ``/steer`` as the
next user turn.

``run_conversation`` only injects steer into a *following* tool result. A steer
that lands after the final tool batch (or in a text-only turn) is returned as
``result["pending_steer"]`` for the caller to re-deliver. ``cli.py`` does this;
the ``tui_gateway`` runner used to drop it, so desktop steers (whose default
busy-input mode is "steer") silently vanished. This test pins the fix:
``_run_prompt_submit`` must fire a second turn carrying the steered text.
"""
from __future__ import annotations

import threading
import time

import pytest

from tui_gateway import server


class _FakeAgent:
    """Returns a leftover steer on the first turn, nothing on the second."""

    def __init__(self) -> None:
        self.calls: list = []
        self.model = "test-model"
        self.base_url = ""
        self.api_key = ""
        self.provider = ""

    def run_conversation(self, message, **kwargs):
        self.calls.append(message)
        if len(self.calls) == 1:
            return {"messages": [], "final_response": "", "pending_steer": "GUIDE-ME"}
        return {"messages": [], "final_response": ""}


@pytest.fixture
def _stub_runner(monkeypatch, tmp_path):
    """No-op the runner's external side effects so we exercise only control flow."""
    monkeypatch.setattr(server, "_wire_callbacks", lambda sid: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda session: None)
    monkeypatch.setattr(server, "_session_cwd", lambda session: str(tmp_path))
    monkeypatch.setattr(server, "make_stream_renderer", lambda cols: None)
    monkeypatch.setattr(server, "_get_usage", lambda agent: {})
    monkeypatch.setattr(server, "_session_info", lambda agent, session=None: {})
    monkeypatch.setattr(
        server, "_sync_session_key_after_compress", lambda *a, **k: None
    )
    monkeypatch.setattr(server, "render_message", lambda raw, cols: "")

    events: list = []
    monkeypatch.setattr(
        server, "_emit", lambda etype, sid, payload=None: events.append((etype, sid))
    )

    # drain_notifications is consulted post-turn; keep it empty + deterministic.
    from tools.process_registry import process_registry

    monkeypatch.setattr(process_registry, "drain_notifications", lambda: [])
    return events


def _make_session(agent: _FakeAgent) -> dict:
    return {
        "session_key": "sk-test",
        "history": [],
        "history_version": 0,
        "attached_images": [],
        "agent": agent,
        "cols": 80,
        "cwd": ".",
        "running": True,  # the prompt.submit handler sets this before dispatch
        "history_lock": threading.RLock(),
    }


def _wait_for_calls(agent: _FakeAgent, n: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(agent.calls) >= n:
            return
        time.sleep(0.02)


def test_pending_steer_is_redelivered_as_next_turn(_stub_runner, monkeypatch):
    sid = "sess-1"
    agent = _FakeAgent()
    session = _make_session(agent)
    monkeypatch.setitem(server._sessions, sid, session)

    server._run_prompt_submit("rid-1", sid, session, "hello")

    # First turn ("hello") returns pending_steer; the runner must chain a second
    # turn carrying that steered text.
    _wait_for_calls(agent, 2)

    assert agent.calls[0] == "hello"
    assert agent.calls[1] == "GUIDE-ME", "leftover steer was not re-delivered as a turn"
    # And it must not loop forever — the second turn returns no pending_steer.
    time.sleep(0.1)
    assert len(agent.calls) == 2
    # Session is left idle after the chained turn settles.
    assert session["running"] is False


def test_no_followup_when_no_pending_steer(_stub_runner, monkeypatch):
    sid = "sess-2"
    agent = _FakeAgent()
    agent.calls.append("__prime__")  # force run_conversation to take the no-steer branch
    session = _make_session(agent)
    monkeypatch.setitem(server._sessions, sid, session)

    server._run_prompt_submit("rid-2", sid, session, "hello")
    _wait_for_calls(agent, 2)
    time.sleep(0.1)

    # Only the primed entry + the single real turn — no chained follow-up.
    assert agent.calls == ["__prime__", "hello"]
    assert session["running"] is False

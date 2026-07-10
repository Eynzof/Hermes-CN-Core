"""P-041: tool-call-only turns must commit the assistant message before tools run.

The core loop now fires ``tool_calls_committed_callback`` when it appends an
assistant message that contains tool calls.  The gateway must relay that as an
``assistant.tool_calls_committed`` event so the desktop UI has a clean boundary
between ``message.start`` and ``tool.start``.
"""

from __future__ import annotations

import threading
import time

import pytest

from tui_gateway import server


def _make_session(agent: object) -> dict:
    return {
        "session_key": "sk-test",
        "history": [],
        "history_version": 0,
        "attached_images": [],
        "agent": agent,
        "cols": 80,
        "cwd": ".",
        "running": True,
        "history_lock": threading.RLock(),
    }


@pytest.fixture
def _stub_runner(monkeypatch, tmp_path):
    """No-op the runner's external side effects so we exercise only control flow."""
    monkeypatch.setattr(server, "_wire_callbacks", lambda sid: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda session: None)
    monkeypatch.setattr(server, "_session_cwd", lambda session: str(tmp_path))
    monkeypatch.setattr(server, "make_stream_renderer", lambda cols: None)
    monkeypatch.setattr(server, "_get_usage", lambda agent: {})
    monkeypatch.setattr(server, "_session_info", lambda agent, session=None: {})
    monkeypatch.setattr(server, "_sync_session_key_after_compress", lambda *a, **k: None)
    monkeypatch.setattr(server, "render_message", lambda raw, cols: "")
    monkeypatch.setattr(server, "_start_turn_watchdog", lambda sid, session: lambda: None)

    events: list = []
    monkeypatch.setattr(
        server, "_emit", lambda etype, sid, payload=None: events.append((etype, sid, payload))
    )

    from tools.process_registry import process_registry

    monkeypatch.setattr(process_registry, "drain_notifications", lambda: [])
    return events


class _FakeAgentWithCommittedCb:
    """Calls the tool_calls_committed_callback inside run_conversation."""

    def __init__(self) -> None:
        self.calls: list = []
        self.model = "test-model"
        self.base_url = ""
        self.api_key = ""
        self.provider = ""
        self.tool_calls_committed_callback = None

    def run_conversation(self, message, **kwargs):
        self.calls.append(message)
        # Simulate a tool-call-only assistant message being committed.
        if self.tool_calls_committed_callback:
            self.tool_calls_committed_callback(
                {
                    "role": "assistant",
                    "content": "",
                    "finish_reason": "tool_calls",
                    "tool_calls": [{"id": "call_1", "function": {"name": "write_file"}}],
                }
            )
        # Simulate the core loop flushing a None stream delta before tools.
        stream_callback = kwargs.get("stream_callback")
        if stream_callback:
            stream_callback(None)
        return {"messages": [], "final_response": "ok"}


def test_agent_cbs_exposes_tool_calls_committed_callback(_stub_runner, monkeypatch):
    sid = "sess-committed"
    events = _stub_runner
    monkeypatch.setitem(server._sessions, sid, _make_session(object()))

    cbs = server._agent_cbs(sid)
    assert "tool_calls_committed_callback" in cbs

    cbs["tool_calls_committed_callback"](
        {
            "role": "assistant",
            "content": "",
            "finish_reason": "tool_calls",
            "tool_calls": [{"id": "call_1", "function": {"name": "write_file"}}],
        }
    )

    assert any(etype == "assistant.tool_calls_committed" for etype, _sid, _payload in events)
    event = next(
        payload for etype, _sid, payload in events if etype == "assistant.tool_calls_committed"
    )
    assert event["tool_call_ids"] == ["call_1"]
    assert event["finish_reason"] == "tool_calls"
    assert event["has_content"] is False


def test_run_prompt_submit_relays_tool_calls_committed(_stub_runner, monkeypatch):
    sid = "sess-relay"
    events = _stub_runner
    agent = _FakeAgentWithCommittedCb()
    # Wire the gateway callback exactly as _make_agent does for real agents.
    agent.tool_calls_committed_callback = server._agent_cbs(sid)[
        "tool_calls_committed_callback"
    ]
    session = _make_session(agent)
    monkeypatch.setitem(server._sessions, sid, session)

    server._run_prompt_submit("rid-1", sid, session, "hello")

    # Wait for the turn thread to finish.
    deadline = time.time() + 5
    while session.get("running") and time.time() < deadline:
        time.sleep(0.02)

    event_types = [etype for etype, _sid, _payload in events]
    assert "message.start" in event_types
    assert "assistant.tool_calls_committed" in event_types
    assert "message.complete" in event_types
    # The None stream delta from the fake agent must be swallowed, not emitted
    # as a message.delta with a null text payload.
    assert "message.delta" not in event_types

"""Integration tests for the groupchat.* gateway methods (CN fork P-052).

Drives the orchestration with a fake agent (no real LLM, no HERMES_HOME switch)
and captures emitted events, asserting @mention routing + per-member sender
attribution on the message.* stream. Complements the pure-function coverage in
tests/agent/test_groupchat_loop.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tui_gateway.server as server


class _FakeAgent:
    """Stand-in for a built AIAgent: streams one delta, returns a final dict."""

    def __init__(self, reply: str):
        self._reply = reply

    def run_conversation(
        self,
        user_message,
        system_message=None,
        conversation_history=None,
        stream_callback=None,
        **kwargs,
    ):
        if stream_callback is not None:
            stream_callback(self._reply)
        return {
            "final_response": self._reply,
            "messages": [],
            "interrupted": False,
            "error": None,
        }


@pytest.fixture
def captured_events(monkeypatch):
    events: list[tuple[str, str, dict | None]] = []
    monkeypatch.setattr(
        server, "_emit", lambda ev, sid, payload=None: events.append((ev, sid, payload))
    )
    return events


@pytest.fixture
def stub_profiles(monkeypatch, tmp_path):
    """All profiles exist, resolve to a tmp home, and carry a stub description.

    Also neutralizes the HERMES_HOME ContextVar override so the turn never
    touches a real profile directory.
    """
    from hermes_cli import profiles as profiles_mod

    monkeypatch.setattr(profiles_mod, "normalize_profile_name", lambda n: n.lower())
    monkeypatch.setattr(profiles_mod, "profile_exists", lambda n: True)
    monkeypatch.setattr(profiles_mod, "get_profile_dir", lambda n: tmp_path / n)
    monkeypatch.setattr(
        profiles_mod, "read_profile_meta", lambda d: {"description": f"角色 {Path(d).name}"}
    )
    monkeypatch.setattr(server, "set_hermes_home_override", lambda h: None)
    monkeypatch.setattr(server, "reset_hermes_home_override", lambda t: None)


def _create(members, title="研究室", rid="rid-create"):
    return server._methods["groupchat.create"](rid, {"members": members, "title": title})


def test_create_validates_and_returns_members(stub_profiles):
    result = _create(["alice", "bob"])
    assert "result" in result
    room = result["result"]
    assert room["room_id"].startswith("gc_")
    assert [m["name"] for m in room["members"]] == ["alice", "bob"]


def test_create_rejects_unknown_profile(monkeypatch, tmp_path):
    from hermes_cli import profiles as profiles_mod

    monkeypatch.setattr(profiles_mod, "normalize_profile_name", lambda n: n.lower())
    monkeypatch.setattr(profiles_mod, "profile_exists", lambda n: n == "alice")
    monkeypatch.setattr(profiles_mod, "get_profile_dir", lambda n: tmp_path / n)
    monkeypatch.setattr(profiles_mod, "read_profile_meta", lambda d: {})
    result = server._methods["groupchat.create"]("rid", {"members": ["alice", "ghost"]})
    assert "error" in result


def test_submit_routes_only_to_mentioned_member(stub_profiles, captured_events, monkeypatch):
    room_id = _create(["alice", "bob"])["result"]["room_id"]
    monkeypatch.setattr(server, "_make_agent", lambda sid, key, **kw: _FakeAgent("我是 alice 的回复"))

    res = server._methods["groupchat.submit"]("rid", {"room_id": room_id, "text": "@alice 你好"})
    assert res["result"]["replied"] == ["alice"]

    starts = [p for ev, _s, p in captured_events if ev == "message.start"]
    completes = [p for ev, _s, p in captured_events if ev == "message.complete"]
    assert len(starts) == 1 and len(completes) == 1
    assert starts[0]["sender_name"] == "alice"
    assert completes[0]["sender_name"] == "alice"
    assert completes[0]["text"] == "我是 alice 的回复"
    assert completes[0]["status"] == "complete"


def test_submit_no_mention_defaults_to_all(stub_profiles, captured_events, monkeypatch):
    # P-052 UX: a plain message with no @ addresses the whole room (everyone
    # replies), instead of studio's "message lands but nobody answers".
    room_id = _create(["alice", "bob"])["result"]["room_id"]
    monkeypatch.setattr(server, "_make_agent", lambda sid, key, **kw: _FakeAgent("hi"))

    res = server._methods["groupchat.submit"]("rid", {"room_id": room_id, "text": "大家好呀，没点名"})
    assert set(res["result"]["replied"]) == {"alice", "bob"}
    assert not any(ev == "groupchat.no_targets" for ev, _s, _p in captured_events)


def test_submit_unmatched_mention_emits_no_targets(stub_profiles, captured_events, monkeypatch):
    # An @ that matches no member is a real "no targets" (user typed a wrong name).
    room_id = _create(["alice", "bob"])["result"]["room_id"]
    monkeypatch.setattr(server, "_make_agent", lambda sid, key, **kw: _FakeAgent("x"))

    res = server._methods["groupchat.submit"]("rid", {"room_id": room_id, "text": "@nobody 在吗"})
    assert res["result"]["replied"] == []
    assert any(ev == "groupchat.no_targets" for ev, _s, _p in captured_events)
    assert not any(ev == "message.start" for ev, _s, _p in captured_events)


def test_submit_all_routes_to_every_member_and_accumulates(stub_profiles, captured_events, monkeypatch):
    room_id = _create(["alice", "bob"])["result"]["room_id"]
    monkeypatch.setattr(server, "_make_agent", lambda sid, key, **kw: _FakeAgent("hi"))

    res = server._methods["groupchat.submit"]("rid", {"room_id": room_id, "text": "@all 大家好"})
    assert set(res["result"]["replied"]) == {"alice", "bob"}

    # Shared transcript accumulates: 1 user message + 2 member replies.
    room = server._group_rooms[room_id]
    assert len(room.transcript) == 3
    assert room.transcript[0]["sender_id"] == "user"
    assert {m["sender_name"] for m in room.transcript[1:]} == {"alice", "bob"}


def test_submit_unknown_room_errors(stub_profiles):
    res = server._methods["groupchat.submit"]("rid", {"room_id": "gc_missing", "text": "@alice hi"})
    assert "error" in res


def test_web_server_serves_transcript_with_sender(stub_profiles, monkeypatch):
    """The reload path: web_server serves the in-memory transcript WITH sender
    attribution, instead of the DB sub-sessions (gc_<id>:<profile>) that carry
    none — this is what fixed the "identity reverts to global on complete/reload"
    bug (P-052)."""
    from hermes_cli.web_server import _group_chat_room_messages

    room_id = _create(["alice", "bob"])["result"]["room_id"]
    monkeypatch.setattr(server, "_make_agent", lambda sid, key, **kw: _FakeAgent("hi from alice"))
    server._methods["groupchat.submit"]("rid", {"room_id": room_id, "text": "@alice hi"})

    rows = _group_chat_room_messages(room_id)
    assert rows is not None
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["sender_name"] == "alice"
    assert rows[1]["sender_agent_id"] == "alice"
    assert rows[1]["content"] == "hi from alice"
    assert rows[1]["timestamp"]  # non-zero, set from time.time()

    # A per-member sub-session id or a regular session must NOT be served as a room.
    assert _group_chat_room_messages(f"{room_id}:alice") is None
    assert _group_chat_room_messages("regular_session_id") is None


def test_info_returns_room_members(stub_profiles):
    room_id = _create(["alice", "bob"], title="研究室")["result"]["room_id"]
    info = server._methods["groupchat.info"]("rid", {"room_id": room_id})
    assert info["result"]["room_id"] == room_id
    assert info["result"]["title"] == "研究室"
    assert [m["name"] for m in info["result"]["members"]] == ["alice", "bob"]
    assert "error" in server._methods["groupchat.info"]("rid", {"room_id": "gc_missing"})


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

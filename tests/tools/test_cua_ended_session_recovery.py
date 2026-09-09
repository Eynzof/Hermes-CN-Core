"""Regression for Desktop #599: a live MCP transport can reject an ended session."""
import asyncio
from types import SimpleNamespace

import pytest

from tools.computer_use.cua_backend import _CuaDriverSession, _extract_tool_result


def result(text, *, error=False):
    return _extract_tool_result(SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        structuredContent=None, isError=error,
    ))


@pytest.mark.parametrize("revival_fails,retry_fails", [(False, False), (True, False), (False, True)])
def test_ended_session_redeclares_identity_and_retries_at_most_once(revival_fails, retry_fails):
    session = _CuaDriverSession.__new__(_CuaDriverSession)
    session._started = True
    session._declared_session_id = None
    session._require_started = lambda: None
    session._bridge = SimpleNamespace(run=lambda coro, timeout: asyncio.run(coro))
    calls = []
    ended = result("Session hermes-task has ended. Call start_session to start again.", error=True)

    async def call(name, args):
        calls.append((name, dict(args)))
        if name == "start_session":
            return result("denied", error=True) if len(calls) > 1 and revival_fails else result("started")
        return ended if len(calls) == 2 or retry_fails else result("clicked")

    session._call_tool_async = call
    session.call_tool("start_session", {"session": "hermes-task"})
    actual = session.call_tool("click", {"pid": 10, "element_index": 3})
    assert calls[:3] == [
        ("start_session", {"session": "hermes-task"}),
        ("click", {"pid": 10, "element_index": 3}),
        ("start_session", {"session": "hermes-task"}),
    ]
    assert len(calls) == (3 if revival_fails else 4)
    if not revival_fails:
        assert calls[3] == calls[1]
    assert actual["isError"] is (revival_fails or retry_fails)


def test_unrelated_logical_error_is_not_replayed():
    session = _CuaDriverSession.__new__(_CuaDriverSession)
    session._started = True
    session._declared_session_id = "hermes-task"
    session._require_started = lambda: None
    session._bridge = SimpleNamespace(run=lambda coro, timeout: asyncio.run(coro))
    calls = []
    denied = result("Permission denied", error=True)

    async def call(name, args):
        calls.append(name)
        return denied

    session._call_tool_async = call
    assert session.call_tool("click", {"pid": 10}) == denied
    assert calls == ["click"]

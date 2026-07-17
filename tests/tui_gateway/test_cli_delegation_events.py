"""P-047: delegation.cli.* 事件在 gateway 接线处的行为。

直接驱动 ``server._on_tool_start`` / ``server._on_tool_complete``（monkeypatch
``server._emit`` 收集事件，手法同 test_tool_call_committed_event.py），验证：
前台委派两拍终态、后台委派绑定 process session、启动失败直接 failed、
非 terminal / 非委派命令不产生事件、tool progress 关闭时整体静默。
"""

from __future__ import annotations

import json

import pytest

from tui_gateway import cli_delegation, server


@pytest.fixture
def events(monkeypatch):
    collected: list[tuple[str, str, dict | None]] = []

    def _collect(event_type: str, sid: str, payload: dict | None = None) -> None:
        collected.append((event_type, sid, payload))

    monkeypatch.setattr(server, "_emit", _collect)
    monkeypatch.setattr(server, "_tool_progress_enabled", lambda sid: True)
    monkeypatch.setattr(server, "_session_verbose", lambda sid: False)
    cli_delegation.tracker.reset()
    cli_delegation.tracker.configure(emit=_collect, is_alive=lambda sid: True)
    # 单测里不真正起 watcher 线程（绑定路径仅登记状态）。
    monkeypatch.setattr(
        cli_delegation.DelegationTracker, "_ensure_watcher", lambda self: None
    )
    yield collected
    cli_delegation.tracker.reset()


def _by_type(events_list, event_type):
    return [entry for entry in events_list if entry[0] == event_type]


def test_foreground_claude_json_delegation_started_and_completed(events):
    args = {
        "command": "claude -p 'Analyze auth.py' --output-format json --max-turns 5",
        "workdir": "/project",
    }
    server._on_tool_start("sid-1", "call-1", "terminal", args)

    started = _by_type(events, "delegation.cli.started")
    assert len(started) == 1
    payload = started[0][2]
    assert payload["delegation_id"] == "call-1"
    assert payload["tool_id"] == "call-1"
    assert payload["agent"] == "claude-code"
    assert payload["mode"] == "print"
    assert payload["execution"] == "foreground"
    assert payload["prompt_excerpt"] == "Analyze auth.py"
    assert payload["workdir"] == "/project"

    result = json.dumps({
        "output": json.dumps({
            "type": "result",
            "subtype": "success",
            "session_id": "cc-sess-1",
            "num_turns": 4,
            "total_cost_usd": 0.03,
            "is_error": False,
        }),
        "exit_code": 0,
        "error": None,
    })
    server._on_tool_complete("sid-1", "call-1", "terminal", args, result)

    completed = _by_type(events, "delegation.cli.completed")
    assert len(completed) == 1
    payload = completed[0][2]
    assert payload["status"] == "completed"
    assert payload["exit_code"] == 0
    assert payload["result"]["session_id"] == "cc-sess-1"
    assert payload["result"]["num_turns"] == 4
    # 终态后 tracker 不留状态。
    assert cli_delegation.tracker._entries == {}


def test_foreground_failure_maps_to_failed(events):
    args = {"command": "claude -p 'do thing'"}
    server._on_tool_start("sid-1", "call-2", "terminal", args)
    result = json.dumps({"output": "boom", "exit_code": 1, "error": "exit 1"})
    server._on_tool_complete("sid-1", "call-2", "terminal", args, result)

    completed = _by_type(events, "delegation.cli.completed")
    assert len(completed) == 1
    assert completed[0][2]["status"] == "failed"
    assert completed[0][2]["exit_code"] == 1


def test_background_codex_binds_process_session(events):
    args = {
        "command": "codex exec --json --full-auto 'Refactor auth'",
        "background": True,
        "pty": True,
    }
    server._on_tool_start("sid-1", "call-3", "terminal", args)
    started = _by_type(events, "delegation.cli.started")
    assert started[0][2]["execution"] == "background"

    result = json.dumps({
        "output": "Background process started",
        "session_id": "proc_abc123",
        "pid": 4242,
        "exit_code": 0,
        "error": None,
    })
    server._on_tool_complete("sid-1", "call-3", "terminal", args, result)

    # 后台绑定不发终态，交给 watcher。
    assert _by_type(events, "delegation.cli.completed") == []
    assert cli_delegation.tracker._by_process.get("proc_abc123") == "call-3"


def test_background_start_blocked_is_failed(events):
    args = {"command": "codex exec 'x'", "background": True, "pty": True}
    server._on_tool_start("sid-1", "call-4", "terminal", args)
    result = json.dumps({
        "output": "approval required",
        "exit_code": -1,
        "error": "blocked",
        "status": "blocked",
    })
    server._on_tool_complete("sid-1", "call-4", "terminal", args, result)

    completed = _by_type(events, "delegation.cli.completed")
    assert len(completed) == 1
    assert completed[0][2]["status"] == "failed"
    assert cli_delegation.tracker._by_process == {}


def test_non_delegation_and_non_terminal_are_silent(events):
    server._on_tool_start("sid-1", "call-5", "terminal", {"command": "ls -la"})
    server._on_tool_complete(
        "sid-1", "call-5", "terminal", {"command": "ls -la"},
        json.dumps({"output": "files", "exit_code": 0, "error": None}),
    )
    server._on_tool_start("sid-1", "call-6", "read_file", {"path": "/etc/hosts"})

    assert _by_type(events, "delegation.cli.started") == []
    assert _by_type(events, "delegation.cli.completed") == []
    # 普通 tool.start / tool.complete 不受影响照常发出。
    assert len(_by_type(events, "tool.start")) == 2
    assert len(_by_type(events, "tool.complete")) == 1


def test_tool_progress_disabled_suppresses_delegation_events(events, monkeypatch):
    monkeypatch.setattr(server, "_tool_progress_enabled", lambda sid: False)
    args = {"command": "claude -p 'hi'"}
    server._on_tool_start("sid-1", "call-7", "terminal", args)
    server._on_tool_complete(
        "sid-1", "call-7", "terminal", args,
        json.dumps({"output": "ok", "exit_code": 0, "error": None}),
    )
    assert _by_type(events, "delegation.cli.started") == []
    assert _by_type(events, "delegation.cli.completed") == []

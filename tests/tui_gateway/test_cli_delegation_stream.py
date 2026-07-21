"""P-047: DelegationTracker 后台输出流与终态检测。

不起 watcher 线程，同步调用 ``on_chunk`` / ``_tick_entry`` / ``_sweep``，
验证：跨 chunk 半行 JSONL 拼接、单次冲刷 chunk 上限、累计 256KB 截断、
ANSI 剥离、进程退出状态映射（completed/failed/killed/lost）、进程消失
判 lost、死会话静默清理。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from tui_gateway import cli_delegation
from tui_gateway.cli_delegation import DelegationTracker


@dataclass
class FakeProcessSession:
    id: str
    exited: bool = False
    exit_code: int | None = None
    completion_reason: str = "exited"
    output_buffer: str = ""
    session_key: str = "sk"
    pid: int = 4242
    extra: dict = field(default_factory=dict)


@pytest.fixture
def rig(monkeypatch):
    """独立 tracker 实例 + 事件收集 + 假 process registry。"""
    processes: dict[str, FakeProcessSession] = {}
    collected: list[tuple[str, str, dict]] = []

    tracker = DelegationTracker()
    tracker.configure(
        emit=lambda event, sid, payload: collected.append((event, sid, payload)),
        is_alive=lambda sid: sid != "dead-sid",
    )
    tracker._ensure_watcher = lambda: None  # 测试内同步驱动，不起线程
    monkeypatch.setattr(
        DelegationTracker,
        "_lookup_process",
        staticmethod(lambda psid: processes.get(psid or "")),
    )
    return tracker, processes, collected


def _bind_background(tracker, processes, *, agent="claude", psid="proc_1", sid="sid-1"):
    command = (
        "claude -p 'Summarize repo' --output-format stream-json --verbose"
        if agent == "claude"
        else "codex exec --json 'task'"
    )
    args = {"command": command, "background": True, "pty": agent != "claude"}
    tracker.handle_tool_start(sid, f"deleg-{psid}", "terminal", args)
    tracker.handle_tool_complete(
        sid,
        f"deleg-{psid}",
        "terminal",
        {"output": "Background process started", "session_id": psid, "exit_code": 0},
    )
    process = FakeProcessSession(id=psid)
    processes[psid] = process
    return tracker._entries[f"deleg-{psid}"], process


def _events_of(collected, event_type):
    return [payload for event, _sid, payload in collected if event == event_type]


def test_half_line_jsonl_is_assembled_across_chunks(rig):
    tracker, processes, collected = rig
    entry, process = _bind_background(tracker, processes)

    line = json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hello world"}]},
    })
    tracker.on_chunk(process, line[:20])
    tracker._tick_entry(entry)
    outputs = _events_of(collected, "delegation.cli.output")
    # 半行留在 remainder：chunk 照发，events 为空。
    assert len(outputs) == 1
    assert outputs[0]["events"] == []
    assert outputs[0]["chunk"] == line[:20]

    tracker.on_chunk(process, line[20:] + "\n")
    tracker._tick_entry(entry)
    outputs = _events_of(collected, "delegation.cli.output")
    assert len(outputs) == 2
    assert outputs[1]["events"] == [{"kind": "text", "text": "hello world"}]


def test_chunk_is_capped_and_stream_total_cap_degrades_to_events_only(rig):
    tracker, processes, collected = rig
    entry, process = _bind_background(tracker, processes)

    tracker.on_chunk(process, "x" * 10_000 + "\n")
    tracker._tick_entry(entry)
    outputs = _events_of(collected, "delegation.cli.output")
    assert len(outputs[0]["chunk"]) <= cli_delegation.CHUNK_CAP

    # 灌满累计上限后：chunk 停发、truncated 标记，事件仍解析。
    entry.streamed = cli_delegation.STREAM_TOTAL_CAP + 1
    entry.stream_capped = True
    result_line = json.dumps({
        "type": "result", "subtype": "success", "session_id": "s", "num_turns": 1,
    })
    tracker.on_chunk(process, result_line + "\n")
    tracker._tick_entry(entry)
    outputs = _events_of(collected, "delegation.cli.output")
    last = outputs[-1]
    assert last["chunk"] == ""
    assert last["truncated"] is True
    assert last["events"][0]["kind"] == "result"


def test_ansi_is_stripped_at_ingestion(rig):
    tracker, processes, collected = rig
    entry, process = _bind_background(tracker, processes, agent="codex", psid="proc_2")

    tracker.on_chunk(process, "\x1b[31mred text\x1b[0m\n")
    tracker._tick_entry(entry)
    outputs = _events_of(collected, "delegation.cli.output")
    assert "\x1b" not in outputs[0]["chunk"]
    assert "red text" in outputs[0]["chunk"]


def test_exit_status_mapping_and_result_extraction(rig):
    tracker, processes, collected = rig

    # exit 0 → completed，且从 output_buffer 提取 stream-json result。
    entry, process = _bind_background(tracker, processes, psid="proc_ok")
    process.exited = True
    process.exit_code = 0
    process.output_buffer = "\n".join([
        '{"type":"system","subtype":"init","session_id":"cc-1","model":"opus"}',
        '{"type":"result","subtype":"success","session_id":"cc-1","num_turns":2,'
        '"total_cost_usd":0.01,"is_error":false}',
    ])
    tracker._tick_entry(entry)
    completed = _events_of(collected, "delegation.cli.completed")
    assert completed[-1]["status"] == "completed"
    assert completed[-1]["result"]["session_id"] == "cc-1"
    assert completed[-1]["execution"] == "background"

    # 非零退出 → failed。
    entry, process = _bind_background(tracker, processes, psid="proc_fail")
    process.exited = True
    process.exit_code = 2
    tracker._tick_entry(entry)
    assert _events_of(collected, "delegation.cli.completed")[-1]["status"] == "failed"

    # killed → killed。
    entry, process = _bind_background(tracker, processes, psid="proc_kill")
    process.exited = True
    process.exit_code = -9
    process.completion_reason = "killed"
    tracker._tick_entry(entry)
    assert _events_of(collected, "delegation.cli.completed")[-1]["status"] == "killed"

    # 进程从 registry 消失 → lost。
    entry, process = _bind_background(tracker, processes, psid="proc_gone")
    del processes["proc_gone"]
    tracker._tick_entry(entry)
    assert _events_of(collected, "delegation.cli.completed")[-1]["status"] == "lost"

    # 全部终态后无残留。
    assert tracker._entries == {}
    assert tracker._by_process == {}


def test_dead_session_is_swept_silently(rig):
    tracker, processes, collected = rig
    entry, _process = _bind_background(tracker, processes, psid="proc_dead", sid="dead-sid")
    before = len(_events_of(collected, "delegation.cli.completed"))
    tracker._sweep()
    after = len(_events_of(collected, "delegation.cli.completed"))
    assert before == after  # 静默清理，不发终态
    assert entry.delegation_id not in tracker._entries
    assert tracker._by_process == {}


def test_expired_entry_emits_lost(rig):
    tracker, processes, collected = rig
    entry, _process = _bind_background(tracker, processes, psid="proc_old")
    entry.created_at -= cli_delegation.MAX_AGE_S + 10
    tracker._sweep()
    completed = _events_of(collected, "delegation.cli.completed")
    assert completed[-1]["status"] == "lost"
    assert tracker._entries == {}

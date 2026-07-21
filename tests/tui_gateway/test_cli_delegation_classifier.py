"""P-047: CLI 委派分类器与输出归一化的表驱动单测。

FIXTURES 与桌面端前端分类器共用同一份字面用例
（Hermes-CN-Desktop: web/src/lib/cli-delegation.test.ts），
改动任意一侧必须同步另一侧——两边的分类语义必须一致，
否则旧内核回退模式下桌面端会与新内核事件判定不一致。
"""

from __future__ import annotations

from tui_gateway.cli_delegation import (
    classify_cli_delegation,
    extract_claude_result,
    extract_codex_result,
    parse_claude_stream_json_line,
    parse_codex_jsonl_line,
)

# ── 共享 fixture 表（与 Desktop cli-delegation.test.ts 保持字面一致） ──────

FIXTURES = [
    {
        "name": "claude-print-basic",
        "command": "claude -p 'Add error handling to all API calls in src/' --allowedTools 'Read,Edit' --max-turns 10",
        "args": {"workdir": "/proj"},
        "expect": {
            "agent": "claude-code",
            "mode": "print",
            "prompt": "Add error handling to all API calls in src/",
            "workdir": "/proj",
            "flags": {"print": True, "max_turns": 10},
        },
    },
    {
        "name": "claude-output-json",
        "command": "claude -p 'Analyze auth.py for security issues' --output-format json --max-turns 5",
        "args": {},
        "expect": {
            "agent": "claude-code",
            "mode": "print",
            "prompt": "Analyze auth.py for security issues",
            "flags": {"output_format": "json", "max_turns": 5},
        },
    },
    {
        "name": "claude-stream-json",
        "command": "claude -p 'Write a summary' --output-format stream-json --verbose --include-partial-messages",
        "args": {"background": True},
        "expect": {
            "agent": "claude-code",
            "mode": "print",
            "prompt": "Write a summary",
            "flags": {
                "output_format": "stream-json",
                "verbose": True,
                "include_partial_messages": True,
                "background": True,
            },
        },
    },
    {
        "name": "claude-pipe-tail",
        "command": 'cat notes.md | claude -p "Summarize this document"',
        "args": {},
        "expect": {
            "agent": "claude-code",
            "mode": "print",
            "prompt": "Summarize this document",
        },
    },
    {
        "name": "claude-pipe-head",
        "command": "claude -p 'Explain X' --output-format stream-json --verbose | jq -rj '.text'",
        "args": {},
        "expect": {
            "agent": "claude-code",
            "mode": "print",
            "prompt": "Explain X",
            "flags": {"output_format": "stream-json"},
        },
    },
    {
        "name": "claude-cd-workdir",
        "command": "cd /repo && claude -p 'fix tests'",
        "args": {},
        "expect": {
            "agent": "claude-code",
            "mode": "print",
            "prompt": "fix tests",
            "workdir": "/repo",
        },
    },
    {
        "name": "claude-env-prefix",
        "command": "ANTHROPIC_MODEL=opus claude -p hi",
        "args": {},
        "expect": {"agent": "claude-code", "mode": "print", "prompt": "hi"},
    },
    {
        "name": "claude-timeout-wrapper",
        "command": "timeout 300 claude -p 'long task'",
        "args": {},
        "expect": {"agent": "claude-code", "mode": "print", "prompt": "long task"},
    },
    {
        "name": "claude-bash-lc",
        "command": "bash -lc \"claude -p 'quoted task' --output-format json\"",
        "args": {},
        "expect": {
            "agent": "claude-code",
            "mode": "print",
            "prompt": "quoted task",
            "flags": {"output_format": "json"},
        },
    },
    {
        "name": "claude-resume",
        "command": "claude -p 'Continue the refactor' --resume abc-123 --max-turns 5",
        "args": {},
        "expect": {
            "agent": "claude-code",
            "mode": "resume",
            "prompt": "Continue the refactor",
            "flags": {"resume_session": "abc-123"},
        },
    },
    {
        "name": "claude-continue",
        "command": "claude --continue -p 'keep going'",
        "args": {},
        "expect": {
            "agent": "claude-code",
            "mode": "resume",
            "prompt": "keep going",
            "flags": {"continue": True},
        },
    },
    {
        "name": "claude-interactive-bare",
        "command": "claude",
        "args": {"pty": True},
        "expect": {
            "agent": "claude-code",
            "mode": "interactive",
            "prompt": "",
            "flags": {"pty": True},
        },
    },
    {
        "name": "claude-redirect",
        "command": "claude -p 'Start refactor' --output-format json > /tmp/session.json",
        "args": {},
        "expect": {
            "agent": "claude-code",
            "mode": "print",
            "prompt": "Start refactor",
            "flags": {"output_format": "json"},
        },
    },
    {
        "name": "chained-after-build",
        "command": "echo done && claude -p 'after build'",
        "args": {},
        "expect": {"agent": "claude-code", "mode": "print", "prompt": "after build"},
    },
    {
        "name": "codex-exec",
        "command": "codex exec 'Add dark mode toggle to settings'",
        "args": {"pty": True},
        "expect": {
            "agent": "codex",
            "mode": "exec",
            "prompt": "Add dark mode toggle to settings",
            "flags": {"pty": True},
        },
    },
    {
        "name": "codex-exec-background",
        "command": "codex exec --full-auto 'Refactor the auth module'",
        "args": {"background": True, "pty": True},
        "expect": {
            "agent": "codex",
            "mode": "exec",
            "prompt": "Refactor the auth module",
            "flags": {"full_auto": True, "background": True, "pty": True},
        },
    },
    {
        "name": "codex-exec-json",
        "command": "codex exec --json --full-auto 'task'",
        "args": {},
        "expect": {
            "agent": "codex",
            "mode": "exec",
            "prompt": "task",
            "flags": {"json": True, "full_auto": True},
        },
    },
    {
        "name": "codex-review",
        "command": "codex review --base origin/main",
        "args": {},
        "expect": {"agent": "codex", "mode": "review", "prompt": ""},
    },
    {
        "name": "codex-cd-flag-before-subcommand",
        "command": "codex -C /work exec 'task'",
        "args": {},
        "expect": {
            "agent": "codex",
            "mode": "exec",
            "prompt": "task",
            "workdir": "/work",
        },
    },
    {
        "name": "codex-resume",
        "command": "codex resume 019a-xyz 'continue the task'",
        "args": {},
        "expect": {
            "agent": "codex",
            "mode": "resume",
            "prompt": "continue the task",
            "flags": {"resume_session": "019a-xyz"},
        },
    },
    {
        "name": "codex-login-utility",
        "command": "codex login",
        "args": {},
        "expect": None,
    },
    {
        "name": "claude-version-utility",
        "command": "claude --version",
        "args": {},
        "expect": None,
    },
    {
        "name": "claude-mcp-utility",
        "command": "claude mcp list",
        "args": {},
        "expect": None,
    },
    {
        "name": "tmux-wrapped-excluded",
        "command": "tmux send-keys -t claude-work 'cd /p && claude' Enter",
        "args": {},
        "expect": None,
    },
    {
        "name": "ssh-remote-excluded",
        "command": "ssh host claude -p x",
        "args": {},
        "expect": None,
    },
    {
        "name": "which-claude-not-delegation",
        "command": "which claude",
        "args": {},
        "expect": None,
    },
    {
        "name": "npm-install-not-delegation",
        "command": "npm install -g @anthropic-ai/claude-code",
        "args": {},
        "expect": None,
    },
    {
        "name": "unrelated-command",
        "command": "ls -la /tmp",
        "args": {},
        "expect": None,
    },
]


def test_classifier_fixtures():
    for case in FIXTURES:
        spec = classify_cli_delegation(case["command"], case["args"])
        expect = case["expect"]
        if expect is None:
            assert spec is None, f"{case['name']}: 应判定为非委派，实际 {spec}"
            continue
        assert spec is not None, f"{case['name']}: 应判定为委派，实际 None"
        assert spec.agent == expect["agent"], f"{case['name']}: agent"
        assert spec.mode == expect["mode"], f"{case['name']}: mode"
        assert spec.prompt_excerpt == expect["prompt"], f"{case['name']}: prompt"
        if "workdir" in expect:
            assert spec.workdir == expect["workdir"], f"{case['name']}: workdir"
        for key, value in expect.get("flags", {}).items():
            assert spec.flags.get(key) == value, (
                f"{case['name']}: flags[{key}] 期望 {value!r} 实际 {spec.flags.get(key)!r}"
            )


def test_prompt_excerpt_is_single_line_and_capped():
    prompt = "line one\nline two   with   spaces" + "x" * 400
    spec = classify_cli_delegation(f"claude -p '{prompt}'", {})
    assert spec is not None
    assert "\n" not in spec.prompt_excerpt
    assert len(spec.prompt_excerpt) <= 200


def test_unbalanced_quotes_fall_back_to_whitespace_split():
    spec = classify_cli_delegation("claude -p 'unterminated task", {})
    assert spec is not None
    assert spec.agent == "claude-code"
    assert spec.mode == "print"


# ── 归一化解析 ────────────────────────────────────────────────────────────


def test_parse_claude_stream_json_lines():
    init = parse_claude_stream_json_line(
        '{"type":"system","subtype":"init","session_id":"s-1","model":"opus"}'
    )
    assert init == [{"kind": "init", "session_id": "s-1", "model": "opus"}]

    text = parse_claude_stream_json_line(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}'
    )
    assert text == [{"kind": "text", "text": "hello"}]

    tool = parse_claude_stream_json_line(
        '{"type":"assistant","message":{"content":['
        '{"type":"tool_use","name":"Bash","input":{}},'
        '{"type":"text","text":"running"}]}}'
    )
    assert {"kind": "tool_use", "tool_name": "Bash"} in tool
    assert {"kind": "text", "text": "running"} in tool

    result = parse_claude_stream_json_line(
        '{"type":"result","subtype":"success","session_id":"s-1",'
        '"num_turns":3,"total_cost_usd":0.01,"is_error":false}'
    )
    assert result[0]["kind"] == "result"
    assert result[0]["session_id"] == "s-1"
    assert result[0]["num_turns"] == 3

    # stream_event 增量与 user 回执要静音（完整 assistant 消息会重复内容）。
    assert parse_claude_stream_json_line(
        '{"type":"stream_event","event":{"delta":{"type":"text_delta","text":"h"}}}'
    ) == []
    assert parse_claude_stream_json_line('{"type":"user","message":{}}') == []
    assert parse_claude_stream_json_line("plain text, not json") == []


def test_parse_codex_jsonl_both_generations():
    # 旧 protocol 形态 {"id","msg":{...}}
    assert parse_codex_jsonl_line(
        '{"id":"1","msg":{"type":"agent_message","message":"done"}}'
    ) == [{"kind": "text", "text": "done"}]
    begin = parse_codex_jsonl_line(
        '{"id":"2","msg":{"type":"exec_command_begin","command":["git","status"]}}'
    )
    assert begin[0]["kind"] == "tool_use"
    assert begin[0]["text"] == "git status"
    done = parse_codex_jsonl_line(
        '{"id":"3","msg":{"type":"task_complete","last_agent_message":"all set"}}'
    )
    assert done[0]["kind"] == "result"

    # 新 experimental JSON 形态 {"type":"item.completed",...}
    assert parse_codex_jsonl_line('{"type":"thread.started","thread_id":"t-1"}') == [
        {"kind": "init", "session_id": "t-1"}
    ]
    item = parse_codex_jsonl_line(
        '{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}'
    )
    assert item == [{"kind": "text", "text": "hi"}]
    turn = parse_codex_jsonl_line(
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}'
    )
    assert turn[0]["kind"] == "result"
    assert parse_codex_jsonl_line('{"type":"turn.failed"}')[0]["is_error"] is True
    assert parse_codex_jsonl_line("garbage {not json") == []


def test_extract_claude_result_whole_json_and_stream_tail():
    whole = extract_claude_result(
        '{"type":"result","subtype":"success","session_id":"s-9",'
        '"num_turns":2,"total_cost_usd":0.02,"is_error":false}'
    )
    assert whole is not None and whole["session_id"] == "s-9"

    stream = "\n".join([
        '{"type":"system","subtype":"init","session_id":"s-9","model":"opus"}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}',
        '{"type":"result","subtype":"success","session_id":"s-9","num_turns":1,'
        '"total_cost_usd":0.005,"is_error":false}',
    ])
    tail = extract_claude_result(stream)
    assert tail is not None and tail["num_turns"] == 1

    assert extract_claude_result("plain text output") is None
    assert extract_claude_result("") is None


def test_extract_codex_result_from_jsonl_tail():
    output = "\n".join([
        '{"type":"thread.started","thread_id":"t-2"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
        '{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":4}}',
    ])
    result = extract_codex_result(output)
    assert result is not None and result["output_tokens"] == 4
    assert extract_codex_result("no json here") is None

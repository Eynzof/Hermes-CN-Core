"""P-047: CLI 委派检测与实时输出流（Claude Code / Codex）。

hermes 通过 ``claude-code`` / ``codex`` 技能把编码任务委派给外部 CLI —— 本质
是模型经 ``terminal`` 工具执行 ``claude -p …`` / ``codex exec …``。工具调用
本身没有任何"这是一次委派"的结构化标记，桌面端只能看到普通 tool.start /
tool.complete。本模块在 gateway 侧补齐这一语义：

- ``classify_cli_delegation``：纯函数命令分类器，识别 terminal 命令是否为一次
  CLI 委派（含 ``cd X &&`` / env 前缀 / ``bash -lc`` / 管道 等包装形态）。
- 归一化解析：把 Claude Code 的 ``--output-format stream-json`` 行与 Codex 的
  ``--json`` JSONL 行解析为统一的时间线子事件（init/text/tool_use/result）。
- ``DelegationTracker``：跟踪委派生命周期并发出三个新事件——

    delegation.cli.started    tool.start 分类命中即发（前后台皆有）
    delegation.cli.output     仅后台委派；watcher 线程 ≤2Hz 合并冲刷
    delegation.cli.completed  终态（completed/failed/killed/lost），前台在
                              tool.complete 时发，后台在进程退出时发

关联键 ``delegation_id == tool_call_id``（即 tool.start 的 ``tool_id``），
桌面端据此把同一张工具卡"升级"为品牌化委派卡而不是双渲染。

后台输出不新建轮询：搭 ``process_registry.on_output`` 既有推流钩子的车
（server._wire_agent_terminal_output 已把它接到 agent.terminal.output），
同一 sink 追加喂给本 tracker，由单例 watcher 线程做合并冲刷与终态检测。

显式非目标：tmux 交互式委派（无稳定进程可绑）不产生 delegation 事件；
``process(action="submit")`` 的输入不回显进事件流。
"""

from __future__ import annotations

import json
import re
import shlex
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

CLAUDE_AGENT = "claude-code"
CODEX_AGENT = "codex"

# ── 分类器 ────────────────────────────────────────────────────────────────

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# 这些 basename 开头的段落不视为委派（远端/多路复用器里跑的 claude 无法跟踪）。
_EXCLUDED_LEADERS = {"tmux", "ssh", "scp", "mosh"}

# 可透明剥掉的包装命令（保守列表；剥不干净就当普通命令处理，宁可漏不误报）。
_TRANSPARENT_WRAPPERS = {"nohup", "time", "caffeinate", "stdbuf", "nice", "timeout", "env"}

_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}

# claude 的运维子命令 / 全局开关：出现即判定"不是委派"。
_CLAUDE_UTILITY_SUBCOMMANDS = {
    "auth", "login", "logout", "doctor", "update", "install", "uninstall",
    "config", "mcp", "migrate-installer", "setup-token", "plugin",
}
# codex 的非委派子命令（exec/review/resume 之外的一律运维）。
_CODEX_DELEGATION_SUBCOMMANDS = {"exec", "e", "review", "resume"}
_CODEX_UTILITY_SUBCOMMANDS = {
    "login", "logout", "auth", "mcp", "proto", "completion", "debug",
    "apply", "sandbox", "cloud", "features", "doctor", "env",
}

_VERSION_HELP_FLAGS = {"--version", "-v", "--help", "-h", "-V"}

# 取值型 flag：解析 prompt 位置参数时要把它们的值跳过去。
_CLAUDE_VALUE_FLAGS = {
    "--output-format", "--input-format", "--max-turns", "--model",
    "--resume", "-r", "--session-id", "--allowedTools", "--allowed-tools",
    "--disallowedTools", "--disallowed-tools", "--append-system-prompt",
    "--system-prompt", "--json-schema", "--add-dir", "--mcp-config",
    "--permission-mode", "--permission-prompt-tool", "--settings",
    "--agents", "--fallback-model", "--betas",
}
_CODEX_VALUE_FLAGS = {
    "--model", "-m", "--sandbox", "-s", "--cd", "-C",
    "--output-last-message", "-o", "--output-schema", "--profile", "-p",
    "--image", "-i", "--base",
}

PROMPT_EXCERPT_CAP = 200
COMMAND_CAP = 2000

_MAX_SHELL_RECURSION = 3


@dataclass(frozen=True)
class DelegationSpec:
    """一次已识别的 CLI 委派的静态描述（分类结果，纯数据）。"""

    agent: str                     # CLAUDE_AGENT | CODEX_AGENT
    mode: str                      # print | exec | review | resume | interactive
    prompt_excerpt: str            # ≤200 字符，单行化
    workdir: Optional[str]         # args.workdir 优先，其次命令内 `cd X &&`
    flags: dict                    # output_format/json/model/… + background/pty


def _oneline(text: str, cap: int) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()[:cap]


def _basename(token: str) -> str:
    return re.split(r"[\\/]", token.strip())[-1]


def _tokenize(command: str) -> list[str]:
    """POSIX 分词，`|&;()<>` 作为独立 token；引号不平衡时回退空白切分。"""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return command.split()


def _split_segments(tokens: list[str]) -> list[list[str]]:
    """按 `&&` `||` `;` `|` `(` `)` 与重定向边界切成独立命令段。"""
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        # 控制/重定向标点（`&&` `|` `;` `>` `2>` `>&` …）一律作为段边界；
        # 重定向目标会自然落进一个不会命中分类的哑段。
        if tok and all(ch in "|&;()<>" for ch in tok):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(tok)
    if current:
        segments.append(current)
    return segments


def _strip_wrappers(words: list[str]) -> list[str]:
    """剥掉前导 env 赋值与透明包装命令，返回真正的命令词序列。"""
    i = 0
    n = len(words)
    while i < n:
        w = words[i]
        if _ENV_ASSIGN_RE.match(w):
            i += 1
            continue
        base = _basename(w)
        if base == "env":
            i += 1
            while i < n and _ENV_ASSIGN_RE.match(words[i]):
                i += 1
            continue
        if base == "timeout":
            i += 1
            # timeout [-k dur] [--signal SIG] DURATION cmd…
            while i < n and words[i].startswith("-"):
                i += 2 if words[i] in ("-k", "--kill-after", "-s", "--signal") else 1
            if i < n:
                i += 1  # DURATION
            continue
        if base == "nice":
            i += 1
            if i < n and words[i] == "-n":
                i += 2
            continue
        if base == "stdbuf":
            i += 1
            while i < n and words[i].startswith("-"):
                i += 1
            continue
        if base in _TRANSPARENT_WRAPPERS:
            i += 1
            continue
        break
    return words[i:]


def _parse_flag_walk(
    rest: list[str], value_flags: set[str], int_flags: set[str] | None = None
) -> tuple[dict, list[str]]:
    """通用 flag 游走：返回 (采集到的取值 flag, 位置参数列表)。"""
    values: dict[str, str] = {}
    positionals: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in value_flags:
            if i + 1 < len(rest) and not rest[i + 1].startswith("-"):
                values[tok] = rest[i + 1]
                i += 2
            else:
                i += 1
            continue
        if tok.startswith("--") and "=" in tok:
            name, _, val = tok.partition("=")
            if name in value_flags:
                values[name] = val
            i += 1
            continue
        if tok.startswith("-") and tok != "-":
            i += 1
            continue
        positionals.append(tok)
        i += 1
    return values, positionals


def _classify_claude(words: list[str], args: dict) -> Optional[DelegationSpec]:
    rest = words[1:]
    tokens = set(rest)
    if tokens & _VERSION_HELP_FLAGS:
        return None
    # 首个位置参数若是运维子命令（claude mcp list / claude update …）→ 非委派。
    # print 模式下位置参数是 prompt（`claude -p mcp` 是提示词），不做此判定。
    if not ({"-p", "--print"} & tokens):
        idx = 0
        while idx < len(rest):
            tok = rest[idx]
            if tok.startswith("-"):
                idx += 2 if tok in _CLAUDE_VALUE_FLAGS and idx + 1 < len(rest) else 1
                continue
            if tok in _CLAUDE_UTILITY_SUBCOMMANDS:
                return None
            break

    values, positionals = _parse_flag_walk(rest, _CLAUDE_VALUE_FLAGS)

    print_mode = bool({"-p", "--print"} & tokens)
    continue_mode = bool({"-c", "--continue"} & tokens)
    resume_id = values.get("--resume") or values.get("-r")
    resume_mode = continue_mode or resume_id is not None or "--resume" in tokens or "-r" in tokens

    flags: dict[str, Any] = {
        "background": bool(args.get("background")),
        "pty": bool(args.get("pty")),
    }
    if print_mode:
        flags["print"] = True
    output_format = values.get("--output-format")
    if output_format:
        flags["output_format"] = output_format
    if values.get("--model"):
        flags["model"] = values["--model"]
    if values.get("--max-turns"):
        try:
            flags["max_turns"] = int(values["--max-turns"])
        except ValueError:
            pass
    if resume_id:
        flags["resume_session"] = resume_id
    if continue_mode:
        flags["continue"] = True
    if "--include-partial-messages" in tokens:
        flags["include_partial_messages"] = True
    if "--verbose" in tokens:
        flags["verbose"] = True
    if "--dangerously-skip-permissions" in tokens:
        flags["dangerously_skip_permissions"] = True

    prompt = positionals[0] if positionals else ""
    mode = "resume" if resume_mode else ("print" if print_mode else "interactive")
    return DelegationSpec(
        agent=CLAUDE_AGENT,
        mode=mode,
        prompt_excerpt=_oneline(prompt, PROMPT_EXCERPT_CAP),
        workdir=None,
        flags=flags,
    )


def _classify_codex(words: list[str], args: dict) -> Optional[DelegationSpec]:
    rest = words[1:]
    tokens = set(rest)
    if tokens & _VERSION_HELP_FLAGS:
        return None

    subcommand = ""
    sub_index = -1
    idx = 0
    while idx < len(rest):
        tok = rest[idx]
        if tok.startswith("-"):
            # 跳过全局 flag；取值型 flag 连它的值一起跳过（`-C /work exec …`）。
            idx += 2 if tok in _CODEX_VALUE_FLAGS and idx + 1 < len(rest) else 1
            continue
        if tok in _CODEX_UTILITY_SUBCOMMANDS:
            return None
        if tok in _CODEX_DELEGATION_SUBCOMMANDS:
            subcommand = "exec" if tok == "e" else tok
            sub_index = idx
        break

    scan = rest[:sub_index] + rest[sub_index + 1:] if sub_index >= 0 else rest
    values, positionals = _parse_flag_walk(scan, _CODEX_VALUE_FLAGS)

    flags: dict[str, Any] = {
        "background": bool(args.get("background")),
        "pty": bool(args.get("pty")),
    }
    if "--json" in tokens:
        flags["json"] = True
    if "--full-auto" in tokens:
        flags["full_auto"] = True
    if {"--yolo", "--dangerously-bypass-approvals-and-sandbox"} & tokens:
        flags["yolo"] = True
    if values.get("--sandbox") or values.get("-s"):
        flags["sandbox"] = values.get("--sandbox") or values.get("-s")
    if values.get("--model") or values.get("-m"):
        flags["model"] = values.get("--model") or values.get("-m")

    workdir = values.get("--cd") or values.get("-C")
    prompt = positionals[0] if positionals else ""
    mode = subcommand or "interactive"
    if mode == "resume":
        # `codex resume <session-id> [prompt]`：首位置参数是会话 id。
        if positionals:
            flags["resume_session"] = positionals[0]
            prompt = positionals[1] if len(positionals) > 1 else ""
    return DelegationSpec(
        agent=CODEX_AGENT,
        mode=mode,
        prompt_excerpt=_oneline(prompt, PROMPT_EXCERPT_CAP),
        workdir=workdir,
        flags=flags,
    )


def _classify_segment(
    words: list[str], args: dict, depth: int
) -> Optional[DelegationSpec]:
    words = _strip_wrappers(words)
    if not words:
        return None
    base = _basename(words[0])
    if base in _EXCLUDED_LEADERS:
        return None
    if base in _SHELLS:
        # bash|sh|zsh -c / -lc '<payload>' → 递归分类 payload 字符串。
        if depth >= _MAX_SHELL_RECURSION:
            return None
        for idx in range(1, len(words)):
            tok = words[idx]
            if re.match(r"^-[A-Za-z]*c[A-Za-z]*$", tok) and idx + 1 < len(words):
                return _classify_command(words[idx + 1], args, depth + 1)
            if not tok.startswith("-"):
                break
        return None
    if base == "claude":
        return _classify_claude(words, args)
    if base == "codex":
        return _classify_codex(words, args)
    return None


def _classify_command(
    command: str, args: dict, depth: int = 0
) -> Optional[DelegationSpec]:
    tokens = _tokenize(command)
    segments = _split_segments(tokens)
    pending_workdir: Optional[str] = None
    for seg in segments:
        stripped = _strip_wrappers(seg)
        if len(stripped) >= 2 and _basename(stripped[0]) == "cd":
            pending_workdir = stripped[1]
            continue
        spec = _classify_segment(seg, args, depth)
        if spec is not None:
            workdir = args.get("workdir") or spec.workdir or pending_workdir
            if workdir != spec.workdir:
                spec = DelegationSpec(
                    agent=spec.agent,
                    mode=spec.mode,
                    prompt_excerpt=spec.prompt_excerpt,
                    workdir=workdir,
                    flags=spec.flags,
                )
            return spec
    return None


def classify_cli_delegation(command: str, args: dict | None = None) -> Optional[DelegationSpec]:
    """判定 terminal 命令是否为一次 Claude Code / Codex 委派。

    识别不到（或属于运维/排除形态）返回 None。分类是尽力而为的启发式：
    宁可漏报（普通 ToolCard 照常展示），不做激进猜测。
    """
    if not command:
        return None
    if "claude" not in command and "codex" not in command:
        return None
    return _classify_command(str(command), dict(args or {}))


# ── 输出归一化 ────────────────────────────────────────────────────────────

_EVENT_TEXT_CAP = 300


def _clip(text: Any, cap: int = _EVENT_TEXT_CAP) -> str:
    return _oneline(str(text or ""), cap)


def _load_json_line(line: str) -> Optional[dict]:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def parse_claude_stream_json_line(line: str) -> list[dict]:
    """把 Claude Code ``--output-format stream-json`` 的一行解析为 0..n 个子事件。

    形态: {"kind": init|text|tool_use|result, ...}；stream_event 增量与 user
    (tool_result 回执) 一律跳过——完整 assistant 消息对象会重复覆盖同样内容。
    """
    obj = _load_json_line(line)
    if obj is None:
        return []
    kind = obj.get("type")
    if kind == "system" and obj.get("subtype") == "init":
        return [{
            "kind": "init",
            "session_id": obj.get("session_id"),
            "model": obj.get("model"),
        }]
    if kind == "assistant":
        message = obj.get("message") or {}
        content = message.get("content") or []
        events: list[dict] = []
        texts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                events.append({
                    "kind": "tool_use",
                    "tool_name": _clip(part.get("name"), 80),
                })
            elif part.get("type") == "text" and part.get("text"):
                texts.append(str(part["text"]))
        if texts:
            events.append({"kind": "text", "text": _clip("".join(texts))})
        return events
    if kind == "result":
        return [{
            "kind": "result",
            "session_id": obj.get("session_id"),
            "num_turns": obj.get("num_turns"),
            "total_cost_usd": obj.get("total_cost_usd"),
            "subtype": obj.get("subtype"),
            "is_error": obj.get("is_error"),
        }]
    return []


def parse_codex_jsonl_line(line: str) -> list[dict]:
    """把 Codex ``--json`` JSONL 行解析为 0..n 个子事件。

    Codex 的 JSON 事件形态随版本演进（旧 protocol 是 {"id","msg":{...}}，
    新 experimental JSON 是 {"type":"item.completed","item":{...}}），这里对
    两代形态都做防御式解析，字段全部可选，解析不出就丢弃。
    """
    obj = _load_json_line(line)
    if obj is None:
        return []

    msg = obj.get("msg")
    if isinstance(msg, dict):
        mtype = msg.get("type")
        if mtype == "session_configured":
            return [{"kind": "init", "session_id": msg.get("session_id")}]
        if mtype == "agent_message" and msg.get("message"):
            return [{"kind": "text", "text": _clip(msg.get("message"))}]
        if mtype == "exec_command_begin":
            cmd = msg.get("command")
            if isinstance(cmd, list):
                cmd = " ".join(str(part) for part in cmd)
            return [{"kind": "tool_use", "tool_name": "shell", "text": _clip(cmd)}]
        if mtype == "task_complete":
            return [{
                "kind": "result",
                "text": _clip(msg.get("last_agent_message")),
            }]
        return []

    otype = obj.get("type")
    if otype == "thread.started":
        return [{"kind": "init", "session_id": obj.get("thread_id")}]
    if otype == "item.completed":
        item = obj.get("item") or {}
        if not isinstance(item, dict):
            return []
        itype = item.get("type") or item.get("item_type")
        if itype == "agent_message" and item.get("text"):
            return [{"kind": "text", "text": _clip(item.get("text"))}]
        if itype == "command_execution":
            return [{
                "kind": "tool_use",
                "tool_name": "shell",
                "text": _clip(item.get("command")),
            }]
        return []
    if otype == "turn.completed":
        usage = obj.get("usage") or {}
        return [{
            "kind": "result",
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        }]
    if otype == "turn.failed":
        return [{"kind": "result", "is_error": True}]
    if otype == "error" and obj.get("message"):
        return [{"kind": "text", "text": _clip(obj.get("message")), "is_error": True}]
    return []


def normalize_output_line(agent: str, line: str) -> list[dict]:
    if agent == CLAUDE_AGENT:
        return parse_claude_stream_json_line(line)
    if agent == CODEX_AGENT:
        return parse_codex_jsonl_line(line)
    return []


def extract_claude_result(output: str) -> Optional[dict]:
    """从前台/累计输出中提取 Claude 的结果对象。

    兼容 ``--output-format json``（整段单对象）与 ``stream-json``（末尾
    ``{"type":"result"}`` 行）；纯文本输出返回 None。
    """
    if not output:
        return None
    text = output.strip()
    whole = _load_json_line(text) if text.startswith("{") and "\n" not in text else None
    if whole is None and text.startswith("{"):
        try:
            candidate = json.loads(text)
            whole = candidate if isinstance(candidate, dict) else None
        except Exception:
            whole = None
    if whole is not None and (whole.get("type") == "result" or "session_id" in whole):
        return {
            "session_id": whole.get("session_id"),
            "num_turns": whole.get("num_turns"),
            "total_cost_usd": whole.get("total_cost_usd"),
            "subtype": whole.get("subtype"),
            "is_error": whole.get("is_error"),
        }
    for line in reversed(output.splitlines()[-200:]):
        for event in parse_claude_stream_json_line(line):
            if event.get("kind") == "result":
                event.pop("kind", None)
                return event
    return None


def extract_codex_result(output: str) -> Optional[dict]:
    if not output:
        return None
    for line in reversed(output.splitlines()[-200:]):
        for event in parse_codex_jsonl_line(line):
            if event.get("kind") == "result":
                event.pop("kind", None)
                return event
    return None


def _extract_result(agent: str, output: str) -> Optional[dict]:
    if agent == CLAUDE_AGENT:
        return extract_claude_result(output)
    return extract_codex_result(output)


# ── 生命周期跟踪 ──────────────────────────────────────────────────────────

FLUSH_INTERVAL_S = 0.5
CHUNK_CAP = 4096
STREAM_TOTAL_CAP = 256 * 1024
EVENTS_PER_FLUSH_CAP = 20
OUTPUT_TAIL_CAP = 4000
PENDING_BUFFER_CAP = 64 * 1024
MAX_AGE_S = 6 * 3600

_TERMINAL_FAILURE_STATUSES = {"error", "blocked", "disabled", "pending_approval"}


@dataclass
class _Entry:
    delegation_id: str
    sid: str
    spec: DelegationSpec
    command: str
    created_at: float = field(default_factory=time.time)
    process_session_id: Optional[str] = None
    pending: str = ""
    remainder: str = ""
    streamed: int = 0
    stream_capped: bool = False


def _redact(text: str) -> str:
    from agent.redact import redact_sensitive_text

    return redact_sensitive_text(text, force=True)


def _strip_ansi(text: str) -> str:
    from tools.ansi_strip import strip_ansi

    return strip_ansi(text)


class DelegationTracker:
    """CLI 委派生命周期跟踪器（gateway 进程内单例）。

    线程模型：``handle_tool_start``/``handle_tool_complete`` 来自 agent 线程；
    ``on_chunk`` 来自 process_registry 的 reader 线程；冲刷与终态检测在自有
    watcher daemon 线程。所有共享状态由 ``_lock`` 保护，事件发送不持锁。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._emit: Optional[Callable[[str, str, dict], None]] = None
        self._is_alive: Optional[Callable[[str], bool]] = None
        self._entries: dict[str, _Entry] = {}
        self._by_process: dict[str, str] = {}
        self._watcher: Optional[threading.Thread] = None

    # -- 装配 --------------------------------------------------------------

    def configure(
        self,
        emit: Callable[[str, str, dict], None],
        is_alive: Callable[[str], bool] | None = None,
    ) -> None:
        self._emit = emit
        self._is_alive = is_alive

    def reset(self) -> None:
        """仅测试用：清空全部状态。"""
        with self._lock:
            self._entries.clear()
            self._by_process.clear()

    def _send(self, event: str, sid: str, payload: dict) -> None:
        emit = self._emit
        if emit is None:
            return
        try:
            emit(event, sid, payload)
        except Exception:
            pass

    # -- 工具调用钩子 --------------------------------------------------------

    def handle_tool_start(self, sid: str, tool_call_id: str, name: str, args: dict) -> None:
        if name != "terminal" or not tool_call_id:
            return
        command = str((args or {}).get("command") or "")
        spec = classify_cli_delegation(command, args)
        if spec is None:
            return
        entry = _Entry(
            delegation_id=tool_call_id,
            sid=sid,
            spec=spec,
            command=command,
        )
        with self._lock:
            self._entries[tool_call_id] = entry
        self._send("delegation.cli.started", sid, self._started_payload(entry))

    def handle_tool_complete(
        self, sid: str, tool_call_id: str, name: str, result: Any
    ) -> None:
        if name != "terminal":
            return
        with self._lock:
            entry = self._entries.get(tool_call_id)
        if entry is None:
            return

        parsed = result if isinstance(result, dict) else None
        if parsed is None and isinstance(result, str):
            try:
                candidate = json.loads(result)
                parsed = candidate if isinstance(candidate, dict) else None
            except Exception:
                parsed = None

        status = str((parsed or {}).get("status") or "")
        error = (parsed or {}).get("error")
        exit_code = (parsed or {}).get("exit_code")
        failed = (
            status in _TERMINAL_FAILURE_STATUSES
            or bool(error)
            or (isinstance(exit_code, int) and exit_code != 0)
        )

        if entry.spec.flags.get("background"):
            process_session_id = str((parsed or {}).get("session_id") or "")
            if process_session_id and not failed:
                with self._lock:
                    entry.process_session_id = process_session_id
                    self._by_process[process_session_id] = tool_call_id
                self._ensure_watcher()
                return
            # 后台启动失败：直接终态。
            self._complete(
                entry,
                status="failed",
                exit_code=exit_code if isinstance(exit_code, int) else None,
                output=str((parsed or {}).get("output") or ""),
            )
            return

        output = str((parsed or {}).get("output") or ("" if parsed is not None else result or ""))
        self._complete(
            entry,
            status="failed" if failed else "completed",
            exit_code=exit_code if isinstance(exit_code, int) else None,
            output=output,
        )

    # -- 后台输出流 ----------------------------------------------------------

    def on_chunk(self, process_session: Any, chunk: str) -> None:
        if not chunk:
            return
        psid = str(getattr(process_session, "id", "") or "")
        if not psid:
            return
        with self._lock:
            delegation_id = self._by_process.get(psid)
            if delegation_id is None:
                return
            entry = self._entries.get(delegation_id)
            if entry is None:
                return
            entry.pending += _strip_ansi(str(chunk))
            if len(entry.pending) > PENDING_BUFFER_CAP:
                entry.pending = entry.pending[-PENDING_BUFFER_CAP:]

    def _ensure_watcher(self) -> None:
        with self._lock:
            if self._watcher is not None and self._watcher.is_alive():
                return
            thread = threading.Thread(
                target=self._watch_loop, name="cli-delegation-watch", daemon=True
            )
            self._watcher = thread
        thread.start()

    def _watch_loop(self) -> None:
        while True:
            time.sleep(FLUSH_INTERVAL_S)
            with self._lock:
                bound = [
                    entry for entry in self._entries.values()
                    if entry.process_session_id is not None
                ]
                if not bound:
                    self._watcher = None
                    return
            for entry in bound:
                try:
                    self._tick_entry(entry)
                except Exception:
                    pass
            self._sweep()

    def _tick_entry(self, entry: _Entry) -> None:
        chunk_out, events = self._drain(entry, final=False)
        if chunk_out or events:
            self._send("delegation.cli.output", entry.sid, {
                "delegation_id": entry.delegation_id,
                "process_session_id": entry.process_session_id,
                "chunk": chunk_out,
                "truncated": entry.stream_capped,
                "events": events,
            })

        process_session = self._lookup_process(entry.process_session_id)
        if process_session is None:
            self._complete(entry, status="lost", exit_code=None, output="")
            return
        if getattr(process_session, "exited", False):
            reason = str(getattr(process_session, "completion_reason", "") or "")
            exit_code = getattr(process_session, "exit_code", None)
            if exit_code == 0:
                status = "completed"
            elif reason == "killed":
                status = "killed"
            elif reason in ("lost", "failed_start"):
                status = "lost"
            else:
                status = "failed"
            buffer = str(getattr(process_session, "output_buffer", "") or "")
            self._complete(
                entry,
                status=status,
                exit_code=exit_code if isinstance(exit_code, int) else None,
                output=buffer,
            )

    @staticmethod
    def _lookup_process(process_session_id: Optional[str]) -> Any:
        if not process_session_id:
            return None
        try:
            from tools.process_registry import process_registry

            return process_registry.get(process_session_id)
        except Exception:
            return None

    def _drain(self, entry: _Entry, final: bool) -> tuple[str, list[dict]]:
        with self._lock:
            pending = entry.pending
            entry.pending = ""
            data = entry.remainder + pending
            if final:
                lines = data.splitlines()
                entry.remainder = ""
            else:
                parts = data.split("\n")
                entry.remainder = parts.pop()
                lines = parts
            capped = entry.stream_capped
            entry.streamed += len(pending)
            if entry.streamed > STREAM_TOTAL_CAP:
                entry.stream_capped = True

        events: list[dict] = []
        for line in lines:
            if len(events) >= EVENTS_PER_FLUSH_CAP:
                break
            events.extend(normalize_output_line(entry.spec.agent, line))
        events = events[:EVENTS_PER_FLUSH_CAP]

        if capped or not pending:
            chunk_out = ""
        else:
            chunk_out = _redact(pending[-CHUNK_CAP:])
        return chunk_out, events

    def _sweep(self) -> None:
        now = time.time()
        is_alive = self._is_alive
        with self._lock:
            stale = [
                entry for entry in self._entries.values()
                if (now - entry.created_at) > MAX_AGE_S
                or (is_alive is not None and not _safe_alive(is_alive, entry.sid))
            ]
        for entry in stale:
            if entry.process_session_id is not None and (now - entry.created_at) > MAX_AGE_S:
                self._complete(entry, status="lost", exit_code=None, output="")
            else:
                self._forget(entry)

    # -- 终态 ---------------------------------------------------------------

    def _complete(
        self, entry: _Entry, status: str, exit_code: Optional[int], output: str
    ) -> None:
        with self._lock:
            if entry.delegation_id not in self._entries:
                return
        chunk_out, events = self._drain(entry, final=True)
        if chunk_out or events:
            self._send("delegation.cli.output", entry.sid, {
                "delegation_id": entry.delegation_id,
                "process_session_id": entry.process_session_id,
                "chunk": chunk_out,
                "truncated": entry.stream_capped,
                "events": events,
            })
        tail = _strip_ansi(str(output or ""))[-OUTPUT_TAIL_CAP:]
        payload = {
            "delegation_id": entry.delegation_id,
            "agent": entry.spec.agent,
            "execution": "background" if entry.spec.flags.get("background") else "foreground",
            "status": status,
            "exit_code": exit_code,
            "duration_s": round(time.time() - entry.created_at, 3),
            "output_tail": _redact(tail),
            "result": _extract_result(entry.spec.agent, output),
        }
        self._forget(entry)
        self._send("delegation.cli.completed", entry.sid, payload)

    def _forget(self, entry: _Entry) -> None:
        with self._lock:
            self._entries.pop(entry.delegation_id, None)
            if entry.process_session_id is not None:
                self._by_process.pop(entry.process_session_id, None)

    def _started_payload(self, entry: _Entry) -> dict:
        spec = entry.spec
        return {
            "delegation_id": entry.delegation_id,
            "tool_id": entry.delegation_id,
            "agent": spec.agent,
            "mode": spec.mode,
            "execution": "background" if spec.flags.get("background") else "foreground",
            "command_redacted": _redact(entry.command)[:COMMAND_CAP],
            "prompt_excerpt": spec.prompt_excerpt,
            "workdir": spec.workdir,
            "flags": spec.flags,
        }


def _safe_alive(is_alive: Callable[[str], bool], sid: str) -> bool:
    try:
        return bool(is_alive(sid))
    except Exception:
        return True


tracker = DelegationTracker()

---
title: "Hermes Agent — 使用、配置与扩展 Hermes Agent"
sidebar_label: "Hermes Agent"
description: "Use, configure, theme, extend, and orchestrate Hermes Agent"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Hermes Agent

Use, configure, theme, extend, and orchestrate Hermes Agent.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/autonomous-ai-agents/hermes-agent` |
| Version | `3.2.0` |
| Author | Hermes Agent + Teknium |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `hermes`, `setup`, `configuration`, `multi-agent`, `spawning`, `cli`, `gateway`, `bots`, `bot-mode`, `features`, `themes`, `skins`, `desktop-plugins`, `tui-widgets`, `petdex`, `development` |
| Related skills | [`claude-code`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-claude-code), [`codex`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-codex), [`opencode`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-opencode) |

## 参考：当前 SKILL.md 英文原文

:::info
以下定义由当前 SKILL.md 生成，保留英文原文以与 agent 实际加载的指令一致。旧版译文中的 pip 安装和 Windows 测试绕过方案已移除；开发与测试请按原文链接读取当前 contributor-guide 和 windows-quirks 参考。
:::

# Hermes Agent

Hermes Agent is an open-source AI agent framework by Nous Research that runs in your terminal, a native desktop app, messaging platforms, and IDEs. It's in the same category as Claude Code (Anthropic), Codex (OpenAI), and OpenClaw — autonomous coding and task-execution agents that use tool calling to interact with your system. Hermes works with any LLM provider (OpenRouter, Anthropic, OpenAI, Google, DeepSeek, xAI, local models, and 20+ others) and runs on Linux, macOS, Windows, and WSL.

What makes Hermes different:

- **Self-improving through skills** — Hermes learns from experience by saving reusable procedures as skills that load into future sessions.
- **Persistent memory across sessions** — remembers who you are, your preferences, environment details, and lessons learned. Pluggable memory backends.
- **Multi-platform gateway** — the same agent runs on Telegram, Discord, Slack, WhatsApp, iMessage, Signal, Matrix, Teams, Email, and a dozen more platforms with full tool access, not just chat.
- **Many surfaces** — the same agent core drives the CLI, the Ink TUI, a native Electron desktop app, a web dashboard, and an ACP server for IDEs (VS Code / Zed / JetBrains).
- **Provider-agnostic** — swap models and providers mid-workflow; credential pools rotate across multiple API keys automatically.
- **Profiles** — run multiple independent Hermes instances with isolated configs, sessions, skills, and memory.
- **Extensible & themeable** — plugins, MCP servers, custom tools, webhook triggers, cron scheduling, skins that theme every surface, desktop UI plugins, TUI widgets, and pet mascots.

**This skill is a hub.** The body covers identity, quick start, spawning/orchestration, and hard invariants. Everything else lives in reference files — **load the matching reference (below) before answering**; do not answer detail questions from the body alone.

**Docs:** https://hermes-agent.nousresearch.com/docs/

## Scope & Verification

This skill is a concise operating guide, not the complete source of truth for every Hermes feature. If a Hermes feature, command, or setting is not mentioned here or in a reference, do not treat that absence as evidence that it does not exist. Check the live repository and official docs before giving a negative answer.

Good verification targets, cheapest first:

- **Every shipped feature, one line each: https://hermes-agent.nousresearch.com/docs/llms.txt.** Start here for any "can Hermes do X?" or "how do I do X?" — it indexes the entire documentation set with a link to the page that answers. It is generated from the docs tree on every build, so it is never behind the product. Fetch it with `web_extract`, or `curl -s https://hermes-agent.nousresearch.com/docs/llms.txt` when web tools are off. The whole documentation set in one file is at `/docs/llms-full.txt`.
- CLI commands: `hermes --help`, `hermes <command> --help`, and `hermes_cli/main.py`
- Source tree: https://github.com/NousResearch/hermes-agent

Never answer "Hermes can't do that" from memory. Hermes ships far more than this skill body describes, and the index exists so a negative answer is always checkable.

## Quick Start

```bash
# Install (shell installer — bootstraps PM, Python, dependencies, and the launcher)
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# Interactive chat (default surface; set display.interface: tui to launch the Ink TUI instead)
hermes

# Single query
hermes chat -q "What is the capital of France?"

# Setup wizard  /  pick model+provider  /  health check
hermes setup
hermes model
hermes doctor

# Other surfaces
hermes desktop                 # launch the native desktop app (alias: hermes gui)
hermes dashboard               # web admin panel + embedded chat
hermes proxy                   # OpenAI-compatible local proxy backed by your OAuth provider
```

## Key Paths

```
~/.hermes/config.yaml       Main configuration (settings — never secrets)
~/.hermes/.env              API keys and secrets ONLY (under $HERMES_HOME if set)
$HERMES_HOME/skills/        Installed skills
~/.hermes/skins/            Custom themes (see references/themes.md)
~/.hermes/desktop-plugins/  Desktop app UI plugins (see references/desktop-plugins.md)
~/.hermes/tui-widgets/      TUI widget apps (see references/tui-widgets.md)
~/.hermes/pets/             Installed pet mascots (see references/petdex.md)
~/.hermes/state.db          Canonical session store (SQLite + FTS5)
~/.hermes/sessions/         Gateway routing index, request dumps, *.jsonl transcripts
~/.hermes/logs/             Gateway and error logs
~/.hermes/auth.json         OAuth tokens and credential pools
~/.hermes/hermes-agent/     Source code (if git-installed)
```

Profiles use `~/.hermes/profiles/<name>/` with the same layout. When a profile is active, resolve the real home from `$HERMES_HOME` — never hardcode `~/.hermes`.

## Routing Table — load the reference for the task

| User wants... | Load |
|---|---|
| **Anything not listed below — "can Hermes do X?", "how do I set up X?"** | **https://hermes-agent.nousresearch.com/docs/llms.txt** |
| Bots that chat, run routines, or message each other; the Bots tab | docs: `/user-guide/bot-mode` |
| CLI commands, subcommands, flags, "how do I run X" | `references/cli-reference.md` |
| In-session slash commands | `references/slash-commands.md` |
| Provider setup, API keys, OAuth | `references/providers-and-models.md` |
| config.yaml sections, toolsets, voice/STT/TTS | `references/configuration.md` |
| AGENTS.md / .hermes.md / CLAUDE.md project rules | `references/project-context-files.md` |
| Secret redaction, PII, approval modes, "reset permissions" | `references/security-privacy.md` |
| Delegation, cron, curator, kanban | `references/background-systems.md` |
| MCP servers (add, catalog, `hermes mcp`) | `references/native-mcp.md` |
| Webhook routes and event-driven runs | `references/webhooks.md` |
| A custom theme/skin ("synthwave theme", "change the gold ●") | `references/themes.md` + `templates/skin.yaml` |
| A desktop app UI element (pane, widget, ⌘K command, page) | `references/desktop-plugins.md` + `templates/plugin.js` |
| A live TUI panel or modal widget (ticker, clock, dashboard) | `references/tui-widgets.md` + `templates/clock.mjs` |
| Pet mascots — install, select, scale, diagnose | `references/petdex.md` |
| Windows-specific issues (keybinds, WinError 10106, BOM) | `references/windows-quirks.md` |
| Debugging: voice, tools missing, gateway, aux models | `references/troubleshooting.md` |
| Contributing code: adding tools, slash commands, tests | `references/contributor-guide.md` |
| delegate_task "capped at N" reports | `references/delegate-task-concurrency-diagnosis.md` |
| "Can app X use my Nous Portal subscription/OAuth?" | `references/portal-auth-for-third-party-apps.md` |
| Connecting a messaging platform (Telegram, Discord, Slack, WhatsApp, …) | docs: `/user-guide/messaging` |

The reference list above is not the feature list — it is the set of topics that
need more than their docs page. For everything else Hermes ships, fetch
`llms.txt` and it maps the question to the page that answers it.

Two theming rules that hold even without loading the reference: **you apply skins yourself** (`hermes config set display.skin <name>` — every surface repaints live within ~a second; don't tell the user to run `/skin`), and **to tweak one color, edit the ACTIVE skin** (`hermes skin set <key> <hex>`) — never fork `default`, which drops the palette and resets the background.

## Spawning Additional Hermes Instances

Run additional Hermes processes as fully independent subprocesses — separate sessions, tools, and environments.

### When to Use This vs delegate_task

| | `delegate_task` | Spawning `hermes` process |
|-|-----------------|--------------------------|
| Isolation | Separate conversation, shared process | Fully independent process |
| Duration | Minutes (bounded by parent loop) | Hours/days |
| Tool access | Subset of parent's tools | Full tool access |
| Interactive | No | Yes (PTY mode) |
| Use case | Quick parallel subtasks | Long autonomous missions |

### One-Shot Mode

```
terminal(command="hermes chat -q 'Research GRPO papers and write summary to ~/research/grpo.md'", timeout=300)

# Background for long tasks:
terminal(command="hermes chat -q 'Set up CI/CD for ~/myapp'", background=true)
```

### Interactive PTY Mode (via tmux)

Hermes uses prompt_toolkit, which requires a real terminal. Use tmux for interactive spawning:

```
# Start
terminal(command="tmux new-session -d -s agent1 -x 120 -y 40 'hermes'", timeout=10)

# Wait for startup, then send a message
terminal(command="sleep 8 && tmux send-keys -t agent1 'Build a FastAPI auth service' Enter", timeout=15)

# Read output
terminal(command="sleep 20 && tmux capture-pane -t agent1 -p", timeout=5)

# Send follow-up
terminal(command="tmux send-keys -t agent1 'Add rate limiting middleware' Enter", timeout=5)

# Exit
terminal(command="tmux send-keys -t agent1 '/exit' Enter && sleep 2 && tmux kill-session -t agent1", timeout=10)
```

### Multi-Agent Coordination

```
# Agent A: backend
terminal(command="tmux new-session -d -s backend -x 120 -y 40 'hermes -w'", timeout=10)
terminal(command="sleep 8 && tmux send-keys -t backend 'Build REST API for user management' Enter", timeout=15)

# Agent B: frontend
terminal(command="tmux new-session -d -s frontend -x 120 -y 40 'hermes -w'", timeout=10)
terminal(command="sleep 8 && tmux send-keys -t frontend 'Build React dashboard for user management' Enter", timeout=15)

# Check progress, relay context between them
terminal(command="tmux capture-pane -t backend -p | tail -30", timeout=5)
terminal(command="tmux send-keys -t frontend 'Here is the API schema from the backend agent: ...' Enter", timeout=5)
```

### Session Resume

```
# Resume most recent session
terminal(command="tmux new-session -d -s resumed 'hermes --continue'", timeout=10)

# Resume specific session
terminal(command="tmux new-session -d -s resumed 'hermes --resume 20260225_143052_a1b2c3'", timeout=10)
```

### Tips

- **Prefer `delegate_task` for quick subtasks** — less overhead than spawning a full process
- **Use `-w` (worktree mode)** when spawning agents that edit code — prevents git conflicts
- **Set timeouts** for one-shot mode — complex tasks can take 5-10 minutes
- **Use `hermes chat -q` for fire-and-forget** — no PTY needed
- **Use tmux for interactive sessions** — raw PTY mode has `\r` vs `\n` issues with prompt_toolkit
- **For scheduled tasks**, use the `cronjob` tool instead of spawning — handles delivery and retry
- **"delegate_task is capped at N" reports** — see `references/delegate-task-concurrency-diagnosis.md`. Three real cap paths in Hermes; if none fired, the model is self-limiting and rationalising it as "the runtime caps."
- **"Can $external_app use my Nous Portal subscription / OAuth?"** — see `references/portal-auth-for-third-party-apps.md`. Walk the user through three layers (plugin-vs-app, what Portal actually exposes, local-broker-proxy option).

## Surfaces (quick orientation)

- **Desktop app** (`hermes desktop` / `hermes gui`) — native Electron app for macOS/Linux/Windows: streaming chat, session list, Cmd+K palette, drag-and-drop files, native notifications, per-profile remote-gateway login. Extend it with UI plugins — `references/desktop-plugins.md`.
- **Web dashboard** (`hermes dashboard`) — full admin panel: messaging channels, MCP catalog, webhooks, memory, profile builder, plus an embedded `hermes --tui` chat. Secured behind an OAuth/token gate.
- **Ink TUI** (`hermes --tui` or `display.interface: tui`) — terminal UI with docked widget apps — `references/tui-widgets.md`.
- **OpenAI-compatible proxy** (`hermes proxy`) — a local OpenAI API backed by whichever OAuth provider you're signed into. Point Codex CLI, Aider, Cline, or any script at it — no API key.

## Hard Invariants (never violate, regardless of what you loaded)

- **Never break prompt caching** — don't change past context, toolsets, or the system prompt mid-conversation. The only exception is context compression.
- **Message role alternation** — never two assistant or two user messages in a row; only `tool` results can repeat.
- **Secrets in `.env`, settings in `config.yaml`** — never tell a user to put a non-credential setting in `.env`.
- **Profile-safe paths** — `get_hermes_home()` in code, `$HERMES_HOME` when resolving paths in a session.
- **Never hand-edit `config.yaml` for the user** — use `hermes config set KEY VAL`; a stray indent can corrupt the file and break the live gateway.
### 提示

- **快速子任务优先使用 `delegate_task`** — 比生成完整进程开销更小
- **生成编辑代码的 agent 时使用 `-w`（worktree 模式）** — 防止 git 冲突
- **为单次模式设置超时** — 复杂任务可能需要 5-10 分钟
- **fire-and-forget 使用 `hermes chat -q`** — 无需 PTY
- **交互式会话使用 tmux** — 原始 PTY 模式与 prompt_toolkit 存在 `\r` vs `\n` 问题
- **定时任务使用 `cronjob` 工具而非生成进程** — 处理投递和重试

---

## 持久化与后台系统

四个系统与主对话循环并行运行。此处为快速参考；完整开发者说明位于 `AGENTS.md`，面向用户的文档位于 `website/docs/user-guide/features/`。

### 委派（`delegate_task`）

同步子 agent 生成——父 agent 等待子 agent 的摘要后再继续自身循环。隔离的上下文和终端会话。

- **单个：** `delegate_task(goal, context)`。
- **批量：** `delegate_task(tasks=[{goal, ...}, ...])` 并行运行子任务，上限由 `delegation.max_concurrent_children`（默认 3）控制。
- **角色：** `leaf`（默认；不能再委派）vs `orchestrator`（可以生成自己的 worker，受 `delegation.max_spawn_depth` 限制）。
- **非持久化。** 如果父 agent 被中断，子 agent 会被取消。对于必须在当前轮次之后继续的工作，使用 `cronjob` 或 `terminal(background=True, notify_on_complete=True)`。

配置：`config.yaml` 中的 `delegation.*`。

### Cron（定时任务）

持久化调度器——`cron/jobs.py` + `cron/scheduler.py`。通过 `cronjob` 工具、`hermes cron` CLI（`list`、`add`、`edit`、`pause`、`resume`、`run`、`remove`）或 `/cron` 斜杠命令驱动。

- **调度格式：** 持续时间（`"30m"`、`"2h"`）、"every" 短语（`"every monday 9am"`）、5 字段 cron（`"0 9 * * *"`）或 ISO 时间戳。
- **每任务选项：** `skills`、`model`/`provider` 覆盖、`script`（预运行数据收集；`no_agent=True` 使脚本成为整个任务）、`context_from`（将任务 A 的输出链接到任务 B）、`workdir`（在特定目录中运行，加载其 `AGENTS.md` / `CLAUDE.md`）、多平台投递。
- **不变量：** 每次运行 3 分钟硬中断，`.tick.lock` 文件防止跨进程重复 tick，cron 会话默认传递 `skip_memory=True`，cron 投递使用页眉/页脚框架而非镜像到目标 gateway 会话（保持角色交替完整）。

用户文档：https://hermes-agent.nousresearch.com/docs/user-guide/features/cron

### Curator（skill 生命周期）

agent 创建的 skill 的后台维护。跟踪使用情况，将闲置 skill 标记为过时，归档过时的 skill，保留运行前的 tar.gz 备份以防数据丢失。

- **CLI：** `hermes curator <verb>` — `status`、`run`、`pause`、`resume`、`pin`、`unpin`、`archive`、`restore`、`prune`、`backup`、`rollback`。
- **斜杠命令：** `/curator <subcommand>` 与 CLI 对应。
- **范围：** 仅处理 `created_by: "agent"` 来源的 skill。内置和 hub 安装的 skill 不在范围内。**从不删除** — 最具破坏性的操作是归档。已固定的 skill 不受任何自动转换和任何 LLM 审查的影响。
- **遥测：** `~/.hermes/skills/.usage.json` 中的 sidecar 保存每个 skill 的 `use_count`、`view_count`、`patch_count`、`last_activity_at`、`state`、`pinned`。

配置：`curator.*`（`enabled`、`interval_hours`、`min_idle_hours`、`stale_after_days`、`archive_after_days`、`backup.*`）。
用户文档：https://hermes-agent.nousresearch.com/docs/user-guide/features/curator

### Kanban（多 agent 工作队列）

用于多 profile/多 worker 协作的持久化 SQLite 看板（kanban）。用户通过 `hermes kanban <verb>` 驱动；调度器生成的 worker 看到由 `HERMES_KANBAN_TASK` 控制的专注 `kanban_*` toolset，orchestrator profile 可以选择加入更广泛的 `kanban` toolset。普通会话除非配置，否则没有任何 `kanban_*` schema 占用。

- **CLI 动词（常用）：** `init`、`create`、`list`（别名 `ls`）、`show`、`assign`、`link`、`unlink`、`comment`、`complete`、`block`、`unblock`、`archive`、`tail`。不常用：`watch`、`stats`、`runs`、`log`、`dispatch`、`daemon`、`gc`。
- **Worker/orchestrator toolset：** `kanban_show`、`kanban_complete`、`kanban_block`、`kanban_heartbeat`、`kanban_comment`、`kanban_create`、`kanban_link`；在调度器生成的任务之外显式启用 `kanban` toolset 的 profile 还可获得 `kanban_list` 和 `kanban_unblock` 用于看板路由。
- **调度器** 默认在 gateway 内运行（`kanban.dispatch_in_gateway: true`）——回收过期认领、推进就绪任务、原子认领、生成已分配的 profile。在配置的 `kanban.failure_limit` 次连续非成功尝试后自动阻塞任务（默认：2）。
- **隔离：** 看板是硬边界（worker 在环境中固定 `HERMES_KANBAN_BOARD`）；租户是看板内用于工作区路径和记忆键隔离的软命名空间。

用户文档：https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban

---

## Windows 特有问题

Hermes 在 Windows 上原生运行。默认用于执行命令的 shell 是 **Git Bash**（如果已安装 Git for Windows），回退到 **PowerShell**（优先使用 pwsh 7.x，自动回退到 Windows PowerShell 5.1）。可在 config.yaml 中设置 `terminal.shell: pwsh` / `terminal.shell: powershell` 强制使用 PowerShell，或 `terminal.shell: bash` 显式指定 Git Bash。大多数功能开箱即用，但 Win32 和 POSIX 之间有一些差异曾给我们带来麻烦——遇到新问题时请在此记录，以免下一个人（或下一个会话）重新踩坑。

### 输入/键绑定

**Alt+Enter 不插入换行。** Windows Terminal 在终端层拦截 Alt+Enter 以切换全屏——该按键永远不会到达 prompt_toolkit。请改用 **Ctrl+Enter**。Windows Terminal 将 Ctrl+Enter 作为 LF（`c-j`）传递，与普通 Enter（`c-m` / CR）不同，CLI 仅在 `win32` 上将 `c-j` 绑定到换行插入（参见 `_bind_prompt_submit_keys` + `cli.py` 中仅限 Windows 的 `c-j` 绑定）。副作用：在 Windows 上，原始 Ctrl+J 按键也会插入换行——这是不可避免的，因为 Windows Terminal 在 Win32 控制台 API 层将 Ctrl+Enter 和 Ctrl+J 折叠为相同的键码。Windows 上 Ctrl+J 没有冲突的绑定，因此这是无害的副作用。

mintty / git-bash 行为相同（Alt+Enter 全屏），除非你在选项 → 键中禁用 Alt+Fn 快捷键。直接使用 Ctrl+Enter 更简单。

**诊断键绑定。** 运行 `python scripts/keystroke_diagnostic.py`（仓库根目录）可查看 prompt_toolkit 在当前终端中如何识别每个按键。可回答"Shift+Enter 是否作为独立键传入？"（几乎从不——大多数终端将其折叠为普通 Enter）或"我的终端为 Ctrl+Enter 发送什么字节序列？"等问题。Ctrl+Enter = c-j 这一事实就是通过此方式确认的。

### 配置/文件

**首次运行时 HTTP 400 "No models provided"。** `config.yaml` 保存时带有 UTF-8 BOM（Windows 应用写入时常见）。重新保存为不带 BOM 的 UTF-8。`hermes config edit` 写入时不带 BOM；手动在记事本中编辑是常见原因。

### `execute_code` / 沙箱

**WinError 10106**（"无法加载或初始化请求的服务提供商"）来自沙箱子进程——它无法创建 `AF_INET` socket，因此回退的 loopback-TCP RPC 在 `connect()` 之前失败。根本原因通常**不是**损坏的 Winsock LSP；而是 Hermes 自身的环境清理器从子进程环境中删除了 `SYSTEMROOT` / `WINDIR` / `COMSPEC`。Python 的 `socket` 模块需要 `SYSTEMROOT` 来定位 `mswsock.dll`。通过 `tools/code_execution_tool.py` 中的 `_WINDOWS_ESSENTIAL_ENV_VARS` 允许列表修复。如果仍然遇到此问题，在 `execute_code` 块内 echo `os.environ` 以确认 `SYSTEMROOT` 已设置。完整诊断方案见 `references/execute-code-sandbox-env-windows.md`。

### 测试/贡献

**`scripts/run_tests.sh` 在 Windows 上无法直接使用** — 它查找 POSIX venv 布局（`.venv/bin/activate`）。Hermes 安装的 venv 位于 `venv/Scripts/`，也没有 pip 或 pytest（为减小安装体积而精简）。解决方案：将 `pytest + pyyaml` 安装到系统 Python 3.14 用户站点，然后设置 `PYTHONPATH` 直接调用 pytest：

```bash
"/c/Program Files/Python311/python" -m pip install --user pytest pyyaml
export PYTHONPATH="$(pwd)"
"/c/Program Files/Python311/python" -m pytest tests/foo/test_bar.py -v --tb=short
```

仓库已不再使用 pytest-xdist——规范 runner 通过 `run_tests_parallel.py` 做按文件子进程隔离，但该 wrapper 仅支持 POSIX，其 CI 一致性保证不适用于非 POSIX 环境。

**仅 POSIX 的测试需要跳过守卫。** 代码库中已有的常见标记：
- 符号链接——Windows 上需要提升权限
- `0o600` 文件模式——POSIX 模式位在 NTFS 上默认不强制执行
- `signal.SIGALRM`——仅 Unix（每测试超时不再直接使用它；参见 `tests/conftest.py::pytest_configure` 中的 win32 timeout-method shim）
- Winsock / Windows 特有回归——`@pytest.mark.skipif(sys.platform != "win32", ...)`

使用现有的跳过模式风格（`sys.platform == "win32"` 或 `sys.platform.startswith("win")`）以与测试套件其余部分保持一致。

### 路径/文件系统

**行尾。** Git 可能警告 `LF will be replaced by CRLF the next time Git touches it`。这是外观问题——仓库的 `.gitattributes` 会规范化。不要让编辑器自动将已提交的 POSIX 换行文件转换为 CRLF。

**正斜杠几乎在所有地方都有效。** `C:/Users/...` 被每个 Hermes 工具和大多数 Windows API 接受。在代码和日志中优先使用正斜杠——避免在 bash 中转义反斜杠。

---

## 故障排查

### 语音不工作
1. 检查 `config.yaml` 中 `stt.enabled: true`
2. 验证提供商：`pip install faster-whisper` 或设置 API key
3. 在 gateway 中：`/restart`。在 CLI 中：退出并重新启动。

### 工具不可用
1. `hermes tools` — 检查 toolset 是否为你的平台启用
2. 某些工具需要环境变量（检查 `.env`）
3. 启用工具后执行 `/reset`

### 模型/提供商问题
1. `hermes doctor` — 检查配置和依赖
2. `hermes auth` — 重新认证 OAuth 提供商（或 `hermes auth add <provider>`）
3. 检查 `.env` 中是否有正确的 API key
4. **Copilot 403**：`gh auth login` 的 token **不适用于** Copilot API。必须通过 `hermes model` → GitHub Copilot 使用 Copilot 专用 OAuth 设备码流程。

### 变更未生效
- **工具/skill：** `/reset` 以更新后的 toolset 启动新会话
- **配置变更：** 在 gateway 中：`/restart`。在 CLI 中：退出并重新启动。
- **代码变更：** 重启 CLI 或 gateway 进程

### Skill 未显示
1. `hermes skills list` — 验证已安装
2. `hermes skills config` — 检查平台启用状态
3. 显式加载：`/skill name` 或 `hermes -s name`

### Gateway 问题
首先检查日志：
```bash
grep -i "failed to send\|error" ~/.hermes/logs/gateway.log | tail -20
```

常见 gateway 问题：
- **SSH 注销后 gateway 停止**：启用 linger：`sudo loginctl enable-linger $USER`
- **WSL2 关闭后 gateway 停止**：WSL2 需要 `/etc/wsl.conf` 中的 `systemd=true` 才能使 systemd 服务工作。没有它，gateway 回退到 `nohup`（会话关闭时停止）。
- **Gateway 崩溃循环**：重置失败状态：`systemctl --user reset-failed hermes-gateway`

### 平台特定问题
- **Discord bot 静默**：必须在 Bot → Privileged Gateway Intents 中启用 **Message Content Intent**。
- **Slack bot 仅在私信中工作**：必须订阅 `message.channels` 事件。没有它，bot 会忽略公共频道。
- **Windows 特有问题**（`Alt+Enter` 换行、WinError 10106、UTF-8 BOM 配置、测试套件、行尾）：参见上方专门的 **Windows 特有问题** 部分。

### 辅助模型不工作
如果 `auxiliary` 任务（视觉、压缩）静默失败，`auto` 提供商找不到后端。请设置 `OPENROUTER_API_KEY` 或 `GOOGLE_API_KEY`，或显式配置每个辅助任务的提供商：
```bash
hermes config set auxiliary.vision.provider <your_provider>
hermes config set auxiliary.vision.model <model_name>
```

---

## 查找资源

| 查找内容... | 位置 |
|----------------|----------|
| 配置选项 | `hermes config edit` 或[配置文档](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) |
| 可用工具 | `hermes tools list` 或[工具参考](https://hermes-agent.nousresearch.com/docs/reference/tools-reference) |
| 斜杠命令 | 会话内 `/help` 或[斜杠命令参考](https://hermes-agent.nousresearch.com/docs/reference/slash-commands) |
| Skill 目录 | `hermes skills browse` 或[Skill 目录](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) |
| 提供商设置 | `hermes model` 或[提供商指南](https://hermes-agent.nousresearch.com/docs/integrations/providers) |
| 平台设置 | `hermes gateway setup` 或[消息文档](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/) |
| MCP 服务器 | `hermes mcp list` 或[MCP 指南](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) |
| Profiles | `hermes profile list` 或[Profiles 文档](https://hermes-agent.nousresearch.com/docs/user-guide/profiles) |
| Cron 任务 | `hermes cron list` 或[Cron 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron) |
| 记忆 | `hermes memory status` 或[记忆文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory) |
| 环境变量 | `hermes config env-path` 或[环境变量参考](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) |
| CLI 命令 | `hermes --help` 或[CLI 参考](https://hermes-agent.nousresearch.com/docs/reference/cli-commands) |
| Gateway 日志 | `~/.hermes/logs/gateway.log` |
| 会话文件 | `~/.hermes/sessions/` 或 `hermes sessions browse` |
| 源代码 | `~/.hermes/hermes-agent/` |

---

## 贡献者快速参考

面向偶尔贡献者和 PR 作者。完整开发者文档：https://hermes-agent.nousresearch.com/docs/developer-guide/

### 项目结构

<!-- ascii-guard-ignore -->
```
hermes-agent/
├── run_agent.py          # AIAgent — core conversation loop
├── model_tools.py        # Tool discovery and dispatch
├── toolsets.py           # Toolset definitions
├── cli.py                # Interactive CLI (HermesCLI)
├── hermes_state.py       # SQLite session store
├── agent/                # Prompt builder, context compression, memory, model routing, credential pooling, skill dispatch
├── hermes_cli/           # CLI subcommands, config, setup, commands
│   ├── commands.py       # Slash command registry (CommandDef)
│   ├── config.py         # DEFAULT_CONFIG, env var definitions
│   └── main.py           # CLI entry point and argparse
├── tools/                # One file per tool
│   └── registry.py       # Central tool registry
├── gateway/              # Messaging gateway
│   └── platforms/        # Platform adapters (telegram, discord, etc.)
├── cron/                 # Job scheduler
├── tests/                # ~3000 pytest tests
└── website/              # Docusaurus docs site
```
<!-- ascii-guard-ignore-end -->

配置：`~/.hermes/config.yaml`（设置）、`~/.hermes/.env`（API key）。

### 添加工具（3 个文件）

**1. 创建 `tools/your_tool.py`：**
```python
import json, os
from tools.registry import registry

def check_requirements() -> bool:
    return bool(os.getenv("EXAMPLE_API_KEY"))

def example_tool(param: str, task_id: str = None) -> str:
    return json.dumps({"success": True, "data": "..."})

registry.register(
    name="example_tool",
    toolset="example",
    schema={"name": "example_tool", "description": "...", "parameters": {...}},
    handler=lambda args, **kw: example_tool(
        param=args.get("param", ""), task_id=kw.get("task_id")),
    check_fn=check_requirements,
    requires_env=["EXAMPLE_API_KEY"],
)
```

**2. 添加到 `toolsets.py`** → `_HERMES_CORE_TOOLS` 列表。

自动发现：任何包含顶层 `registry.register()` 调用的 `tools/*.py` 文件都会自动导入——无需手动列出。

所有处理器必须返回 JSON 字符串。路径使用 `get_hermes_home()`，永远不要硬编码 `~/.hermes`。

### 添加斜杠命令

1. 在 `hermes_cli/commands.py` 的 `COMMAND_REGISTRY` 中添加 `CommandDef`
2. 在 `cli.py` → `process_command()` 中添加处理器
3. （可选）在 `gateway/run.py` 中添加 gateway 处理器

所有消费方（帮助文本、自动补全、Telegram 菜单、Slack 映射）均自动从中央注册表派生。

### Agent 循环（高层概述）

```
run_conversation():
  1. Build system prompt
  2. Loop while iterations < max:
     a. Call LLM (OpenAI-format messages + tool schemas)
     b. If tool_calls → dispatch each via handle_function_call() → append results → continue
     c. If text response → return
  3. Context compression triggers automatically near token limit
```

### 测试

```bash
python -m pytest tests/ -o 'addopts=' -q   # 完整套件
python -m pytest tests/tools/ -q            # 特定区域
```

- 测试自动将 `HERMES_HOME` 重定向到临时目录——永远不会触及真实的 `~/.hermes/`
- 推送任何变更前运行完整套件
- 使用 `-o 'addopts='` 清除任何内置的 pytest 标志

**Windows 贡献者：** `scripts/run_tests.sh` 目前查找 POSIX venv（`.venv/bin/activate` / `venv/bin/activate`），在 Windows 上会报错，因为布局是 `venv/Scripts/activate` + `python.exe`。Hermes 安装的 venv 位于 `venv/Scripts/`，也没有 `pip` 或 `pytest`——为终端用户安装体积而精简。解决方案：将 pytest + pyyaml 安装到系统 Python 3.14 用户站点（`/c/Program Files/Python314/python -m pip install --user pytest pyyaml`），然后直接运行测试：

```bash
export PYTHONPATH="$(pwd)"
"/c/Program Files/Python311/python" -m pytest tests/tools/test_foo.py -v --tb=short
```

仓库已不再使用 pytest-xdist——规范 runner 通过 `run_tests_parallel.py` 做按文件子进程隔离，但该 wrapper 仅支持 POSIX，其 CI 一致性保证不适用于非 POSIX 环境。

**跨平台测试守卫：** 使用仅 POSIX 系统调用的测试需要跳过标记。代码库中已有的常见标记：
- 符号链接创建 → `@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require elevated privileges on Windows")`（参见 `tests/cron/test_cron_script.py`）
- POSIX 文件模式（0o600 等）→ `@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX mode bits not enforced on Windows")`（参见 `tests/hermes_cli/test_auth_toctou_file_modes.py`）
- `signal.SIGALRM` → 仅 Unix（每测试超时不再直接使用它；参见 `tests/conftest.py::pytest_configure` 中的 win32 timeout-method shim）
- 实时 Winsock / Windows 特有回归测试 → `@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific regression")`

**仅 monkeypatch `sys.platform` 是不够的**，当被测代码还调用 `platform.system()` / `platform.release()` / `platform.mac_ver()` 时。这些函数独立重新读取真实 OS，因此在 Windows runner 上将 `sys.platform = "linux"` 的测试仍会看到 `platform.system() == "Windows"` 并走 Windows 分支。需要同时 patch 三者：

```python
monkeypatch.setattr(sys, "platform", "linux")
monkeypatch.setattr(platform, "system", lambda: "Linux")
monkeypatch.setattr(platform, "release", lambda: "6.8.0-generic")
```

参见 `tests/agent/test_prompt_builder.py::TestEnvironmentHints` 中的完整示例。

### 扩展系统 prompt 的执行环境块

关于宿主 OS、用户 home、cwd、终端后端和 shell（Windows 上的 bash vs PowerShell）的事实性指导从 `agent/prompt_builder.py::build_environment_hints()` 输出。WSL 提示和每个后端的探测逻辑也在此处。约定：

- **本地终端后端** → 输出宿主信息（OS、`$HOME`、cwd）+ Windows 特有说明（hostname ≠ username，`terminal` 使用 bash 而非 PowerShell）。
- **远程终端后端**（`_REMOTE_TERMINAL_BACKENDS` 中的任何内容：`docker, singularity, modal, daytona, ssh, vercel_sandbox, managed_modal`）→ **完全抑制**宿主信息，仅描述后端。通过 `tools.environments.get_environment(...).execute(...)` 在后端内运行实时 `uname`/`whoami`/`pwd` 探测，每进程缓存在 `_BACKEND_PROBE_CACHE` 中，探测超时时使用静态回退。
- **prompt 编写的关键事实：** 当 `TERMINAL_ENV != "local"` 时，*每个*文件工具（`read_file`、`write_file`、`patch`、`search_files`）都在后端容器内运行，而非宿主上。在这种情况下，系统 prompt 绝不能描述宿主——agent 无法访问它。

完整设计说明、确切输出字符串和测试陷阱：`references/prompt-builder-environment-hints.md`。

**重构安全模式（POSIX 等价守卫）：** 当你将内联逻辑提取到添加 Windows/平台特定行为的辅助函数时，在测试文件中保留一个 `_legacy_<name>` oracle 函数，它是旧代码的逐字副本，然后对其进行参数化差异比较。示例：`tests/tools/test_code_execution_windows_env.py::TestPosixEquivalence`。这锁定了 POSIX 行为逐位相同的不变量，并使任何未来的偏差以清晰的差异明显失败。

### 提交约定

```
type: concise subject line

Optional body.
```

类型：`fix:`、`feat:`、`refactor:`、`docs:`、`chore:`

### 关键规则

- **永远不要破坏 prompt 缓存** — 不要在对话中途更改上下文、工具或系统 prompt
- **消息角色交替** — 永远不要连续出现两条 assistant 或两条 user 消息
- 所有路径使用 `hermes_constants` 中的 `get_hermes_home()`（profile 安全）
- 配置值放入 `config.yaml`，密钥放入 `.env`
- 新工具需要 `check_fn`，以便仅在满足要求时才显示

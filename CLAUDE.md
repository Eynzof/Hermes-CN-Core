# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## This is a fork

**Hermes-CN-Core** (git remote `Eynzof/Hermes-CN-Core`; the Python package is still named `hermes-agent`) is a
long-lived Chinese-community **fork** of [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent).
It tracks upstream while carrying a documented patch set for Chinese provider metadata, the desktop runtime, and
Dashboard APIs consumed by [`Hermes-CN-Desktop`](https://github.com/Eynzof/Hermes-CN-Desktop). The
`pyproject.toml` project name is still `hermes-agent` and the CLI entry point is still `hermes` — do not assume
"clean reimplementation," assume "downstream patches on top of upstream." (`README.md` is the Chinese version;
`README.en.md` is English. Some maintenance docs such as `MAINTAINING.md` still use the older `hermes-agent-cn` name.)

- **Every fork-specific behavioral change is tracked in [`FORK_NOTES.md`](./FORK_NOTES.md) as `P-NNN`** (currently through P-045). Read it
  before touching `hermes_cli/web_server.py`, `tui_gateway/`, or `hermes_cli/config.py`'s `OPTIONAL_ENV_VARS` —
  those files carry deliberate divergence from upstream. New behavioral patches use a `[CN-fork] P-NNN` commit
  prefix and must be added to the FORK_NOTES table.
- **`apps/desktop/` is upstream's official desktop app — this fork carries ZERO modifications to it.** The CN
  desktop lives in the separate `Hermes-CN-Desktop` repo; on every upstream sync, `apps/desktop/` is taken
  wholesale from `upstream/main` (fork-side changes there, if any appear, are dropped).
- **Branch model (see [`MAINTAINING.md`](./MAINTAINING.md)):** `origin/main` is the stable fork branch;
  `upstream/main` is read-only. **Never merge `upstream/main` directly into `main`** — sync via
  `./scripts/sync-upstream.sh`, which creates a `chore/sync-*` branch for a PR. Fork patches go on `cn/P-xxx-*`
  branches; clean branches for official upstream PRs go on `upstream-pr/*`. `runtime-v*` tags publish signed
  desktop runtime artifacts.
- **The `cn-desktop` extra in `pyproject.toml` is the source of truth for what the frozen PyInstaller runtime
  bundles.** The frozen runtime *cannot* lazy-install (no working pip), so any backend the desktop exposes
  (web, anthropic, mcp, feishu/钉钉/企微/微信) MUST be pre-baked there even though `[all]` deliberately excludes
  lazy-installable backends. This diverges from `[all]` on purpose — see P-014/P-015.

## AGENTS.md is the canonical deep guide

[`AGENTS.md`](./AGENTS.md) (~1,385 lines) is the authoritative development reference — architecture internals,
the tool registry chain, slash-command registry, TUI/Dashboard/Electron surfaces, plugins, skills, delegation,
curator, cron, kanban, profiles, and the full "Known Pitfalls" list. **Read it for any non-trivial change.**
This file is the orientation layer + fork specifics; AGENTS.md is the detail.

## Commands

```bash
# Dev install (editable, all extras)
pip install -e ".[all,dev]"        # or: uv pip install -e ".[all,dev]"  (Python 3.11–3.13)

# Tests — ALWAYS use the wrapper, not raw pytest. It enforces CI parity
# (unset API keys, TZ=UTC, C.UTF-8, per-file subprocess isolation).
scripts/run_tests.sh                                   # full suite
scripts/run_tests.sh tests/gateway/                    # one directory
scripts/run_tests.sh tests/agent/test_foo.py::test_x   # one test
scripts/run_tests.sh tests/foo.py -- --tb=long         # path + pytest args (after `--`)

# Lint / typecheck
ruff check .       # BLOCKING (lint.yml) — enforces PLW1514 (unspecified-encoding); other ruff rules advisory-diff only
ty check           # type checker (astral ty), advisory for Python
# Also blocking in CI: scripts/check-windows-footguns.py (lint.yml) and TypeScript `npm run typecheck` (typecheck.yml)

# Run the app
hermes             # interactive CLI    | hermes --tui (Ink TUI) | hermes gateway (messaging)
hermes dashboard --no-open   # localhost SPA + API; the fork smoke test for CN-only APIs lives in MAINTAINING.md
./hermes           # local launcher equivalent to the installed `hermes` command
# (extra entry points: `hermes-agent` → run_agent:main, `hermes-acp` → acp_adapter.entry:main)

# TypeScript TUI (ui-tui/) — npm workspace rooted at repo top
cd ui-tui && npm install
npm run dev        # watch (rebuild hermes-ink + tsx --watch)
npm run build      # full build   | npm run typecheck | npm run lint | npm test (vitest)
```

The Python test suite is large — ~1,700 test files; CI slices it 8 ways. Run the full suite before pushing.

## 开发前预检（双仓同步 + Worktree 隔离）

Hermes CN 的需求与 bug 修复通常**同时横跨 Core 与 [Desktop](https://github.com/Eynzof/Hermes-CN-Desktop) 两个仓库**。正式动手写代码前，两个仓库都必须先过这道预检，**不要直接在 `main` 上改**：

1. **确认主分支已与远端同步**。对 Core 与 Desktop 分别 `git fetch origin`，确认本地 `main` 与 `origin/main` 一致（`git rev-list --left-right --count main...origin/main` 应为 `0  0`）；落后就先快进，工作区脏就先收拾干净。Core 是 fork——**永远不要把 `upstream/main` 直接并进 `main`**，上游同步走 `./scripts/sync-upstream.sh`（见 "This is a fork"）。
2. **为每个仓库开独立的功能分支 + git worktree**，让 Core 与 Desktop 的改动互不干扰、可并行：
   ```bash
   git -C <repo> fetch origin
   git -C <repo> worktree add ../wt/<repo>-<topic> -b <branch> origin/main
   ```
   分支命名沿用各仓库既有约定：Core 的 fork 行为补丁用 `cn/P-xxx-*`（并登记进 `FORK_NOTES.md`），干净上游 PR 用 `upstream-pr/*`，文档/杂项用 `docs/` `chore/`；Desktop 沿用 Conventional 风格。
3. 不要在同一个工作目录里来回 `git checkout` 切分支——双仓并行时极易串味；每条线一个 worktree。

**收尾流程（每个仓库都要走完，缺一不可）**：改完 → 跑各自校验（Core：`scripts/run_tests.sh` 全套 + `ruff check .`；Desktop：`pnpm typecheck && pnpm test:unit && cargo check`）→ commit → push → 开 PR → **盯 PR 上 GitHub Actions 全绿**（Core：`lint.yml` + 测试切片），没过就回去修，别把任务当完成。

## Git & GitHub Conventions

**本仓库是 fork。所有 issue 查询、PR 创建、合并都只针对 fork 仓库，绝不针对上游仓库。**

- **目标仓库永远是 fork：** Core 是 `Eynzof/Hermes-CN-Core`，Desktop 是 `Eynzof/Hermes-CN-Desktop`。
  上游 `NousResearch/hermes-agent` 是只读参照，**不要**在它上面查 issue、开 PR 或合并（向上游提交干净 PR 是
  唯一例外，且必须显式声明、走 `upstream-pr/*` 分支——见 "This is a fork"）。
- **每个 `gh` 命令执行前先核验 repo scope。** 不要依赖 `gh` 的当前目录自动推断（fork 仓库的 `gh` 默认可能
  指向上游）。显式带上 `--repo Eynzof/Hermes-CN-Core`（或对应 Desktop 仓库），例如
  `gh issue list --repo Eynzof/Hermes-CN-Core`、`gh pr create --repo Eynzof/Hermes-CN-Core`、
  `gh pr merge --repo Eynzof/Hermes-CN-Core`。
- **创建 PR 时再确认 base 仓库。** `gh pr create` 默认的 base 是上游 fork 源；务必确认 base 落在 fork 自身
  （`--repo` 指定 fork + base 分支为该 fork 的 `main`），避免把 PR 误开到上游。

## Architecture big picture

Hermes is a **self-improving AI agent** that runs the same agent core across many front-ends and many chat
platforms. Two languages: **Python** owns the agent loop, tools, sessions, providers, and gateway; **TypeScript**
owns the interactive screens (Ink TUI, web Dashboard, Electron desktop).

**Python core dependency chain** (load-bearing — see AGENTS.md "File Dependency Chain"):
```
tools/registry.py        # no deps; every tool file calls registry.register() at import time
  → tools/*.py           # auto-discovered: any tools/*.py with a top-level register() is imported
  → model_tools.py       # tool discovery + handle_function_call() dispatch (+ triggers plugin discovery)
  → run_agent.py         # AIAgent — the synchronous conversation loop (run_conversation())
  → cli.py, gateway/, batch_runner.py, tui_gateway/
```
Adding a built-in tool needs **two** edits: create `tools/your_tool.py` (auto-discovered) AND list its name in a
toolset in `toolsets.py` (auto-discovery registers the schema but a tool is only exposed if it's in a toolset).
For local/custom tools, prefer a `~/.hermes/plugins/<name>/` plugin over editing core.

**Entry points / surfaces:**
- `hermes_cli/main.py` — CLI command dispatch; `_apply_profile_override()` sets `HERMES_HOME` before imports.
- `run_agent.py` — `AIAgent` (~70-param constructor); messages are OpenAI-format dicts.
- `gateway/` — single multi-platform messaging process; one adapter per platform in `gateway/platforms/`.
- `tui_gateway/` (Python JSON-RPC) ⇄ `ui-tui/` (Ink/React) — the `hermes --tui` experience. The Dashboard
  `/chat` pane and the embedded chat **reuse this same TUI over a PTY** — do not re-implement the chat
  transcript/composer in React (see AGENTS.md "TUI in the Dashboard").
- `apps/desktop/` — a *separate* Electron chat app over the same `tui_gateway` JSON-RPC.
- Pluggable subsystems each have their own discovery + ABC: `plugins/model-providers/`, `plugins/memory/`,
  `providers/`, `skills/` + `optional-skills/`, plus cron, curator, delegation, and kanban.

User state lives under `~/.hermes/` (config.yaml = settings, `.env` = secrets only), **profile-scoped** via
`get_hermes_home()`.

## Critical rules (the ones most likely to bite)

- **Never break prompt caching.** Do not alter past context, change toolsets, or rebuild system prompts
  mid-conversation (compression is the only exception). Cache-mutating slash commands default to deferred
  invalidation with an opt-in `--now`.
- **Never hardcode `~/.hermes`.** Use `get_hermes_home()` (code paths) and `display_hermes_home()` (user-facing
  messages) from `hermes_constants` — hardcoding breaks profiles.
- **Dependency pinning is a supply-chain control.** Core `dependencies` are exact-pinned (`==X.Y.Z`); optional
  backends live in extras and lazy-install via `tools/lazy_deps.py`. Regenerate `uv.lock` (`uv lock`) after any
  bump. See AGENTS.md "Dependency Pinning Policy".
- **Don't write change-detector tests** (snapshots of model catalogs, config-version literals, enumeration
  counts). Assert relationships/invariants instead. See AGENTS.md "Don't write change-detector tests".
- **Tests must not write to `~/.hermes/`** — the autouse `_hermetic_environment` fixture in `tests/conftest.py` redirects `HERMES_HOME` to a per-test tempdir.
- **Plugins must not modify core files.** Extend the generic plugin surface (a new hook / ctx method) instead.

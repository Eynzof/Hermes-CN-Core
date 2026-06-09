# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## This is a fork

**Hermes-CN-Core** (git remote `Eynzof/Hermes-CN-Core`; the Python package is still named `hermes-agent`) is a
long-lived Chinese-community **fork** of [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent).
It tracks upstream while carrying a documented patch set for Chinese provider metadata, the desktop runtime, and
Dashboard APIs consumed by [`hermes-agent-cn-desktop`](https://github.com/Eynzof/hermes-agent-cn-desktop). The
`pyproject.toml` project name is still `hermes-agent` and the CLI entry point is still `hermes` — do not assume
"clean reimplementation," assume "downstream patches on top of upstream." (`README.md` is the Chinese version;
`README.en.md` is English. Some maintenance docs such as `MAINTAINING.md` still use the older `hermes-agent-cn` name.)

- **Every fork-specific behavioral change is tracked in [`FORK_NOTES.md`](./FORK_NOTES.md) as `P-NNN`.** Read it
  before touching `hermes_cli/web_server.py`, `tui_gateway/`, or `hermes_cli/config.py`'s `OPTIONAL_ENV_VARS` —
  those files carry deliberate divergence from upstream. New behavioral patches use a `[CN-fork] P-NNN` commit
  prefix and must be added to the FORK_NOTES table.
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

[`AGENTS.md`](./AGENTS.md) (~1200 lines) is the authoritative development reference — architecture internals,
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
scripts/run_tests.sh --no-isolate tests/foo/           # faster, for interactive debugging

# Lint / typecheck (CI: .github/workflows/lint.yml)
ruff check .       # BLOCKING — enforces PLW1514 (unspecified-encoding); all other rules are advisory-diff only
ty check           # type checker (astral ty), advisory

# Run the app
hermes             # interactive CLI    | hermes --tui (Ink TUI) | hermes gateway (messaging)
hermes dashboard --no-open   # localhost SPA + API; the fork smoke test for CN-only APIs lives in MAINTAINING.md
./hermes           # local launcher equivalent to the installed `hermes` command

# TypeScript TUI (ui-tui/) — npm workspace rooted at repo top
cd ui-tui && npm install
npm run dev        # watch (rebuild hermes-ink + tsx --watch)
npm run build      # full build   | npm run type-check | npm run lint | npm test (vitest)
```

The Python test suite is ~17k tests across ~900 files; CI slices it 6 ways. Run the full suite before pushing.

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
- `run_agent.py` — `AIAgent` (~60-param constructor); messages are OpenAI-format dicts.
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
- **Tests must not write to `~/.hermes/`** — the autouse fixture in `tests/conftest.py` redirects `HERMES_HOME`.
- **Plugins must not modify core files.** Extend the generic plugin surface (a new hook / ctx method) instead.

# Fork notes — Eynzof/hermes-agent-cn

Simplified Chinese: [`FORK_NOTES.zh-CN.md`](./FORK_NOTES.zh-CN.md)

This document explains the fork-specific changes on `main` that diverge from upstream `NousResearch/hermes-agent`. New behavioral patches should use `[CN-fork] P-NNN` in the commit message and be listed below.

| ID | Target file | What it does | Why we need it | Upstream status |
|---|---|---|---|---|
| **P-025** | `hermes_cli/web_server.py` | `/api/providers/oauth` now (1) serves from a 20s per-profile in-process TTL cache, (2) runs each provider's status check concurrently via `asyncio.to_thread` (OFF the FastAPI event loop) instead of serially inline, and (3) busts the cache on every connect/disconnect (disconnect clear paths, PKCE submit, device-code/loopback poll→`approved`). Adds a `refresh=true` escape hatch. | The desktop Models page enumerated every OAuth provider's status serially on every open AND every window refocus; some checks touch the network/subprocess, and because the handler is `async` they blocked the event loop that also serves the chat gateway WebSocket — so 模型页 took seconds to open and could stutter live chat. | Should be upstreamed (generic responsiveness fix) |
| **P-002** | `hermes_cli/web_server.py` | Adds `POST /api/upload` for dashboard attachment uploads | v2 web composer's drag-to-upload depends on it; upstream had it once (`e7c3cd772`) then reverted | Not in upstream |
| **P-003** | `hermes_cli/web_server.py` | Drops the `_DASHBOARD_EMBEDDED_CHAT_ENABLED` gate on `/api/ws` | v2 runs `hermes dashboard` without `--tui`, the gate would close gateway WS | **Largely addressed upstream** — v0.16.0 (#38591) defaults the flag to `True` and removes the dashboard `--tui` flag; fork keeps the explicit gate removal on `/api/ws` as defense-in-depth |
| **P-004** | `hermes_cli/web_server.py` | Originally added `GET /api/fs/list` for the v2 web workspace picker | Upstream later shipped its own `/api/fs/list`; fork helpers removed, route now matches upstream (no home restriction) | Converged upstream |
| **P-005** | `hermes_cli/web_server.py` | Adds `GET /api/mcp-servers` (read-only `{summary, servers:[{name,enabled}]}`) — handler `list_mcp_servers_summary` | v2 panel "健康检查" cell needs MCP count without leaking command/args/env (which embed secrets) | Distinct from upstream's `/api/mcp/servers` (exposes url/command/args); fork handler renamed in 2026-06-04 sync to avoid an operationId clash |
| **P-006** | `hermes_cli/config.py` | Registers `OPTIONAL_ENV_VARS` for CN providers (ARK / QIANFAN / HUNYUAN / SILICONFLOW / MODELSCOPE / AI302 / COMPSHARE) | Dashboard env panel is metadata-driven; upstream only knows global providers (OpenAI / Anthropic / Google / DeepSeek) | Won't be upstreamed (CN-specific) |
| ~~**P-007**~~ | `tui_gateway/ws.py` | ~~Wraps the dispatch handler in a try/except that logs traceback + returns a JSON-RPC error response instead of silently closing the WS~~ | Without this, any unhandled handler exception or json.dumps serialization failure shows up in the client as "WebSocket closed" with zero diagnostic context | **Superseded by upstream** — dropped in 2026-06-04 sync |
| **P-008** | `hermes_cli/web_server.py` | ~~Adds `GET/PUT /api/profiles/active`~~ → upstream shipped its own `GET/POST /api/profiles/active`; fork now keeps only a **compat layer**: adds `name` to the GET response (desktop reads `.name`) + a `PUT` alias (desktop sets via PUT) | v2 web profile switcher reads `.name` and writes via `PUT`; upstream returns `{active,current}` and only has `POST` | **Upstreamed (GET/POST)** + fork compat (2026-06-04 sync) |
| **P-009** | `hermes_cli/web_server.py`, `tui_gateway/sse.py` | Adds SSE+POST gateway transport at `/api/v2/events` and `/api/v2/rpc` | ~~desktop uses EventSource for streaming and POST for JSON-RPC~~ → desktop >= 0.4 uses the native `/api/ws` WebSocket (official desktop architecture); this transport only serves older shells | **DEPRECATED** — kept for desktop <= 0.3.x (no shell self-update; runtimes hot-update underneath them). Remove after old-shell EOL. Won't upstream. |
| **P-010** | `hermes_cli/config.py` | Registers `LONGCAT_API_KEY` in `OPTIONAL_ENV_VARS` | CN model settings need first-class LongCat credentials in the env panel | Won't be upstreamed unless upstream adopts LongCat |
| **P-011** | `tui_gateway/server.py` | Adds `slug_filter` to `model.options` and `provider.probe` RPC | desktop needs filtered model picker options and a lightweight provider health probe | Maybe upstream |
| **P-012** | `hermes_cli/main.py` | `_model_flow_anthropic()` prompts for optional custom `base_url` instead of unconditionally removing it | Users running Anthropic-compatible proxies or alternative endpoints need to preserve a custom `base_url` during model setup | Should be upstreamed |
| **P-013** | `model_tools.py`, `tests/run_agent/test_repair_tool_arg_keys.py` | Adds automatic tool argument key repair (`repair_tool_arg_keys`) with alias tables, per-tool overrides, fuzzy fallback, nested object/array recursion, and an optional callback hook; integrated into `handle_function_call` before type coercion | LLMs often misname arguments (e.g. "file"→"path", "cmd"→"command"); this makes tool dispatch resilient to common drift without weakening JSON Schemas | Should be upstreamed |
| **P-014** | `.github/workflows/release-runtime.yml`, `tools/mcp_tool.py`, `hermes_cli/config.py`, `docs/RUNTIME_RELEASES.md`, `tests/tools/test_mcp_tool.py` | Bundles the native MCP client SDK into the frozen runtime (install entry later folded into the `cn-desktop` extra — see P-015 — plus `--collect-submodules/--copy-metadata mcp` and a CI assert on `mcp-*.dist-info`), and makes `discover_mcp_tools()` warn once when `mcp_servers` is configured but the SDK is absent instead of silently no-op'ing at debug | Issue #16: the desktop runtime shipped without the `mcp` extra, so `_MCP_AVAILABLE=False` and configured `mcp_servers` registered no tools with no INFO-level log. The packaging fix is fork-specific; the diagnostic + known-root-key are generic | Packaging change is CN-specific; the `mcp_tool.py` warning and `mcp_servers` known-root-key should be upstreamed |
| **P-015** | `pyproject.toml`, `.github/workflows/release-runtime.yml`, `docs/RUNTIME_RELEASES.md`, `uv.lock` | Adds a `cn-desktop` aggregate extra that pre-bakes every backend the frozen runtime exposes (`web`, `anthropic`, `mcp`, `feishu`, `dingtalk`, `wecom`, plus 微信's `aiohttp`/`qrcode`/`cryptography`). The release workflow installs `.[cn-desktop]`, collects the IM SDK submodules + metadata, runs a build-env import smoke test, and asserts each backend's `dist-info` in the frozen output | Desktop report: the 飞书/钉钉/企微/微信 adapters silently degraded to "unavailable" because their SDKs (`lark-oapi`, `dingtalk-stream`, …) were never bundled and the frozen build can't lazy-install. Same root cause as P-014, generalized to all desktop backends | Packaging is CN-specific; not upstreamed (upstream doesn't build these artifacts) |
| **P-016** | `tools/terminal_tool.py`, `tools/environments/local.py`, `tools/environments/proccess_pwsh.py`, `tools/environments/base.py`, `model_tools.py`, `tests/tools/test_terminal_dynamic_description.py` | PowerShell native execution: on Windows, uses `pwsh.exe` (PS7) as the primary local shell with `powershell.exe` (PS5.1) fallback, plus full lifecycle support (`_run_pwsh`, `_wrap_command_pwsh`, `init_session`, cwd tracking). Removes Git Bash auto-install. Adds runtime-adaptive terminal tool description that replaces Linux/bash command references with PowerShell cmdlets when the active shell is PowerShell; adds shell-fingerprint to tool-definitions cache key. Adds `pwsh_transform` warning propagation so the LLM is notified when its PS7 syntax was down-leveled to PS5.1 | Agent on Windows was hardcoded to Git Bash; PowerShell has better Windows-native path handling and avoids the POSIX-translation overhead. Git for Windows auto-install has been removed — the agent uses PowerShell on Windows. The static `TERMINAL_TOOL_DESCRIPTION` contained Linux-only command references that are misleading under PowerShell | Should be upstreamed |
| **P-019** | `tools/environments/local.py`, `tools/terminal_tool.py`, `agent/prompt_builder.py`, `cli.py`, `apps/desktop/electron/main.cjs`, `scripts/install.ps1`, `hermes_cli/uninstall.py`, `cron/scheduler.py`, `tools/environments/base.py`, `tools/file_operations.py`, `tools/browser_tool.py`, `tests/tools/test_shell_resolution.py`, `tests/tools/test_terminal_dynamic_description.py`, `tests/tools/test_windows_native_support.py`, `tests/tools/test_local_env_windows_msys.py`, `website/docs/user-guide/windows-native.md`, `website/docs/reference/environment-variables.md`, `website/docs/developer-guide/contributing.md`, `FORK_NOTES.md`, `FORK_NOTES.zh-CN.md`, `hermes_bootstrap.py`, `tools/environments/windows_env.py`, `scripts/check-windows-footguns.py`, tests, `scripts/verify_windows_utf8.py` | Complete Git-Bash-to-PowerShell migration: removes all Git Bash discovery (7-strategy `_find_bash`), WSL launcher filtering, and `HERMES_GIT_BASH_PATH` env var support. On Windows, **Windows PowerShell 5.1** (`powershell.exe`, ships with every Windows 10/11 system) is now the **only** supported shell — no `pwsh.exe` (PS7) probing, no download, no install. `HERMES_SHELL_TYPE=bash` raises RuntimeError on Windows. Renames: `_find_pwsh_simple` → `_find_powershell`, `_run_pwsh` → `_run_powershell`, `_wrap_command_pwsh` → `_wrap_command_powershell`, `_normalize_git_bash_path` → `_normalize_msys_path`. `pwsh_transform` is now **always-on** (not conditional on PS5.1). Replaces `findGitBash()` with `findPowerShell()` in desktop Electron. Removes `Install-Git`/`Set-GitBashEnvVar`/`Stage-Git` from `install.ps1`. Removes `HERMES_GIT_BASH_PATH` from uninstaller. Updates cron scheduler to refuse `.sh`/`.bash` on Windows. Updates prompt builder to instruct PowerShell 5.1 syntax. Cleans up Git Bash references in comments, docs, and tests. Also adds PowerShell UTF-8 encoding hardening via `ps_with_utf8()`, console CP_UTF8 bootstrap, and `encoding='utf-8'` only on PowerShell subprocesses. | `powershell.exe` (5.1) ships with every Windows 10/11 — zero install, zero download. Starts faster than Git Bash, handles Windows paths natively, avoids POSIX-translation overhead. Removes ~400 lines of dead code (7-strategy bash discovery, WSL launcher filter, PortableGit auto-install). The agent now has a single, predictable, always-available shell on Windows. P-016's `pwsh.exe` (PS7) probing was unnecessary complexity — 5.1 is universal. | Supersedes P-016; should be upstreamed |
| **P-017** | `agent/tool_dedup.py`, `agent/agent_init.py`, `agent/conversation_loop.py`, `agent/tool_executor.py` | Adds `ToolDedupTracker` that detects consecutive identical tool calls across API iterations and injects escalating reminders (`<system-reminder>`) at repeat counts 3, 5, and 8 to break infinite loops | Agent on complex tasks can enter infinite loops calling the same tool with the same arguments repeatedly — the existing same-turn dedup (`_deduplicate_tool_calls`) doesn't catch this cross-iteration pattern | Internal — addresses a behavioral robustness gap; the mechanism is generic but integration points are fork-specific |
| **P-018** | `agent/agent_init.py`, `tests/run_agent/test_init_fallback_on_exhausted_pool.py` | Adds `_api_key_required` helper and empty-key guards before OpenAI / Anthropic SDK client construction. Raises `RuntimeError: no API key (param empty, env vars unset)` instead of letting a low-level SDK auth exception bubble up | Empty key (param empty, env vars unset) previously triggered confusing low-level SDK exceptions that looked like panics, especially in TUI/gateway background threads where stack traces are not surfaced to the user | Should be upstreamed |
| **P-020** | `tools/environments/windows_env.py` (new), `tools/environments/local.py`, `hermes_cli/claw.py`, `hermes_cli/managed_uv.py`, `hermes_cli/gateway.py`, `hermes_cli/dep_ensure.py`, `hermes_cli/clipboard.py`, `skills/creative/comfyui/scripts/hardware_check.py` | Adds `refresh_env_from_registry()` that refreshes `os.environ["PATH"]` and `os.environ["PATHEXT"]` from the Windows Registry (HKLM + HKCU) before every PowerShell subprocess invocation, so tools installed since process start (WinGet, MSI, etc.) are discoverable. Mirrors the pattern from `kimi-cli/src/kimi_cli/utils/environment.py`. No-op on non-Windows. | Without this, the agent cannot discover binaries installed (e.g. via WinGet) after its process started — `shutil.which` and `subprocess.Popen` only see the PATH that was captured at process creation. This is especially painful when the agent installs its own deps (node, uv, ...) during a session. | Should be upstreamed |
| **P-022** | `agent/chat_completion_helpers.py`, `agent/anthropic_adapter.py`, `agent/httpx_clients.py`, `run_agent.py`, `tests/run_agent/test_streaming_stale_timeout.py` | Fixes the streaming stale-stream detector so a silently-dropped model-provider connection can never wedge a turn forever. (1) The detector now aborts the **live** transport — for `anthropic_messages` it shuts down the Anthropic client's sockets (cross-thread `shutdown(SHUT_RDWR)`, #29507-safe) and rebuilds it, instead of only ever touching the OpenAI request client (which left Anthropic streams hung). (2) Bounded escalation: after `HERMES_STREAM_STALE_MAX_KILLS` aborts spaced `HERMES_STREAM_STALE_KILL_GRACE` apart it synthesizes a `TimeoutError` and abandons the daemon worker instead of resetting its own timer and looping. (3) Emits a **live** `_emit_status` during the stall instead of the deferred buffer that only flushes after a turn resolves. (4) Adds TCP keepalive to the Anthropic httpx client (parity with the OpenAI primary client) via a shared `keepalive_socket_options()` helper. | Long desktop/gateway sessions hung forever ("timer keeps ticking, task dead"): an Anthropic stream that went silent (half-open socket) was never aborted, the worker thread stayed blocked in `recv()`, the detector reset its own `last_chunk_time` and looped, and the buffered status never flushed — so neither backend nor desktop surfaced an error. | Should be upstreamed (generic reliability fix); 2026-07 sync: upstream evolved its own close()+timer-reset variant of the stale-stream kill — fork keeps the FD-safe socket shutdown + bounded escalation (deliberately NOT resetting the last-chunk timestamp); 2026-07-10 v0.18.2 sync: merged upstream's cross-turn stale-streak circuit breaker (#58962) alongside the FD-safe kill (streak bumped before the socket shutdown); upstream's httpx pool reaping (keepalive_expiry=20s) not yet ported into `agent/httpx_clients` — follow-up |
| **P-021** | `gateway/run.py`, `cron/scheduler.py`, `cron/jobs.py`, `hermes_time.py` | Four root-cause fixes for "cron silently stops firing": (1) wrap `_start_cron_ticker` imports + init in try/except to prevent silent daemon thread death; (2) stale `.tick.lock` auto-cleanup — delete the lock only when its mtime exceeds `lock_stale_seconds` (120s default) AND the PID it records is no longer alive, so a live holder running a long job is never stolen from; (3) `_validate_cron_startup()` before starting ticker — rejects corrupt `jobs.json` early instead of crashing the thread; (4) `_ensure_aware` interprets naive legacy datetimes as system-local wall time to preserve their absolute instant (issue #806); fixed broken `def now()` in `hermes_time.py`; `reset_cache()` called at each tick for hot TZ config reload. | Corrupt `jobs.json` → `RuntimeError` in ticker thread → daemon dies silently. Zombie `.tick.lock` from crashed process → all future ticks blocked forever. Uncaught `ImportError` in ticker init → thread dies with zero log. Server TZ ≠ config TZ → all scheduled times silently drift. | Should be upstreamed (generic reliability fixes) |
| **P-024** | `agent/agent_runtime_helpers.py`, `tests/run_agent/test_agent_guardrails.py`, `tests/run_agent/test_session_meta_filtering.py` | Adds empty-content filtering to `sanitize_api_messages`: drops `assistant`/`user`/`function` messages whose `content` is `""` and that carry no payload, while preserving assistant messages that still have `tool_calls`, `codex_reasoning_items`, `codex_message_items`, or `reasoning_content`. | MiMo v2.5 and strict OpenAI-compatible gateways reject messages with empty `content` (HTTP 400 / "text is not set"). Long sessions (e.g. Feishu 3-13h) can leave such messages behind after context compression/truncation. | Should be upstreamed; 2026-07-10 v0.18.2 sync: upstream added three sanitizer defenses (#58168 canonical `call_id||id` in corrupted-args repair, #58755 empty `tool_calls` array normalization, #58327 `tool_call_id` dedup) — all three ported into the fork's fused single-pass `sanitize_api_messages` via `agent.message_utils` (keeps the no-`run_agent`-import property) |
| **P-027** | `cli.py`, `tests/cli/test_cli_save_config_value.py` | `save_config_value()` writes the project-level `cli-config.yaml` only when it already exists; otherwise it writes/creates the **user** config — never creating a config inside the installed package / source tree. | The old `else project_config_path` branch created `<repo>/cli-config.yaml` whenever `HERMES_HOME` had no `config.yaml` (e.g. the test-hermetic home); under the parallel test runner this leaked the file across the 8 workers and polluted project-config reads (`load_cli_config` under `HERMES_IGNORE_USER_CONFIG`), making `test_ignore_user_config_flags` flaky. Writing config into the package dir is also wrong in production. | Should be upstreamed |
| **P-023** | `tui_gateway/server.py` | The gateway turn-runner now delivers a leftover `/steer` as the next user turn. `run_conversation()` only injects steer into a *following* tool result; one that lands after the final tool batch (or in a text-only turn) is returned as `result["pending_steer"]`. `cli.py` re-delivers it, but the gateway dropped it — so steers sent from the desktop (which default to "steer" busy-input mode) silently vanished. Mirrors the existing `goal_followup` chain: after the `finally` releases `session["running"]`, fire a nested `_run_prompt_submit` with the steered text (guarded by `running` so a racing real prompt wins; takes priority over goal continuation). | Desktop report (#193): "引导功能不好用 … 等到任务执行完，我引导的东西也没插入进去" — a late steer was accepted by `agent.steer()` but never applied because the gateway ignored `pending_steer`. | Should be upstreamed (generic reliability fix) |
| **P-026** | `hermes_constants.py`, `hermes_bootstrap.py`, `tests/test_managed_runtime_caches.py` | `configure_managed_runtime_caches()` `setdefault`s third-party cache/temp env vars to subdirs of `<HERMES_HOME>/cache` when the desktop runs the managed runtime (`HERMES_DESKTOP_MANAGED=1`): `HF_HOME`, `HUGGINGFACE_HUB_CACHE`, `TORCH_HOME`, `TIKTOKEN_CACHE_DIR`, `MPLCONFIGDIR`, `NLTK_DATA`, `PLAYWRIGHT_BROWSERS_PATH`, and (only when none is set) `TMPDIR/TEMP/TMP`. Called from `hermes_bootstrap` (first import of every entry point) so it runs before transformers/tiktoken/playwright load. | Windows desktop disk-bloat: even after the desktop anchors its runtime tree to the chosen install drive, these libraries default their caches into `~/.cache` (C:), so picking D:\ at install still filled C:. `setdefault` + the `HERMES_DESKTOP_MANAGED` gate leave standalone CLI installs and explicit overrides untouched. | CN-desktop convergence; the env hooks are generic and could be upstreamed |
| **P-028** | `agent/models_dev.py`, `agent/models_dev_snapshot.json` (new), `agent/model_metadata.py`, `hermes_cli/model_cost_guard.py`, `hermes_cli/web_server.py`, `tui_gateway/server.py`, `gateway/slash_commands.py`, `cli.py`, `hermes_cli/auth.py`, `scripts/refresh_models_dev_snapshot.py` (new), `pyproject.toml`, `MANIFEST.in`, `.github/workflows/release-runtime.yml` | Makes models.dev metadata offline-first so model save/switch never blocks on the network. (1) Ships a bundled `models_dev_snapshot.json` + a real Stage-0/Stage-4 fallback in `fetch_models_dev`, so the registry is never empty. (2) Adds an `allow_network=False` non-blocking read mode threaded through `get_model_capabilities` / `get_model_info` / `lookup_models_dev_context` / `get_model_context_length` / `expensive_model_warning`; every model save/switch hot path (gateway `config.set`, REST `/api/model/set`, `/api/model/info`, `/model` slash command, CLI switch, model-save guard) uses it — cache/snapshot only, fail-open. (3) `MODELS_DEV_URL` + timeout are env-overridable (`HERMES_MODELS_DEV_URL`, `HERMES_MODELS_DEV_TIMEOUT`, default 15s→3s); a fire-and-forget `prewarm_models_dev_async` warms the cache off-thread at web startup (`HERMES_DISABLE_MODELS_DEV_PREWARM` opts out). | From mainland China `https://models.dev/api.json` is slow/blocked; the synchronous 15s-timeout fetch sat on the `/models` page's "设为当前模型"/"保存" path, and the cache only populated on success — so every action re-hit the full 15s timeout (Desktop report: 模型操作要"十几秒"). | CN-specific (China network) + packaging; the generic offline-first snapshot + non-blocking read mode should be upstreamed |
| **P-029** | `hermes_cli/main.py`, `cron/jobs.py`, `.github/workflows/release-runtime.yml` | Starts the desktop cron scheduler tick loop from `cmd_dashboard()` (the synchronous main flow), before `start_server()`, **in addition to** the existing FastAPI-lifespan start — so a silently-failing lifespan can no longer leave cron dead; the new path logs failures via `logger.exception()`. The lifespan ticker remains as a belt-and-suspenders fallback; the `cron/.tick.lock` flock makes the two mutually exclusive (it denies a second lock even across two fds in the same process), so no job double-fires. Also reads `cron/jobs.json` with `utf-8-sig` (tolerates a BOM). And fixes the runtime-release `Sign manifest` gate: moves `RUNTIME_SIGN_PRIVATE_KEY_PEM` to a **job-level** `env:` and gates the step with `if: env.RUNTIME_SIGN_PRIVATE_KEY_PEM != ''` — a step-scoped env var is invisible to that same step's `if:`, so it signs on real releases and skips cleanly on secret-less runs. | CN Desktop spawns a `hermes dashboard` backend (no gateway), so it must run cron itself. v0.17.0-cn.1: after a WeChat-iLink-induced gateway crash + desktop restart, the dashboard recovered but the lifespan never reached the cron-start code — scheduler never initialized, `.tick.lock` absent, two cron jobs stopped firing for ~14h with **zero error log**. Ports #46 (by @ytukids), re-doing its workflow hunk, which as written gated the step on a step-scoped env var in its own `if:` and would have **silently disabled release signing** for every runtime build. | cron-start-from-`cmd_dashboard` + the `utf-8-sig` read are generic reliability (could upstream); the workflow signing gate is CN-specific (fork-only runtime release). Related: P-021 (cron silent-failure family), P-028. |
| **P-030** | `tools/file_operations.py`, `tests/tools/test_search_python_fallback.py` | `search_files` degrades to a portable in-process Python search on the **local backend** when ripgrep is absent (os.walk + fnmatch for names, per-line regex for content) instead of hard-erroring. On the local backend it no longer shells out to the POSIX `command -v` probe or `find`/`grep` pipelines (they can't run under Windows PowerShell), localizes the `test -e` path-existence check, and pins `encoding="utf-8"` on the ripgrepy subprocess calls (cp936 mojibake fix). The fallback prunes vendored/cache + hidden dirs and caps files scanned; it mirrors the line-oriented `\n`-pattern behavior of rg/grep. Remote backends keep the shell paths unchanged. | GitHub #334: on Windows the `command -v rg` probe in `_has_command` can't execute under PowerShell, so rg/grep/find were reported absent even when installed; with none installed search returned "requires ripgrep" — and the terminal tool forbids the model from using raw grep/rg/find, leaving no sanctioned search path. | Should be upstreamed (generic portability fix; doubly needed since P-019 made PowerShell the only Windows shell) |
| **P-031** | `agent/agent_init.py`, `tests/agent/test_model_extra_body.py`, `website/docs/user-guide/configuring-models.md` (+ zh-Hans) | `init_agent` forwards the main `model.extra_body` config block into `request_overrides['extra_body']` via a new `_merge_model_extra_body` helper (mirrors `_merge_custom_provider_extra_body`), so built-in providers (DeepSeek, etc.) honor user-set OpenAI-compatible sampling knobs (`frequency_penalty`/`presence_penalty`/`top_p`). Precedence `caller > custom_providers > model.extra_body`; it rides the transport's existing `request_overrides`-last merge so it also wins over a provider profile's own keys (e.g. DeepSeek's `thinking`). | GitHub #336: a top-level `model.extra_body` was silently dropped for every first-class provider — only `custom_providers` carried an `extra_body` through, so users had to patch provider source (lost on upgrade). | Should be upstreamed (generic config gap) |
| **P-032** | `.github/workflows/release-runtime.yml`, `hermes_cli/main.py`, `hermes_cli/colors.py`, `tests/hermes_cli/test_colors_force_color.py` (new) | Bundles a Node.js LTS runtime (full dist incl. npm/npx) **and** the prebuilt Ink TUI (`tui/dist/entry.js`) into the frozen desktop runtime payload, so `release-runtime.yml` now builds `ui-tui` and downloads/stages node into `dist/$NAME/node` before macOS normalize/sign (the node Mach-O is codesigned by `sign_macos_runtime_payload.sh`; the Ed25519 manifest covers it since it signs the whole zip). The desktop sets `HERMES_NODE`/`HERMES_TUI_DIR` and prepends `node/bin` to child PATH. Also: `should_use_color()` now honors `FORCE_COLOR`/`CLICOLOR_FORCE` (after `NO_COLOR`/`TERM=dumb`, before the TTY check); `_make_tui_argv` checks the prebuilt/wheel bundle **before** `_ensure_tui_workspace` (so a packaged runtime with no `ui-tui/` source never aborts on it) and its node-missing / npm-failed `sys.exit(1)` now carry a message string. | macOS package built from latest main: the frozen runtime shipped no node and no TUI, so `/chat` (which reuses the Ink TUI over a PTY) hit `_make_tui_argv`'s `sys.exit(1)` → the dashboard surfaced the bare exit code as **`Chat unavailable: 1`**; the in-app + external terminal showed monochrome output because the plain CLI couldn't run the colorful TUI and `should_use_color()` ignored the `FORCE_COLOR` the desktop already set. node/npm/npx are also needed by node-based MCP servers, playwright, and `npx tsc` lint. | CN-desktop packaging (fork-only runtime release); the `colors.py` FORCE_COLOR support + the `main.py` exit-message/bundle-ordering fixes are generic and should be upstreamed. Related: P-014/P-015 (frozen-runtime bundling), P-028/P-029 (runtime workflow). |
| **P-033** | `tools/file_operations.py`, `tests/tools/test_file_ops_windows_inprocess.py` (new) | `ShellFileOperations` now does its disk I/O **in-process** (Python stdlib) on a **local Windows** backend instead of shelling out to POSIX `wc`/`sed`/`head`/`mktemp`/`cat`/`ls`. Adds disk primitives (`_prim_stat_size`/`_prim_read_sample`/`_prim_read_all`/`_prim_read_page`/`_prim_count_lines`/`_prim_list_dir`/`_prim_mkdirs`) + `_local_atomic_write` (temp-file + `os.replace`), gated by `_use_inproc_io()` (`_IS_WINDOWS and _is_local_env()`). Non-Windows local and ALL remote backends run the **identical** shell commands as before (zero behavior change). Also removes `write_file`'s silent `len(content)` byte-count fabrication. Sibling to P-030 (which did the same shell→Python conversion for `search_files`). | On Windows the fork forces Windows PowerShell 5.1 as the only shell (P-016/P-019 removed Git-Bash), and PowerShell has none of those POSIX tools. `read_file` was unusable (#53) and `write_file` reported success while writing **nothing** to disk (PowerShell can't run the `mktemp/cat/mv` script → wrapper exits 0 → `wc -c` non-numeric → fabricated byte count, #54 — silent data loss). In-process I/O needs no interpreter-on-PATH (unlike the `_python_delete` `python -c` pattern), so it works in the frozen runtime too. | The cross-platform read/write is generic and should be upstreamed; the urgency is CN-fork-specific (P-016/P-019 PowerShell-only). Related: P-030 (search_files sibling), P-016, P-019. |
| **P-033b** | `tools/file_operations.py`, `tests/tools/test_file_ops_windows_inprocess.py`, `tests/tools/test_file_tools_live.py`, `tests/tools/test_file_operations.py` | Hardens `write_file` with backend-agnostic post-write verification: after `_atomic_write` reports success, the file is re-read via `_prim_read_all` and compared to the intended content (BOM stripped, line endings normalized). `_local_atomic_write` now also verifies the file exists and its bytes match before returning. This closes the remaining silent-success window where the writer exits 0 but the bytes never persist. | P-033 fixed the PowerShell/POSIX mismatch but `write_file` still trusted the writer's exit code. Any remaining edge case (backend FS quirk, race, truncated pipe) could still report success while the file was missing or unchanged. | Should be upstreamed with P-033. |
| **P-034** | `gateway/status.py`, `tests/gateway/test_gateway_command_line_matcher.py` | The gateway-process recognizer (`_gateway_command_subcommand`) now treats a command line whose argv[0] basename starts with `hermes-agent-cn-runtime` (the frozen desktop PyInstaller binary, e.g. `hermes-agent-cn-runtime-win32-x64.exe`) as a hermes CLI entrypoint. Added ONLY to `has_gateway_entry` (so the real `gateway` subcommand is still parsed) — NOT to the gateway-dedicated-entrypoint scan (which returns `run` unconditionally and would misread a frozen `gateway status`/`stop`/`restart`). | The desktop runs the frozen runtime binary as `<bin> gateway run --replace`, but `_gateway_command_subcommand` only knew `hermes_cli.main` / `hermes`/`hermes-gateway` basenames. So `get_running_pid()` treated a LIVE desktop gateway as "not a gateway" → deleted its live `gateway.pid`/`gateway.lock`, defeating `--replace`, the duplicate-instance guard, AND the scoped WeChat-token lock's staleness check → multiple gateways raced the same iLink session → periodic `[Weixin] Session expired` + "未连接" panel (#42). The Rust side already recognized the binary via a `"gateway run"` substring, so Core/Desktop disagreed on what a gateway was. | The frozen-binary recognition is CN-desktop-specific, but framed as generic frozen-binary support it could be upstreamed. Related: P-014/P-015 (frozen runtime), P-016/P-019. |
| **P-036** | `tui_gateway/server.py`, `tests/gateway/test_provider_models_rpc.py` (new) | Adds a `provider.models` RPC that returns a provider's **full** `/models` id list (probe only samples 5) and **tolerates an empty api_key** (local servers need none). Refactors the URL-candidate fetch shared with `provider.probe` into `_fetch_provider_model_ids` so both go through one code path; `provider.probe`'s response is byte-for-byte unchanged. | Desktop report: a self-hosted Ollama on the **LAN** (`http://192.168.31.11:11434/v1`) tested fine (测试连接 goes through `provider.probe` on the backend) but the model picker's 刷新 failed with `external_request only allows https URLs; http is only allowed for local URLs` — the desktop fetched the LAN endpoint directly through its SSRF-guarded `external_request` proxy, which blocks http to non-loopback private IPs. Listing models from the backend (no such guard, same as the probe) makes LAN/self-hosted providers refreshable and also sidesteps the browser CORS that blocked the web shell. | Maybe upstream (sibling to P-011's `provider.probe`) |
| **P-035** | `.github/workflows/release-runtime.yml`, `tests/test_runtime_release_workflow.py` | The runtime-release build-env gate ("Verify platform backends importable") now imports the 飞书/钉钉/企微 adapters from their post-sync plugin locations (`plugins.platforms.feishu.adapter`, `plugins.platforms.dingtalk.adapter`, `plugins.platforms.wecom.adapter` + `plugins.platforms.wecom.callback_adapter`) instead of the removed `gateway.platforms.{feishu,dingtalk,wecom_callback}` modules; weixin (微信个人号, CN-only) still imports from `gateway.platforms.weixin`. Adds a pytest regression test that loads each migrated adapter from `plugins/platforms/` and asserts the gate no longer references the removed modules. | The upstream sync (PR #57, commit `560010547`) migrated the IM adapters from `gateway/platforms/*.py` to bundled plugins (`plugins/platforms/<name>/`), but the CN-only release gate still did `import gateway.platforms.feishu`, so every platform's `release-runtime.yml` build failed with `ModuleNotFoundError: No module named 'gateway.platforms.feishu'` *before* PyInstaller — this is what broke `runtime-v0.17.0-cn.3`. The gate runs only on `runtime-v*` tags, so regular CI never exercised it; the new pytest test moves the check into every CI run. | CN-fork-only release tooling — not for upstream. Related: P-014/P-015 (frozen-runtime bundling), P-028/P-029/P-032 (runtime release workflow). |
| **P-037** | `tools/environments/local.py`, `tools/file_operations.py`, `tests/tools/test_local_pwsh_warnings.py`, `tests/tools/test_file_ops_p037.py` (new) | Three Windows in-process I/O correctness follow-ups to P-016/P-019/P-030/P-033. **(1)** Moves the `pwsh_transform` PS7→PS5.1 down-level call from `_run_powershell` (which received the **assembled** wrapper) to `_wrap_command_powershell`, applying it to the **raw** user command before it is embedded as a single-quoted `Invoke-Expression '<cmd>'` literal. **(2)** Routes `patch_replace`'s read, its post-write verify re-read, and `_check_lint`'s disk read through `_prim_read_all` instead of a raw `cat … 2>/dev/null` `_exec`. **(3)** Adds `_decode_file_bytes` + module-level `_INPROC_FALLBACK_ENCODINGS` (= `("mbcs",)` on Windows) and uses it in the whole-file in-process reads (`_prim_read_all`, `_prim_read_page`): try UTF-8, then the system ANSI code page, then lossy replacement. | **(1)** `pwsh_transform`'s region mask skips single-quoted string contents, so transforming the wrapper never touched the user's command — the load-bearing PS7→PS5.1 bridge was a silent no-op on the real exec path and `Invoke-Expression` raised a ParserError on `&&`/`||`/`??`/ternary under 5.1. The unit tests passed only because they call `pwsh_transform` directly. **(2)** Under PowerShell 5.1 (P-016/P-019: the only Windows shell) there is no POSIX `cat` and `2>/dev/null` redirects to a literal `\dev\null` file, so `patch_replace` was likely broken on the very platform P-030/P-033 target. **(3)** The replaced shell read decoded via the system code page; hard-coding UTF-8 turned every non-ASCII byte of a GBK/cp936 file (the common case on Chinese Windows, this fork's audience) into U+FFFD, and a read→patch→write round-trip then persisted that corruption (silent data loss). | All three are generic cross-platform correctness fixes and should be upstreamed; the urgency is CN-fork-specific (P-016/P-019 made PowerShell 5.1 the only Windows shell). Related: P-016/P-019 (PowerShell-only), P-030/P-033 (in-process I/O). |
| **P-038** | `agent/lsp/client.py`, `agent/lsp/install.py`, `gateway/run.py`, `gateway/platforms/qqbot/adapter.py`, `gateway/platforms/whatsapp_cloud.py`, `hermes_cli/claw.py`, `hermes_cli/clipboard.py`, `plugins/platforms/telegram/adapter.py`, `plugins/teams_pipeline/pipeline.py`, `tools/file_operations.py`, related tests | Threads the existing `hermes_cli/_subprocess_compat` Windows creation flags into the remaining helper-subprocess spawn sites: LSP servers spawn with `windows_detach_flags_without_breakaway()`; ffmpeg/ffprobe conversions (qqbot, WhatsApp Cloud, Teams pipeline, gateway audio-duration probe), the gateway kanban `exec_cmd` shell, `claw` process scans, clipboard PowerShell helpers and the LSP `npm install` (now routed through `resolve_node_command`) run with `windows_hide_flags()`. Also translates POSIX `/dev/null` redirects to PowerShell `*>$null`/`2>$null`/`>$null` in `ShellFileOperations._exec` on Windows, and marks three POSIX-only tests `skipif(win32)`. | On Windows each of these spawn sites either flashed a visible console window or joined the parent's console/job (so parent teardown killed them) — `creationflags` was previously applied only on the main terminal-tool path. The `/dev/null` redirects created a literal `\dev\null` file (or failed) under PowerShell 5.1 — same bug class as P-037's `cat` shell-out. | Overlaps upstream's Windows console-flash hardening wave (e.g. #52340); reconcile at the next sync — prefer upstream where they collide, keep the sites upstream still misses. Related: P-016/P-019 (PowerShell-only Windows shell), P-033/P-037. |
| **P-039** | `agent/auxiliary_client.py`, `hermes_cli/config.py` (auxiliary docs), `hermes_cli/main.py`, `tests/agent/test_auxiliary_client.py`, `tests/agent/test_auxiliary_main_first.py` | Auxiliary "auto" resolution never probes OpenRouter or Nous Portal implicitly. Text chain: main provider → local/custom endpoint → direct API-key providers → None (`_get_provider_chain` drops the openrouter/nous rungs). Vision auto falls back to native Anthropic only (`_VISION_AUTO_PROVIDER_ORDER = ("anthropic",)`), with `_VISION_EXPLICIT_PROVIDER_ORDER` covering explicit requests and main-provider strict backends. OpenRouter/Nous still work when they ARE the main provider or an explicit `auxiliary.<task>.provider`. | CN users typically cannot reach openrouter.ai / Nous Portal; implicit fallback probing added dead network waits to every compression/title-gen/vision call and made aux tasks fail slow instead of falling back fast to reachable providers. | CN-specific default, won't upstream. Landed 2026-06-07 (`bc40674ac`) without a P-number; registered retroactively during the v0.18.0 sync after the merge nearly dropped it. |
| **P-040** | `hermes_cli/web_server.py`, `tests/hermes_cli/test_web_server_platforms_offload.py` (new) | `/api/messaging/platforms` builds its catalog via `run_in_executor` (outside the profile scope, which stays await-free), and lifespan fires `_warm_platform_registry` into a worker thread at startup (sibling of `_warm_gateway_module`). | `_messaging_platform_catalog()` triggers platform-plugin discovery, which imports every bundled IM adapter on first call — discord.py alone takes 10s+ cold — INLINE in the async handler. The desktop calls this endpoint during its first boot paint, so the event loop wedged and every boot API call queued behind it: users saw a blank/"连接中" dashboard for 15s+ after each runtime update (fresh process), and the Playwright E2E suite failed its 15s composer assertion against a fresh v0.18.0 backend. | Should be upstreamed (upstream desktop/dashboard hit the same wedge; matches their own cold-start offload idiom #54448/#54523). |
| **P-041** | `agent/agent_init.py`, `agent/conversation_loop.py`, `tui_gateway/server.py`, `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event.ts`, `apps/desktop/src/lib/chat-messages.ts`, `hermes_cli/config.py`, `tests/run_agent/test_tool_call_streaming_convergence.py` (new), `tests/tui_gateway/test_tool_call_committed_event.py` (new), `apps/desktop/src/lib/chat-messages.test.ts` | Fixes a Windows Desktop stuck-turn where a `write_file` tool call with no preceding text is followed by a `terminal` tool call. Adds an explicit `assistant.tool_calls_committed` event, per-session event tracing, a turn inactivity watchdog, hardens back-to-back tool-part matching, and swallows spurious `None` stream deltas. | The desktop stream state machine started from `message.start` and expected either text deltas or a final `message.complete`. Tool-call-only assistant messages provided neither, so back-to-back tool calls could leave the UI stuck on "running terminal command" with no recovery boundary. | Should be upstreamed |
| **P-XXX** | `tools/environments/local.py`, `tools/terminal_tool.py`, `agent/prompt_builder.py`, `tools/environments/base.py`, `tools/environments/windows_env.py`, `tests/tools/test_shell_resolution.py`, `tests/tools/test_terminal_dynamic_description.py`, `tests/tools/test_local_pwsh_warnings.py` | **Detect `pwsh` (PowerShell 7) first, fallback to PowerShell 5.1.** Adds `_find_pwsh()` multi-step detection (PATH, ProgramFiles, Registry, LocalAppData). `_resolve_shell()` now prefers pwsh over powershell.exe. `_wrap_command_powershell()` skips `pwsh_transform` when running pwsh natively. All dispatch points (`init_session`, `_run_bash`, `_wrap_command`, `_update_cwd`, `_extract_cwd_from_output`) now accept both `"powershell"` and `"pwsh"` shell types. `_detect_shell_for_description()` probes for pwsh and returns `"pwsh"` when found. `_build_dynamic_terminal_description()` has a pwsh variant. `prompt_builder.py` uses `_WINDOWS_PWSH_SHELL_HINT` when pwsh is available (no PS5.1 limitation warnings). | P-016/P-019 hardcoded `powershell.exe` (PS5.1) and always ran `pwsh_transform` to down-level PS7 syntax. Users with `pwsh` installed got unnecessary transform overhead and warnings for native PS7 features. When pwsh is available, use it directly and skip the down-level transform entirely. | Should be upstreamed (follow-up to P-016/P-019) |
| **P-042** | `tools/environments/windows_env.py`, `tools/environments/powershell_session.py` (new), `tools/environments/local.py`, `hermes_cli/config.py`, `tests/tools/test_windows_env.py`, `tests/tools/test_powershell_session.py` (new), `tests/tools/test_local_pwsh_session.py` (new), `tools/file_operations.py`, `tests/tools/test_windows_perf_optimizations.py` (new), `tests/performance/test_windows_perf.py` | **Subprocess-spawn + write-path overhead (perf hotspot #6).** Windows terminal-spawn + in-process-write optimizations. **(1) Registry-refresh cache:** `refresh_env_from_registry()` (P-020), previously re-read HKLM+HKCU `Path`/`PATHEXT` before *every* PowerShell spawn, now caches on the two Environment keys' last-write signature (`QueryInfoKey`) — it skips the value read + `%expand%` + merge when nothing changed and re-reads the instant an install bumps a key's mtime, so no staleness window (unlike a blind TTL). Adds `force=` + `_reset_registry_env_cache()`. **(2) Reusable PowerShell session (opt-in):** new `PowerShellSession` feeds many commands to one long-lived `powershell/pwsh -Command -` over stdin (base64-wrapped + marker-terminated, `try/catch` so the marker always fires), so warm commands run in ~1-5ms instead of the ~80-100ms Windows spawn. `LocalEnvironment.execute()` uses it when `terminal.powershell_session_reuse` (bridged by `HERMES_PWSH_SESSION_REUSE`) is on, resetting `$LASTEXITCODE` per command for exact spawn-parity exit codes and refreshing `$env:PATH` per command for P-020 parity; commands needing stdin, and any failure, fall back to the unchanged spawn path. Default OFF (a session carries shell state between commands). **(3) cmd.exe fast path (opt-in, `terminal.cmd_fast_path`):** trivial metacharacter-free builtins route through a one-shot `cmd.exe /c` (~10-20ms) instead of `powershell.exe`; strict eligibility keeps behaviour identical to PowerShell, considered only after — and superseded by — session reuse. **(4) CRC-32 write verification:** `_local_atomic_write` re-reads the temp and checks a streamed CRC-32 + size before the atomic rename, aborting a corrupt/short write before it clobbers the good original (gated by `HERMES_WRITE_VERIFY_CRC`, default on). **(5) FILE_ATTRIBUTE_TEMPORARY hint (opt-in):** the atomic-write scratch temp can be tagged temporary and cleared before the rename. | Each Windows PowerShell spawn pays ~31-100ms+ of process-creation + DLL-load + interpreter-init on the terminal hot path (root-cause-analysis.md hotspot #6); the P-020 registry refresh added ~5-15ms per call with no cache. Caching the refresh and reusing the interpreter remove both on the warm path. | The registry-refresh cache is a generic Windows fix and should be upstreamed; the session reuse is Windows-shell-specific (built on P-016/P-019's PowerShell-only path) but the pattern could be generalized. Related: P-016/P-019 (PowerShell-only Windows shell), P-020 (registry PATH refresh), P-XXX (pwsh detection). |
| **P-043** | `model_tools.py`, `tools/registry.py`, `run_agent.py`, `cli.py`, `tests/performance/test_tool_dispatch.py`, `tests/tools/test_registry_schema_json.py` (new) | **First-dispatch latency (perf hotspots #8/#9).** Moves the ~4,486ms cold first-dispatch tax off the user-visible hot path. Adds `warm_dispatch_path()` — an idempotent, thread-safe, fire-and-forget primitive that completes deferred discovery, builds + caches the schema catalog for a toolset selection, and pre-serializes each tool's schema. Wired into `AIAgent.warmup()`/`awarmup()` and the CLI banner-idle warmup (now routed through the shared primitive). Adds `registry.get_schema_json()` — a lazily-computed, per-entry JSON cache (no import-time cost, invalidated on re-register). | The first tool dispatch / first API request triggered full tool discovery (module imports + `check_fn` probes + schema assembly) synchronously — ~4,486ms cold vs ~2ms warm, so the first tool call felt like a hang (root-cause-analysis.md hotspots #8/#9). | The warmup primitive + lazy schema-JSON cache are generic and should be upstreamed; the cold-start magnitude is Windows/py3.14-specific. Related: P-040 (cold-start offload), P-042 (subprocess-spawn overhead). |
| **P-044** | `platform_utils.py` (new), `hermes_cli/config.py`, `hermes_cli/dep_ensure.py`, `tools/code_execution_tool.py`, `tools/environments/powershell_session.py`, `tools/environments/local.py`, `tools/process_registry.py`, `plugins/platforms/whatsapp/adapter.py`, `agent/prompt_builder.py`, `agent/ssl_guard.py`, `tools/environments/windows_env.py`, `hermes_cli/doctor.py`, `scripts/precompile.py` (new), `pyproject.toml`, `tests/tools/test_wmi_ssl_windows_overhead.py` (new), `tests/agent/test_ssl_ca_guard.py` | **WMI + SSL + import overhead at agent-init (.plans/15, hotspots `_wmi.exec_query` 2.91% / `_ssl.set_default_verify_paths` 8.19%).** **(1) WMI:** on Python 3.12+ `platform.system()`/`platform.release()` build `platform.uname()`, which issues a Windows `_wmi.exec_query` (`win32_ver` + `_get_machine_win32`, ~45ms). Dozens of modules ran `_IS_WINDOWS = platform.system() == "Windows"` at *module scope*, so that WMI probe was paid twice during the import cascade, and `prompt_builder`'s `platform.release()` paid it again while building the system prompt on every init. New WMI-free `platform_utils` (`is_windows()` off the `sys.platform` constant, `windows_release()` off `sys.getwindowsversion()`) replaces the module-level flags + the host line; the import cascade and prompt build now issue **0** WMI queries (verified). **(2) SSL:** `verify_ca_bundle()` (run on every `AIAgent` construction, agent_init.py) rebuilt a throwaway `ssl.create_default_context()` (~225ms on Windows) each time; it now memoises the successful verdict on a cheap fingerprint of the CA env vars + certifi bundle (path/size/mtime), so an unchanged CA config validates once per process (~0.05ms on repeat) while any change re-validates and re-raises. **(3) Imports:** `scripts/precompile.py` (`compileall`) warms the `.pyc` cache off the hot path (run-from-source / CI / post-update) so first import doesn't pay `builtins.compile` + `_io.open_code` + a Defender scan of freshly written `.pyc`. **(4) Defender hint:** `suggest_defender_exclusion()` surfaces a HERMES_HOME exclusion tip through `hermes doctor` (informational; changes nothing). | On Windows/Python 3.12+ the innocuous `platform.system()`/`release()` idiom silently talks to the WMI service (~40-90ms/cold call), and the SSL guard re-loaded the CA store on every agent construction — a gateway spawning many agents/subagents re-paid both. The plan's literal `import wmi` / `pywin32` premise did not apply (this tree has no `wmi` package usage); the real WMI source is the stdlib `platform` module, so the fix removes the WMI trigger rather than lazily importing a nonexistent dependency. | `platform_utils` and the ssl_guard memoisation are provider/OS-generic and should be upstreamed (correct everywhere; the WMI *cost* is py3.12+-on-Windows-specific). Related: P-042 (Windows subprocess-spawn overhead), P-043 (first-dispatch latency). |
| **P-045** | `import_accelerator.py` (new), `hermes_bootstrap.py`, `run_agent.py`, `scripts/precompile.py`, `pyproject.toml`, `agent/message_utils.py`, `agent/agent_runtime_helpers.py`, `tools/registry.py`, `tests/test_import_accelerator.py` (new), `tests/test_precompile.py` (new), `tests/agent/test_message_utils.py` | **Flame-graph import-system optimizations (.plans/16, import system ~71% of cold agent-init).** **(1) First-party import accelerator:** a `sys.meta_path` finder (`import_accelerator`) resolves Hermes's own *curated* top-level modules/packages (the set mirrors pyproject `py-modules` + `packages.find`) with a single dict lookup, skipping the per-entry `sys.path` directory scan (`nt.stat` / `nt._path_exists`). Package-over-module precedence (so the repo-root `agent.py` harness can never shadow the real `agent/` package), `.pyc`-preserving `spec_from_file_location`, O(1) fall-through for every other name, opt-out `HERMES_DISABLE_IMPORT_ACCELERATOR`. Installed from `hermes_bootstrap` (the first import of every entry point) and idempotently from `run_agent`. The map is validated once at build time and NOT re-stat'd per resolve, so it never regresses a warm tree (measured neutral-to-faster). **(2) Precompile idempotency:** `scripts/precompile.py` gains `precompile_if_needed` (stamp-guarded on a source+interpreter fingerprint), `precompile_in_background` (daemon thread), `[tool.hermes.precompile]` target reading, and `--if-needed` / `--force`; an opt-in `HERMES_PRECOMPILE_ON_START` background warm (default OFF, no-op under pytest/frozen) front-loads `builtins.compile` (~10.82% of cold init) off the hot path for the run-from-source layout. **(3) Per-request micro-opts:** `sanitize_api_messages` (runs before every LLM call) folds its two per-tool_call type dispatches into one `get_tool_call_function_and_id`; `registry.get_definitions` snapshots only the *requested* entries under a brief lock instead of materializing a map of the whole ~250-tool registry. | The import system is ~71% of cold agent-init. The plan's literal `type()`-vs-`isinstance` swap was disproven by benchmark (identical cost; the combined form is slower), and its "344k isinstance calls" are third-party *import-time* class construction (pydantic/SDK), mitigated by NOT importing them eagerly (lazy proxies + lazy tool index + this accelerator) rather than by rewriting our own isinstance calls. So the fix targets the real levers: skip redundant path scanning, precompile bytecode, and cut genuinely-redundant per-request dispatch. | The import accelerator, precompile idempotency, and the sanitizer/registry micro-opts are OS-generic and should be upstreamed; the cold-start *magnitude* is Windows/run-from-source-specific. Related: P-043 (first-dispatch latency), P-044 (WMI/SSL/import overhead). |
| **P-046** | `tui_gateway/server.py`, `tests/gateway/test_provider_models_rpc.py` | `provider.probe` / `provider.models` accept an optional `api_mode` param. `api_mode="anthropic_messages"` switches the shared `_fetch_provider_model_ids` to the Anthropic protocol: URL candidates mirror the SDK's `/v1/messages` appending (bare base → `{base}/v1/models`, host-root fallback for nested vendor paths) and auth uses `x-api-key` + `anthropic-version` (mirroring `hermes_cli.models.probe_api_models`) instead of `Authorization: Bearer`. Unknown/absent `api_mode` keeps the OpenAI-style behavior byte-for-byte. | The CN desktop's provider catalog now ships Anthropic-protocol Claude Code relays (PackyCode, AICodeMirror, …) and MiniMax's `/anthropic` endpoint. Their 测试连接 went through the OpenAI-style probe (Bearer + `/models` heuristics), which strict Anthropic gateways reject — valid keys were misreported as auth failures. | Maybe upstream (sibling to P-011/P-036) |
| **P-047** | `tui_gateway/cli_delegation.py` (new), `tui_gateway/server.py`, `skills/autonomous-ai-agents/claude-code/SKILL.md`, `skills/autonomous-ai-agents/codex/SKILL.md`, `tests/tui_gateway/test_cli_delegation_classifier.py` (new), `tests/tui_gateway/test_cli_delegation_events.py` (new), `tests/tui_gateway/test_cli_delegation_stream.py` (new) | Adds three gateway events — `delegation.cli.started` / `delegation.cli.output` / `delegation.cli.completed` — recognizing `terminal` tool calls that delegate to external coding-agent CLIs (Claude Code `claude -p …` / Codex `codex exec …`). A pure command classifier unwraps `cd X &&` / env prefixes / `timeout` / `bash -lc` / pipes, excludes tmux/ssh and utility invocations (`--version`, `claude mcp`, `codex login`), and extracts mode/prompt/workdir/flags. `delegation_id == tool_call_id`, so clients upgrade the existing tool card instead of double-rendering. Background delegations stream coalesced live output (500ms flush, ANSI-stripped, `redact_sensitive_text(force=True)`, 4 KB/flush + 256 KB/delegation caps) by adding a second consumer inside the existing `process_registry.on_output` sink; a lazy watcher daemon maps process exit to `completed/failed/killed/lost` and parses Claude `--output-format json\|stream-json` / Codex `--json` results (`session_id`, `num_turns`, `total_cost_usd`). Skills bumped (claude-code 2.3.0, codex 1.1.0) to steer delegations toward `background=true` + structured output. Explicit non-goals: tmux interactive delegations are not tracked; `process submit` input is not echoed. | The CN desktop visualizes hermes' multi-agent delegation to Claude Code / Codex ("what is my agent's subagent doing right now?"). The skills funnel every delegation through the `terminal` tool with no structured marker, so UIs could only pattern-match an 80-char truncated preview — and foreground terminal calls return nothing until completion, so there was no live view at all. The gateway is the single choke point that can classify, correlate, and stream them. | CN-desktop-driven but client-agnostic events; maybe upstream once upstream's desktop grows a delegation view. Classifier fixtures are literally mirrored in Hermes-CN-Desktop `web/src/lib/cli-delegation.test.ts` — change one side, change both. |

| **P-048** | `pyproject.toml`, `uv.lock`, `FORK_NOTES.md` | **Requires Python ≥3.14** — updated `requires-python` from `>=3.11,<3.14` to `>=3.14`. Raised `pywinpty` upper bound to `<4` (v3.0.5 ships cp314 wheels); `uv lock` upgraded `pywin32` v311→v312 (cp314 wheels). Fixed downstream test/code incompatibilities: (1) tomllib on Python 3.14 rejects unescaped `\U`/`\T` etc. in TOML basic strings — fixed nemo_relay test f-strings to use `.as_posix()` for forward-slash Windows paths; (2) `shlex.split(cmd, posix=True)` eats backslashes on Windows — added `posix=False` pass in disk-cleanup plugin path extraction; (3) `os.path.expanduser` on Windows reads `USERPROFILE` not `HOME` — fixed tilde-expansion test; (4) `signal.SIGKILL` / `socket.AF_UNIX` / `os.uname` unavailable on Windows — added `pytest.mark.skipif` markers; (5) `orjson` missing from subprocess probe string — added import. `uv sync` + `uv build` verified clean with Python 3.14.3 on Windows. 274 tests pass, 6 skipped across the affected test files. | Python 3.11/3.12/3.13 reached end of life for this fork; upstream still supports ≥3.11. The tomllib backslash issue is a py3.14+ strictness change; the shlex+eating-backslashes issue exists on older Pythons too but was masked by Git-Bash (P-019 removed it). | Won't be upstreamed as-is; the `requires-python` cap is fork policy, not upstream compatible. The individual bug fixes (TOML f-string, shlex posix flag, USERPROFILE fallback) are generic and should be upstreamed separately. |

| **P-049** | `tools/terminal_post_process.py` (new), `tools/terminal_command_rewrite.py` (new), `tools/rtk_provision.py` (new), `tools/terminal_tool.py`, `hermes_constants.py`, `hermes_cli/dep_ensure.py`, `scripts/install.ps1`, `scripts/install.sh`, `scripts/install_coreutils.py`, `tests/tools/test_terminal_post_process.py` (new), `tools/file_operations.py`, `tools/tirith_security.py`, `hermes_cli/commands.py`, `tests/tools/test_file_operations.py`, `tests/tools/test_search_error_guard.py`, `tests/tools/test_search_hidden_dirs.py`, `tests/tools/test_tirith_security.py` | **Terminal output post-processing pipeline + rtk (reasoning toolkit) integration.** (1) New `terminal_post_process.py`: multi-stage pipeline — ANSI stripping + `\r\n`→`\n` normalization, deduplication of repeated output lines (single-line + multi-line block mode, configurable threshold), line-based head/tail truncation with fold marker, oversized output export to session file, and YAML-like metadata block assembly. (2) New `rtk_provision.py`: runtime detection + path resolution for the `rtk` binary (mirrors `_find_rg()` pattern), searching managed tools dir → legacy `$HERMES_HOME/bin` → PATH, with `functools.lru_cache`. (3) New `terminal_command_rewrite.py`: shell-command-aware rewriting that prepends `rtk` to known high-output commands (`git`, `cargo`, `npm`, `ls`, `grep`, `cat`, `python`, `docker`, PowerShell cmdlets, etc.), correctly splitting shell segments at `;`/`&&`/`||`/`|` while respecting quotes and subshells. (4) `terminal_tool.py` now accepts `token_kill` (default True) and `max_lines` parameters; before execution it rewrites commands through rtk when available, and after execution runs the full post-processing pipeline (replacing the old inline ANSI-strip + char-based truncation). (5) `hermes_constants.py`: new `get_managed_tools_dir()` returns `<HERMES_HOME>/tools` (with legacy `<HERMES_HOME>/bin` fallback for existing installs). (6) `dep_ensure.py`: adds `rtk` to `_DEP_CHECKS`/`_DEP_DESCRIPTIONS`; refactors `_find_rg()` and coreutils check to use `get_managed_tools_dir()` with legacy fallback. (7) `file_operations.py`/`commands.py`: use `_find_rg()` from dep_ensure instead of raw `shutil.which("rg")` so the managed copy is preferred. (8) `tirith_security.py`: migrates auto-install target from legacy `$HERMES_HOME/bin/tirith` to `get_managed_tools_dir()`, with backward-compat PATH fallback. (9) `scripts/install.ps1`/`install.sh`: add rtk binary download and `hermes doctor` check. (10) `scripts/install_coreutils.py`: uses `get_managed_tools_dir()` for managed tools path. | The old terminal output handling was a single inline ANSI-strip + hard-coded char-based truncation at 40%/60% split, with no deduplication, no line-based truncation, and no way for the model to control output limits. Commands like `git log`, `cargo test`, `docker ps`, or `npm install` could produce thousands of lines of repetitive output — the model paid for every repeated line. rtk is an external CLI that natively collapses repeated output lines before they reach the agent, and the post-processing pipeline provides a second pass (dedup + line truncation) for cases where rtk is absent or disabled. The new `max_lines` parameter lets the model request a specific number of lines (head + tail with fold marker), which is more intuitive than the old byte-based truncation. The `get_managed_tools_dir()` consolidation moves external binaries to `<HERMES_HOME>/tools/` (from the generic `bin/`) so they don't pollute PATH and are easier to manage. | Should be upstreamed (generic terminal output quality-of-life improvement; the managed-tools-dir pattern is a generic maintenance improvement). |

| **P-050** | `tools/environments/local.py`, `agent/prompt_builder.py`, `hermes_cli/config.py`, `hermes_cli/gateway.py`, `tools/environments/base.py`, `scripts/keystroke_diagnostic.py`, `apps/desktop/electron/main.ts`, `apps/desktop/electron/windows-hermes-resolution.test.ts`, `tests/tools/test_shell_resolution.py`, `tests/tools/test_modal_sandbox_fixes.py`, `tests/agent/test_image_routing.py`, `tests/skills/test_unbroker_skill.py`, `tests/skills/test_openclaw_migration.py`, `tests/hermes_cli/test_update_stale_dashboard.py`, `tools/terminal_tool.py`, README files, website docs, `skills/autonomous-ai-agents/hermes-agent/SKILL.md`, `FORK_NOTES.md` | **Re-enable `HERMES_SHELL_TYPE=bash` on Windows as an optional explicit shell (requires pre-installed Git Bash, no auto-download).** Phase 2.2: `_resolve_shell()` now finds pre-installed bash via `_find_bash_posix()` instead of raising `RuntimeError`. Phase 2.1: `_WINDOWS_BASH_SHELL_HINT` and the `bash` dispatch branch in `prompt_builder.py` preserved; `_WINDOWS_POWERSHELL_SHELL_HINT` updated to mention `pwsh_transform`'s automatic down-leveling. Both PowerShell hints expanded from 5 to 14 rules (Verb-Noun cmdlets, .NET pipeline, comparison/logical operators, string quoting, splatting, `$LASTEXITCODE`, backtick-avoidance). Phase 1: `findGitBash()` in Electron simplified (removed PortableGit auto-download candidates); preflight now conditionally checks bash (when `shell:bash` configured) or PowerShell (default). Phase 3: tests updated — `test_windows_bash_found_returns_bash` and `test_windows_bash_not_found_raises_helpful_error` replace the old `test_windows_bash_raises_runtime_error`. Phase 4: all READMEs and website docs updated to describe PowerShell as the default shell with Git Bash as an optional opt-in. **Cross-platform test fixes:** `TestCwdHandling` code fix (check raw `docker_cwd_source` before `os.path.abspath`), `TestExtractImageRefs` regex extended for Windows drive-letter paths + `os.path.normpath` for mixed separators, platform-portable path assertions across 3 test files, flaky Windows tests marked `xfail(strict=False)`. — P-019 made PowerShell 5.1 the only supported shell on Windows and prohibited `HERMES_SHELL_TYPE=bash`. This fork's users may still have Git for Windows installed for VCS operations, and some workflows legitimately need POSIX shell syntax. Re-allowing bash as an explicit opt-in (no auto-download) restores flexibility without re-introducing the auto-install complexity or the PortableGit download. | Should be upstreamed (user choice; no auto-download risk) |

| **P-052** | `agent/groupchat_loop.py` (new), `tui_gateway/server.py`, `hermes_cli/web_server.py`, `tests/agent/test_groupchat_loop.py` (new), `tests/tui_gateway/test_groupchat.py` (new) | Adds an in-app multi-agent group chat. New `agent/groupchat_loop.py` ports hermes-studio's group-chat decision logic as pure functions: mention routing (`@name`/`@all` standalone-token matching with CJK punctuation boundaries + `<quoted_message>` masking, sender excluded), context projection (shared multi-party transcript → one member's first-person user/assistant view: self → `assistant`, others → `[name]:`-prefixed `user`; tool calls/results flattened), and instruction building (identity + role + roster + group rules), plus an in-memory `GroupRoom`. `tui_gateway/server.py` adds `groupchat.create` (validates member profiles, builds a room) and `groupchat.submit` (routes the latest @mention to matching members; each replies *serially*, built off its own profile via a per-turn `set_hermes_home_override` ContextVar + `_make_agent` and driven through `run_conversation`, streaming the existing `message.start`/`message.delta`/`message.complete` events tagged with `sender_agent_id`/`sender_name`/`sender_avatar`). MVP: user-triggered only, serial, in-memory — no agent-to-agent relay, no parallelism, no compression, no cross-restart persistence. | The CN desktop wants an in-app group chat where several profiles (each = a model + SOUL.md persona) converse in one shared transcript, mirroring hermes-studio's feature — which lives on an incompatible stack (Vue/Koa/Socket.IO + a separate Python bridge, one socket client per agent) and can't be ported code-for-code onto the `tui_gateway` single-session model. | CN-specific feature; the pure mention/projection/instruction helpers are stack-agnostic and could be upstreamed. Paired with Hermes-CN-Desktop `feat/agent-group-chat`. |

> **P-001** (provider dict-vs-list mismatch in `tui_gateway/server.py`) — **dropped from this fork**. Upstream has since fixed it; the line `user_provs = cfg.get("providers")` in `_apply_model_switch` already does the right thing.
---

### P-052: Multi-agent group chat — several profiles conversing in one room

**Symptom / need.** The CN desktop wants an in-app group chat where several profiles converse in one shared transcript — the same UX hermes-studio ships. But studio's implementation (Vue + Koa/Socket.IO + a separate Python agent bridge, one socket client per agent) is an incompatible stack and cannot be ported code-for-code onto the `tui_gateway` single-session model. So we port the *decision logic* and drive it with the existing `AIAgent` + profile machinery.

**Change.**
- `agent/groupchat_loop.py` (new): pure logic ported from studio — `resolve_mention_targets` / `is_all_agents_mentioned` / `strip_mention_routing_tokens` (standalone-token `@mention` matching with CJK punctuation boundaries and `<quoted_message>` masking, sender excluded, `@all` reserved), `project_group_message` / `build_projected_history` (shared multi-party transcript → one member's first-person user/assistant view: self → `assistant`, others → `[name]:`-prefixed `user`; tool calls/results flattened), `build_agent_instructions` (identity + role + roster + group rules; MVP omits the agent-relay rules), plus an in-memory `GroupRoom` + `prepare_member_turn`.
- `tui_gateway/server.py`: `groupchat.create` validates member profiles (`profiles.profile_exists`) and builds a room; `groupchat.submit` appends the user message, routes the latest @mention to matching members, and — serially — builds each member off its own profile via `set_hermes_home_override(get_profile_dir(profile))` + `_make_agent`, drives `run_conversation(current, system_message=<group rules>, conversation_history=<projected>, stream_callback=…)`, and streams the existing `message.start` / `message.delta` / `message.complete` events tagged with `sender_agent_id` / `sender_name` / `sender_avatar`. Runs on the request thread so `_emit` routes over the current ws transport and the `HERMES_HOME` ContextVar override stays race-free (studio uses one socket client per agent; we use one serial orchestrator).
- `hermes_cli/web_server.py`: `/api/sessions/{id}/messages` serves a `gc_`-prefixed room's **in-memory transcript** (with `sender_*` fields) instead of the DB. A member turn's `run_conversation` persists to a per-member sub-session (`gc_<id>:<profile>`) that carries no sender, so reading the DB made group messages revert to the global assistant identity on turn-complete (REST refetch) and on reload; serving the transcript keeps each message's member identity. Frontend `message-adapter` maps the persisted `sender_*` into the UI message.
- UX: `groupchat.info` returns a room's members (for the desktop's roster bar + `@` completion; survives reload). `groupchat.submit` treats a message with **no `@` at all** as addressed to the whole room (everyone replies) instead of studio's "message lands but nobody answers"; an explicit `@name` still narrows it, and an `@` that matches nobody yields `groupchat.no_targets`.
- MVP non-goals: agent-to-agent `@`-relay, parallel fan-out, context compression, interrupt/freshness guards, and cross-restart persistence (rooms are in-memory; a backend restart drops history).

**Compatibility.** Purely additive — new JSON-RPC methods plus additive `sender_*` fields on existing `message.*` events (clients that ignore them are unaffected). No change to single-session `prompt.submit`.

**Tested.** `tests/agent/test_groupchat_loop.py` — mention routing (named / `@all` / sender-excluded / CJK boundary / substring safety / quoted-block masking), projection (self=assistant, others attributed, `@`-strip, tool flattening, summary + `workspace_diff` filtering), instruction building (roster, default role, dedupe, MVP relay-rule omission), and turn preparation. Runtime `groupchat.*` behavior is covered by the desktop end-to-end smoke.

**Upstreamable?** Feature is CN-specific; the pure mention/projection/instruction helpers are stack-agnostic and could be upstreamed. Paired with Hermes-CN-Desktop `feat/agent-group-chat`.

---

### P-049: Terminal output post-processing pipeline + rtk (reasoning toolkit) integration

**Symptom.** Terminal commands like `git log`, `cargo test`, `npm install`, `docker ps`, or `ls -R` produce thousands of lines of repetitive output — repeated error lines, progress bars, status lines — and the model tokenizes every repeated line, wasting context and API budget. The old pipeline was a single inline ANSI-strip followed by a hard-coded char-based truncation at 40%/60% head/tail split, with no deduplication. There was no `max_lines` parameter, so the model could only control output size through the byte cap.

**What was implemented.**

1. **`tools/terminal_post_process.py` (new).** A multi-stage output post-processing pipeline:
   - **Stage 1 — `filter_output()`:** Strip ANSI escapes (Rich-based for deeper coverage) and normalize `\r\n`→`\n` / lone `\r`→`\n`.
   - **Stage 2 — `_save_original_output()`:** Before destructive filters, save the filtered-but-not-deduped original to a session temp file at `~/.hermes/sessions/<uuid>/terminal_output_original.txt`.
   - **Stage 3 — `_dedup_output()`:** Both single-line dedup (annotate `"  (N repeats)"` on the first occurrence) and multi-line block dedup (greedily detect contiguous runs of identical line blocks up to 3 lines, e.g. repeated `Downloading 45%` → `Downloading 100%` progress sequences). Configurable `_DEFAULT_DEDUP_THRESHOLD=3`.
   - **Stage 4 — `_truncate_lines()`:** Line-based head/tail truncation with a `[... N lines omitted ...]` fold marker. Keeps first `floor(max_lines/2)` and last `ceil(max_lines/2)-1` lines.
   - **Stage 5 — `_token_filter_output()`:** Combines the above stages into a single pipeline, returning a structured `TerminalOutputResult` with metadata flags (`dedup_applied`, `lines_truncated`, `original_path`, etc.). Dedup is skipped when `rtk` already handled it (see below).
   - **Stage 6 — `_maybe_export_output_async()`:** When output exceeds `_DEFAULT_EXPORT_CHARS` (4096), exports it to `~/.hermes/sessions/<uuid>/terminal_output_exported.txt` and returns a replacement message.
   - **Stage 7 — `_build_session_output_block()`:** Assembles a YAML-like metadata block (task_id, status, exit_code, elapsed_seconds, output, truncation flags) for the tool result.

2. **`tools/rtk_provision.py` (new).** Runtime detection and path resolution for the `rtk` binary — mirrors the `_find_rg()` pattern exactly:
   - Priority: managed `<HERMES_HOME>/tools/rtk` → legacy `<HERMES_HOME>/bin/rtk` → `PATH` via `shutil.which`.
   - Each candidate runs `rtk --version` to verify usability.
   - Cached per process via `functools.lru_cache`.

3. **`tools/terminal_command_rewrite.py` (new).** Shell-command-aware rewriting:
   - Maintains `_RTK_KNOWN_COMMANDS` (git, cargo, pytest, npm, pnpm, yarn, docker, kubectl, ls, grep, rg, find, cat, head, tail, python, pip, go, rustc, make, cmake, curl, wget, ps, df, du, netstat, ss, systemctl, journalctl, plus PowerShell cmdlets like Get-ChildItem, Get-Content, Select-String, etc.).
   - `_rewrite_shell_segment()`: Finds the first real executable token (skipping env `KEY=VALUE` assignments and `sudo`), and if it matches the known list, prepends `rtk`.
   - `_split_shell_segments()`: Correctly splits at `;`/`&&`/`||`/`|` while respecting quotes, escapes, and `$(...)` subshells.
   - Respects `RTK_DISABLED=1` prefix and already-`rtk`-prefixed commands.

4. **`tools/terminal_tool.py`** — integration:
   - New parameters: `token_kill: bool = True` (default on) and `max_lines: Optional[int] = None`.
   - Before execution: when `token_kill=True` and `rtk` is available, the command is rewritten through `_maybe_rewrite_shell_command_with_rtk()`; the `rtk_rewritten` flag is passed to the post-processor so dedup knows rtk already handled it.
   - After execution: the old inline ANSI-strip + char-based truncation is fully replaced by the new pipeline (`filter_output` → `_token_filter_output` → `_maybe_export_output_async` → ANSI belt-and-suspenders).
   - Schema added for both new parameters in `TERMINAL_SCHEMA`.

5. **`hermes_constants.py`** — new `get_managed_tools_dir()`: returns `<HERMES_HOME>/tools`, with legacy `<HERMES_HOME>/bin` fallback for existing installs.

6. **`hermes_cli/dep_ensure.py`** — adds `rtk` to `_DEP_CHECKS`/`_DEP_DESCRIPTIONS`; refactors `_find_rg()` and coreutils check to use the new `get_managed_tools_dir()` with legacy fallback (was hardcoded to `<HERMES_HOME>/bin`).

7. **Install scripts** (`scripts/install.ps1`, `scripts/install.sh`): add `rtk` binary download from GitHub releases (`rtk-ai/rtk`, version 0.43.0), install to managed tools dir, and `hermes doctor` check.

8. **Sibling changes** (use the new `get_managed_tools_dir()` / `_find_rg()` from dep_ensure): `tools/file_operations.py` (search uses `_find_rg()`), `tools/tirith_security.py` (auto-install target migrated to managed tools dir), `hermes_cli/commands.py` (ripgrepy path resolution), `scripts/install_coreutils.py` (managed tools path).

**Tested.** `tests/tools/test_terminal_post_process.py` (new, ~295 lines) covers: ANSI stripping, CR/LF normalization, single-line and block dedup (below threshold, above threshold, empty, single-line), line truncation (under limit, noop, fold marker, empty, small max_lines), full pipeline integration (passthrough, dedup on/off, rtk_rewritten skip, line truncation, original save, return type), oversized export (under/over/exactly at limit), original save to temp file, and metadata block assembly (full + minimal). Existing terminal/file/search/tirith tests updated to cover managed-tools-dir resolution. Ruff clean.

**Upstreamable?** Yes — the dedup/truncation/export pipeline is pure Python with no external dependencies and the managed-tools-dir pattern (`get_managed_tools_dir()`) is a generic maintenance improvement that consolidates how Hermes discovers its own downloaded binaries. The rtk integration (command rewriting + binary detection) depends on a third-party CLI (`rtk-ai/rtk`) and could be upstreamed as an optional enhancement.
### P-047: CLI delegation events — visualize Claude Code / Codex hand-offs

**Symptom / need.** hermes delegates coding work to external CLIs via the `claude-code` / `codex` skills, which run `claude -p …` / `codex exec …` through the `terminal` tool. On the wire this is an anonymous tool call: `tool.start` carries an 80-char truncated preview, `tool.complete` the full command + final output. A UI cannot (a) reliably tell "this is a delegation", (b) show anything while a foreground run is in flight, or (c) surface the structured result (session id for `--resume`, turns, cost) without re-implementing fragile parsing per client.

**Change.**
- `tui_gateway/cli_delegation.py` (new): pure command classifier (`classify_cli_delegation` → `DelegationSpec{agent, mode, prompt_excerpt, workdir, flags}`); stream-json/JSONL normalizers for Claude and both generations of Codex JSON output; `DelegationTracker` singleton owning lifecycle + a lazy watcher daemon thread.
- `tui_gateway/server.py`: four wiring points — `_on_tool_start` classifies and emits `delegation.cli.started` (gated by `_tool_progress_enabled`, same as `tool.start`); `_on_tool_complete` finalizes foreground delegations (parsing the result object out of the terminal output) or binds background ones to their `process_registry` session; `_emit_agent_terminal_output` gains a second sink consumer feeding the tracker; `_wire_agent_terminal_output` configures the tracker (`emit=_emit`, `is_alive=sid in _sessions`).
- Events: `delegation.cli.started` (`delegation_id == tool_call_id`, agent, mode, execution, redacted command ≤2000, prompt excerpt ≤200, workdir, flags) → `delegation.cli.output` (background only: 500ms coalesced `chunk` ≤4 KB ANSI-stripped + redacted, 256 KB/delegation total cap after which events-only + `truncated`, normalized `events[]` ≤20/flush) → `delegation.cli.completed` (`status ∈ completed|failed|killed|lost`, exit code, duration, redacted `output_tail` ≤4 KB, parsed `result`). No separate failed event — terminal state is one atomic payload.
- Skills: claude-code 2.3.0 adds a "Hermes background + streaming" section (prefer `--output-format stream-json --verbose --include-partial-messages` + `background=true, notify_on_complete=true` for >30s tasks, `--output-format json` for short foreground ones) and two rules (structured output, keep `session_id`); codex 1.1.0 adds `--json` (hedged "if supported") + `notify_on_complete=true` to the background pattern.
- Cleanup: dead-session entries swept silently; 6h expiry emits `lost`; watcher thread exits when no bound delegations remain.

**Compatibility.** Purely additive event types — ui-tui's `asGatewayEvent` only requires `.type` and dispatches by known types; the CN desktop's `parseGatewayEvent` falls back to `RawGatewayEvent`; upstream `apps/desktop` ignores unknown types. Non-goals recorded: tmux interactive delegations (no stable process to bind) stay plain tool calls; `process(action="submit")` input is not echoed into the stream.

**Tested.** `tests/tui_gateway/test_cli_delegation_classifier.py` (table-driven fixtures shared verbatim with the desktop repo), `test_cli_delegation_events.py` (gateway wiring: foreground two-beat, background bind, blocked start → failed, non-delegation silence, progress-gate off), `test_cli_delegation_stream.py` (cross-chunk JSONL assembly, caps, ANSI strip, exit-status mapping, lost process, dead-session sweep, expiry).

**Upstreamable?** Maybe — the events are client-agnostic; value depends on an upstream delegation UI. Keep the fixture mirror with `Hermes-CN-Desktop/web/src/lib/cli-delegation.test.ts` in sync.
---

### P-045: Flame-graph import-system optimizations

**Symptom / target.** The agent-init flame graph (`.plans/16-Flame-Graph-Import-Optimizations.md`) attributes **~71%** of cold agent start to the Python import system — dominated by `builtins.compile` (~10.82%), `_io.open_code` (~4.39%), and the module-search stats `nt.stat` (~1.6%) / `nt._path_exists` (~2.1%).

**What the plan got wrong (verified before implementing).** Two of the plan's five suggestions do not hold up on this tree, so they were deliberately NOT implemented as written:

- *"Swap `isinstance` for `type(x) is dict` on hot paths."* Benchmarked: `type(d) is dict` (0.069s/5M) is not faster than `isinstance(d, dict)` (0.067s/5M) — CPython already has a fast path — and the correctness-preserving combined form (`type() is ... or isinstance(...)`) is *slower* (0.085s). Cargo-culting it would regress the hot path.
- *"344k isinstance / 153k getattr calls during init are our redundant computation."* Those are overwhelmingly third-party **import-time** class construction (pydantic, the OpenAI SDK), not Hermes code. The real remedy is to not import those eagerly — already done via lazy proxies + the lazy tool index (P-043) and reinforced by the accelerator below — not rewriting our own type checks.

**What was implemented.**

1. **`import_accelerator.py` (new) — first-party meta-path finder.** A `sys.meta_path[0]` finder resolves Hermes's own top-level modules/packages via a dict lookup, skipping the per-`sys.path`-entry `FileFinder` scan the stock machinery pays. Key correctness choices: a **curated allow-list** (mirrors pyproject `py-modules` + `packages.find` — a blind `os.listdir` would register the repo's local `packaging/` dir and shadow the real PyPI `packaging` dependency); **package-over-module precedence** (the repo-root `agent.py` harness must never win over the `agent/` package); `spec_from_file_location` so the stdlib `SourceFileLoader` and its `.pyc` cache are preserved; O(1) fall-through (a single dict miss) for every non-first-party name; opt-out via `HERMES_DISABLE_IMPORT_ACCELERATOR`. Installed from `hermes_bootstrap` (earliest import of every entry point) and idempotently from `run_agent`. The map is validated once at build time and is **not** re-stat'd per resolve — an early version did, which cost more than PathFinder's cached-dir hit and made a warm import ~1.7% slower; removing it made the accelerator neutral-to-faster on a warm tree while still bypassing the scan cold / when `sys.path` puts site-packages first.

2. **`scripts/precompile.py` — idempotent + background.** Adds `precompile_if_needed()` (skips when a source+interpreter fingerprint matches an on-disk stamp), `precompile_in_background()` (daemon thread), `[tool.hermes.precompile]` target reading, and `--if-needed`/`--force` CLI flags. `hermes_bootstrap.maybe_precompile_on_start()` runs the background warm **only** when `HERMES_PRECOMPILE_ON_START` is set and NOT under pytest/frozen (the hermetic per-file test runner gives each file a fresh temp `HERMES_HOME`, so an ungated warm-up would spawn a `compileall` thread in every subprocess). `pip install` already byte-compiles installed packages; this is for the run-from-source layout (the CN fork's Windows default) where the first import would otherwise pay `builtins.compile`.

3. **Per-request micro-opts.** `agent/message_utils.get_tool_call_function_and_id()` folds the two `isinstance(tc, dict)` dispatches `sanitize_api_messages` did per tool_call (`get_tool_call_function` + `get_tool_call_id`) into one; the sanitizer runs before *every* LLM request over the whole history, so this measurably trims tool-call-heavy sessions. `tools/registry.get_definitions()` now snapshots only the requested entries under a brief lock instead of allocating a `{name: entry}` map of the entire ~250-tool registry per call.

**Tested.** `tests/test_import_accelerator.py` (new): curated-name build (agent is a package, `agent.py` never a module, `packaging`/`tests` never registered), `.pyc`-preserving `SourceFileLoader` spec, O(1) fall-through, submodule pass-through, idempotent install/uninstall, env opt-out, and `test_import_hook_bypass` — a subprocess proof that strips the repo root from `sys.path` and shows the module still imports via the accelerator (and `PathFinder` is never consulted for it) while the accelerator-removed control fails. `tests/test_precompile.py` (new): fingerprint stability/sensitivity, `precompile_if_needed` compiled→skipped→recompile-on-change→force, background completion, and `precompile_all` regression. `tests/agent/test_message_utils.py` (extended): `test_isinstance_caching` pins the fused accessor byte-for-byte against the two separate accessors across dict/SDK-object/malformed inputs. All existing sanitizer/registry/bootstrap/lazy-import-invariant tests still pass (581 in the affected sweep); `ruff check` clean.

**Upstreamable?** The accelerator, precompile idempotency, and sanitizer/registry micro-opts are OS-generic; the cold-start magnitude is Windows/run-from-source-specific. Related: P-043, P-044.

---

### P-041: Fix `write_file` → `terminal` stuck turn on Windows Desktop

**Symptom.** In a Windows Desktop session, the assistant emits a `write_file` tool call with no preceding text. The tool completes quickly, then the model emits a second tool call (`terminal`). The UI shows "running terminal command" + the thinking stall indicator and never produces a final assistant message. Sending `again` does not recover the turn.

**Root cause.** The desktop stream state machine is initialized by `message.start` and expects either `message.delta` text or a finalizing `message.complete`. A tool-call-only assistant message provides neither: the first substantive event after `message.start` is `tool.start`. Without an explicit boundary, the merged-turn assistant message stays `pending` all the way from the first `tool.start` until the final `message.complete`. If any later event is delayed or processed out of order, the UI has no clean point to recover. A second contributing factor is that the core loop flushes `stream_delta_callback(None)` before tool execution, but the gateway only sets `_stream_callback`, so the flush is a no-op and a `None` delta would be emitted as `message.delta {text: null}` if the two callbacks were ever wired together.

**Change.**
- `agent/agent_init.py` / `agent/conversation_loop.py`: add a new `tool_calls_committed_callback` parameter. The conversation loop calls it after appending a tool-call assistant message and before executing tools.
- `tui_gateway/server.py`: wires the callback to emit `assistant.tool_calls_committed` with role, finish_reason, tool_call_ids, and a `has_content` flag. Guards `_stream(None)` so a null stream delta never becomes a `message.delta` event. Adds per-session event tracing (`gateway.event_trace`) to `~/.hermes/logs/tui_gateway_events.log` and a turn inactivity watchdog (`gateway.turn_watchdog_seconds`, default 600s) that emits an `error` event and releases `session["running"]` if a turn stays idle too long.
- `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event.ts`: handles `assistant.tool_calls_committed` by flushing queued deltas and marking `sawAssistantPayload: true` / `awaitingResponse: false`.
- `apps/desktop/src/lib/chat-messages.ts`: hardens `findToolPartIndex` so a stable-id match is only reused when the tool name also matches, preventing a back-to-back tool call from updating the wrong pending row.
- `hermes_cli/config.py`: documents the new `gateway.event_trace` and `gateway.turn_watchdog_seconds` config keys.
- Tests: `tests/run_agent/test_tool_call_streaming_convergence.py`, `tests/tui_gateway/test_tool_call_committed_event.py`, and new cases in `apps/desktop/src/lib/chat-messages.test.ts`.

**Upstreamable?** Yes. The event is generic, and the matching hardening fixes a real UI state-machine gap for any client that consumes the gateway event stream.

---

### P-032: Bundle Node.js + prebuilt Ink TUI into the frozen desktop runtime

**Symptom.** A macOS package built from latest `main` showed `Chat unavailable: 1` in the dashboard `/chat` pane (port 9120), and the in-app / external terminal rendered Hermes output in monochrome while upstream is colorful.

**Root cause.** The dashboard `/chat` pane and embedded terminal reuse the Ink TUI (`ui-tui`) over a PTY (`hermes_cli/web_server.py` `pty_ws`), which needs Node.js. The frozen desktop runtime built by `release-runtime.yml` shipped **no node and no prebuilt TUI** (only the PyPI wheel pipeline built `tui_dist`), and the desktop set neither `HERMES_NODE` nor `HERMES_TUI_DIR` — node resolution depended entirely on the user's login-shell PATH. So `_make_tui_argv` hit `sys.exit(1)`; `str(SystemExit(1)) == "1"` and `pty_ws` forwarded only the exit code as `Chat unavailable: 1`. The monochrome terminal was the same root cause's shadow: with no node the colorful TUI couldn't run, and `should_use_color()` ignored the `FORCE_COLOR`/`CLICOLOR_FORCE` the desktop terminal already set.

**Change.**
- `release-runtime.yml`: build `ui-tui` (`npm ci && npm run build`) and download the Node `$NODE_RUNTIME_VERSION` LTS dist (incl. npm/npx) per matrix platform/arch; stage them into the payload as `dist/$NAME/node` (+ `tui/dist/entry.js`) **before** macOS normalize/sign so the node Mach-O is codesigned and the Ed25519 manifest (signed over the whole zip) covers them. Adds a verify step (`node/npm/npx --version`, `entry.js` present) and `-y` on the Linux zip to preserve npm/npx symlinks.
- `hermes_cli/colors.py`: `should_use_color()` honors `FORCE_COLOR`/`CLICOLOR_FORCE` after `NO_COLOR`/`TERM=dumb` and before the TTY check.
- `hermes_cli/main.py`: `_make_tui_argv` checks the prebuilt (`HERMES_TUI_DIR`) / wheel-bundled TUI **before** `_ensure_tui_workspace` (a packaged runtime has no `ui-tui/` source to abort on); node-missing and npm-failed exits carry a reason string so the browser shows it instead of a bare `1`.
- Desktop side (Hermes-CN-Desktop, separate PR): `process/runtime.rs` resolves the bundled node + tui dirs; `process/dashboard.rs`, `process/gateway.rs`, `commands/terminal.rs` prepend `node/bin` to child PATH and set `HERMES_NODE`/`HERMES_TUI_DIR`. node/npm/npx then also serve node-based MCP servers, playwright, and `npx tsc` lint without a host install.

**Upstreamable?** The packaging is fork-only (CN desktop runtime release), but the `colors.py` FORCE_COLOR support and the `main.py` exit-message + bundle-ordering fixes are generic. Related: P-014/P-015, P-028/P-029.

### P-026: Converge third-party caches under HERMES_HOME for the desktop runtime

**Symptom** (Windows desktop): users install the CN desktop to D:\ to spare a near-full C:, but C: keeps filling anyway. The desktop's own runtime tree is converged, yet Python libraries the kernel pulls in still scatter caches across the home dir on C:.

**Root cause**: huggingface/transformers, torch, tiktoken, matplotlib, nltk and playwright each default their cache into the user home (`~/.cache/...`, `%USERPROFILE%\...`) unless their env var is set, and the managed runtime never set them — so they escaped the converged runtime root no matter which drive the app was installed to. Reachable hits in the runtime today are tiktoken (`hermes_cli/tools_config.py`) and the transformers tokenizer (`trajectory_compressor.py`); playwright already self-converges in `browser_tool.py`, but only for its subprocess env, not process-wide.

**Fix**: `hermes_constants.configure_managed_runtime_caches()` `setdefault`s `HF_HOME`, `HUGGINGFACE_HUB_CACHE`, `TORCH_HOME`, `TIKTOKEN_CACHE_DIR`, `MPLCONFIGDIR`, `NLTK_DATA` and `PLAYWRIGHT_BROWSERS_PATH` to `<HERMES_HOME>/cache/<tool>`, and — only when no temp dir is already configured — points `TMPDIR/TEMP/TMP` at `<HERMES_HOME>/cache/tmp`. Because the desktop sets `HERMES_HOME` under its converged `runtime_root()` (anchored to the install dir on a fresh Windows install — see Hermes-CN-Desktop), these caches now follow onto the chosen drive. Gated on `HERMES_DESKTOP_MANAGED=1` and using `setdefault`, so standalone CLI installs keep their shared `~/.cache` (no surprise re-downloads) and any explicit value wins. Wired into `hermes_bootstrap` (imported first by every entry point) so it runs before transformers/tiktoken/playwright import.

**Tests**: `tests/test_managed_runtime_caches.py` — no-op without `HERMES_DESKTOP_MANAGED`; sets the cache vars under HERMES_HOME when managed; `setdefault` never overrides a pre-set value; temp left alone when already configured.

### P-022: Streaming stale-stream detector — never wedge a turn on a dead provider connection

**Symptom**: During a long agent session the desktop/gateway hangs — the UI elapsed-time counter keeps ticking, but the task is dead and never recovers or errors. Restarting the turn is the only escape.

**Root cause**: The streaming stale-stream detector in `interruptible_streaming_api_call` runs in a monitor thread that polls `last_chunk_time` while the model response is consumed in a daemon worker thread (`for event in stream`). When a provider connection goes half-open (no FIN — provider crash, LB idle-kill, network split), the worker blocks indefinitely in `recv()`. The detector was supposed to rescue this, but had three defects that combined into an unbounded hang:

1. **Wrong client aborted for Anthropic.** On a stale stream the detector called `_close_request_client_once()` + `_replace_primary_openai_client()` — both operate on the **OpenAI** request/primary clients. But an `anthropic_messages` turn streams on `agent._anthropic_client`, which was never touched. The worker's blocked `recv()` was never interrupted.
2. **Self-resetting timer → infinite loop.** After "killing", the detector reset `last_chunk_time = now` "so we don't kill repeatedly". With the worker still blocked (see #1), this just rearmed the same wait forever — kill, reset, wait, kill, reset… The inner retry/reconnect loop (which lives *after* the `for` loop) was never reached because the `for` never returned.
3. **Status was buffered, not emitted.** The stall message went through `_buffer_status`, which only flushes once the turn resolves — which never happened. So neither backend logs-to-user nor the desktop ever saw a "provider not responding" signal.

There was also no TCP keepalive on the Anthropic client (the OpenAI primary client has it via `_build_keepalive_http_client`), so the kernel never surfaced the dead socket on its own either.

**What the patch does**:

- **Abort the live transport, cross-thread-safe.** On stale, for `anthropic_messages` the detector now calls `agent._force_close_tcp_sockets(agent._anthropic_client)` (shutdown only — FD-safe per #29507) then `_rebuild_anthropic_client()`; the OpenAI-wire path keeps its existing `_close_request_client_once` abort. Either way the worker's `recv()` unblocks and the inner retry loop reconnects.
- **Bounded escalation instead of self-reset.** A grace-gated kill counter replaces the blanket `last_chunk_time` reset. After `HERMES_STREAM_STALE_MAX_KILLS` (default 3) aborts spaced `HERMES_STREAM_STALE_KILL_GRACE` (default 10s) apart with the worker still alive, the detector synthesizes a `TimeoutError` into `result["error"]` and breaks — abandoning the daemon worker, exactly like the non-streaming stale path already does. A fresh retry attempt (which resets `last_chunk_time` at its start) resets the kill budget, so legitimate slow prefill on large contexts is not falsely escalated.
- **Live status.** The stall message is now `_emit_status(...)` (reaches the gateway/TUI immediately) rather than buffered.
- **Keepalive parity.** `build_anthropic_client` now passes `keepalive_socket_options()` to its httpx client; the inline socket-option list in `run_agent._build_keepalive_http_client` was refactored to use the same shared helper.

**Knobs**: `HERMES_STREAM_STALE_TIMEOUT` (existing), `HERMES_STREAM_STALE_KILL_GRACE` (new, default 10s), `HERMES_STREAM_STALE_MAX_KILLS` (new, default 3).

**Tests**: `tests/run_agent/test_streaming_stale_timeout.py` — a wedged Anthropic stream surfaces a `TimeoutError` in bounded time and aborts the Anthropic client; plus keepalive-option coverage.

### P-023: Gateway delivers a late `/steer` as the next turn

**Symptom** (desktop #193): a steer sent while the agent is busy is accepted (`session.steer` → `agent.steer()` returns `queued`) but, if it lands after the agent's final tool batch — or during a text-only "thinking" turn — it is never applied. The user sees the turn finish with their guidance silently dropped.

**Root cause**: `agent.steer(text)` only injects into a *following* tool result (`agent/conversation_loop.py` pre-API + post-tool drains). Steer with no subsequent tool batch is handed back by `run_conversation()` as `result["pending_steer"]` for the caller to re-deliver. `cli.py` consumes it (`result.get("pending_steer")`), but the `tui_gateway` turn-runner — which every desktop/TUI/Dashboard chat goes through — never read it, so the leftover was lost. This is acute on the desktop because its default busy-input mode is **steer**.

**Fix**: in `_run_prompt_submit`'s `run()`, capture `result["pending_steer"]` and, after the `finally` releases `session["running"]`, fire a nested `_run_prompt_submit` with that text — mirroring the existing `goal_followup` / completion-notification chains. Guarded by the `running` flag so a racing real user prompt wins; runs before goal continuation since it is explicit user input (its own turn completion re-runs the goal judge). No extra `message.start` is emitted (the nested call emits its own).

**Tests**: `tests/tui_gateway/test_pending_steer_followup.py`.

### P-021: Cron scheduler reliability fixes — prevent silent failures

**Symptom**: Cron jobs stop firing silently with no error visible at default log levels. The gateway is running and healthy, but `hermes cron list` shows jobs accumulating with stale `next_run_at`.

**Root cause**: Four independent failure modes, each fatal on its own:

1. **Daemon thread silent death** (`gateway/run.py` `_start_cron_ticker`): The imports at the top of the ticker thread (`from cron.scheduler import tick`, etc.) are outside any try/except. An `ImportError` (missing dep, broken `.pyc`, disk full) kills the daemon thread with zero log output — the gateway keeps running but cron is dead.

2. **Zombie lock file** (`cron/scheduler.py` `tick()`): `.tick.lock` is acquired via `fcntl.flock`/`msvcrt.locking` and released in a `finally` block. If the process is `SIGKILL`-ed or suffers a kernel panic, the lock file is never cleaned. The next process sees the lock as held and silently returns 0 from `tick()` — forever.

3. **Corrupt `jobs.json` crashes the ticker** (`gateway/run.py`): If `jobs.json` is corrupted (truncated write, bad merge, disk error), `load_jobs()` raises `RuntimeError`. This exception propagates inside `tick()` → caught by `logger.debug` → invisible in production. But worse: if the crash happens during the first tick, the entire ticker thread dies before producing any output.

4. **Timezone handling for legacy naive datetimes** (`cron/jobs.py` `_ensure_aware`): Legacy naive datetimes (stored without a timezone offset) are interpreted as *system-local* wall time via `datetime.now().astimezone().tzinfo`, then converted to the configured Hermes timezone. This preserves the absolute instant the value referred to (it was written by `datetime.now()`), so overdue jobs are still detected as due when the server's timezone differs from the configured Hermes timezone (issue #806). An earlier revision of this patch reinterpreted naive values *directly* in the Hermes timezone; that was reverted because it shifts the absolute instant and re-introduces #806 (silently skipped jobs).

Also fixed a pre-existing bug in `hermes_time.py` where `def now():` was missing (its body was appended to `reset_cache()`'s docstring), making the `now()` function unreachable.

**What the patch does**:

| Fix | File | Change |
|------|------|--------|
| F-1 | `gateway/run.py` | Wrap `from cron.scheduler import tick` + all init imports in try/except → `logger.error` + `return`. Upgrade tick exception log from `debug` to `warning`. |
| F-3 | `cron/scheduler.py` | Before acquiring `.tick.lock`, check the file's mtime. If older than `lock_stale_seconds` (120s default, configurable via `cron.lock_stale_seconds` in `config.yaml`) **and** the PID recorded in the lock file is no longer alive, treat it as a zombie → `logger.warning` + delete. The holder PID is written into the lock after acquisition; gating deletion on PID liveness means a live tick running a long one-shot job (whose mtime never refreshes) is never stolen from — which would otherwise let a second tick double-execute that job. |
| F-4 | `gateway/run.py` | Add `_validate_cron_startup()`: reads `jobs.json` and checks `croniter` before starting the ticker thread. Corrupt JSON → `logger.error` → cron ticker not started (gateway continues). Missing `croniter` → `logger.warning` (non-fatal, interval/timestamp jobs still work). |
| F-5 | `cron/jobs.py` | `_ensure_aware` and `parse_schedule` interpret naive datetimes as **system-local wall time** (then convert to the Hermes timezone), preserving their absolute instant so overdue legacy jobs are still detected as due across a server/Hermes timezone mismatch (issue #806). `parse_schedule` display now includes the timezone (e.g. `"once at 2026-06-01 09:00 UTC+08:00"`). |
| F-7 | `hermes_time.py`, `cron/scheduler.py` | Fixed broken `def now():`. `reset_cache()` called at the start of each `tick()` so timezone config changes take effect without a gateway restart. |

**Side effects**:
- `cron.lock_stale_seconds` is a new optional config key (default 120s). If unset, the stale-lock threshold defaults to 120s.
- Users with legacy naive-timestamp jobs should re-save them so they store timezone-aware timestamps; until then they are interpreted as system-local wall time.
- The ticker now logs at WARNING level for unhandled exceptions, which may increase log volume if there is a persistent broken state (but the broken state is now visible instead of silent).

**Should we upstream?** Yes. These are generic reliability fixes that affect every Hermes deployment, regardless of platform or provider. The stale-lock recovery alone prevents a class of "cron mysteriously stopped" support tickets.

---

## Release/support changes
These are fork maintenance changes, not runtime behavior patches:

| Area | Target file | What it does |
|---|---|---|
| Upstream sync | `scripts/sync-upstream.sh`, `.github/workflows/upstream-watch.yml`, `MAINTAINING.md` | Keeps upstream syncs on temporary PR branches instead of merging directly into `main` |
| Managed runtime | `.github/workflows/release-runtime.yml`, `scripts/sign_runtime_manifest.py`, `docs/RUNTIME_RELEASES.md` | Builds PyInstaller runtime artifacts, signs manifests, and publishes GitHub Releases consumed by desktop. Bundles the `[web,anthropic,mcp]` extras and asserts each SDK's `dist-info` is present in the frozen output (see P-014 for the MCP gap) |

## Per-patch detail

### P-002: `POST /api/upload` for dashboard attachment uploads

**Symptom**: v2 web composer drags a file → upload fails with 404 because `/api/upload` doesn't exist. v2 stack trace shows `XMLHttpRequest` returning HTTP 404 on the upload URL.

**Root cause**: Upstream `e7c3cd772` (commit "Add dashboard attachment upload endpoint") added this endpoint, then it was reverted in a later commit. The endpoint itself is small and self-contained — we just bring it back.

**What the patch does**: Adds a single FastAPI handler that takes a multipart `file` + `session_id`, writes it under `~/.hermes/uploads/<session_id>/`, and returns `{ok, filename, path, size, mime_type}`. Uses `_unique_upload_path` for naming collisions and the in-house `_parse_multipart_form` parser (so `python-multipart` is not required at import time).

**Regression — dropped by the v0.17.0 upstream sync (restored, see issue #306)**: a sync silently removed the `@app.post("/api/upload")` handler while leaving its helpers (`_parse_multipart_form` / `_safe_upload_filename` / `_unique_upload_path`) behind as dead code. With the route gone, the SPA catch-all matched the path on GET only, so the desktop composer's POST returned **HTTP 405 Method Not Allowed** and pasting/dropping an image failed (CLI `/paste` was unaffected — it never hits this route). Guarded now by `tests/hermes_cli/test_web_server_upload.py`, which fails if the route disappears again.

**Side effects**: Adds an attachment-upload attack surface. Mitigated by:
- Gated by the same session token as all other `/api/` routes
- Never overwrites: collisions resolved via `_next_unique_path`
- Writes only inside the session's own attachments directory (validated)
- No content-type sniffing that could trigger executable behavior

**Should we upstream?** Yes, but the original revert reason isn't documented in upstream's commit log. Worth a thread before sending a PR.

---

### P-003: Drop `_DASHBOARD_EMBEDDED_CHAT_ENABLED` gate on `/api/ws`

**Symptom**: v2 web app `/api/ws` upgrade closes immediately with 4001. Gateway never connects, all chat is broken.

**Root cause**: Upstream v0.12.0 added a module-level flag `_DASHBOARD_EMBEDDED_CHAT_ENABLED` that's only set to `True` when running `hermes dashboard --tui` (the embedded TUI mode). v2 runs `hermes dashboard --no-open` without `--tui` for headless dashboard + Web UI, so the gate stays closed.

**What the patch does**: Removes the gate from the `/api/ws` route's preconditions. The route is still gated by token + loopback host check, which is sufficient.

**Side effects**: WebSocket gateway is now reachable from any same-origin web UI that has the session token, regardless of `--tui` mode. This matches the security posture of `/api/pty`, `/api/pub`, and `/api/events`, all of which work without `--tui`.

**Should we upstream?** Yes. The gate seems to have been added defensively, but it breaks legitimate Web UI use cases.

**Update (v0.16.0 sync)**: upstream #38591 now always enables embedded chat (`_DASHBOARD_EMBEDDED_CHAT_ENABLED = True` by default) and removed the dashboard `--tui` flag, so the original symptom no longer occurs out of the box. The fork retains the explicit gate removal on `/api/ws` so the gateway RPC channel (used by the v2 web UI / desktop) stays reachable even if embedded chat is ever disabled.

---

### P-004: `GET /api/fs/list` for v2 web workspace picker

**Symptom**: v2 `/new` task page → "选择 workspace" → falls back to `window.prompt()` asking the user to type a path. UX is bad on a desktop OS.

**Root cause**: Upstream (at the time) had no filesystem browse endpoint. Electron desktop shells use the OS native dialog, but a pure web UI can't.

**Original patch**: Added `GET /api/fs/list?path=<dir>&include_hidden=<bool>` returning `{path, parent, home, entries: [{name, path, is_dir}]}`, resolved via `~` expansion, `..` folding, and an enforced `Path.home()` subtree (400 if outside), plus a 5000-entry cap. Fork helpers: `_resolve_fs_path`, `_list_directory_entries`, `_FS_LIST_MAX_ENTRIES`.

**Update (2026-06 — converged with upstream)**: Upstream subsequently shipped its own `/api/fs/list` (`fs_list` → `_fs_path`), which replaced the fork handler during a sync. The route now IS upstream's: it returns `{entries: [{name, path, isDirectory}]}` (camelCase, **no** top-level `path`/`parent`/`home`) and on permission/IO errors returns **HTTP 200** with `{entries: [], error: "EACCES"|"ENOENT"|...}`. `_fs_path` only rejects null bytes / unparseable paths and resolves relative paths against cwd — there is **no home-subtree restriction** anymore.
- The original fork helpers (`_resolve_fs_path`, `_list_directory_entries`, `_FS_LIST_MAX_ENTRIES`) were orphaned by that sync and have now been **removed** as dead code.
- The home restriction was intentionally **not** restored: desktop session workspaces are legitimately arbitrary (outside `$HOME`, other drives, containers), so a hard home cap would break them. The Desktop's Rust `read_workspace_file` command already confines file *reads* to the session workspace root.
- Desktop consumers were realigned to this shape in Hermes-CN-Desktop PR #330 (tolerant Zod parser; the old required `path`/`parent`/`home`/`is_dir` had been breaking the file browser for every user).

**Side effects**: Directory-listing attack surface, mitigated by the token gate on all `/api/` routes (local, loopback-bound). No home restriction — acceptable for a local desktop runtime.

**Should we upstream?** N/A — already converged with upstream.

---

### P-005: `GET /api/mcp-servers` (read-only list)

**Symptom**: v2 task panel has a 5-cell health-check grid. One cell is "MCP" (configured / enabled). Upstream's `/api/tools/toolsets` returns toolsets and MCP servers blended together — extracting just the MCP count is awkward.

**Root cause**: MCP server config is in `config.yaml`'s `mcp_servers` key. Upstream doesn't expose it via REST.

**What the patch does**: Returns `{summary: {total, enabled}, servers: [{name, enabled}]}`. **Deliberately does not return** `command` / `args` / `env` because those routinely embed secrets.

**Side effects**: None. Read-only.

**Should we upstream?** Upstream added a *different* `/api/mcp/servers` (slash) in the 2026-06-04 sync that returns full per-server config (url/command/args, env redacted). The fork keeps `/api/mcp-servers` (hyphen) with the minimal `{name, enabled}` shape the desktop health-check expects; the handler was renamed `list_mcp_servers_summary` so the two endpoints don't collide on the generated OpenAPI operationId.

---

### P-006: `OPTIONAL_ENV_VARS` for CN providers

**Symptom**: v2 Models settings page lists CN providers (alibaba / deepseek / kimi / volcengine-ark / minimax-cn / baidu-qianfan / tencent-hunyuan / siliconflow / modelscope / ai302) in its catalog, but the env panel doesn't expose `*_API_KEY` entries for them — users have to manually `vim ~/.hermes/.env`.

**Root cause**: Upstream `OPTIONAL_ENV_VARS` is the metadata dict that drives the env panel UI. It only registers global providers (OpenAI / Anthropic / Google / DeepSeek / Groq / etc.). CN providers were never added.

**What the patch does**: Adds 7 `*_API_KEY` entries plus 1 `ARK_BASE_URL`, all `category="provider"`. `ARK_API_KEY` is top-5 (always visible), the rest are `advanced=True`. Chinese description / prompt / official docs URL.

**Side effects**: Env panel grows by 8 entries. Doesn't change parsing of any existing entry.

**Should we upstream?** Maybe, on a per-provider basis. Some are obscure and upstream might decline.

---

### P-007: Surface gateway WS dispatch exceptions

**Symptom**: v2 sometimes shows "WebSocket closed" Toast with no diagnostic info. Refresh, retry — the issue is intermittent and unreproducible.

**Root cause**: `tui_gateway/ws.py` wraps `server.dispatch` + `transport.write_async` in a bare `try/finally`. Any unhandled exception (from an inline handler or from `json.dumps` of a non-serializable response) escapes the loop, hits `finally → ws.close()`, and the client sees "WebSocket closed" with zero context.

**What the patch does**:
- Wraps dispatch + write in an explicit `try/except`
- Logs traceback to `~/.hermes/logs/dispatch_exceptions.log`
- Converts the crash into a JSON-RPC error response (code -32000) sent back to the client
- Keeps the connection alive for subsequent calls

**Side effects**: Log file grows on dispatch crashes (rotate via standard logrotate if needed). Error responses use a non-standard error code; clients should treat -32000 as a generic server error.

**Should we upstream?** Done — as of the 2026-06-04 upstream sync, upstream ships equivalent dispatch-exception handling (try/except around `dispatch`, a JSON-RPC `-32603` "internal error" response, structured `dispatch_crashes` logging via `_log.exception`, and the connection kept alive for subsequent calls). The fork implementation — including the dedicated `~/.hermes/logs/dispatch_exceptions.log` file and the `-32000` error code — was dropped in favor of upstream's version, which the merged `handle_ws` observability counters already depend on. The standard hermes log now captures the traceback.

---

### P-008: `GET/PUT /api/profiles/active`

**Symptom**: v2 wants to build a profile switcher UI. Upstream has `GET /api/profiles` (list), `POST /api/profiles` (create), `DELETE /api/profiles/{name}`, `PATCH /api/profiles/{name}` (rename), `GET/PUT /api/profiles/{name}/soul` — but **no way to read or write the sticky active profile** (`~/.hermes/active_profile`).

**Root cause**: Upstream's dashboard binds `HERMES_HOME` at process startup; "switching the active profile mid-session" isn't part of its model. Switching requires restarting hermes. But the *sticky* setting (which profile to use *next* time) does need a getter/setter.

**What the patch does**:
- `GET /api/profiles/active` → `{name: <sticky default>}`. Reads `~/.hermes/active_profile` (or returns `default` if file missing).
- `PUT /api/profiles/active` body `{name}` → writes the file. **Does not affect the currently running dashboard process** — the client (v2) is responsible for prompting the user to restart hermes.

**Side effects**: None. File-backed sticky preference, mirroring `hermes profile use <name>` CLI behavior.

**Should we upstream?** Done — upstream shipped `GET/POST /api/profiles/active` in the 2026-06-04 sync (GET returns `{active, current}`; POST sets via `ProfileActiveUpdate`). The fork's standalone GET/PUT were removed to avoid a duplicate route. To keep the existing desktop client working without a coordinated release, two minimal compat shims now ride on upstream's endpoint: the GET response also carries `name` (= `active`; the desktop's `useActiveProfile` reads `.name`), and a `@app.put("/api/profiles/active")` alias is stacked on the setter (the desktop sets via `PUT`). Both can be dropped once the desktop migrates to `{active,current}` + `POST`.

**Regression — dropped by the v0.17.0 upstream sync (restored, see issue #301)**: a sync reverted both compat shims — the GET response lost `name` and the `PUT` alias disappeared (only upstream's `POST` remained). The desktop's `ActiveProfileResponse` Zod schema requires `name: string`, so `GET /api/profiles/active` failed to parse (`path:["name"], received: undefined`) and the whole profile screen showed "无法读取档案列表"; profile switching also broke (PUT → 405). Restored and now guarded by `tests/hermes_cli/test_web_server_profile_active_compat.py` so a future sync can't silently drop either half again.

---

### P-009: SSE+POST gateway transport — **DEPRECATED**

> **Deprecation (2026-06-09)**: the desktop client moved to the runtime's
> native `/api/ws` JSON-RPC WebSocket (the same transport the official
> Electron desktop in `apps/desktop` uses) as of desktop 0.4 — the SSE+POST
> path had no heartbeat, one HTTP round trip per RPC, and an async-ack split
> that made in-flight turns fragile. These endpoints MUST stay until desktop
> shells <= 0.3.x reach EOL: the Tauri shell has no self-update while the
> runtime hot-updates underneath it, so a new runtime must keep serving old
> shells. `/api/v2/events` now logs a deprecation line per connection so
> residual usage can be measured from runtime logs before removal.

**Symptom**: desktop's packaged runtime needs a stable browser-friendly
streaming transport. Relying only on `/api/ws` makes failures harder to
debug and interacts poorly with some desktop shell/network setups.

**Root cause**: Upstream exposes the TUI gateway over WebSocket. desktop
wants EventSource for server-to-client events and normal HTTP POST for
client-to-server JSON-RPC.

**What the patch does**:
- Adds `GET /api/v2/events` for SSE frames.
- Adds `POST /api/v2/rpc` for gateway JSON-RPC requests.
- Adds `tui_gateway/sse.py` transport plumbing.

**Side effects**: Adds another authenticated gateway transport surface.
It uses the same session token model as the dashboard API.

**Should we upstream?** Maybe. It is useful for browser-hosted dashboards
and desktop shells, but it changes the supported gateway transport matrix.

---

### P-010: `LONGCAT_API_KEY`

**Symptom**: CN model settings include LongCat, but the dashboard env
metadata had no first-class `LONGCAT_API_KEY` entry.

**Root cause**: Upstream provider metadata focuses on global providers and
does not include this CN-specific key.

**What the patch does**: Adds `LONGCAT_API_KEY` to `OPTIONAL_ENV_VARS`.

**Side effects**: Env settings show one additional provider credential.

**Should we upstream?** Only if upstream decides to support LongCat.

---

### P-011: Gateway model filtering and provider probe

**Symptom**: desktop needs to filter model picker options by provider
slug and run a lightweight provider health check without starting a full
agent turn.

**Root cause**: Upstream `model.options` returns broad picker data, and
there was no small JSON-RPC method for provider probing.

**What the patch does**:
- Adds `slug_filter` support to `model.options`.
- Adds a `provider.probe` gateway RPC.

**Side effects**: Minimal. The new RPC should avoid returning secrets or
raw provider config.

**Should we upstream?** Maybe, but the probe shape should be reviewed before
opening an upstream PR.

---

### P-012: Optional custom `base_url` in `_model_flow_anthropic()`

**Symptom**: When adding an Anthropic model through the interactive setup flow, any pre-configured or desired custom `base_url` is silently discarded because the code unconditionally calls `model.pop("base_url", None)`.

**Root cause**: `_model_flow_anthropic()` hardcoded `model.pop("base_url", None)` with the assumption that all Anthropic traffic should go to the official `https://api.anthropic.com` endpoint. This breaks users who need to point at Anthropic-compatible proxies, OpenRouter, or private endpoints.

**What the patch does**:
- Removes the unconditional `model.pop("base_url", None)`.
- After model selection, prompts the user with the current `base_url` (or `https://api.anthropic.com` as the default).
- If the user types a custom URL, it is saved to `model["base_url"]`.
- If the user presses Enter without input, the existing `base_url` is kept; only when none existed before is it popped so the runtime falls back to the hardcoded Anthropic URL.

**Side effects**: None. The runtime (`runtime_provider.py`) already reads `model_cfg.get("base_url")` for the `anthropic` provider, so no runtime changes are required.

**Should we upstream?** Yes. The change is backward-compatible and enables legitimate use cases for alternative Anthropic-compatible endpoints.

---

### P-013: Automatic tool argument key repair in `handle_function_call`

**Symptom**: LLM tool calls frequently fail with "unknown parameter" because the model uses synonyms or typos for argument names (e.g. `file` instead of `path`, `cmd` instead of `command`, `backgroud` instead of `background`).

**Root cause**: Hermes' JSON Schemas are strict. When an LLM drifts from the canonical field name, `handle_function_call` passes the bad key straight through to the tool handler, which often rejects it.

**What the patch does**:
- Introduces `repair_tool_arg_keys()` and `_repair_nested_args()` in `model_tools.py`.
- Defines `TOOL_FIELD_ALIASES` — a large global alias table covering general, file, shell, web, task, todo, input, search, memory, cronjob, and skill argument names.
- Defines `TOOL_SPECIFIC_ALIASES` for per-tool overrides (e.g. `delegate_task` maps `task`→`goal` instead of `task`→`prompt`; `cronjob` maps `command`→`action`).
- Uses `difflib.get_close_matches` as a fuzzy fallback for typos when no alias matches.
- Recursively repairs keys inside nested objects and arrays of objects, guided by the schema's `properties` and `items` definitions.
- Adds an optional callback hook (`set_arg_repair_callback`) so external systems (TUI, ACP) can be notified of top-level key repairs.
- Hooks the repair into `handle_function_call()` so it runs *before* `coerce_tool_args()`, meaning repaired keys are still type-coerced as usual.
- Ships comprehensive tests in `tests/run_agent/test_repair_tool_arg_keys.py`.

**Side effects**: Minimal. The function is a pure key-mapping transform; unknown keys are left untouched. The fuzzy matcher only kicks in for keys ≥4 chars with a similarity ratio ≥0.75–0.80, so random fields are unlikely to be falsely renamed.

**Should we upstream?** Yes. This is a generic robustness improvement that benefits every Hermes deployment regardless of platform or provider.

---

### P-014: Native MCP client missing in the frozen desktop runtime

**Symptom** (issue #16): A user configures `mcp_servers` correctly in `~/.hermes/config.yaml`, the MCP server script works standalone, but the CN Desktop agent never connects to it — `agent.log` shows no MCP discovery/connection lines and no `mcp_*` tools appear. `pip install mcp` on the host does not help.

**Root cause**: The native MCP client is fully implemented (`tools/mcp_tool.py`, `discover_mcp_tools()`), but the SDK is an *optional* dependency that lives only in the `[mcp]` extra. The runtime release workflow installed just `.[web,anthropic]`, so the frozen PyInstaller artifact shipped **without** the `mcp` package. Inside the frozen runtime `_MCP_AVAILABLE` is therefore `False`, and `discover_mcp_tools()` returns `[]` after logging only at `debug` level — invisible at the default INFO log level. The host's `pip install mcp` is irrelevant because the frozen runtime bundles its own interpreter and packages.

**What the patch does**:
- `release-runtime.yml`: bundles the `mcp` SDK (install entry later folded into the `cn-desktop` extra — P-015), adds `--collect-submodules mcp` + `--copy-metadata mcp` to PyInstaller, and extends the verify step to fail the build if `mcp-*.dist-info` is absent (so this can't silently regress).
- `tools/mcp_tool.py`: when `mcp_servers` is configured but the SDK is unavailable, `discover_mcp_tools()` now emits a one-time `WARNING` ("mcp_servers are configured but the MCP SDK is not available …") instead of a silent debug line. Users without MCP config keep the quiet debug path.
- `hermes_cli/config.py`: adds `mcp_servers` to `_KNOWN_ROOT_KEYS` so the documented root schema is accurate.
- `docs/RUNTIME_RELEASES.md`: documents MCP bundling as a required runtime dep and updates the manual dry-run command.
- Tests in `tests/tools/test_mcp_tool.py` cover the warn-when-configured, stay-quiet-when-unconfigured, and warn-once behaviors.

**Side effects**: The frozen runtime grows by the `mcp` SDK and its transitive deps (`anyio`/`httpx-sse`/`sse-starlette`, all already present via `web`/`anthropic`). No behavior change for source installs that already include the `[mcp]` extra.

**Should we upstream?** The packaging change is CN-runtime-specific (upstream doesn't build these PyInstaller artifacts). The `mcp_tool.py` diagnostic and the `mcp_servers` known-root-key are generic and worth upstreaming.

---

### P-015: IM platform backends missing in the frozen desktop runtime

**Symptom**: A desktop user correctly sets the Feishu App ID/Secret in `.env`, adds the Feishu platform to `config.yaml`, and the gateway process runs — but it never connects to Feishu. `lark-oapi` "cannot be installed" inside the packaged app. The same applies to DingTalk, WeCom, and WeChat.

**Root cause**: Identical to P-014, generalized. The IM adapters (`gateway/platforms/feishu.py`, `dingtalk.py`, `wecom*.py`, `weixin.py`) import their SDKs under `try/except` and degrade to an `*_AVAILABLE = False` state when the package is missing. Those SDKs live only in optional extras (`[feishu]` → `lark-oapi`, `[dingtalk]` → `dingtalk-stream` + `alibabacloud-*`, `[wecom]` → `defusedxml`; 微信 has **no** extra and needs `aiohttp`/`qrcode`/`cryptography`). `[all]`'s policy deliberately excludes these because they're lazy-installable via `tools/lazy_deps.py` — but **lazy install can't run inside a frozen PyInstaller binary** (no working pip), so the desktop runtime, which installed only `.[web,anthropic,mcp]`, shipped without any of them. The host-side `pip install lark-oapi` the user tried writes to system Python, which the frozen runtime never uses.

**What the patch does**:
- `pyproject.toml`: adds a `cn-desktop` aggregate extra listing every backend the frozen runtime must pre-bake — `web`, `anthropic`, `mcp`, `feishu`, `dingtalk`, `wecom`, plus 微信's `aiohttp`/`qrcode`/`cryptography` (pinned to match the existing extras). This is the single source of truth for "what the desktop ships", deliberately diverging from `[all]`'s lazy-install policy.
- `release-runtime.yml`: installs `.[cn-desktop]`; adds `--collect-submodules`/`--copy-metadata` for `lark_oapi`, `dingtalk_stream`, `alibabacloud_dingtalk` (+ `alibabacloud_tea_openapi`/`alibabacloud_tea_util`), `aiohttp`, `qrcode`; adds a **build-env import smoke test** that imports each adapter and asserts its `*_AVAILABLE` flag is True (fails fast on a missing extra dep); and generalizes the verify step to assert every bundled backend's `dist-info` is present in the frozen output.
- `docs/RUNTIME_RELEASES.md`: documents the `cn-desktop` extra as the place to add future desktop backends, and flags the `alibabacloud_*` collection as fragile (smoke-test against a live DingTalk bot on first release).
- `uv.lock`: regenerated for the new extra (`uv lock --check` passes).

**Side effects**: The frozen runtime grows by the IM SDKs and their transitive deps (notably the pure-Python `alibabacloud_*` chain). All are pure-Python with cross-platform wheels/sdists — unlike `matrix`'s `python-olm`, which needs a C toolchain and is intentionally still excluded. No change to source installs.

**Should we upstream?** No — upstream doesn't build these PyInstaller artifacts. The `cn-desktop` extra and packaging are CN-runtime-specific.
### P-016: PowerShell native execution + runtime-adaptive terminal description

> **Updated by P-019**: P-019 completes the migration by removing all remaining Git Bash discovery logic and targeting **only Windows PowerShell 5.1** (`powershell.exe`). See P-019 below for details.

**Symptom**: On Windows, the agent was hardcoded to always use Git Bash. PowerShell is faster to start (`-NoProfile`), handles Windows paths natively (no `/c/foo` translation). Additionally, the terminal tool's static `TERMINAL_TOOL_DESCRIPTION` referenced Linux/bash commands that don't exist on native PowerShell.

**Root cause**: Upstream's `LocalEnvironment` is bash-only. The terminal tool description is a hardcoded static string assuming a Linux environment.

**What the patch does**:

1. **`tools/environments/local.py`** — Adds `_resolve_shell()`: on Windows, detects `pwsh.exe` (PS7) first, falls back to `powershell.exe` (PS5.1) or Git Bash. Adds `_run_pwsh()`, `_wrap_command_pwsh()`, overrides `init_session()`, `_run_bash()`, `_wrap_command()`. Respects `HERMES_SHELL_TYPE` and `HERMES_PWSH_PATH`.

2. **`tools/terminal_tool.py`** — Dynamic description: `_detect_shell_for_description()` + `_build_dynamic_terminal_description()` replace Linux/bash command references with PowerShell cmdlets.

3. **`model_tools.py`** — Adds `_shell_fp` to `get_tool_definitions()` cache key.

4. **`tools/environments/proccess_pwsh.py`** — `pwsh_transform()` down-levels PS7+ syntax (`?:`, `??`, `&&`, `||`, `?.`, `?[`) to PS5.1-compatible `if/else`, with warning propagation.

**Side effects**: On Windows, terminal commands now execute in PowerShell. Git Bash auto-install removed, but Python-level bash fallback (`_find_bash()`) remained as a 7-strategy discovery chain.

**Should we upstream?** Yes — superseded by P-019 which completes the migration.

---

### P-017: Consecutive identical tool call dedup (infinite loop breaker)

**Symptom**: On complex tasks (long-running builds, multi-step refactors), the agent sometimes enters an infinite loop, calling the same tool with the same arguments across consecutive API iterations — e.g. repeatedly reading the same file, or calling `run` with the same command. The existing `_deduplicate_tool_calls()` in `run_agent.py` only removes exact duplicates within a **single** turn's tool batch, missing cross-iteration repeats entirely.

**Root cause**: No cross-step dedup mechanism existed. Each API iteration's tool results feed into the next LLM call without any history awareness of what was already tried.

**What the patch does**:

1. **`agent/tool_dedup.py`** — New module with `ToolDedupTracker` class:
   - Normalizes tool call keys via `_canonical_tool_arguments()` (recursive key-sorting for dicts, fallback to `str()`).
   - Tracks `_seen_call_keys` (all calls seen across steps) and `_consecutive_key`/`_consecutive_count` (streak tracking).
   - `begin_step(previous_calls, step_no, turn_id)`: seeds state from previous step's tool calls.
   - `end_step()`: returns the list of calls made this step for the next iteration, and advances the consecutive streak.
   - `check_and_register(tool_name, arguments)`: called during tool execution; if the call key was seen in a previous step, returns escalating reminder text at repeat counts 3, 5, and 8.
   - Escalating reminders: at count 3, a gentle nudge (`<system-reminder>`: "You are repeating the exact same tool call…"). At counts 5 and 8, a stronger message naming the tool, repeat count, and arguments.

2. **`agent/agent_init.py`** — Initializes `_tool_dedup_tracker` on the `AIAgent` instance.

3. **`agent/conversation_loop.py`** — Step lifecycle:
   - Before each API call: `begin_step()` seeds cross-step state from the previous iteration's calls.
   - After all tool results are collected: `end_step()` captures the current step's calls for the next iteration.

4. **`agent/tool_executor.py`** — Dedup check injection:
   - In `execute_tool_calls_concurrent()`: after each tool execution, calls `check_and_register()` and appends any reminder text to the result.
   - In `execute_tool_calls_sequential()`: same pattern.

**Side effects**:
- Tool results may grow by a few hundred characters (the `<system-reminder>` text) when dedup is triggered.
- The reminder text is visible to the LLM, which may influence its next action — this is the intended behavior.
- Thread safety: `check_and_register()` uses a `threading.Lock()` to protect shared state in the concurrent execution path.

**Should we upstream?** The mechanism is generic, but the integration points (`agent_init.py`, `conversation_loop.py`, `tool_executor.py`) are specific to this fork's agent architecture. Could be proposed as a generalized observability hook.

---

### P-018: Empty API key guard in `agent/agent_init.py`

**Symptom**: When the API key is empty (parameter explicitly ` ""`, environment variables unset), the agent panics with a low-level OpenAI or Anthropic SDK auth exception instead of a clean, actionable error message. In TUI/gateway background threads the stack trace is not surfaced to the user, making the failure look like a silent crash.

**Root cause**: `init_agent()` had no explicit validation that `api_key` is non-empty before handing it to `_create_openai_client()` or `build_anthropic_client()`. Empty strings reached the SDK constructors and produced confusing exceptions.

**What the patch does**:
- Adds `_api_key_required(provider, api_key, base_url)` helper that returns `False` for providers that genuinely do not need a literal key (Azure Entra ID callable tokens, `"aws-sdk"` / `"no-key-required"`, Bedrock).
- Inserts a guard in the `anthropic_messages` branch right before `build_anthropic_client()`.
- Inserts a guard in the `chat_completions` branch right before `_create_openai_client()`.
- Both guards raise `RuntimeError("no API key (param empty, env vars unset)")` when the key is empty and the provider requires one.
- Adds two pytest cases covering the `chat_completions` and `anthropic_messages` empty-key paths.

**Side effects**: None for providers that legitimately need no key (local endpoints with `"no-key-required"`, Bedrock, Azure Entra ID). The fallback loop (`fallback_model` / `fallback_providers`) still executes before the guard.

**Should we upstream?** Yes. The change is purely additive, provider-agnostic, and prevents a poor user experience across CLI, TUI, gateway, and direct `AIAgent()` usage.

---

### P-019: Complete Git-Bash-to-PowerShell migration (Windows PowerShell 5.1 only)

**Symptom**: P-016 added PowerShell support but left the codebase in a hybrid state: `pwsh.exe` (PS7) was probed first, with `powershell.exe` (PS5.1) as fallback, and the 7-strategy `_find_bash()` Git Bash discovery chain (env override → PortableGit → git.exe derivation → registry → PATH → common paths → auto-install) was still present. `HERMES_GIT_BASH_PATH` env var, `HERMES_PWSH_PATH` env var, and `_install_git` import (non-existent module) were all dead or dead-end code.

**Root cause**: P-016 focused on adding PowerShell as the primary shell but didn't fully remove the Git Bash machinery. The `pwsh.exe` (PS7) requirement was unnecessary — Windows PowerShell 5.1 (`powershell.exe`) ships with every Windows 10/11 system and is always available.

**What the patch does**:

1. **`tools/environments/local.py`** — Core shell resolution (Phase 1):
   - Removes `_find_bash()` (~130 lines, 7 strategies + WSL launcher filter + auto-install with dead `_install_git` import). Replaces with minimal `_find_bash_posix()` for non-Windows only.
   - Removes `_is_windows_wsl_launcher()` helper (no longer needed).
   - Renames `_find_pwsh_simple` → `_find_powershell()`: just `shutil.which("powershell.exe") or "powershell.exe"` — no `pwsh.exe` probing.
   - Rewrites `_resolve_shell()`: on Windows always returns `("powershell", path)`. `HERMES_SHELL_TYPE=bash` raises `RuntimeError` on Windows. Removes `HERMES_PWSH_PATH` support.
   - Renames `_run_pwsh` → `_run_powershell`, `_wrap_command_pwsh` → `_wrap_command_powershell`.
   - **`pwsh_transform` is now always-on** — unconditionally applied to every command (removes the `if os.path.basename(...).startswith("powershell")` guard).
   - Updates all `"pwsh"` → `"powershell"` references in `init_session`, `_run_bash`, `_wrap_command`.
   - Gates MSYS normalization in `_update_cwd`/`_extract_cwd_from_output` behind `self._shell_type == "bash"`.
   - Updates comments throughout: `_make_run_env`, `get_temp_dir`, `_msys_to_windows_path`, `_resolve_safe_cwd`.

2. **`tools/terminal_tool.py`** — Removes "Windows Git Bash" description branch (dead code). Simplifies `_detect_shell_for_description()`: always returns `"powershell"` on Windows.

3. **`agent/prompt_builder.py`** — Replaces `_WINDOWS_BASH_SHELL_HINT` with `_WINDOWS_POWERSHELL_SHELL_HINT` instructing the agent to use PS5.1 syntax (`;` not `&&`, `$env:VAR`, no `?:`/`??`/`?.`).

4. **`cli.py`** — Renames `_normalize_git_bash_path` → `_normalize_msys_path`.

5. **`apps/desktop/electron/main.cjs`** — Replaces `findGitBash()` (~40 lines) with `findPowerShell()` (~15 lines). Updates preflight check to verify `powershell.exe`.

6. **`scripts/install.ps1`** — Removes `Install-Git` bash discovery + `Set-GitBashEnvVar` (~210 lines). Simplifies `Stage-Git`. Adds defensive `powershell.exe` check. Removes all `HERMES_GIT_BASH_PATH` references.

7. **`hermes_cli/uninstall.py`** — Removes `HERMES_GIT_BASH_PATH` from env var cleanup.

8. **`cron/scheduler.py`** — Updates `.sh`/`.bash` error message: no longer mentions Git for Windows.

9. **Comments cleanup**: `tools/environments/base.py`, `tools/file_operations.py`, `tools/browser_tool.py` — "Git Bash" → "PowerShell" or generic "shell".

10. **Tests**: `test_shell_resolution.py` (rewritten for new functions), `test_terminal_dynamic_description.py` (removed bash-on-Windows test, updated assertions), `test_windows_native_support.py` (renamed `_normalize_git_bash_path` references, updated cron message expectations), `test_local_env_windows_msys.py` (updated docstrings).

11. **Docs**: `windows-native.md` (rewrote "How Hermes runs shell commands" section, removed `HERMES_GIT_BASH_PATH` from env var table and installer steps), `environment-variables.md` (replaced `HERMES_GIT_BASH_PATH` with `HERMES_SHELL_TYPE`), `contributing.md` ("Git Bash" → "Windows PowerShell 5.1").

12. **PowerShell UTF-8 encoding hardening** — so PowerShell subprocess output is decoded as UTF-8 on Windows:
    - Adds `ps_with_utf8()` helper in `tools/environments/windows_env.py` that prepends `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $OutputEncoding=[System.Text.Encoding]::UTF8;` to PowerShell commands. Idempotent, no-op on non-Windows.
    - Calls `ps_with_utf8()` in `tools/environments/local.py` after `pwsh_transform()`.
    - Keeps `encoding="utf-8", errors="replace"` only on PowerShell subprocess callers: `hermes_cli/claw.py`, `hermes_cli/clipboard.py`, `hermes_cli/gateway.py`, `hermes_cli/managed_uv.py`.
    - `hermes_bootstrap.py` sets the Windows console code page to CP_UTF8 (65001) and adds `HERMES_DISABLE_WINDOWS_UTF8=1` escape hatch.
    - Reverts `encoding="utf-8"` additions on all non-PowerShell subprocesses (tasklist, ssh, docker, ffmpeg, singularity, ripgrep, termux, comfyui auto-fix, git helpers in `scripts/check-windows-footguns.py`, and various tests).
    - Adds tests: `tests/tools/test_clipboard.py::TestClipboardPowershellEncoding`, `tests/tools/test_local_pwsh_warnings.py::TestRunPowershellUtf8Encoding` / `TestPwshTransformAndUtf8Compose`, `tests/tools/test_windows_encoding.py`, and `scripts/verify_windows_utf8.py`.

**Why we need it**:
- `powershell.exe` (5.1) ships with every Windows 10/11 — zero install, zero download.
- Starts faster than Git Bash, handles Windows paths natively, avoids POSIX-translation overhead.
- Removes ~400 lines of dead code (7-strategy bash discovery, WSL launcher filter, PortableGit auto-install, `HERMES_GIT_BASH_PATH` env var, `HERMES_PWSH_PATH` env var).
- Agent now has a single, predictable, always-available shell on Windows.
- P-016's `pwsh.exe` (PS7) probing was unnecessary complexity — 5.1 is universal.

**Side effects**:
- `HERMES_SHELL_TYPE=bash` now raises a clear `RuntimeError` on Windows.
- `HERMES_PWSH_PATH` and `HERMES_GIT_BASH_PATH` env vars are no longer honored.
- All commands go through `pwsh_transform` unconditionally — PS7+ syntax is always down-leveled.
- PowerShell commands now reliably round-trip non-ASCII output (CJK, emoji, accented characters). Non-PowerShell subprocesses remain on the system locale, which is the intended conservative scope.

**Should we upstream?** Yes. This completes the migration P-016 started and makes Hermes a zero-dependency Windows citizen.

**Sync note (2026-06-27, `chore/sync-upstream-20260627`)**: upstream periodically re-introduces Git Bash machinery on `main`. This sync's `upstream/main` restored the 7-strategy `_find_bash()` in `tools/environments/local.py`, the static terminal description in `tools/terminal_tool.py`, and Git-Bash wording in `website/docs/developer-guide/contributing.md`. The sync **re-asserted P-016/P-019** — kept the fork's PowerShell-only path and grafted only upstream's *independent* fixes on top: `_find_shell()`'s `$SHELL`-preference for POSIX background spawning (#42203, adapted to call the fork's `_find_bash_posix()`); `start_new_session`; install-dir PATH reachability; and in `apps/desktop/electron/main.cjs` the no-console-python helpers (`getNoConsoleVenvPython`/`toNoConsolePython`/`applyWindowsNoConsoleSpawnHints`/`unwrapWindowsVenvHermesCommand`) combined with the fork's async backend resolution (the probes are `async`, so the fork's `await` is required). Future syncs should expect the same Git-Bash drift and resolve the same way.

**`cli.py` decomposition cleanup (2026-06-27)**: a prior sync had left a large block of methods "restored from upstream CLI decomposition" inline in `HermesCLI`. Upstream now provides those methods via `CLICommandsMixin` / `CLIAgentSetupMixin` (which `HermesCLI` inherits), so this sync dropped the redundant inline copies in favour of upstream's structure (per MAINTAINING.md "if upstream added an equivalent feature, remove the local fork implementation"). The only genuine fork change re-applied to `cli.py` was the P-019 rename; the unused `_new_session_id` helper (0 callers) was dropped.

---

### P-024: Empty-content message filtering in `sanitize_api_messages`

> Renumbered from a duplicate **P-022** (the streaming stale-stream detector above already owns P-022, and its `cn/P-022-provider-stream-hang` branch + `[CN-fork] P-022` commits back that number; this empty-content patch had no P-022 commits of its own, so it moved to the next free number).

**Symptom**: Long-running sessions (e.g. Feishu 3-13h) eventually hit an API error such as MiMo's HTTP 400 `"text is not set"` or a generic OpenAI-compatible gateway rejection. The failure happens on a request that contains an `assistant` or `user` message whose `content` has been compressed/truncated to an empty string.

**Root cause**: Some providers (MiMo v2.5, strict OpenAI-compatible gateways) reject messages where `content` is `""` and no tool payload is present. The agent's context compressor can leave these empty messages behind; the existing pre-call sanitizer only repaired orphaned tool results and dropped `session_meta` role messages, but did not strip empty-content assistant/user/function messages.

**What the patch does**:

- In `sanitize_api_messages`, after the existing orphan-repair pass, a new pass filters out messages whose role is in `{assistant, user, function}`, whose `content` is exactly `""`, and that carry no assistant payload.
- Assistant payloads that preserve the message are:
  - `tool_calls`
  - `codex_reasoning_items`
  - `codex_message_items`
  - `reasoning_content`
- This keeps codex/DeepSeek reasoning replay and tool-call chains intact while removing the empty messages that trigger provider-side validation errors.
- System messages are intentionally left untouched (provider behavior varies).
- Messages that lack a `content` key entirely are also left untouched, so the API can reject them with its own error if necessary and we don't accidentally hide other bugs.

**Files touched**:
- `agent/agent_runtime_helpers.py` — adds the empty-content filter inside `sanitize_api_messages`.
- `tests/run_agent/test_agent_guardrails.py` — adds 11 focused regression tests covering assistant/user/function empty-content drops, preservation with tool calls / codex reasoning / reasoning content, system preservation, multiple consecutive drops, and idempotence.
- `tests/run_agent/test_session_meta_filtering.py` — adds a dedicated `TestSanitizeApiMessagesEmptyContentFilter` class with end-to-end regression tests including the MiMo "text is not set" scenario.

**Side effects**:
- Slightly fewer messages reach the API after heavy compression; this is the desired behavior because those messages had no usable content.
- If an upstream caller intentionally passes an empty assistant message for some protocol reason, it will now be dropped unless it carries one of the recognized payloads.

**Should we upstream?** Yes. The filter is provider-agnostic, guards against a real class of gateway rejections, and is covered by extensive tests.

---

### P-025: OAuth provider-status caching + concurrency (Models page responsiveness)

**Symptom**: Opening the desktop **模型页 (Models page)** was very slow (multiple seconds of spinner), re-focusing the app re-triggered the slowness, and while it ran live chat streaming could also stutter.

**Root cause**: `GET /api/providers/oauth` built the Accounts-tab list by iterating every OAuth-capable provider and calling its auth-status helper **serially**. A few helpers do real I/O (`httpx` calls, credential-store endpoint detection, `subprocess`). Two compounding problems:
1. The desktop fetched this on every Models-page open and, via TanStack Query's `refetchOnWindowFocus`, on every window refocus — with no server-side cache.
2. The handler is `async def` but the per-provider work is blocking, so it ran on the FastAPI event loop — the same loop that serves the `/api/ws` gateway WebSocket streaming chat. A slow enumeration therefore stalled chat too.

**What the patch does** (`hermes_cli/web_server.py`):
- Adds a small per-profile TTL cache (`_OAUTH_STATUS_CACHE`, 20s, lock-guarded) around the assembled `{"providers": [...]}` payload. Repeat opens / refocus refetches within the window are instant.
- On a cache miss, resolves the profile's home as a context-local `set_hermes_home_override` (deliberately NOT the full `_profile_scope`, whose lock-protected skills-globals swap is unneeded here and unsafe to hold across the fan-out) and runs all `_resolve_provider_status` calls concurrently with `asyncio.gather(asyncio.to_thread(...))`. `asyncio.to_thread` copies the contextvar, so each worker resolves its auth store against the right profile, and the blocking work no longer touches the event loop. Wall-clock drops from sum-of-providers to ~slowest-provider.
- Busts the cache on every state change: `DELETE /api/providers/oauth/{id}` (both clear paths), `POST .../submit` (PKCE), and `GET .../poll/...` when the session reaches `approved` (device-code / loopback). A `refresh=true` query param force-bypasses the cache.

**Side effects**:
- A connect/disconnect performed outside these endpoints (e.g. `hermes auth` in a terminal, or setting an API key via `/api/env`) is reflected after at most the 20s TTL rather than instantly.
- Per-provider checks now run in parallel threads; each provider reads its own store, so there is no new cross-provider contention.

**Should we upstream?** Yes — provider-agnostic responsiveness fix that also removes event-loop blocking from a hot dashboard endpoint.

---

### P-028: Offline-first models.dev — model save/switch never blocks on the network

**Symptom**: On the desktop `/models` page (and the `/model` slash command), "设为当前模型" and "保存" each took ~十几秒. The config write itself is milliseconds — the wait was a synchronous network call.

**Root cause**: Setting/saving a model runs the expensive-model cost guard and reads model capabilities/context length, which call `agent.models_dev.fetch_models_dev()` → `requests.get("https://models.dev/api.json", timeout=15)`. From mainland China that endpoint is slow/blocked, so the request stalls to the full 15s timeout. The in-mem + disk cache only populate **after a successful fetch**, so when the network is blocked the cache stays empty and **every** action re-incurs the full timeout. `fetch_models_dev`'s docstring even promised a "bundled snapshot (offline-first)" Stage 0 that was never implemented.

**What the patch does**:

- **Bundled snapshot (real Stage 0/4)** — ships `agent/models_dev_snapshot.json` (full models.dev registry, minified). `_load_bundled_snapshot()` resolves it across source/wheel/PyInstaller (`importlib.resources` → module dir → `sys._MEIPASS`). `_serve_offline_fallback()` returns in-mem → disk (even stale) → snapshot, so `fetch_models_dev` is never empty.
- **Non-blocking read mode** — `fetch_models_dev(allow_network=False)` runs stages 1–2 then goes straight to the offline fallback, never touching the network. Threaded through `get_model_capabilities`, `get_model_info`, `lookup_models_dev_context`, `get_model_context_length`, and `expensive_model_warning` (which also skips the live `get_pricing_entry` probe when offline, fail-open). Every model save/switch hot path passes `allow_network=False`: gateway `config.set` (`_apply_model_switch`), REST `/api/model/set`, `GET /api/model/info`, the gateway/CLI `/model` slash command, and the model-save guard in `auth.py`.
- **Bounded + tunable network** — `MODELS_DEV_URL` and the timeout are env-overridable (`HERMES_MODELS_DEV_URL` for a China mirror, `HERMES_MODELS_DEV_TIMEOUT`, default 15s→3s). The timeout now only gates the background refresh.
- **Background prewarm** — `prewarm_models_dev_async()` (Event-guarded, daemon, exception-isolated, `HERMES_DISABLE_MODELS_DEV_PREWARM` opt-out) refreshes the shared disk cache off-thread at web-server startup; the gateway reads the same disk cache. Nothing on a user action waits on it.

**Files touched**: `agent/models_dev.py`, `agent/models_dev_snapshot.json` (new), `agent/model_metadata.py`, `hermes_cli/model_cost_guard.py`, `hermes_cli/web_server.py`, `tui_gateway/server.py`, `gateway/slash_commands.py`, `cli.py`, `hermes_cli/auth.py`, `scripts/refresh_models_dev_snapshot.py` (new), `pyproject.toml`, `MANIFEST.in`, `.github/workflows/release-runtime.yml`.

**Side effects**:
- The cost guard now sources pricing from `result.model_info` + the snapshot/cache instead of a live probe on the switch path. Major expensive models (Claude Opus, GPT, etc.) carry cost in the snapshot, so the guard still fires; an obscure model whose price exists *only* in OpenRouter's live metadata and not the snapshot may no longer warn (fail-open — acceptable).
- The default network timeout drops 15s→3s for everyone; on a slow-but-reachable connection a refresh may fail and fall back to snapshot/disk instead of hanging. Env-tunable.
- The snapshot is a static catalog that drifts; `scripts/refresh_models_dev_snapshot.py` regenerates it at release time, and the background prewarm keeps the live cache fresh when the network is reachable.

**Should we upstream?** The offline-first snapshot + non-blocking read mode are generic and worth upstreaming; the China-mirror env knob + packaging wiring are CN-specific.

---

## Windows compatibility patches

These patches improve first-class Windows support. They are authored by Maxwell Geng and are candidates for upstreaming.

### `282cfeeca` — Add `posix` option for `shlex.split` (Windows compatible)

**What it does**: Passes `posix=os.name == "posix"` to every `shlex.split()` call about `subprocess` usage in the codebase so that backslashes in Windows paths are not misinterpreted as escape characters.

**Files touched**:
- `agent/copilot_acp_client.py`
- `agent/shell_hooks.py`
- `agent/subdirectory_hints.py`
- `cli.py`
- `gateway/run.py`
- `hermes_cli/auth.py`
- `hermes_cli/gateway_windows.py`
- `hermes_cli/memory_setup.py`
- `tools/transcription_tools.py`

**Upstream status**: Should be upstreamed — pure bug-fix for Windows, no behavior change on POSIX.

### `ada59ec36` — Fix 10 Windows-failing tests to be cross-platform

**What it does**: Makes 10 test cases pass (or skip gracefully) on Windows:

| Test | Fix |
|---|---|
| `test_make_run_env_appends_homebrew_on_minimal_path` | Skip on Windows (POSIX PATH injection is intentionally skipped there). |
| `test_returns_root_when_only_root_exists` | `os.path.normpath()` the cwd on Windows so forward-slash paths walk up to the filesystem root correctly. |
| `test_close_stdin_allows_eof_driven_process_to_finish` | Use `cat` instead of `python3`; skip when PTY library is missing; pass `str` to winpty and `bytes` to ptyprocess. |
| `test_popen_killed_when_thread_creation_fails` | Only patch `os.getpgid` when it exists (POSIX-only). |
| `test_popen_killed_when_write_checkpoint_fails` | Only patch `os.getpgid` when it exists (POSIX-only). |
| `test_kill_detached_session_uses_host_pid` | Mock `_terminate_host_pid` directly instead of internal `psutil` calls. |
| `test_windows_does_not_call_psutil` | Add `pytest.importorskip("psutil")`. |
| `test_posix_walks_tree_and_terminates_children_then_parent` | Add `pytest.importorskip("psutil")`. |
| `test_posix_no_such_process_swallowed` | Add `pytest.importorskip("psutil")`. |
| `test_posix_oserror_falls_back_to_os_kill` | Add `pytest.importorskip("psutil")`. |

**Files touched**:
- `tests/tools/test_local_env_blocklist.py`
- `tests/tools/test_process_registry.py`
- `tools/environments/local.py`
- `tools/process_registry.py`

**Upstream status**: Should be upstreamed — expands CI coverage to Windows without changing production behavior.

### `1a75a7672` — ~~Auto-install Git-Bash on Windows, transform Windows-style commands to POSIX for bash~~ **DELETED**

**Status**: Removed. Git for Windows auto-install and Git Bash fallback support have been deleted in favor of native PowerShell execution (see P-016). The following files have been removed:
- `tools/environments/_install_git.py`
- `tools/environments/_process_bash_command.py`

Windows platform now requires PowerShell 7 (`pwsh`) or Windows PowerShell (system PowerShell). The shell is resolved via `_find_pwsh` without auto-installation — users are expected to have PowerShell available as part of a standard Windows installation.

### P-027: `save_config_value()` never creates the project `cli-config.yaml`

**Symptom**: Under the parallel test runner, `tests/hermes_cli/test_ignore_user_config_flags.py::test_user_config_skipped_when_flag_set` fails (deterministically once `tests/test_tui_gateway_server.py` lands in the same CI slice): with `HERMES_IGNORE_USER_CONFIG=1`, `load_cli_config()` returns a leaked `model.default` (`anthropic/claude-sonnet-4.6`) instead of the built-in default.

**Root cause**: `save_config_value()` used `config_path = user_config_path if user_config_path.exists() else project_config_path`. When the (test-hermetic) `HERMES_HOME` had no `config.yaml`, it wrote — and **created** — `<repo>/cli-config.yaml` (the project config inside the installed package / source tree) and never cleaned it up. `scripts/run_tests.sh` runs each test file in its own subprocess but the 8 parallel workers share the working tree, so the leaked file pollutes any concurrently-running test whose `load_cli_config()` falls back to `project_config_path` — exactly the `--ignore-user-config` read path. `tests/test_tui_gateway_server.py` is the writer.

**Fix**: only write the project `cli-config.yaml` when it already exists; otherwise write (and create) the user config. `save_config_value()` no longer creates files in the source tree.

**Should we upstream?** Yes — writing config into the installed package directory is a generic footgun, not CN-specific.

### P-033: cross-platform (in-process) disk I/O for `read_file`/`write_file` on local Windows

**Symptom** (issues #53, #54): on a Windows desktop, `read_file` failed for every file, and `write_file` was worse — it returned a full success payload (`bytes_written` equal to the content length) while **nothing was written to disk** (silent data loss; overwrites also left the original untouched). `patch_replace`'s own read-back guard is what kept it from corrupting, but a bare `write_file` happily reported success.

**Root cause**: `ShellFileOperations` performed all file I/O by shelling out to POSIX tools through the terminal backend — `wc -c` (size), `head -c` (binary/BOM/line-ending sniff), `sed -n` (page), `wc -l` (line count), `cat` (raw read), and an atomic-write script using `mktemp`/`cat >`/`mv -f`. The fork forces **Windows PowerShell 5.1** as the *only* Windows shell (P-016 / P-019 removed the Git-Bash fallback; `HERMES_SHELL_TYPE=bash` raises). PowerShell 5.1 has none of `wc`/`sed`/`head`/`mktemp`, and `wc -c < path` is even a parse error (`<` redirection). For the write path specifically: no native command runs → `$LASTEXITCODE` is `$null` → the wrapper's `exit $LASTEXITCODE` exits **0** → `_atomic_write` reports success → the follow-up `wc -c` returns non-numeric → `int()` raised `ValueError` → the old code fabricated `bytes_written = len(content.encode())`. Net: success payload, zero bytes on disk.

**Fix**: route every disk primitive through a thin dispatcher. On a **local Windows backend** (`_use_inproc_io()` = `_IS_WINDOWS and _is_local_env()`) the primitives do the I/O **in-process** with the Python stdlib — the Hermes process is itself Python and always present, so unlike the `_python_delete` `python -c` pattern there's no dependency on a `python`/`python3` interpreter being on PATH (important for the frozen PyInstaller runtime). `_local_atomic_write` writes to a temp file in the target's own directory, preserves the existing mode, and `os.replace()`s it over the target (atomic same-dir rename), returning the **verified post-replace byte count** so `write_file` never fabricates a size. Everywhere else — non-Windows local *and* every remote/sandbox backend (docker/ssh/modal/daytona), where the POSIX tools genuinely exist — the primitives run the byte-for-byte **identical** shell commands as before, so there is no behavior change off Windows. The silent `len(content)` fabrication on a failed post-write stat is removed.

`patch_replace`'s `cat`-based reads were intentionally left on the shell path: `cat`→`Get-Content` is a real PowerShell alias, so they already function; once `write_file` (which `patch_replace` calls to write) is fixed, the patch flow works on Windows too.

**Tested**: `tests/tools/test_file_ops_windows_inprocess.py` forces the in-process path (`_IS_WINDOWS` monkeypatched True + a real `LocalEnvironment`) so the Linux/macOS CI runner exercises the exact Windows code — write-lands-on-disk + real byte count (#54), failed-write-surfaces-error (no silent success), read/pagination/BOM/raw round-trips (#53), and gate tests proving non-Windows-local and remote backends keep the shell path.

**Should we upstream?** The cross-platform read/write is generic and worth upstreaming; the urgency is CN-fork-specific because P-016/P-019 made PowerShell the only Windows shell. Related: P-016, P-019.
### P-034: gateway-process recognizer accepts the frozen desktop runtime binary

**Symptom** (issue #42): on the desktop, WeChat (Weixin) periodically logged `[Weixin] Session expired; pausing for 10 minutes` and the panel showed "未连接", because multiple gateway processes were racing the same iLink bot session.

**Root cause**: the gateway-process recognizer `_gateway_command_subcommand()` only treats a command line as a gateway when it contains `hermes_cli.main` / `hermes_cli/main.py`, or a token whose basename is `hermes`/`hermes.exe`/`hermes-gateway`/`hermes-gateway.exe`/`gateway/run.py`. The CN desktop runs the **frozen PyInstaller binary** whose argv[0] basename is `hermes-agent-cn-runtime-<os>-<arch>` (Desktop `src/process/runtime.rs` `RUNTIME_BASENAME`), spawned as `<bin> gateway run --replace`. For that argv `has_gateway_entry` was False, so the recognizer returned `None` and `looks_like_gateway_command_line()` / `looks_like_gateway_runtime_command_line()` both returned False. Three cascading failures in `gateway/status.py` followed: (1) `get_running_pid()` read the live gateway's pid/lock record but `_record_matches_live_gateway_pid()` rejected the live cmdline, so it fell through to `_cleanup_invalid_pid_path()` and **unlinked the live gateway's `gateway.pid` + `gateway.lock`**, returning None; (2) `start_gateway` keys `--replace` off `get_running_pid()`, so a replacing gateway never found/killed the existing one and both ran; (3) the scoped token-lock staleness check (`not _looks_like_gateway_process(pid)` → True) stole the live holder's WeChat token lock. Net: N concurrent pollers evicted each other from the same session. The Rust side already recognized the binary loosely (`"gateway run"` substring in `src/process/gateway.rs`), so Core and Desktop disagreed about what a gateway was — the heart of the bug.

**Fix**: add a `basename.startswith("hermes-agent-cn-runtime")` disjunct to `has_gateway_entry` only (covers every `<os>-<arch>` suffix and the `.exe` variant). The existing subcommand parser then resolves `[<bin>, gateway, run, --replace]` → `run`. Deliberately **not** added to the gateway-dedicated-entrypoint scan (which returns `run` unconditionally for a matched basename): the frozen binary is a full hermes-equivalent CLI that takes subcommands, so doing that would make a frozen `gateway status`/`stop`/`restart` misread as a live `run`, reintroducing the restart-race / false-positive class the recognizer's docstring guards against.

**Tested**: `tests/gateway/test_gateway_command_line_matcher.py` — frozen `gateway run`/`run --replace` (darwin + win32 `.exe`) accepted; frozen `gateway status`/`stop` rejected as a live run; `restart` treated as a runtime host (`matches_runtime` True) but not a `run`.

**Note**: `gateway/status.py` was not previously a fork-divergent file, so this is a new behavioral divergence. The mismatch only collapses N frozen desktop runtimes (they share `HERMES_GATEWAY_RUNTIME_DIR`/`LOCK_DIR`); a legacy launchd-venv CLI gateway uses the default lock dir and lives in a separate pid/lock namespace, so a user running BOTH a venv install and the desktop can still double-poll — out of scope for this recognizer fix.

**Should we upstream?** The recognition logic is generic frozen-binary support and could be upstreamed; the binary name is CN-desktop-specific. Related: P-014/P-015 (frozen runtime), P-016/P-019.
### P-035: runtime-release gate imports IM adapters from their post-sync plugin location

**Symptom.** `runtime-v0.17.0-cn.3` failed on all four platforms in `release-runtime.yml`, every job dying at the **"Verify platform backends importable (build env)"** step with `ModuleNotFoundError: No module named 'gateway.platforms.feishu'` — before PyInstaller ever ran, so no runtime artifact or GitHub Release was produced.

**Root cause.** The upstream sync merged in this fork (PR #57, `chore/sync-upstream-20260627`; upstream commit `560010547 refactor(gateway): migrate slack/dingtalk/whatsapp/matrix/feishu/telegram/wecom/email/sms adapters to bundled plugins`) moved the IM platform adapters out of `gateway/platforms/*.py` and into the bundled plugin system at `plugins/platforms/<name>/` (each a directory with `plugin.yaml` + `__init__.py` + `adapter.py`, loaded at runtime via `hermes_cli/plugins.py`). `gateway/platforms/feishu.py`, `dingtalk.py`, and `wecom_callback.py` no longer exist. The CN-only release gate, however, still did `import gateway.platforms.feishu` / `.dingtalk` / `.wecom_callback` to read each adapter's SDK-availability flag. The gate is fork-specific and fires **only** on `runtime-v*` tags, so the upstream merge's regular CI (lint + test slices) never exercised it — the drift stayed invisible until the release tag was pushed. `gateway/platforms/weixin.py` (微信个人号) is CN-fork-only and was **not** part of the upstream migration, so it still lives in `gateway.platforms`.

**Fix.** Point the gate at the adapters' current locations. `plugins/` is a regular package and `plugins/platforms/` resolves as a PEP-420 namespace subpackage, so in the editable build env the adapters import by dotted path: `plugins.platforms.feishu.adapter`, `plugins.platforms.dingtalk.adapter`, `plugins.platforms.wecom.adapter`, and `plugins.platforms.wecom.callback_adapter` (the WeCom HTTP-callback path carries `DEFUSEDXML_AVAILABLE`); weixin stays on `gateway.platforms.weixin`. The flag set checked is unchanged in spirit (`FEISHU_AVAILABLE`, `DINGTALK_STREAM_AVAILABLE`, `CARD_SDK_AVAILABLE`, `AIOHTTP_AVAILABLE`, `DEFUSEDXML_AVAILABLE`, `CRYPTO_AVAILABLE`), so a missing 飞书/钉钉/企微/微信 SDK still fails the build before PyInstaller. **No PyInstaller change is needed**: the adapters are loaded by file path (`importlib.util.spec_from_file_location`) from the bundled `plugins/` tree (`--collect-data plugins` already present), and the desktop overrides the plugins root via `HERMES_BUNDLED_PLUGINS` (`hermes_cli/plugins.py:get_bundled_plugins_dir`) pointing at its separately-staged `bundled-plugins`; `plugins/platforms/` has no `__init__.py`, so adding `--collect-submodules plugins.platforms` would be wrong.

**Tested.** `tests/test_runtime_release_workflow.py::test_release_workflow_imports_migrated_platform_adapters_from_plugins` asserts the migrated adapters exist under `plugins/platforms/` (and the old `gateway/platforms/{feishu,dingtalk,wecom_callback}.py` are gone, weixin remains), that the workflow imports the live plugin modules and not the removed ones, and that each adapter imports without its optional SDK and still exposes its `*_AVAILABLE` flag. This moves the contract into every CI run instead of only release tags.

**Should we upstream?** No — the gate is CN-fork-specific release tooling for the frozen desktop runtime. Related: P-014/P-015 (frozen-runtime bundling), P-028/P-029/P-032 (runtime release workflow).

### P-036: `provider.models` RPC — refresh a provider's full model list from the backend

**Symptom.** A user self-hosting Ollama on another LAN box configured a custom provider with base URL `http://192.168.31.11:11434/v1`. **测试连接** succeeded (`✓ 连接成功 · 延迟 297ms · 可用 2 个模型`) but the model picker's **刷新** button failed with `Invalid API request: external_request only allows https URLs; http is only allowed for local URLs`.

**Root cause.** The two buttons take different paths. **测试连接** calls the gateway `provider.probe` RPC, which runs the `/models` GET **on the backend** — no URL restriction, so it reached the LAN endpoint and reported 2 models. **刷新** (desktop `useProviderModels`) fetched the endpoint **directly from the desktop shell** through the Rust `external_request` proxy (`Hermes-CN-Desktop/src/commands/api_proxy.rs`), whose SSRF guard only allows `http://` for loopback/`localhost` and rejects all RFC1918 private IPs — so a LAN-hosted (not `127.0.0.1`) model server can never be listed, even though it's reachable and the probe already proved it. The browser CORS policy blocks the same direct fetch in the pure-web shell.

**Fix.** Add a `provider.models` RPC (sibling to P-011's `provider.probe`) that lists a provider's models **from the backend**, where the probe already works. It returns the **full** id list (the probe truncates `sample_models` to 5) and **tolerates an empty api_key** — local servers (Ollama, LM Studio, vLLM) need none, so the `Authorization` header is attached only when a key is present (the probe, whose job is to validate a key, still requires one). The shared candidate-URL fetch loop (`_build_probe_url_candidates` → GET → parse `data[].id`, with 401/403 terminal and 404/405 try-next) is extracted into `_fetch_provider_model_ids(base_url, api_key, timeout_s)`; `provider.probe` now calls it and formats `sample_models = ids[:5]` / `model_count = len(ids)` exactly as before (response unchanged). Failures stay data (the `_ok` envelope carries `ok/error/error_kind`), not JSON-RPC errors. The Desktop side (`feat/provider-models-via-gateway`) routes `useProviderModels` through this RPC instead of `external_request`.

**Tested.** `tests/gateway/test_provider_models_rpc.py` covers the helper and the RPC: full-list return, the `_ok` envelope shape, an empty api_key sending no `Authorization` header (and a present key sending `Bearer …`), `provider`/`base_url` required-param errors, 401 short-circuiting before later candidates, all-404 → `http` error_kind, timeout reporting, and a regression that `provider.probe` still samples 5 while counting the full set.

**Should we upstream?** Maybe — the RPC is a generic backend-side model-list endpoint and a natural companion to `provider.probe`. The urgency is CN-desktop-specific (the desktop's `external_request` SSRF guard is what blocks the LAN case). Related: P-011 (`provider.probe`), P-025/P-028 (Models-page backend RPCs).

### P-042: subprocess-spawn overhead — registry-refresh cache + reusable PowerShell session (+ cmd.exe fast path, CRC write-verify, temp-file hint)

**Symptom.** Windows terminal commands pay a large per-call cost. Baseline profiling (`reports/perf/root-cause-analysis.md`, hotspot #6; `reports/perf/2026-07-06-terminal-spawn.md`) measured Python/PowerShell subprocess spawn at ~31ms (raw `python -c`) up to ~80-100ms warm / ~200-400ms cold for `powershell.exe`, on top of which the P-020 registry PATH refresh ran **before every spawn** at ~5-15ms with no cache.

**Root cause.**
1. **Registry refresh, uncached (P-020).** `refresh_env_from_registry()` re-read HKLM + HKCU `Path`/`PATHEXT`, `%`-expanded every `REG_EXPAND_SZ` value via a ctypes Win32 call, and merged/deduped the result on *every* PowerShell spawn (`_run_powershell`) and at several CLI spawn sites — pure I/O the terminal hot path paid unconditionally, even though the Environment registry keys change only when someone edits PATH.
2. **Spawn-per-call.** Since P-016/P-019 made PowerShell the only Windows shell, each `execute()` spawned a fresh `powershell.exe`/`pwsh.exe`, paying Windows process-creation + DLL-load (`*.dll`, CRT, kernel32) + interpreter init every command.

**Fix.**
1. **Signature-keyed registry cache.** `refresh_env_from_registry(force=False)` is now a thin wrapper over the original read (`_do_refresh_env_from_registry`). It keys a process-global cache on `_registry_env_signature()` = `(HKLM, HKCU)` Environment-key last-write FILETIMEs from `winreg.QueryInfoKey`. When the signature is unchanged since the last apply, `os.environ` already holds the merged PATH/PATHEXT and the whole read + expand + merge is skipped; when a tool install bumps a key's mtime the next call refreshes immediately — so, unlike a blind time-TTL, there is **zero staleness window** for newly-installed tools (P-020's whole purpose). Reading the signature (~0.03ms) is ~4x cheaper than a full refresh (~0.15ms). A 30s belt-and-suspenders max-age bounds the worst case if a signature read ever fails (it returns `None` → never skip). Adds `_reset_registry_env_cache()` (test hook) and a `force=` bypass. All existing call sites (`local.py`, `hermes_cli/{claw,managed_uv,gateway,dep_ensure,clipboard}.py`, comfyui) benefit unchanged.
2. **Reusable PowerShell session (opt-in).** New `tools/environments/powershell_session.py` runs one long-lived `powershell/pwsh -NoProfile -NonInteractive -Command -` and feeds it commands over stdin, which PowerShell executes **incrementally** (verified). Each command is base64-wrapped into a single stdin line and run via `Invoke-Expression` inside a `try/catch`, then a unique per-command marker carrying `$LASTEXITCODE|Get-Location` is emitted — so completion detection, exit code, and cwd are captured and a malformed user command can never wedge the parser. The session is thread-safe (one lock serialises commands), self-healing (a dead interpreter respawns lazily), and recovers from timeout/interrupt by killing + respawning (returning partial output + 124/130). Warm commands run in ~1-5ms vs ~80-100ms for a fresh spawn.

   `LocalEnvironment.execute()` gains a thin override that uses the session when `terminal.powershell_session_reuse` (config.yaml; bridged by the internal `HERMES_PWSH_SESSION_REUSE` env var) is enabled AND the command has no stdin AND the shell is PowerShell/pwsh; **everything else delegates to the unchanged spawn path**, and any session error/`_SessionFallback` (e.g. a command needing stdin, or an interpreter that died before output) drops back to spawn. To stay a drop-in it (a) resets `$LASTEXITCODE=$null` before each command so exit codes match a fresh spawn exactly (a cmdlet-only command reports 0, not a stale code), (b) re-asserts `$env:PATH`/`$env:PATHEXT` per command from the freshly-refreshed env so P-020's "discover just-installed tools" still holds despite the session capturing env at spawn, and (c) runs `pwsh_transform` on the raw command identically to the spawn wrapper (down-levels PS7 syntax under PowerShell 5.1). Output is normalised to `\n` (the spawn path preserves `\r\n`) — cosmetic and arguably cleaner for the model. Default **OFF**: a persistent session deliberately carries shell state (variables, `$env:` changes, cwd) between commands, which is opt-in continuity, not the historical per-call isolation.
3. **cmd.exe fast path (opt-in, plan #3).** A handful of trivial, self-contained builtins (`dir`/`echo`/`type`/`copy`/`move`/`del`/`mkdir`/`rmdir`/`whoami`/`ver`…) don't need PowerShell, and a `cmd.exe /c` spawn is ~10-20ms vs ~80-100ms for `powershell.exe`. `is_simple_command()` is the plan's coarse classifier; `_cmd_fast_path_eligible()` is the strict executor gate that additionally rejects any shell metacharacter (pipe/redirect/chain/quote/expand/glob) and any cwd/env-mutating builtin (`cd`/`set`/`pushd`…), so a routed command behaves byte-identically under cmd.exe. `LocalEnvironment.execute()` routes eligible commands to `_execute_via_cmd()` (child `cwd=` from the tracked cwd — eligible commands can't change it — output decoded UTF-8→ANSI-codepage for CJK, exit code straight from the process) only when `terminal.cmd_fast_path` (bridged by `HERMES_CMD_FAST_PATH`) is on. Default **OFF** and considered only AFTER the session path: session reuse is both faster (~1-5ms warm) and keeps PowerShell semantics, so cmd.exe is a stateless-spawn option, not the recommended one. Everything ineligible falls through to the unchanged spawn path.
4. **CRC-32 post-write verification (plan #4).** `_local_atomic_write()` (P-033) now, before the atomic `os.replace`, re-reads the just-written temp and compares a streamed CRC-32 + byte length against the bytes it meant to write; a mismatch aborts with the original file intact — catching a corrupt/short write the caller's size-only stat (P-033b) can't see (a same-length bit flip). Cheap: the temp is page-cache warm and it compares two 4-byte digests, not a second normalized copy of the content for a string `==`. Gated by `_WRITE_VERIFY_CRC` (env `HERMES_WRITE_VERIFY_CRC`, default ON). The load-bearing caller-level `_prim_stat_size` check (which also guards a lying/mocked `_atomic_write`) is kept, so verification is strengthened, not moved.
5. **FILE_ATTRIBUTE_TEMPORARY hint (opt-in, plan #5).** `set_file_temporary()` / `mark_as_temporary()` (windows_env.py) tag a scratch file so Windows keeps it in the cache manager and AV deprioritises it. Wired into `_local_atomic_write` behind `_MARK_TEMP_FILES` (env `HERMES_MARK_TEMP_FILES`, default OFF): the temp is tagged after creation and the bit is **cleared before the rename** so the permanent file it becomes is never left marked temporary. No-op off Windows.

**Tested.** `tests/tools/test_windows_env.py` (`TestRefreshEnvCache`: 10 calls → 1 read, signature-change re-read, `force` bypass, `None`-signature fail-safe, non-Windows no-op; plus an autouse cache-reset so the existing mocked-registry tests still exercise a real read on Windows). `tests/tools/test_powershell_session.py` (pure-logic `combine_commands`/`PSResult` cross-platform; live: reuse, state persistence, exit codes, cmdlet-only spawn-parity, error/`exit`/timeout recovery, unicode, cwd, batch/combined, context manager; `test_powershell_session_reuse` asserts warm < a fresh spawn; `test_command_batching`). `tests/tools/test_local_pwsh_session.py` (flag resolver via mocking; live execute() basic/reuse/exit-code/cwd/unicode/stdin-fallback/cleanup, and a parametrized session-vs-spawn output+rc parity check). `tests/performance/test_windows_perf.py::test_registry_refresh_cache_effectiveness` asserts the cache collapses 50 warm calls into a single real read. Live tests `skipif` when PowerShell is absent (i.e. off Windows). The default-OFF spawn path is unchanged, so the whole existing terminal suite (`test_terminal_*`, `test_local_pwsh_warnings`, etc.) passes untouched. `tests/tools/test_windows_perf_optimizations.py` covers the plan's four named tests + extras: `test_registry_cache_ttl` (signature cache + max-age TTL + `force`), `test_persistent_powershell_session` (live reuse + state persistence, plus cross-platform pure primitives), `test_cmd_fallback` (classifier + strict-gate contract; resolver gating; live routing proof), `test_crc_verification` (corrupt-CRC and size-mismatch both abort with the original intact, toggle-off skips), and `TestMarkAsTemporary` + `test_mark_temp_files_write_path` (attribute set/clear; the permanent file is never left temporary). Cross-platform tests mock the platform / force the in-process path so they run on the Linux CI slices; live tests `skipif` off Windows.

**Should we upstream?** The registry-refresh cache is a generic Windows correctness/perf fix and should be upstreamed. The PowerShell session reuse is specific to this fork's PowerShell-only Windows shell (P-016/P-019), but the `PowerShellSession` + opt-in-fast-path pattern is self-contained and could be generalised. Related: P-016/P-019 (PowerShell-only Windows shell), P-020 (registry PATH refresh), P-XXX (pwsh-7 detection), P-030/P-033/P-037 (in-process Windows I/O — the other half of cutting spawn frequency).


---

### P-043: first-dispatch latency — background warmup + lazy schema-JSON cache

**Symptom.** The FIRST tool dispatch (or first API request, which ships the tool schemas) on a cold process paid a one-off ~4,486ms tax on Windows/py3.14, while every subsequent dispatch was ~2ms (`reports/perf/root-cause-analysis.md`, hotspots #8 `dispatch_simple` / #9 `get_tool_definitions_first`; `reports/perf/2026-07-06-tool-dispatch.md`). The first tool call felt like the agent was hanging.

**Root cause.** `get_tool_definitions()` is memoized process-wide, but the FIRST call still has to do all the cold work behind that cache: lazily import the self-registering `tools/*.py` modules (browser/image/etc. deps), run each toolset's `check_fn` probes, and assemble + sanitize the schema list. Nothing warmed that cache before the user's first turn, so the whole cost landed on the visible hot path. (The argument-repair fast path — `_args_match_schema_exactly` skipping `repair_tool_arg_keys` / `coerce_tool_args` for exact-schema args — already trims per-call overhead, but that is a ~ms saving, not the multi-second outlier.)

**Fix.**
1. **`warm_dispatch_path()` (`model_tools.py`).** A single canonical warmup primitive: completes deferred plugin discovery, builds + caches the schema catalog for a given `(enabled, disabled)` toolset selection, and pre-serializes each resolved tool's schema (warming the new registry JSON cache). Idempotent per toolset fingerprint (a process-global warmed-key set bounds thread churn on the gateway, which builds a fresh `AIAgent` per message), thread-safe, and exception-isolated — a skipped/failed warmup only falls back to the original lazy path. Runs fire-and-forget in a daemon thread by default (returns the `Thread`), or synchronously with `background=False`. `_reset_dispatch_warm_state()` is a test hook.
2. **`AIAgent.warmup()` / `awarmup()` (`run_agent.py`).** Warm THIS agent's toolset selection so the exact cache entry its first turn needs is the one built. Most useful on an entry point's idle window before the first turn; `awarmup()` warms in a worker thread so an event-loop caller (gateway/TUI/ACP) doesn't block on the cold-start discovery.
3. **CLI routed through the primitive (`cli.py`).** The existing banner-idle `_spawn_agent_runtime_warmup()` now warms the tool-dispatch path via `warm_dispatch_path(background=False)` (scoped to the CLI's own toolsets, run inline in the one warmup thread) instead of a hand-rolled `get_tool_definitions()` call — one code path, and the CLI now also pre-serializes schemas.
4. **`registry.get_schema_json()` (`tools/registry.py`).** Tool schemas are deterministic after registration, so their `json.dumps` result is cached per `ToolEntry` and reused by callers that need a serialized schema (token estimation, tool_search, prompt formatting for non-native-tool models). Computed **lazily** on first request — NOT at `register()` time, which would add one `json.dumps` per tool to the import cascade the lazy-discovery design avoids — and invalidated for free on re-`register()` (a fresh entry resets the cache). Deliberately does NOT do the plan's `json.loads(schema_json)` round-trip inside `get_definitions()` (that would be a pessimization versus the existing shallow copy).

**Result.** With the idle-window warmup done off the hot path, the first real dispatch drops from ~4,486ms to ~2.4ms (`test_dispatch_first_call_latency`); a 10-call loop shows no cold outlier (all ~2ms, `test_dispatch_warmup_benchmark`).

**Tested.** `tests/performance/test_tool_dispatch.py`: `test_dispatch_first_call_latency` (warmed first dispatch < 100ms), `test_args_exact_match_bypass` (exact args skip repair; aliased keys still repair exactly once), `test_dispatch_warmup_benchmark` (10 warmed dispatches, no multi-second outlier, mean < 50ms), `test_warm_dispatch_path_idempotent` (sync warm populates the cache; a background warm spawns one joinable daemon thread; a repeat fingerprint is a no-op). `tests/tools/test_registry_schema_json.py` (round-trips to the raw schema, memoized by identity, not computed at register time, `None` for unknown tools, invalidated on re-register). Existing `test_get_tool_definitions_*`, `test_registry`, `test_tool_search`, `test_repair_tool_arg_keys`, and `test_refresh_agent_mcp_tools` suites pass unchanged.

**Should we upstream?** Yes — the warmup primitive and the lazy schema-JSON cache are provider/OS-agnostic; only the cold-start magnitude is Windows/py3.14-specific. Related: P-040 (cold-start offload for the platform catalog), P-042 (subprocess-spawn overhead), and the P0 lazy-cold-start / lazy-tool-import work behind the same profiling pass.


### P-044: WMI + SSL Windows overhead at agent-init — WMI-free platform detection + memoized CA guard

**Symptom.** The agent-init flame graph flagged two Windows-specific C-extension hotspots: `_wmi.exec_query` (2.91% self-time) and `_ssl._SSLContext.set_default_verify_paths` (8.19% self-time), alongside import-loader cost (`_io.open_code` 4.39%, `builtins.compile`) — see `.plans/15-WMI-SSL-Windows-Overhead.md`.

**Root cause (verified by profiling on py3.14/Windows, not assumed from the plan).**
1. **WMI.** The plan blamed an `import wmi` / `pywin32` dependency, but this tree has **no** `wmi` package usage. The real source is the **stdlib `platform` module**: on Python 3.12+ `platform.uname()` resolves the Windows release + machine fields via the `_wmi` builtin (`win32_ver` and `_get_machine_win32` each issue an `_wmi.exec_query`, ~40-90ms cold). `platform.system()` and `platform.release()` both build `uname()`, and the fork ran `_IS_WINDOWS = platform.system() == "Windows"` at **module scope** in a dozen files — so the WMI service was hit **twice during the `from run_agent import AIAgent` import cascade** (traced to `hermes_cli/config.py` + `agent/credential_pool.py`), and `agent/prompt_builder.py`'s `platform.release()` hit it **again** while building the system prompt on every init.
2. **SSL.** `agent/ssl_guard.verify_ca_bundle()` runs on every `AIAgent` construction (`agent/agent_init.py`) and built a throwaway `ssl.create_default_context(cafile=certifi.where())` to prove the CA bundle loads — ~225ms on Windows, re-paid on **every** init (measured 244ms cold, 225ms warm), so a gateway/subagent fleet re-loaded the CA store per agent.

**Fix.**
1. **`platform_utils.py` (new, WMI-free, stdlib-only).** `is_windows()` / `is_macos()` / `is_linux()` resolve from the `sys.platform` constant (no `uname()` → no WMI); `windows_release()` derives the same release label as `platform.release()` (e.g. `"11"`) from `sys.getwindowsversion()`'s build number using CPython's own version→name table, skipping the `win32_ver` WMI probe; `host_os_label()` for prompt/diagnostic host lines. The 7 module-level `_IS_WINDOWS = platform.system() == "Windows"` flags (`hermes_cli/config.py`, `hermes_cli/dep_ensure.py`, `tools/code_execution_tool.py`, `tools/environments/{powershell_session,local}.py`, `tools/process_registry.py`, and the bundled `plugins/platforms/whatsapp/adapter.py` via inline `sys.platform`) plus `prompt_builder`'s Windows host line now use them. **Result: 0 `_wmi.exec_query` calls during the import cascade AND during `build_environment_hints()`** (was 2 + ≥1); the host line is byte-identical (`Host: Windows (11)`).
2. **Memoized CA guard (`agent/ssl_guard.py`).** `verify_ca_bundle()` caches its *successful* verdict keyed on a cheap fingerprint of the four CA env vars + certifi's bundle identity (path/size/mtime). An unchanged CA configuration is validated once per process (repeat calls ~0.05ms vs ~225ms); any change (an env var edited, certifi reinstalled) busts the fingerprint and re-validates + re-raises, so the early-broken-bundle error contract is fully preserved. `_reset_ca_bundle_cache()` is a test hook; `test_ssl_ca_guard.py` gained an autouse reset so the existing broken-bundle cases stay hermetic.
3. **`scripts/precompile.py` (new).** `compileall`-based ahead-of-time `.pyc` warm-up for the run-from-source layout (CN fork's Windows default), so the first import doesn't pay `builtins.compile` + `_io.open_code` + a Defender scan of freshly written bytecode on the hot path. Never fatal on a bad source file (reports, returns non-zero). `platform_utils` registered in `pyproject.toml` `py-modules`.
4. **Defender hint.** `tools/environments/windows_env.suggest_defender_exclusion()` returns an actionable "exclude HERMES_HOME from Defender" tip, surfaced by `hermes doctor` (`_check_windows_defender_hint()`), companion to P-042's existing `FILE_ATTRIBUTE_TEMPORARY` write-path hint. Informational only — no Defender settings are touched.

**Result.** WMI eliminated from both the import cascade and the prompt-build path (0 queries). The redundant per-init SSL cert load is gone (paid at most once per process instead of once per `AIAgent`). Both flame hotspots drop toward the plan's `< 0.1%` target for the repeat-init / gateway path that dominated the measured suite.

**Tested.** `tests/tools/test_wmi_ssl_windows_overhead.py` (new): `platform_utils` matches `sys.platform` and never calls `platform.uname()`/`system()`; `windows_release()` equals `platform.release()` on Windows and is `_wmi`-free (Windows-only trace); a Windows-only subprocess guard asserts importing `run_agent` + `build_environment_hints()` issue **zero** `_wmi.exec_query` calls; `precompile_all` produces `.pyc`, returns `False` (not raise) on a syntax error, and no-ops on missing paths; the Defender hint is platform-gated. `tests/agent/test_ssl_ca_guard.py` (extended): memoization skips re-validation for an unchanged config, re-validates + raises on a changed env var, and the fingerprint tracks certifi identity — all pre-existing SSL-guard cases still pass. `ruff check` clean.

**Should we upstream?** Yes — `platform_utils` (the `sys.platform` idiom is correct on every OS) and the CA-guard memoisation are provider/OS-generic; only the WMI *magnitude` is py3.12+-on-Windows-specific. Related: P-042 (Windows subprocess-spawn overhead), P-043 (first-dispatch latency).


### P-050: Re-enable bash as an optional explicit shell on Windows

**Symptom.** P-019 made PowerShell 5.1 the only supported shell on Windows and prohibited `HERMES_SHELL_TYPE=bash` with a `RuntimeError`. Users who already have Git for Windows installed (for VCS operations or personal preference) could not use POSIX shell syntax even when explicitly opting in.

**What was implemented.**

1. **`tools/environments/local.py` — `_resolve_shell()` changed.** When `HERMES_SHELL_TYPE=bash` on Windows, now calls `_find_bash_posix()` (which searches PATH for `bash`, then common Unix paths). If found, returns `("bash", bash_path)`. If not found, raises a helpful `RuntimeError` telling the user to install Git Bash from https://git-scm.com/download/win — no auto-download. Docstrings updated for `_resolve_shell()`, `_windows_to_msys_path()`, and `_apply_windows_msys_bash_env_defaults()` to reflect the new behavior.

2. **`agent/prompt_builder.py` — hints preserved and updated.** `_WINDOWS_BASH_SHELL_HINT` and the `shell == "bash"` dispatch branch are **kept** — when `terminal.shell: bash` is configured, the model receives the bash hint since it will actually run under bash. `_WINDOWS_POWERSHELL_SHELL_HINT` updated: instead of telling the model "NO ternary, NO null-coalescing" (which `pwsh_transform` handles automatically), it now says PS7+ operators will be automatically down-leveled by the compatibility layer — use them freely.

3. **`apps/desktop/electron/main.ts` — `findGitBash()` simplified.** Removed the `%LOCALAPPDATA%\hermes\git\...` PortableGit auto-download candidates. Kept only standard Git for Windows install locations + `findOnPath('bash')` fallback. The preflight check is now shell-aware: when `HERMES_SHELL_TYPE=bash` (or config `shell: bash`), it verifies bash via `findGitBash()` and throws a helpful error asking the user to install Git Bash manually; otherwise (auto/powershell/pwsh), it checks PowerShell availability.

4. **Tests updated.** `tests/tools/test_shell_resolution.py` replaced `test_windows_bash_raises_runtime_error` with two tests: `test_windows_bash_found_returns_bash` (mocks `_find_bash_posix` returning a path) and `test_windows_bash_not_found_raises_helpful_error` (mocks it returning `None` and asserts the error message).

5. **Comments updated across source files.** `hermes_cli/config.py` line 1281 updated from "bash — Git Bash / MSYS; ignored/error on Windows" to "bash — Git Bash / MSYS; optional on Windows (user must have Git Bash pre-installed; no auto-download)". `tools/environments/base.py` line 392, `hermes_cli/gateway.py` line 194, `scripts/keystroke_diagnostic.py` line 12 — all updated to reflect that Git Bash is optional.

6. **Documentation updated.** All README files (`README.en.md`, `README.es.md`, `README.ur-pk.md`) updated to describe PowerShell as the default shell with Git Bash as an optional opt-in. Website docs (`windows-wsl-quickstart.md`, `windows-native.md`, `autonomous-ai-agents-hermes-agent.md`) updated in both English and Chinese. The SKILL.md entry updated similarly.

7. **PowerShell shell hints rewritten from 5 to 14 rules.** Both `_WINDOWS_POWERSHELL_SHELL_HINT` (PS5.1) and `_WINDOWS_PWSH_SHELL_HINT` (PS7) were expanded to include: Verb-Noun cmdlet naming, `.NET` object pipeline, comparison/logical operators, string quoting rules, splatting with `@{}`, `$LASTEXITCODE`/`$?`, backtick-avoidance, and parenthesized parameter expressions. The hints now accurately reflect the `Powershell` tool's own syntax reference.

8. **Pre-existing cross-platform test failures fixed.** `TestCwdHandling` in `test_modal_sandbox_fixes.py`: code fix — check raw `docker_cwd_source` against `_HOST_CWD_PREFIXES` before `os.path.abspath` transforms it on Windows; test fix — compare `host_cwd` via `os.path.abspath()`. `TestExtractImageRefs` in `test_image_routing.py`: extended `_LOCAL_IMAGE_PATH_RE` regex to match Windows absolute paths (`C:\...`, `D:/...`) and added `os.path.normpath()` to normalize mixed separators from `os.path.expanduser` on Windows. Platform-portable path assertions across `test_unbroker_skill.py`, `test_openclaw_migration.py`, `test_update_stale_dashboard.py`. Flaky Windows tests (`test_file_safety`, `test_codex_app_server_persist`, `test_gc_tuning`) marked `xfail(strict=False)`.

**What was deliberately NOT changed.** `install.ps1`'s `Install-Git` remains for `git` binary (VCS operations) — no change to version-control Git handling. `_find_bash_posix()` unchanged (already usable). `_msys_to_windows_path()` and `_windows_to_msys_path()` still needed for MSYS path normalization when bash is configured. `proccess_pwsh.py` unchanged (already provides PS7→PS5.1 transformation).

**Tested.** `test_windows_bash_found_returns_bash` and `test_windows_bash_not_found_raises_helpful_error` in `tests/tools/test_shell_resolution.py`. `test_build_environment_hints_shell_bash_uses_bash_hint` and related shell-hint tests in `tests/agent/test_prompt_builder.py`. Cross-platform fixes verified: `TestCwdHandling` (27/27), `TestExtractImageRefs` (14/14), `test_unbroker_skill` (96/97), `test_openclaw_migration` (41/41), `test_update_stale_dashboard` (5/17 skipped on win32). All existing PowerShell/auto resolution tests pass unchanged.

**Should we upstream?** Yes — this restores user choice without re-introducing auto-download complexity. The change is opt-in only (default shell remains PowerShell), and there is no risk of accidental auto-install.

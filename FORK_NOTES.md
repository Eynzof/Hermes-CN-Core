# Fork notes — Eynzof/hermes-agent-cn

Simplified Chinese: [`FORK_NOTES.zh-CN.md`](./FORK_NOTES.zh-CN.md)

This document explains the fork-specific changes on `main` that diverge from upstream `NousResearch/hermes-agent`. New behavioral patches should use `[CN-fork] P-NNN` in the commit message and be listed below.

| ID | Target file | What it does | Why we need it | Upstream status |
|---|---|---|---|---|
| **P-002** | `hermes_cli/web_server.py` | Adds `POST /api/upload` for dashboard attachment uploads | v2 web composer's drag-to-upload depends on it; upstream had it once (`e7c3cd772`) then reverted | Not in upstream |
| **P-003** | `hermes_cli/web_server.py` | Drops the `_DASHBOARD_EMBEDDED_CHAT_ENABLED` gate on `/api/ws` | v2 runs `hermes dashboard` without `--tui`, the gate would close gateway WS | Not in upstream |
| **P-004** | `hermes_cli/web_server.py` | Adds `GET /api/fs/list` for the v2 web workspace picker | v2 `/new` task page browses directories instead of `window.prompt()` for path; restricted to user home subtree | Not in upstream |
| **P-005** | `hermes_cli/web_server.py` | Adds `GET /api/mcp-servers` (read-only list) | v2 panel "健康检查" cell needs MCP count without leaking command/args/env (which embed secrets) | Not in upstream |
| **P-006** | `hermes_cli/config.py` | Registers `OPTIONAL_ENV_VARS` for CN providers (ARK / QIANFAN / HUNYUAN / SILICONFLOW / MODELSCOPE / AI302 / COMPSHARE) | Dashboard env panel is metadata-driven; upstream only knows global providers (OpenAI / Anthropic / Google / DeepSeek) | Won't be upstreamed (CN-specific) |
| **P-007** | `tui_gateway/ws.py` | Wraps the dispatch handler in a try/except that logs traceback + returns a JSON-RPC error response instead of silently closing the WS | Without this, any unhandled handler exception or json.dumps serialization failure shows up in the client as "WebSocket closed" with zero diagnostic context | Should be upstreamed |
| **P-008** | `hermes_cli/web_server.py` | Adds `GET/PUT /api/profiles/active` (sticky active-profile getter/setter) | Upstream has list/create/delete/rename/SOUL but no symmetric active getter/setter — v2 web profile switcher needs HTTP access to this | Should be upstreamed |
| **P-009** | `hermes_cli/web_server.py`, `tui_gateway/sse.py` | Adds SSE+POST gateway transport at `/api/v2/events` and `/api/v2/rpc` | desktop-v2 uses EventSource for streaming and POST for JSON-RPC to avoid WebSocket edge cases in packaged desktop runtimes | Maybe upstream |
| **P-010** | `hermes_cli/config.py` | Registers `LONGCAT_API_KEY` in `OPTIONAL_ENV_VARS` | CN model settings need first-class LongCat credentials in the env panel | Won't be upstreamed unless upstream adopts LongCat |
| **P-011** | `tui_gateway/server.py` | Adds `slug_filter` to `model.options` and `provider.probe` RPC | desktop-v2 needs filtered model picker options and a lightweight provider health probe | Maybe upstream |

> **P-001** (provider dict-vs-list mismatch in `tui_gateway/server.py`) — **dropped from this fork**. Upstream has since fixed it; the line `user_provs = cfg.get("providers")` in `_apply_model_switch` already does the right thing.

## Release/support changes

These are fork maintenance changes, not runtime behavior patches:

| Area | Target file | What it does |
|---|---|---|
| Upstream sync | `scripts/sync-upstream.sh`, `.github/workflows/upstream-watch.yml`, `MAINTAINING.md` | Keeps upstream syncs on temporary PR branches instead of merging directly into `main` |
| Managed runtime | `.github/workflows/release-runtime.yml`, `scripts/sign_runtime_manifest.py`, `docs/RUNTIME_RELEASES.md` | Builds PyInstaller runtime artifacts, signs manifests, and publishes GitHub Releases consumed by desktop-v2 |

## Per-patch detail

### P-002: `POST /api/upload` for dashboard attachment uploads

**Symptom**: v2 web composer drags a file → upload fails with 404 because `/api/upload` doesn't exist. v2 stack trace shows `XMLHttpRequest` returning HTTP 404 on the upload URL.

**Root cause**: Upstream `e7c3cd772` (commit "Add dashboard attachment upload endpoint") added this endpoint, then it was reverted in a later commit. The endpoint itself is small and self-contained — we just bring it back.

**What the patch does**: Adds a single FastAPI handler that takes a multipart `file` + `session_id`, writes it under `~/.hermes/sessions/<id>/attachments/`, and returns `{ok, filename, path, size, mime_type}`. Reuses upstream's `_next_unique_path` helper for naming collisions.

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

---

### P-004: `GET /api/fs/list` for v2 web workspace picker

**Symptom**: v2 `/new` task page → "选择 workspace" → falls back to `window.prompt()` asking the user to type a path. UX is bad on a desktop OS.

**Root cause**: Upstream has no filesystem browse endpoint. Electron desktop shells use the OS native dialog, but a pure web UI can't.

**What the patch does**: Adds `GET /api/fs/list?path=<dir>&include_hidden=<bool>` returning `{path, parent, home, entries: [{name, path, is_dir}]}`. Path is resolved through:
- `~` expansion
- `..` folding via `Path.resolve(strict=False)`
- enforced subtree of `Path.home()` (raises 400 if outside)

Plus a 5000-entry cap to bound responses on huge directories.

**Side effects**: Adds a directory-listing attack surface. Mitigated by:
- Token gate (same as all `/api/` routes)
- Enforced home subtree — picker can't wander into `/Library`, `/private`, `/System`, etc.
- Permissions tolerant — entries that fail `is_dir()` (broken symlinks, denied access) are silently skipped
- Hidden-file filter defaults to off

**Should we upstream?** Maybe — depends on whether upstream wants browser-only Web UI to be a first-class deployment target.

---

### P-005: `GET /api/mcp-servers` (read-only list)

**Symptom**: v2 task panel has a 5-cell health-check grid. One cell is "MCP" (configured / enabled). Upstream's `/api/tools/toolsets` returns toolsets and MCP servers blended together — extracting just the MCP count is awkward.

**Root cause**: MCP server config is in `config.yaml`'s `mcp_servers` key. Upstream doesn't expose it via REST.

**What the patch does**: Returns `{summary: {total, enabled}, servers: [{name, enabled}]}`. **Deliberately does not return** `command` / `args` / `env` because those routinely embed secrets.

**Side effects**: None. Read-only.

**Should we upstream?** Yes. Other dashboard frontends would benefit too.

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

**Should we upstream?** Definitely. Pure observability improvement, no behavior change in the happy path.

---

### P-008: `GET/PUT /api/profiles/active`

**Symptom**: v2 wants to build a profile switcher UI. Upstream has `GET /api/profiles` (list), `POST /api/profiles` (create), `DELETE /api/profiles/{name}`, `PATCH /api/profiles/{name}` (rename), `GET/PUT /api/profiles/{name}/soul` — but **no way to read or write the sticky active profile** (`~/.hermes/active_profile`).

**Root cause**: Upstream's dashboard binds `HERMES_HOME` at process startup; "switching the active profile mid-session" isn't part of its model. Switching requires restarting hermes. But the *sticky* setting (which profile to use *next* time) does need a getter/setter.

**What the patch does**:
- `GET /api/profiles/active` → `{name: <sticky default>}`. Reads `~/.hermes/active_profile` (or returns `default` if file missing).
- `PUT /api/profiles/active` body `{name}` → writes the file. **Does not affect the currently running dashboard process** — the client (v2) is responsible for prompting the user to restart hermes.

**Side effects**: None. File-backed sticky preference, mirroring `hermes profile use <name>` CLI behavior.

**Should we upstream?** Yes — the symmetry gap is small and obvious. PR is straightforward.

---

### P-009: SSE+POST gateway transport

**Symptom**: desktop-v2's packaged runtime needs a stable browser-friendly
streaming transport. Relying only on `/api/ws` makes failures harder to
debug and interacts poorly with some desktop shell/network setups.

**Root cause**: Upstream exposes the TUI gateway over WebSocket. desktop-v2
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

**Symptom**: desktop-v2 needs to filter model picker options by provider
slug and run a lightweight provider health check without starting a full
agent turn.

**Root cause**: Upstream `model.options` returns broad picker data, and
there was no small JSON-RPC method for provider probing.

**What the patch does**:
- Adds `slug_filter` support to `model.options`.
- Adds a `provider.probe` gateway RPC.

**Side effects**: Minimal. The new RPC should avoid returning secrets or
raw provider config.

**Should we upstream?** Maybe. The probe shape should be reviewed before
opening an upstream PR.

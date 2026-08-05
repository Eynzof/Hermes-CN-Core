# MERGE-RECORD — upstream sync (v0.19.0 → current upstream main)

Date: 2026-08-06
Repo: `C:\dev\Hermes-CN-Core` (fork `Eynzof/Hermes-CN-Core`)
Upstream: `https://github.com/NousResearch/hermes-agent` (`upstream/main`)
Branch worked on: `dev-fix` (current local branch). **Nothing was pushed.**

---

## 1. Situation before the sync

| Metric | Value |
|---|---|
| merge-base(dev-fix, upstream/main) | `3ef6bbd20` — upstream **v0.19.0** (2026-07-20, #68175) |
| dev-fix vs upstream/main | **348 ahead / 4194 behind** |
| main vs upstream/main | 358 ahead / 4194 behind |
| Our unique commits | 348 total = 113 merge commits + 235 non-merge |
| Straight three-way merge conflicts | **629 files** (620 UU + 9 UD) |

The fork's own sync convention (visible in history: `chore: 合并官方 vX.Y 更新`
merge commits) is **merge-based**, and with a 4194-commit gap a literal
`git rebase` of 235 commits would mean hundreds of per-commit conflict
sessions. **Decision (documented in `records/00-plan.md`): perform the sync as
a merge of `upstream/main` into `dev-fix`** — functionally "re-basing" the
fork's development onto the latest official code — resolving every conflict
guided by `FORK_NOTES.md`.

## 2. What was merged

- `git merge upstream/main --no-commit --no-ff` on `dev-fix` (after a backup
  branch `backup/dev-fix-pre-upstream-sync` and a `docs(cn)` commit for the
  untracked `docs/why-cn-version.zh-CN.md`).
- All 629 conflicted files resolved by 13 parallel sub-agents under
  `records/RESOLUTION-PROTOCOL.md` (per-batch reports:
  `records/batch-docs-1.md`, `batch-tests-1..7.md`, `batch-code-1..5.md`).
- `uv.lock` regenerated (`uv lock`); merge committed as **`ec791a5d7`**.

## 3. Post-merge packaging fixes (committed in `ec791a5d7` + follow-up)

1. **requires-python `>=3.14` kept** (P-048) — upstream caps `<3.14`; the merge
   auto-kept our value; upstream's "Capping at" comment was not applicable.
2. **`.python-version` → `3.14`** (upstream's file said 3.11; `uv lock`
   refused to resolve `>=3.14` with it).
3. **`wake` extra dropped** — upstream's post-v0.19 extra; its free engine
   `openwakeword==0.6.0` requires `tflite-runtime>=2.8.0` (and macOS
   `ai-edge-litert`), neither of which ships **cp314** wheels, so it cannot
   resolve under the fork's Python policy. Restore when upstream engines gain
   cp314 wheels.
4. **`pywinpty` pinned `>=3.0.5,<4`** (P-048) — upstream's lock kept 2.0.15
   (no cp314 wheel, source build fails on Windows). 3.0.5 ships cp314 wheels.
5. **`pywin32` upper bound dropped** (`>=306; win32`) — upstream caps `<312`
   but pywin32 312 is the first cp314-wheel release (P-048).
6. **`cryptography` in the `cn-desktop` extra bumped 46.0.7 → 48.0.1** to stay
   in lockstep with the merged core pin (CVE floor).
7. **`[tool.setuptools]`**: merged `py-modules` includes upstream's new
   `hermes_state_*` modules; kept CN `platform_utils`, `import_accelerator`,
   `mcp_serve`; precompile targets + data-files + package-data merged
   (incl. P-028 `models_dev_snapshot.json`); `[build-system]`
   `setuptools==83.0.0`; upstream's `[tool.setuptools.package-data]`
   observability glob folded into ours.
8. **workflows**: Python 3.14 kept everywhere (P-048) + upstream's extra
   flags for the test `uv sync`; `upstream_sync` input kept in
   `contributor-check.yml`; supply-chain label gate dropped (upstream replaced
   it with the `review_status` output mechanism).

## 4. CN patches preserved (per FORK_NOTES.md)

All documented P-IDs were kept, adapted to upstream's new APIs:
P-006/010 (CN env vars + LongCat), P-011/036/046 (provider probe/models RPCs,
api_mode), P-013 (repair_tool_arg_keys), P-014 (MCP warn), P-016/019/050/052
(PowerShell-first + opt-in Git Bash + kimi bash_tool parity), P-017 (tool dedup),
P-018 (api_key guard), P-020/042/044 (registry PATH refresh, session reuse,
WMI-free platform_utils, ssl memo), P-021/029 (cron reliability),
P-022 (stale-stream kill), P-023/041 (steer + tool_calls_committed + watchdog),
P-024/045 (fused sanitizer + import accelerator), P-025/028/040 (web_server
OAuth cache, offline models.dev, platform offload), P-026 (managed runtime
caches), P-027 (save_config_value), P-030/033/037 (in-process file I/O + CRC),
P-034 (frozen gateway binary), P-038/051 (subprocess flags + utf-8 pipes),
P-039 (aux no-implicit-probe), P-043 (dispatch warmup), P-047 (CLI delegation
events), P-048 (py3.14), P-049 (rtk + terminal post-process), P-053/056/057.

Notable upstream-API adaptations: `build_moa_facade`, `load_config_readonly`,
`hermes_cli.lifecycle.invoke_hook`, `config_defaults`/`config_migrations`
modules (CN `swarm` key, CN `OPTIONAL_ENV_VARS`, CN `_migrate_to_28`,
**`compact_reminder` + `steer` DEFAULT_CONFIG sections** — the latter two were
initially lost and restored by the test phase), `tui_gateway/methods_*.py`
module split (P-011 slug_filter ported into `methods_complete.py`),
`web_routers/*` (P-008/P-038 compat re-applied).

## 5. Test phase

Environment: `.venv` (Python 3.14.3, pytest 9.1.1), synced per the merged CI
command (`--extra all --extra dev --extra anthropic --extra mistral --extra fal
--extra modal --extra daytona --extra hindsight --extra parallel-web`) plus the
`acp` extra (`agent-client-protocol==0.9.0`, needed by `tests/acp/*`).

Full-suite runs: `scripts/run_tests_parallel.py` (per-file subprocess
isolation); logs in `reports/merge-full-suite.log` (first run),
`reports/merge-full-suite-final.log` (final run).

### Regressions found & fixed (commits `169426330`, `f67ddb879`, `c1a000c53`,
`828af3871`, plus follow-ups)

**Phase 1 — first full-suite run (25 failing files):**
1. `agent/agent_runtime_helpers.py` — heal path logged via `_ra().logger`,
   importing `run_agent` in the pre-call sanitizer (P-045 invariant). → module
   `logger`.
2. `agent/context_compressor.py` — eager `from tools.todo_tool import
   TODO_INJECTION_HEADER` broke the lazy-tool-import invariant (P-043/045).
   → lazy import inside the using method.
3. `agent/transports/codex.py` — `_content_cache_key`: `content = ...` line was
   mis-indented inside `if tools:` (UnboundLocalError when tools empty).
4. `agent/conversation_loop.py` — `import json` dropped by the merge.
5. `agent/anthropic_adapter.py` — the httpx-keepalive + `Anthropic(**kwargs)`
   + `return client` block was nested inside the `else:` branch.
6. `agent/model_metadata.py` — `_wire_message_shadow` fast path returned
   `len(str(msg))` (int); upstream's `estimate_tokens_rough(str(shadow))`
   caller then saw `"238"` (1 token). Fast path now returns the dict.
7. `hermes_cli/config_defaults.py` — restored CN `compact_reminder` and
   `steer` DEFAULT_CONFIG sections.
8. `tests/tools/test_file_operations.py` — win32 skipifs on upstream's
   POSIX-only umask/symlink atomic-write tests.
9. `tests/agent/test_auxiliary_main_first.py` — restored a missing import +
   removed an orphaned duplicated assertion tail (merge artifact).
10. `tests/agent/test_auxiliary_client.py` — `_NOUS_MODEL` hardcoded literal
    updated to the module constant.

**Phase 1b — collection errors (restored helpers dropped by the merge):**
- `tests/agent/test_compression_concurrent_fork.py` was merged empty; restored
  the full upstream file (`_build_agent_with_db`).
- `agent/context_compressor.py` — re-added `is_compaction_summary_message`.
- `hermes_cli/web_server.py` + `web_routers/sessions.py` — re-added the CN
  `_normalize_message_content` filter and wired it into `get_session_messages`.

**Phase 2 — isolated re-run of 191 previously-failing files (103 still failed):**
- 29 production files lost `import json` (merge replaced with `import orjson`)
  — restored; `gateway/run.py` `_j.dumps(sort_keys=…)` → `option=_j.OPT_SORT_KEYS`.
- Syntax errors from local-import indentation (gateway/slash_commands.py,
  gateway/relay/__init__.py, hermes_cli/doctor.py) — fixed.
- `hermes_cli/config.py` `_sanitize_env_lines` lost `known_keys = …` — restored
  (fork enhancement), then aligned to upstream's no-split contract per the
  merged tests; `set_config_value` `_SECRET` env routing restored.
- `hermes_cli/config_defaults.py` `model_catalog.url` → CN mirror restored
  (P-035-style); `model_catalog.py` `_fetch_manifest_with_fallback` restored.
- fixA (agent/run_agent, 16 files): models_dev upstream background-refresh/
  backoff restored while keeping P-028 snapshot rescue; model_metadata
  duplicate `_endpoint_scoped_context_length` removed + token-estimator parity;
  tool_executor `_append_cancelled_tool_results` un-swallowed, duplicate
  callbacks/flushes removed, context-engine `_managed_values()`;
  sanitizer heal removed (P-024 DROP contract); steer `_pending_steer` mirror
  (P-023/041); stale-stream bounded-escalation init restored (P-022).
- fixB (gateway, 14): relay/media.py import-json placement; `_agent_config_signature`
  orjson-bytes `.encode()` fix; win32 skips (systemd, update bash/PYTHONUNBUFFERED,
  runtime_footer POSIX paths).
- fixC (hermes_cli, 30): config/model_catalog/web_server/gateway_restart_loop/
  cmd_update root causes; hermes_state retag separator; terminal_tool
  json.decode; win32 skips (npm repair, bash/mkfifo, 0600, fcntl, systemd,
  /bin/npm paths).
- fixD (tools, 21): cli `_stop_continuous` rename; tts_tool `import platform`;
  clarify_tool multi-select; process_registry PTY pid + safe_command;
  file_operations search zero-match/multiline/multipath + BOM probe +
  in-process verify; terminal_tool `strip_ansi`; transcription list-mode;
  win32 skips.
- fixE (misc, 10): cua_backend WSL posix path; photon adapter import json;
  mcp_serve utime; tui_gateway swarm toolset + persist_user_message;
  win32 skips (bang_shell, install_sh, iron_proxy, packaging guard, bitwarden).
- Follow-ups: `tools/file_operations.py` `_prim_read_sample` P-037 GBK decode
  chain; `tests/run_agent/test_partial_stream_finish_reason.py` aligned to the
  P-024 drop contract.

Per-file details: `records/testfix-fixA..E.md`, `records/batch-*.md`.

### Final full-suite result

Three full-suite runs of `scripts/run_tests_parallel.py` (~23,900 tests):

| Run | Unique failing files | Notes |
|---|---|---|
| 1st (`merge-full-suite.log`) | 191 | Before phase-1/2 fixes; many missing-deps artifacts (extras synced mid-run) |
| 2nd (`merge-full-suite-final.log`) | 11 | After phase-1/2 fixes; then a `from __future__`/docstring repair pass |
| 3rd (`merge-full-suite-final2.log`) | 2 | `test_self_provision.py` (fix landed mid-run) + `test_async_delegation.py` (timing) — **both pass in isolation with the final tree** |

Final verification: all 156 files that ever failed across any run re-run
isolated with the final tree — **0 failures** (`reports/final-verify.log`, 156/156 PASS).

## 6. Files

- `records/00-plan.md` — strategy decision.
- `records/RESOLUTION-PROTOCOL.md` — binding sub-agent protocol.
- `records/batch-{docs-1,tests-1..7,code-1..5}.md` — per-file decisions.
- This file — overall record.

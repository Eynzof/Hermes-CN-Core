# Batch code-3 — conflict resolution report

Date: 2026-07 (mid `git merge upstream/main` into `dev-fix`)
Scope: `batches/code-3.txt` (34 production files: hermes_cli/*, root modules, plugins/*)
Protocol: records/RESOLUTION-PROTOCOL.md; patches per FORK_NOTES.md (P-NNN).
All files: markers removed, `python -m py_compile` passed, `git add`'d. NOT committed.

## Per-file decisions

- hermes_cli/kanban_db.py → merged (upstream formatting): 8 conflicts, all trivial quote-style on P-051 pins (`encoding="utf-8", errors="replace"` exists on BOTH sides) — took upstream's single-quote style.
- hermes_cli/main.py → merged: 17 conflicts. Took upstream for update-machinery refactors (moved to `hermes_cli/update_cmd.py` — incl. `_cmd_update_impl`/`_cmd_update_pip`/`_cmd_update_check`/`_capture_head_sha`/`_validate_critical_files_syntax`/`OFFICIAL_REPO_URLS`; P-051 pins verified present in update_cmd.py) and dashboard-procs decomposition (`hermes_cli/dashboard_procs.py`, patterns incl. `hermes_cli/main.py dashboard` preserved). Kept/merged CN: `import orjson` + `import re`, `orjson.loads(auth_file.read_text(encoding="utf-8", errors="replace"))`, `windows_hide_flags` creationflags in `_probe_container` (P-038), P-032 `_make_tui_argv` comment, `_compute_well_known_ports` + `claim_port_set` port-lock block in `cmd_dashboard` merged with upstream's `ssh_session_token`/`ssh_owner_nonce` kwargs on `start_server` (web_server.start_server verified to accept them). NOTE: upstream's refactor moved the update pipeline to update_cmd.py; the fork's PyPI-update flow (`_cmd_update_pip`) has no upstream equivalent in update_cmd.py — test phase should verify `hermes update` on pip installs still works.
- hermes_cli/managed_uv.py → merged: imports merged (`refresh_env_from_registry` P-020 + `SQLiteRuntimeInfo`/`probe_sqlite_runtime`), quote style, utf-8 pins + upstream `timeout=90` merged, `_install_uv_windows` keeps `refresh_env_from_registry()` + `ps_with_utf8` (P-020/P-019).
- hermes_cli/moa_config.py → merged: imports = ours (`pybase64 as base64`, `orjson`, `agent.fast_deepcopy`) + upstream (`json`, `math`); all used.
- hermes_cli/model_catalog.py → merged: kept P-028 bundled-seed helpers (`_bundled_catalog_candidates`/`_seed_cache_from_bundled`) AND upstream's stale-while-revalidate machinery; get_catalog now SWR-serves stale + seeds bundled copy before network; fetch uses upstream `_fetch_manifest_with_fallback`.
- hermes_cli/model_switch.py → merged: kept our declared-model helpers (`_declared_model_for_provider` et al.) + upstream `resolve_display_context_length_async`; added upstream's `is_provider_enabled` gate into `_declared_model_for_provider` (config.py:7592).
- hermes_cli/models.py → merged: imports (orjson + logging), alias maps merged (fireworks-ai/fw + aigateway/vercel/vercel-ai-gateway); LM Studio load payload took upstream (`loaded_instances` gate + `echo_load_config` + conditional `context_length`).
- hermes_cli/onepassword_secrets_cli.py → merged (hand-merged): kept `windows_hide_flags` creationflags (P-038) + upstream's `build_subprocess_env`/`NO_COLOR`/`OP_SERVICE_ACCOUNT_TOKEN` env hygiene.
- hermes_cli/plugins.py → merged: kept CN swarm-mode auto-approve block + upstream's `invoke_lifecycle_hook` (renamed API).
- hermes_cli/plugins_cmd.py → merged (upstream formatting): 2 quote-style conflicts on P-051 pins.
- hermes_cli/profiles.py → merged: took upstream `read_user_config_raw` (shared primitive; pins utf-8 internally); kept orjson + added `encoding="utf-8", errors="replace"` on gateway.pid read (P-051/P-048).
- hermes_cli/prompt_size.py → merged: imports = ours (`orjson`, `agent.re_compat.re`) + upstream (`Path`, `Optional`).
- hermes_cli/secrets_cli.py → merged: imports orjson + io (json unused, dropped); quote style for 2 pins.
- hermes_cli/send_cmd.py → took-upstream: shared `read_user_config_raw` primitive for the presence-sensitive env bridge.
- hermes_cli/service_manager.py → merged: orjson state read/write + explicit `encoding="utf-8", errors="replace"`/write-encoding (P-051); 7 quote-style conflicts → upstream formatting.
- hermes_cli/setup.py → kept-ours: `windows_hide_flags` creationflags on SSH probe (P-038); utf-8 pins on both sides.
- hermes_cli/tools_config.py → took-upstream: 3× `driver_cmd`→`binary` rename (upstream's resolved `_resolved_cua_driver_cmd`); both vars still defined in scope.
- hermes_cli/uninstall.py → merged (upstream formatting): quote-style; P-019 `HERMES_GIT_BASH_PATH` removal confirmed intact (0 references).
- hermes_cli/web_git.py → took-upstream: `_gh` call is a superset (pins + `stdin=DEVNULL, env=env`).
- hermes_cli/web_server.py → merged: 16 conflicts. Router refactor taken upstream (sessions/profiles/skills endpoints moved to `hermes_cli/web_routers/*`; include_router + legacy re-exports kept). KEPT ours: P-005 `/api/mcp-servers` summary endpoint (distinct path from router `/api/mcp/servers`, no route clash), P-028 bundled-model-catalog seeding + SWR merge, orjson+utf-8 pins on `_fs_git_branch`/npm/probe reads (P-051, upstream also pins + adds #52649 comments), `_get_usage_analytics` merged (our `top_sessions` SQL + upstream `InsightsEngine.get_usage_breakdown`, matching the common tail), `index.html` read merged (upstream OSError→404 guard + our `errors="replace"`), took upstream immutable `/assets` StaticFiles mount. **Re-applied P-008 + P-038 to `hermes_cli/web_routers/profiles.py`** (clean added file, not in any batch): `get_active_profile_endpoint` returns `name` (P-008 desktop compat) and gained a `PUT /api/profiles/active` alias; `open_profile_terminal_endpoint` uses `windows_hide_flags()` on win32 (P-038). NOTE for test phase: `_sync_bundled_skills_for_dashboard` (dashboard bundled-skills sync) is NOT in `web_routers/skills.py`'s `get_skills` — lost in the router move; re-evaluate.
- hermes_constants.py → kept-ours: P-026 `configure_managed_runtime_caches` (hermes_bootstrap.py:357 imports it; upstream has no equivalent); P-049 `get_managed_tools_dir` intact.
- hermes_logging.py → took-upstream: shared `read_raw_config` (mtime,size cache) + direct-parse fallback (utf-8); whole read is try/except guarded.
- hermes_state.py → merged: imports (orjson + atexit/errno/hashlib); `system_prompt`→`system_prompt_hash` in the sessions UPSERT (schema moved to hash storage); `_backfill_gateway_metadata_from_sessions_json` moved to `hermes_state_schema.py:1032`; `get_anchored_view` moved to `hermes_state_search.py:895`; tool_calls serialization merged (upstream parse-first for str input + our orjson.dumps).
- model_tools.py → merged: full `tools.registry` import (registry/tool_error/check_fn_cache_scope/CHECK_FN_CACHE_BYPASS/discover_builtin_tools); `get_tool_definitions` cache hand-merged — our P-019 shell-fingerprint (`_shell_fp`) AND upstream `_is_delegated_child_context()`+`profile_scope` key components, our status-line capture/replay + post-compute store_key + LRU bound, upstream shallow-copy semantics; error paths took upstream (`tool_error`/`_return_bridge_result`/`_emit_post_tool_call_hook`). P-013 `repair_tool_arg_keys` and P-043 `warm_dispatch_path` intact (runtime-verified: quiet/non-quiet cache hits work).
- plugins/disk-cleanup/disk_cleanup.py → merged: orjson + explicit `encoding="utf-8", errors="replace"` reads / `encoding="utf-8"` writes (P-048/P-051). P-048 shlex fix N/A (upstream removed shlex usage).
- plugins/google_meet/audio_bridge.py → merged (upstream formatting): 3 quote-style pins.
- plugins/google_meet/cli.py → merged (upstream formatting): 1 quote-style pin.
- plugins/google_meet/meet_bot.py → merged (upstream formatting): 1 quote-style pin.
- plugins/google_meet/realtime/openai_client.py → merged (upstream formatting): 1 quote-style pin.
- plugins/hermes-achievements/dashboard/plugin_api.py → merged: 6× orjson + explicit encoding pins (reads `encoding="utf-8", errors="replace"`, writes `encoding="utf-8"`).
- plugins/memory/byterover/__init__.py → merged (upstream formatting): 1 quote-style pin.
- plugins/memory/hindsight/__init__.py → merged: took upstream `utf-8-sig` BOM-tolerant .env read; orjson config read merged with explicit utf-8.
- plugins/memory/honcho/__init__.py → merged: orjson config read merged with explicit utf-8 (orjson still used 12× in file).
- plugins/memory/honcho/client.py → merged (upstream formatting): 1 quote-style pin.

## Extra (re-application of documented CN patches to router modules)
- hermes_cli/web_routers/profiles.py (clean add, not in any batch — no other agent owns it): re-applied P-008 (`name` in GET /api/profiles/active + PUT alias) and P-038 (`windows_hide_flags` in open-profile-terminal). Staged with web_server.py.

## Summary
- Files resolved & staged: 34/34 (+1 router file for P-008/P-038).
- Remaining conflict markers in my files: 0.
- Remaining UU in repo (other agents / special): .github/workflows/{ci,contributor-check,docker,supply-chain-audit,tests}.yml, .gitignore, cli-config.yaml.example, pyproject.toml, tui_gateway/server.py, uv.lock (uv.lock intentionally skipped per protocol).
- Lost-CN notes for test phase: (1) main.py pip-update flow (`_cmd_update_pip`) — upstream update_cmd.py has no PyPI-update equivalent; (2) web_server dashboard bundled-skills sync not in web_routers/skills.py; (3) profiles/sessions/skills inline handlers replaced by routers — P-008/P-038 re-applied to profiles router, verify desktop profile-switch + terminal-launch flows.

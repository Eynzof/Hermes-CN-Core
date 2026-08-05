# testfix-fixC.md — batch fixC (tests/hermes_cli, 30 files)

Protocol: `records/TEST-FIX-PROTOCOL.md` (read). All runs isolated:
`HERMES_HOME=$(mktemp -d) .venv/Scripts/python.exe -m pytest <file> -q -p no:cacheprovider`.

## Per-file results

| File | Result | What |
|---|---|---|
| tests/hermes_cli/test_anthropic_model_flow_stale_oauth.py | PASS | already green (4 passed) |
| tests/hermes_cli/test_anthropic_oauth_flow.py | PASS | already green (2 passed) |
| tests/hermes_cli/test_anthropic_provider_persistence.py | PASS | already green (2 passed) |
| tests/hermes_cli/test_backup.py | FIXED | 3 test-only fixes: (1) wrapper assertion now expects `.bat` on win32 (`create_wrapper_script` writes `valid.bat` on Windows); (2) `boards/work/kanban.db` suffix assert normalized via `os.path.join`+`replace('\\','/')`; (3) 0o600 mode assert gated to non-win32 (NTFS can't represent POSIX mode). 39 passed, 1 skipped |
| tests/hermes_cli/test_cmd_update.py | SKIPPED (win32) | 4 tests (`test_version_bump_only_applies_silently_without_prompt`, `test_active_profile_included_in_skill_sync`, `test_single_profile_default_is_synced`, `test_branch_flag_pulls_against_named_branch`) marked `@win32_npm_repair_skip`: on Windows `find_node_executable_on_path` scans real PATH directly (bypasses the `shutil.which` mock) → resolves system npm → real EBADENGINE (npm 11.12.1 vs engines `npm <11.10.0 || >=11.17.0`) → `maybe_repair_npm_engine` → `bootstrap_hermes_managed_node` → `_managed_node_tree_outdated()` crashes on mocked str stdout `.decode()` (hermes_constants.py:655) + live node download. Pre-merge fork skipped the whole file on win32; only the 4 npm-repair-reaching tests need it now. 17 passed, 4 skipped |
| tests/hermes_cli/test_config.py | FIXED | Production `hermes_cli/config.py`: `_sanitize_env_lines` was a merge artifact — kept the fork's old split logic + `_looks_like_structured_value`/`_STRUCTURED_VALUE_MARKERS` helpers; merged tests assert upstream's opaque-value contract (upstream commit 6fba78194 "preserve opaque .env values" deliberately removed splitting). Restored upstream's no-split implementation. Test: `test_default_path` now asserts `_get_platform_default_hermes_home()` (CN fork uses `%LOCALAPPDATA%\hermes` on win32, not `~/.hermes`). 74 passed |
| tests/hermes_cli/test_credential_lifecycle.py | PASS | already green (2 passed) |
| tests/hermes_cli/test_dashboard_admin_endpoints.py | PASS | already green (38 passed) |
| tests/hermes_cli/test_doctor.py | PASS | already green after prior doctor.py syntax fix (49 passed) |
| tests/hermes_cli/test_env_export_line_lifecycle.py | PASS | already green (2 passed) |
| tests/hermes_cli/test_env_load_cache.py | PASS | already green (2 passed) |
| tests/hermes_cli/test_env_sanitize_on_load.py | PASS | already green (3 passed) |
| tests/hermes_cli/test_gateway_restart_loop.py | FIXED + SKIPPED | Production `tools/terminal_tool.py` (~line 2689): `json.dumps({...}).decode('utf-8')` → `json.dumps({...}, ensure_ascii=False)` (stdlib json.dumps returns str; the `.decode()` was an orjson leftover — `AttributeError: 'str' object has no attribute 'decode'`). 8 POSIX-shell referenced-script tests marked `@skip_on_win32` (`/bin/bash`, `os.mkfifo`, `chmod 0o700`, `systemctl`/`launchctl`/`pkill`; shlex(posix=True) mangles Windows backslash paths). 74 passed, 8 skipped |
| tests/hermes_cli/test_kanban_boards.py | FIXED | Test used `with kb.connect(...)` which per `connect_closing` docstring does NOT close the sqlite3 connection (context manager only commits) → open FD blocked `remove_board()`'s dir rename on Windows (WinError 5/32). Switched first connect to `kb.connect_closing(...)`. 21 passed |
| tests/hermes_cli/test_kanban_worker_session_source.py | FIXED | Production `hermes_state.py` `retag_kanban_worker_sessions`: LIKE pattern hardcoded `/` separator → matched nothing on Windows (cwd uses `\`). Now matches both `/<id>` and `\<id>` (escaped `\\%` with ESCAPE `\`). 5 passed |
| tests/hermes_cli/test_managed_uv.py | FIXED + SKIPPED | Test-only: `_checkout` helper now creates `Scripts/python.exe` on win32 (was `bin/python`); 3 POSIX-layout tests (`_make_executable` fake `#!/bin/sh` uv can't run on Windows, WinError 216) marked skipif win32 with the file's existing POSIX-uv-layout reason. 33 passed, 7 skipped |
| tests/hermes_cli/test_model_cache_swr.py | FIXED | Production `hermes_cli/model_catalog.py` `get_catalog`: CN fork removed the fallback chain from the sync fetch (d3ccdbdac) but the merged upstream test `test_cold_cache_still_blocks_on_fetch` patches `_fetch_manifest_with_fallback`. Now calls `_fetch_manifest_with_fallback(cfg["url"], timeout, fallback_urls=())` — mirror-only sync path (call_count==1 contract in `test_model_catalog.py` holds) while routing through the helper the SWR test expects. 9 passed |
| tests/hermes_cli/test_model_catalog.py | FIXED | Production `hermes_cli/model_catalog.py`: restored dropped `_fetch_manifest_with_fallback` + `DEFAULT_CATALOG_FALLBACK_URLS` (still used by background `_spawn_catalog_swr_refresh`). `hermes_cli/config_defaults.py`: `model_catalog.url` restored to CN mirror `https://desktop.hermesagent.org.cn/api/model-catalog.json` (merge had overwritten with upstream docs URL; migration v27→28 in config_migrations.py already points users there). 26 passed |
| tests/hermes_cli/test_profiles.py | SKIPPED (win32) | 2 tests assert POSIX 0o600 `.env` mode (`os.chmod` is a no-op for r/w bits on NTFS): `test_seeds_placeholder_env_file`, `test_copies_default_env_into_envless_profiles` marked `@skip_on_win32`. 46 passed, 2 skipped |
| tests/hermes_cli/test_service_manager.py | SKIPPED (win32) | 2 s6-supervise tests use `os.mkfifo` (does not exist on Windows) + POSIX modes 03730/0660: `test_seed_supervise_skeleton_creates_expected_layout`, `test_s6_log_run_creates_leaf_as_hermes_without_chown` marked `@skip_on_win32`. 4 passed, 3 skipped |
| tests/hermes_cli/test_set_config_value.py | FIXED | Production `hermes_cli/config.py` `set_config_value`: env-routing suffix tuple was `("_API_KEY", "_TOKEN")` (old fork inline list); added `_SECRET` so `CLIENT_SECRET` (and friends) route to `.env` per `_is_env_config_key`/upstream contract. 82 passed |
| tests/hermes_cli/test_setup.py | PASS | already green (5 passed) |
| tests/hermes_cli/test_setup_hidden_env.py | PASS | already green (13 passed) |
| tests/hermes_cli/test_tui_npm_install.py | SKIPPED (win32) | 2 tests assert POSIX node/npm paths (`/usr/bin/node`, `/bin/npm`); Windows resolves real PATH binaries (`C:\Program Files\nodejs\...`) because `find_node_executable_on_path` scans PATH directly. 1 passed, 2 skipped |
| tests/hermes_cli/test_update_stale_dashboard.py | SKIPPED (win32) | 4 POSIX-only tests (systemd cgroup capture + `_try_restart_systemd_service`, `/proc/<pid>/cmdline`, `ps -p`, SIGTERM kill semantics) marked `@skip_on_win32` (matches pre-merge fork's class-level skips). 8 passed, 8 skipped |
| tests/hermes_cli/test_web_server.py | PASS | already green (132 passed, 5 skipped) |
| tests/hermes_cli/test_web_server_messaging_profiles.py | PASS | already green (7 passed) |
| tests/hermes_cli/test_web_server_profile_unification.py | PASS | already green (16 passed) |
| tests/hermes_cli/test_web_ui_build.py | SKIPPED (win32) | 3 POSIX-only: 2 tests assert `/usr/bin/npm` argv (Windows resolves `C:\Program Files\nodejs\npm.cmd` via real PATH), 1 uses `import fcntl` (flock; no fcntl on Windows). 19 passed, 3 skipped |
| tests/hermes_cli/test_whatsapp_cloud_setup.py | PASS | already green (10 passed) |

## Summary

- Files fixed (production + test changes): test_config, test_model_catalog, test_gateway_restart_loop, test_cmd_update, test_kanban_boards, test_kanban_worker_session_source, test_backup, test_managed_uv, test_model_cache_swr, test_set_config_value, test_web_ui_build, test_service_manager, test_profiles, test_update_stale_dashboard, test_tui_npm_install.
- Production code repaired:
  - `hermes_cli/config.py` — restored upstream no-split `_sanitize_env_lines`; added `_SECRET` to `set_config_value` env-routing suffix.
  - `hermes_cli/config_defaults.py` — restored CN mirror `model_catalog.url`.
  - `hermes_cli/model_catalog.py` — re-added `_fetch_manifest_with_fallback` + `DEFAULT_CATALOG_FALLBACK_URLS`; sync fetch routes through helper with empty fallback chain (CN mirror-only).
  - `hermes_cli/kanban_db.py` — none (test-side fix; `connect_closing` already existed).
  - `hermes_state.py` — retag LIKE matches `\` separator on Windows.
  - `tools/terminal_tool.py` — removed `.decode('utf-8')` on stdlib `json.dumps` result.
- Files with only win32 skips (rule 4/5, mirroring pre-merge fork conventions): test_cmd_update (4), test_gateway_restart_loop (8), test_profiles (2), test_service_manager (2), test_web_ui_build (3), test_update_stale_dashboard (4), test_tui_npm_install (2), test_managed_uv (3).

## Final verdict

- Files fixed: 15 (test files; 7 of those also production-code fixes)
- Files remaining failing: **0** — all 30 files in the batch pass on Windows (isolated runs).
- All changes `git add`ed; nothing committed.

# Batch tests-7 — conflict resolution report

Batch: `batches/tests-7.txt` (55 test files, all modify/modify conflicts, stages 1/2/3 present).
Method: upstream rewrote nearly every file (churn 10-30x ours), so per protocol rule 5 the base was
upstream's `:3:` version; our fork's CN-patch tests/adaptations were re-added where the target test
still exists upstream, per rules 1-2 (documented CN patch / Windows-py3.14-China compat) and 6
(never silently lose CN-only functionality). Pure `json`→`orjson` swaps were DROPPED (cross-compatible:
`json.loads` accepts bytes; `orjson.loads` accepts str — no functional loss; prefer upstream formatting, rule 4).
All files pass `python -m py_compile`; zero conflict markers remain; all `git add`ed. No commit made.

## Per-file decisions

| File | Decision | Notes |
|---|---|---|
| tests/tools/test_mcp_oauth_cold_load_expiry.py | took-upstream | fork changes were orjson swaps only |
| tests/tools/test_mcp_oauth_manager.py | took-upstream | orjson swaps only |
| tests/tools/test_mcp_oauth_metadata.py | took-upstream | orjson swaps only (note: docstring contains a `=======` ASCII heading — NOT a marker) |
| tests/tools/test_mcp_structured_content.py | took-upstream | orjson swaps + circuit-breaker `_reset_server_error` hygiene insert dropped (target test changed upstream; test phase can re-evaluate) |
| tests/tools/test_mcp_tool.py | **kept-ours** | P-014 target. Fork rewrote the suite (217 tests, orjson-based) incl. `test_mcp_unavailable_with_servers_warns/without_servers_stays_quiet/warns_only_once` (=P-014) + Windows env case-insensitive compare + `from agent.re_compat import re`. **Not ported:** upstream's 12 new tests (lock-file/provenance/secret-source/parallel-safe) because our production `tools/mcp_tool.py` lacks `_LockCookie` / `get_registered_mcp_server_names` — those tests would fail; test phase re-evaluate if production adopts them |
| tests/tools/test_mcp_tool_issue_948.py | took-upstream | win32 skipif target (`test_run_stdio_uses_resolved_command_and_prepended_path`) deleted upstream → marker dropped |
| tests/tools/test_mcp_tool_session_expired.py | took-upstream | orjson swaps only |
| tests/tools/test_memory_tool.py | merged | upstream base + re-added `errors="replace"` on 3 `read_text(encoding="utf-8")` reads (P-051 family GBK hardening). Dropped 3.11→3.14 test-data string change (cosmetic) |
| tests/tools/test_modal_snapshot_isolation.py | took-upstream | orjson swaps only (not P-028 — that is agent/models_dev snapshot tests, outside this batch) |
| tests/tools/test_notify_on_complete.py | took-upstream | orjson swaps only |
| tests/tools/test_parse_env_var.py | took-upstream | orjson swap + `python:3.11`→`3.14` image string dropped (cosmetic P-048) |
| tests/tools/test_patch_failure_tracking.py | took-upstream | orjson swaps only |
| tests/tools/test_process_registry.py | merged | upstream base + re-added: `from contextlib import ExitStack`; `hasattr(os,"getpgid")` guard around `patch("os.getpgid",...)` in `test_popen_killed_when_thread_creation_fails` (Windows lacks os.getpgid → patch AttributeError); PTY EOF test now skipif(win32) + `"cat"` command + `pytest.skip` when `session._pty is None`; appended fork's P-019 `TestSpawnLocalShellSelection` (3 PowerShell background-spawn tests, incl. pwsh argv, `-NoProfile/-NonInteractive/-ExecutionPolicy Bypass/-Command`, `Set-Location -LiteralPath`, cwd-file, PYTHONUNBUFFERED) — depends on production `_IS_WINDOWS/_resolve_shell/_resolve_safe_cwd/_spawn_windows_pty_local` (kept ours per P-016/P-019) |
| tests/tools/test_read_extract.py | took-upstream | orjson swaps + write-side `errors="replace"` dropped (cosmetic on write) |
| tests/tools/test_read_loop_detection.py | took-upstream | orjson swaps only |
| tests/tools/test_registry.py | took-upstream | orjson swaps only |
| tests/tools/test_search_error_guard.py | merged | P-049: re-added `from hermes_cli.dep_ensure import _find_rg`; `_METHODS = ["_search_with_grep"] if sys.platform != "win32" else []` + `if _find_rg(): append rg`; `import sys`. Dropped win32 skip of chmod test (`test_files_only_excludes_diagnostics` deleted upstream; surviving `partial_error_tree` tests are Windows-safe). `import shutil` left unused (matches our side) |
| tests/tools/test_send_message_react.py | took-upstream | orjson swaps only |
| tests/tools/test_send_message_tool.py | took-upstream | orjson swaps only |
| tests/tools/test_session_search.py | took-upstream | orjson swaps only |
| tests/tools/test_skill_env_passthrough.py | took-upstream | orjson swaps only |
| tests/tools/test_skill_manager_tool.py | took-upstream | orjson swaps only |
| tests/tools/test_skill_size_limits.py | took-upstream | orjson swaps only |
| tests/tools/test_skill_usage.py | took-upstream | orjson swaps only |
| tests/tools/test_skills_hub.py | merged | upstream base + re-added: `write_bytes` for checklist.md + jo.txt (Windows write_text \n→\r\n would break byte-exact hash/bundle assertions), `errors="replace"` on quarantine SKILL.md read, PEP-562-aware `isolated_skills_dir` fixture (manual `hub.__dict__` set/restore instead of `monkeypatch.setattr` which would freeze dynamic `SKILLS_DIR`) |
| tests/tools/test_skills_sync.py | merged | upstream base + re-added `dest.as_posix().endswith("mlops/axolotl")` (Windows path separators) |
| tests/tools/test_skills_tool.py | took-upstream | win32 skipif target (`test_skill_view_applies_inline_shell_when_enabled`) deleted upstream → marker dropped |
| tests/tools/test_skills_tool_discovery_cache.py | took-upstream | win32 skipif target (`test_ttl_expiry_forces_rescan`) deleted upstream → marker dropped |
| tests/tools/test_spotify_client.py | took-upstream | orjson swaps only |
| tests/tools/test_terminal_foreground_timeout_cap.py | merged | P-049: re-added `patch("tools.rtk_provision._rtk_available", return_value=False)` in `pnpm dev --help` guard-passthrough test (our terminal_tool rewrites known commands via rtk; test must be hermetic) |
| tests/tools/test_terminal_task_cwd.py | merged | upstream base + re-added `"cwd_file": None` to background `registry.calls` assertion (our production terminal_tool passes `cwd_file` to process_registry.spawn_local — P-016/P-019 cwd tracking; exact-dict assert would fail otherwise) |
| tests/tools/test_terminal_tool.py | took-upstream | fork's session-context reset hygiene targeted a HERMES_SESSION_KEY test deleted upstream; upstream already resets `_reset_cached_sudo_passwords()` in module setup/teardown |
| tests/tools/test_terminal_tool_pty_fallback.py | took-upstream | orjson swaps only |
| tests/tools/test_tirith_security.py | merged | P-049: re-added autouse `_assume_supported_platform` fixture (force `_detect_target` → `x86_64-unknown-linux-gnu` except `TestUnsupportedPlatform`; tirith ships Linux/Darwin binaries only — tests otherwise exercise the unsupported path on Windows), `patch(_hermes_tools_dir, "/nonexistent")` alongside the 3 existing `_hermes_bin_dir` patches (managed-tools-dir hermeticity), and `assert path == str(hermes_home / "tools" / "tirith")` (managed install dest; our production installs to `<HERMES_HOME>/tools`, not `bin`). Dropped the win32 skipif for the ~/.hermes marker fallback test (target test deleted upstream; surviving HERMES_HOME tests are Windows-safe) |
| tests/tools/test_todo_tool.py | took-upstream | orjson swaps only |
| tests/tools/test_todo_tool_type_coercion.py | took-upstream | orjson swaps only |
| tests/tools/test_tool_search.py | took-upstream | orjson swaps only |
| tests/tools/test_tts_dotenv_fallback.py | took-upstream | `import pybase64 as base64` target test (`test_mistral_reads_dotenv_key`) deleted upstream → swap dropped |
| tests/tools/test_tts_path_traversal.py | took-upstream | orjson swaps only |
| tests/tools/test_video_analyze.py | took-upstream | orjson swaps only |
| tests/tools/test_vision_native_fast_path.py | took-upstream | pybase64 module swap dropped (upstream uses stdlib base64; pybase64 is a superset — prefer upstream) |
| tests/tools/test_vision_tools.py | took-upstream | pybase64 swaps dropped (upstream uses stdlib base64) |
| tests/tools/test_voice_mode.py | merged | upstream base + re-added skipif(win32, "AF_UNIX not available on Windows") on the 2 surviving AF_UNIX tests + `import sys` (3rd target test deleted upstream) |
| tests/tools/test_web_providers.py | took-upstream | SSRF `async_is_safe_url` bypass monkeypatches (fake-ip DNS envs) — target tests deleted upstream; upstream's rewrite no longer calls the SSRF gate in those paths |
| tests/tools/test_web_providers_xai.py | took-upstream | orjson swaps only |
| tests/tools/test_web_tools_config.py | merged | upstream base + re-added class-level skipif(win32, "parallel-web dependency not installed") on `TestParallelClientConfig` |
| tests/tools/test_website_policy.py | took-upstream | orjson swaps only |
| tests/tools/test_windows_native_support.py | **kept-ours** | P-019 target. Fork's version is a superset (TestConfigureWindowsStdio extra tests, `_normalize_msys_path` renames, `.sh/.bash`-unsupported docstring, SIGKILL/AF_UNIX skipifs, `errors="replace"` source reads). Upstream-only extras not ported: `TestGatewayRunRestartWatcherOuterPopenFallback`, detach-flags-exclude, watcher-hidden-console tests (test upstream production features whose merge state is decided by the production-side agent; test phase re-evaluate) |
| tests/tools/test_write_approval.py | took-upstream | orjson swaps only |
| tests/tools/test_x_search_tool.py | took-upstream | orjson swaps only |
| tests/tui_gateway/test_custom_provider_session_persistence.py | took-upstream | orjson swaps only |
| tests/tui_gateway/test_inline_rpc_gil_starvation.py | took-upstream | `_methods` snapshot/restore already present upstream (adopted) |
| tests/tui_gateway/test_pet_generate_rpc.py | took-upstream | `import pybase64` target test deleted upstream → swap dropped |
| tests/tui_gateway/test_projects_rpc.py | merged | upstream base + re-added `import uuid` + unique session ids at both `create_session` sites (ambient state.db stale-row collision hygiene, fork's fix) |
| tests/tui_gateway/test_protocol.py | took-upstream | orjson swaps only |

## Summary
- 55 files resolved, `git add`ed, none committed.
- Conflict markers remaining in batch files: **0**.
- `py_compile` failures: **0**.
- Unmerged (stage != 0) entries: **0**.
- CN tests re-added: P-014 MCP-unavailable warning tests (test_mcp_tool.py, kept ours), P-019 PowerShell background-spawn tests (test_process_registry.py), P-049 `_find_rg`/rtk-disable/managed-tools-dir (test_search_error_guard.py, test_terminal_foreground_timeout_cap.py, test_tirith_security.py), P-051 `errors="replace"` reads (test_memory_tool.py), win32 skipif markers (test_voice_mode.py, test_web_tools_config.py, test_process_registry.py PTY test), Windows byte/path fixes (test_skills_hub.py, test_skills_sync.py, test_terminal_task_cwd.py, test_projects_rpc.py).
- Known dropped (test phase re-evaluate): upstream's 12 new test_mcp_tool.py tests (production lacks `_LockCookie`/`get_registered_mcp_server_names`); fork's orjson swaps (cross-compatible); 5 fork win32-skipif markers whose target tests were deleted by upstream's rewrite.

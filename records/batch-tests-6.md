# Batch tests-6 — merge conflict resolution report

All 60 files resolved, `git add`ed, 0 conflict markers remaining, all `py_compile` clean.
Decision rules applied: FORK_NOTES P-NNN targets + rule 5 (tests default to upstream base; re-add
enumerated CN tests + win32 skipif markers) + rule 2 (Windows/py3.14 compat) + rule 6 (don't lose CN code coverage).

## Top-level tests

- `tests/test_tui_gateway_server.py` → merged — kept CN `context_engine` first-class-toolset assertion, adapted to upstream's `_RECENTLY_SHIPPED_TOOLSETS` back-fill (`{"context_engine","kanban","memory","project"} <= result`, remainder within `_RECENTLY_SHIPPED_TOOLSETS`). 2 hunks.
- `tests/test_tui_gateway_ws.py` → took-upstream — dropped ours' `test_ws_coalesced_tokens_cannot_be_overtaken_by_completion` (not a documented CN test); kept upstream's `test_ws_transport_preserves_cross_batch_order` + `import concurrent.futures` (used by `test_ws_transport_serializes_concurrent_sends`).
- `tests/test_web_server_sessiondb_eventloop.py` → merged — upstream's two-module scan (`web_server` + `web_routers.sessions`) + kept ours' `errors="replace"` on the AST read (rule 2).
- `tests/test_windows_subprocess_no_window_flags.py` → took-upstream — upstream rewrote the file around `bounded_git_probe` (run()→Popen rewrite). Dropped ours' `coding_context`/`context_references`/`_list_repo_files` hide-flag tests (spawn contract now asserted by upstream's `test_bounded_git_probe_fast_path_spawn_contract_windows`, incl. `creationflags`) and an orphan taskkill fragment referencing undefined `captured`. Note: P-051 taskkill pin coverage lives in `tests/test_subprocess_text_pipe_decoding.py` (not in this batch).
- `tests/test_yuanbao_pipeline.py` → kept-ours — CN yuanbao inbound-middleware tests (`test_group_with_at_bot_passes`, `test_extract_quote_context_*`); tested code (`GroupAtGuardMiddleware`/`QuoteContextMiddleware` in `gateway/platforms/yuanbao.py`) still exists post-merge.

## tests/tools — took-upstream (ours-only extra tests for upstream features dropped per rule 5; note any lost coverage)

- `tests/tools/test_async_delegation.py` → took-upstream (dropped 5 background-batch delegation tests; delegation is upstream).
- `tests/tools/test_base_environment.py` → took-upstream — upstream rewrote temp-path tests around `mktemp` (#54314; macOS bash 3.2 has no `$BASHPID`); merged `tools/environments/base.py` bootstrap (common region) is mktemp-based, so upstream assertions match. Dropped ours' uuid-suffix temp tests. Re-evaluate if the base.py resolution keeps a uuid path for PowerShell.
- `tests/tools/test_browser_camofox.py` → took-upstream (browser_camofox is upstream).
- `tests/tools/test_browser_camofox_persistence.py` → took-upstream.
- `tests/tools/test_browser_cdp_tool.py` → took-upstream (dropped 9 pure-ours CDP tests).
- `tests/tools/test_browser_console.py` → took-upstream.
- `tests/tools/test_browser_eval_supervisor_path.py` → took-upstream.
- `tests/tools/test_browser_hardening.py` → took-upstream.
- `tests/tools/test_browser_homebrew_paths.py` → took-upstream.
- `tests/tools/test_browser_lightpanda.py` → took-upstream.
- `tests/tools/test_browser_private_page_action_guard.py` → took-upstream.
- `tests/tools/test_browser_snapshot_ssrf.py` → took-upstream.
- `tests/tools/test_browser_ssrf_local.py` → took-upstream.
- `tests/tools/test_browser_supervisor.py` → took-upstream — imports to stdlib `base64/json/os`; dropped ours' dialog-supervisor pure-ours tests (browser_supervisor/browser_dialog_tool are upstream features; upstream's own tests cover them).
- `tests/tools/test_clarify_tool.py` → took-upstream (json replaces orjson; dropped 5 extra choices-validation tests).
- `tests/tools/test_code_execution.py` → took-upstream.
- `tests/tools/test_computer_use.py` → took-upstream — upstream rewrote blocked-pattern/key-combo tests (loop over payloads) and added `test_cli_fallback_reads_screenshot_from_file`; dropped 10 pure-ours extras (schema/drag/capture-ax/trajectory).
- `tests/tools/test_computer_use_capture_routing.py` → took-upstream.
- `tests/tools/test_cronjob_run_immediate.py` → took-upstream (imports json/threading/time; dropped ours' claim-lost test).
- `tests/tools/test_cronjob_tools.py` → took-upstream — dropped 5 pure-ours cron tests (skill-backed jobs, legacy unsafe-job remediation, session subscription). Note: if the merged cron/kanban code keeps those CN behaviors the test phase should re-add coverage.
- `tests/tools/test_cross_profile_guard.py` → took-upstream (`cross_profile` is now an upstream feature in tools/file_tools.py).
- `tests/tools/test_debug_helpers.py` → took-upstream.
- `tests/tools/test_delegate.py` → took-upstream (dropped 6 pure-ours tests).
- `tests/tools/test_delegation_live_log.py` → took-upstream — dropped live-transcript writer/manifest tests (feature not in the rule-5 re-add list; note for test phase).
- `tests/tools/test_discord_tool.py` → took-upstream (dropped 9 pure-ours action tests).
- `tests/tools/test_docker_environment.py` → took-upstream — dropped 2 `ensure_docker_available` tests; P-051 exact-kwargs subprocess-pin contract lives in code + `tests/test_subprocess_text_pipe_decoding.py` (not in this batch).
- `tests/tools/test_docker_network_config.py` → took-upstream (`TERMINAL_DOCKER_NETWORK` is an upstream env var).
- `tests/tools/test_execute_code_approval_cluster.py` → took-upstream.
- `tests/tools/test_homeassistant_tool.py` → took-upstream — upstream rewrote `test_safe_domain_not_blocked`/`test_missing_domain_rejected` around `AsyncMock` service-call layer; dropped ours' pure-ours tests.
- `tests/tools/test_image_generation_artifacts.py` → took-upstream — upstream rewrote postprocess tests around `sync_manager`/threads; dropped ours' orjson-based tests.
- `tests/tools/test_image_generation_image_to_image.py` → took-upstream.
- `tests/tools/test_image_generation_plugin_dispatch.py` → took-upstream.
- `tests/tools/test_kanban_tools.py` → took-upstream — dropped 17 pure-ours hunks (worker_session_id stamping, board-param routing, gateway/tui session subscription). Note: `worker_session_id` stamping still exists in merged `tools/kanban_tools.py`; test phase should re-add if these CN kanban behaviors are kept.
- `tests/tools/test_mcp_client_cert.py` → took-upstream (`_resolve_client_cert` mTLS is upstream).

## tests/tools — kept-ours / merged (CN / Windows / py3.14 coverage preserved)

- `tests/tools/test_approval.py` → kept-ours — kept `import sys`+`tempfile`, the `skipif(win32)` on `TestDetectDangerousRm` (rule 5 re-add; GNU rm detection differs on Windows), and the gateway-runner session-key AST test (code still calls `set_current_session_key`/`reset_current_session_key` in `gateway/run.py`).
- `tests/tools/test_checkpoint_manager.py` → merged — upstream base + re-added `skipif(win32)` on `test_restore_file_path_confined_to_working_dir` and `test_clear_all_wipes_base_then_is_a_noop` (git/path/env Windows-incompatible; rule 5) + `import sys`. Dropped ours' orjson usage and `test_sets_index_file_when_provided` (not in upstream).
- `tests/tools/test_clipboard.py` → merged — kept upstream's `_VoiceInputMessage` sentinel assertion (#65827) AND ours' `TestClipboardPowershellEncoding` (P-020/P-019: `_run_powershell` must pass `encoding='utf-8'` + `ps_with_utf8()`; both verified present in `hermes_cli/clipboard.py`). Dropped `TestQueueRouting` (obsolete tuple routing — code now uses the sentinel) and orphan `_mock_vision_failure`.
- `tests/tools/test_code_execution_windows_env.py` → kept-ours — `test_popen_env_sets_pythonutf8_mode` (P-042/P-044 Windows env; `child_env["PYTHONUTF8"] = "1"` verified at `tools/code_execution_tool.py:1514`).
- `tests/tools/test_docker_config_migrate.py` → merged — upstream schema-12 migration message + below-floor refusal behavior (matches merged `scripts/docker_config_migrate.py`: `SUPPORT_FLOOR_VERSION`, `Migrating config schema {cur} -> {latest}`) + kept ours' `errors="replace"` on config reads.
- `tests/tools/test_docker_find.py` → kept-ours — CN `tools/environments/docker.py` find_docker tests (`HERMES_DOCKER_BINARY` override, podman fallback, win32 skipif on Unix-permissions tests).
- `tests/tools/test_file_operations.py` → merged — kept ours' densify tests (P-049; `_densify_matches`/`to_dict(densify=)` verified), `_make_env(monkeypatch)`→`LocalEnvironment` (P-030 portable Python search fallback; `_use_inproc_io` verified), `TestParseSearchContentOutput`/ripgrepy-dispatch tests (P-030) + upstream's `TestAtomicWriteNewFilePermissions`/`TestAtomicWriteThroughSymlink`/`TestReadNonUtf8IsBinary`.
- `tests/tools/test_file_ops_cwd_tracking.py` → kept-ours (P-016/P-019 cwd tracking).
- `tests/tools/test_file_read_guards.py` → kept-ours (P-030/P-033 `_get_file_ops` patches).
- `tests/tools/test_file_staleness.py` → kept-ours (P-030/P-033).
- `tests/tools/test_file_state_registry.py` → kept-ours (file-state registry no-false-warning test).
- `tests/tools/test_file_tools.py` → kept-ours (P-030/P-033 `_get_file_ops` patches + hermes-config patch guard).
- `tests/tools/test_file_tools_cwd_resolution.py` → kept-ours (P-016/P-019 cwd resolution).
- `tests/tools/test_file_write_safety.py` → kept-ours (`@_win32` decorator + special-chars roundtrip; P-033 in-process write).
- `tests/tools/test_find_shell.py` → kept-ours (P-050/P-052 `_find_bash`/`_find_bash_posix`/`_find_shell` tests — shell resolution is CN).
- `tests/tools/test_kanban_redaction.py` → kept-ours (kanban secret-scrubbing tests; `tools/kanban_redaction` is CN-only).
- `tests/tools/test_line_ending_preservation.py` → kept-ours (P-033b line-ending roundtrip on patch/new-file).
- `tests/tools/test_local_env_relative_cwd.py` → kept-ours (P-016/P-019 relative-cwd resolution).
- `tests/tools/test_local_env_windows_msys.py` → merged — kept ours' `test_init_session_powershell_skips_bash_bootstrap` (P-019: Windows default is PowerShell; bash must not run) + upstream's `test_init_session_bootstrap_rewrites_backslash_snapshot_paths` (valid for P-050 bash opt-in path).
- `tests/tools/test_local_interrupt_cleanup.py` → kept-ours (`test_kill_process_uses_windows_tree_kill`; `taskkill /PID <pid> /T /F` verified in `tools/process_registry.py`).
- `tests/tools/test_mcp_oauth.py` → kept-ours (skipif(win32) on os.uname test per P-048 + pre-registered-client-id + cached-tokens tests).

## Summary
- Files resolved: 60 / 60 (all staged via `git add`; none committed).
- Remaining conflict markers in batch files: 0.
- Files I could not resolve: none.
- Note for test phase: dropped ours-only coverage for delegation live-logs, kanban worker/session features, cron skill-backed jobs, and browser dialog-supervisor extras (upstream features; re-add only if merged code keeps CN-specific behaviors).

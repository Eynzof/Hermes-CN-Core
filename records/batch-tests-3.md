# Batch tests-3 resolution report

Batch: `batches/tests-3.txt` (60 test files: tests/gateway + tests/hermes_cli).
Date: merge of upstream/main into dev-fix. All files resolved per RESOLUTION-PROTOCOL rule 5
(tests: upstream base; re-add only CN-patch tests + win32 skipifs that upstream lacks).

Summary: 54 took-upstream, 6 merged (CN/Windows adaptations kept). All 60 files pass
`python -m py_compile`, contain zero conflict markers, and are `git add`-ed (no commit).

## tests/gateway (22)

- tests/gateway/test_status_command.py → took-upstream. Ours' extra /profile (custom-root, ignores-stamp, unstamped) + callback-generation-snapshot tests dropped; upstream rewrote /profile for multiplexing + covers callback generation itself.
- tests/gateway/test_stream_consumer_thread_routing.py → merged. Upstream base + RE-ADDED our `test_create_uses_chat_id_when_no_thread` (FeishuAdapter `_send_raw_message` no-thread fallback; Feishu is a CN-fork platform — P-015 — and upstream's own sibling test imports the same CN-only adapter).
- tests/gateway/test_stt_config.py → took-upstream. Dropped 3 ours-only `_enrich_message_with_transcription` tests (2 carried skipif(win32) for `/tmp` path format); upstream's 4 tests cover the STT contract.
- tests/gateway/test_stuck_loop.py → took-upstream. Dropped 5 extra stuck-loop tests (increment-accumulate/drop-inactive, clear-file, suspend-clears, no-file-no-crash); upstream covers #7536.
- tests/gateway/test_systemd_notify.py → took-upstream. Upstream's per-test `not hasattr(socket,"AF_UNIX")` skipif already handles Windows; dropped our module-level skipif(win32) + 2 AF_UNIX datagram tests (upstream has nonblocking-send + watchdog tests that run everywhere).
- tests/gateway/test_teams.py → took-upstream. Upstream's deferred-SDK rewrite (#62935) supersedes our extra requirements/validate-config tests; kept upstream's `import json` (ours used orjson — mechanical, prefer upstream).
- tests/gateway/test_telegram_network_reconnect.py → took-upstream. Upstream's 14-test rewrite (incl. `test_retry_exhaustion_queues_reconnect_before_child_disconnect`) supersedes our 4 extra tests (AST `_looks_like_network_error` classifier checks, heartbeat-exits-on-fatal, disconnect-cancels-heartbeat). NOTE: if the merged telegram adapter still has the shared network classifier, test phase may re-add the AST tests.
- tests/gateway/test_update_command.py → took-upstream. Dropped extra tests (`managed_install_returns_package_manager_guidance`, `no_hermes_binary`, `writes_pending_marker_with_thread_id`, npm/Popen guards); upstream's update-command suite kept.
- tests/gateway/test_update_streaming.py → took-upstream. Dropped extra streaming/prompt-file tests; upstream's `_run_with_idle_timeout` suite kept.
- tests/gateway/test_verbose_command.py → took-upstream. Dropped `/verbose` cycle tests; upstream's tool-progress suite kept.
- tests/gateway/test_vision_memory_leak.py → took-upstream. Dropped memory-context-fence tests; upstream keeps json (ours used orjson — mechanical).
- tests/gateway/test_voice_command.py → took-upstream. Dropped voice-mode persistence tests; upstream's suite kept.
- tests/gateway/test_voice_mode_platform_isolation.py → took-upstream. Dropped platform-isolation tests (orjson→json mechanical).
- tests/gateway/test_webhook_adapter.py → took-upstream. Dropped signature/template/`{__raw__}` tests; upstream has `test_route_profile_validation_fails_closed` (kept); pybase64→base64 mechanical.
- tests/gateway/test_webhook_deliver_only.py → took-upstream. Dropped template-rendering/delivery-exception tests.
- tests/gateway/test_webhook_dynamic_routes.py → took-upstream. Dropped static-precedence/mtime/file-removal tests.
- tests/gateway/test_webhook_signature_rate_limit.py → took-upstream. Dropped valid-signature-rate-limit test.
- tests/gateway/test_weixin.py → took-upstream. Dropped ours-only Weixin tests (context-token-store replace-failure, sync-buf replace-failure, media-builder AES/MD5, formatting extras); upstream's own Weixin inbound-voice + formatting tests kept. WeChat adapter exists in both sides.
- tests/gateway/test_whatsapp_cloud.py → took-upstream. Dropped extra send/dedup/build-url tests; upstream's cloud suite kept.
- tests/gateway/test_whatsapp_connect.py → merged. Upstream base; KEPT our side at the P-051 taskkill assertion: `subprocess.run(["taskkill",…], text=True, errors="replace")` WITHOUT `encoding="utf-8"` — matches our fork's locale-style `_terminate_bridge_process` (P-051: taskkill prints OEM/ANSI codepage; encoding=utf-8 would be wrong on zh-CN). If the merged whatsapp adapter instead keeps upstream's `encoding='utf-8'` call, flip this assertion back.
- tests/gateway/test_whatsapp_group_gating.py → took-upstream. Dropped ~20 extra dm/group-policy gating tests; upstream's 10-test suite kept.
- tests/gateway/test_whatsapp_identity.py → took-upstream. Dropped `test_aliases_resolve_on_legacy_layout` (orjson); upstream's modern-layout test kept.

## tests/hermes_cli (38)

- tests/hermes_cli/test_active_sessions.py → took-upstream. Dropped 7 extra lease/pid-reuse tests; upstream has `test_release_orphaned_leases_reclaims_only_unowned_own_pid_entries` (kept).
- tests/hermes_cli/test_apply_profile_override.py → merged. Upstream's 4 tests as base; KEPT our Windows adaptation (platform-aware `_hermes_root_for_test` + `LOCALAPPDATA=tmp_path` isolation in `_run_apply_profile_override` — required because merged main.py derives the default home from %LOCALAPPDATA% on Windows, so upstream's bare `tmp_path/.hermes` root would read/write the REAL user home) and re-added `skipif(win32, "POSIX sudo/pwd only")` on the sudo test (`import pwd`/`os.geteuid` are POSIX-only, upstream lacks the skip). Dropped 8 extra profile-flag-consumption tests.
- tests/hermes_cli/test_atomic_json_write.py → took-upstream. Dropped 13 extra atomic-write tests; orjson→json mechanical.
- tests/hermes_cli/test_atomic_yaml_write.py → took-upstream. Dropped 1 extra test.
- tests/hermes_cli/test_auth_codex_provider.py → took-upstream. Dropped ~21 extra codex token/refresh/device-code tests; upstream's 11 kept; orjson/pybase64→json/base64 mechanical.
- tests/hermes_cli/test_auth_codex_self_heal.py → took-upstream. Dropped 5 extra self-heal tests.
- tests/hermes_cli/test_auth_commands.py → took-upstream. Dropped ~36 extra auth add/remove/list/suppression tests; upstream's 17 kept.
- tests/hermes_cli/test_auth_nous_provider.py → took-upstream. Dropped ~34 extra nous tests (incl. 5 CA-bundle fallback tests — NOTE: merged `hermes_cli/auth.py` may retain CA-bundle env fallback; test phase can re-add if so). Upstream's 18 kept (incl. 2 timeout tests).
- tests/hermes_cli/test_auth_profile_fallback.py → took-upstream. Restored upstream's `test_release…`/`test_write_pool_never_merges_cooldown_onto_reauthed_entry`/`test_auth_lock_reentrancy…`; dropped 12 extra fallback tests.
- tests/hermes_cli/test_auth_qwen_provider.py → took-upstream. Dropped ~21 extra qwen CLI-token tests; upstream's suite kept.
- tests/hermes_cli/test_auth_xai_oauth_provider.py → took-upstream. Dropped ~20 extra xai oauth tests; upstream's kept.
- tests/hermes_cli/test_backup.py → took-upstream. Restored upstream's `_advance_backup_clock` fixture; dropped extra tests.
- tests/hermes_cli/test_cmd_update.py → took-upstream. Dropped our module-level `pytestmark skipif(win32)` and extra npm/uv tests; upstream's 30-test suite kept (mock-based, Windows-safe).
- tests/hermes_cli/test_codex_cli_model_picker.py → took-upstream. orjson/pybase64→json/base64 mechanical.
- tests/hermes_cli/test_codex_models.py → took-upstream. Dropped 12 extra codex model tests; orjson→json mechanical.
- tests/hermes_cli/test_commands.py → took-upstream. Dropped registry-invariant/category tests.
- tests/hermes_cli/test_completion.py → took-upstream. Dropped extra fish/zsh/subcommand-drift tests; `agent.re_compat` → stdlib `import re` (re_compat defaults to stdlib re anyway — functionally identical).
- tests/hermes_cli/test_config.py → took-upstream. Dropped ~20 extra config tests; `errors="replace"` reads → plain reads (mechanical, P-051-flavored but not required); upstream's TestIsProviderEnabled + kanban duplicate-key tests kept.
- tests/hermes_cli/test_container_boot.py → merged. Upstream's 5 tests + our `_posix_only = skipif(win32)` marker applied to ALL tests (the s6 container code under test uses `os.mkfifo`/`chown` — POSIX-only; upstream lacks the marker and two tests also check `st_mode & 0o111` exec bits). Added `import sys`.
- tests/hermes_cli/test_copilot_token_exchange.py → took-upstream. Dropped 10 extra token-exchange tests; upstream's JwtDiskStoreBounds tests kept; orjson→json mechanical.
- tests/hermes_cli/test_credential_lifecycle.py → took-upstream. Dropped 10 extra delete/update tests; `errors="replace"` helper read → plain (mechanical).
- tests/hermes_cli/test_dashboard_auth_401_reauth.py → took-upstream. Dropped 5 extra reauth tests.
- tests/hermes_cli/test_dashboard_auth_middleware.py → took-upstream. Dropped 8 extra gated-route tests.
- tests/hermes_cli/test_dashboard_unified_launch.py → merged. Upstream's 2 tests as base; KEPT our Windows-aware `_patch_reexec` helper (mocks both `os.execvpe` AND `subprocess.Popen` — on Windows `cmd_dashboard` re-execs via Popen+wait, so upstream's execvpe-only mock would spawn a real dashboard subprocess and hang the session). Both retained tests use it. Dropped 9 extra tests.
- tests/hermes_cli/test_dashboard_web_dist_validation.py → took-upstream. Upstream's `--skip-build` recovery test kept; dropped extra dist-validation tests.
- tests/hermes_cli/test_diagnostics_upload.py → took-upstream. orjson→json mechanical.
- tests/hermes_cli/test_doctor.py → took-upstream. Restored upstream's `test_sqlite_upgrade_hint_*` + `test_doctor_reads_invalid_utf8_env_via_latin1_fallback`; dropped our kimi/custom-endpoint/apt-hint tests (kimi detection exists in BOTH sides' doctor.py — upstream feature; not a documented CN patch).
- tests/hermes_cli/test_env_export_line_lifecycle.py → took-upstream. Dropped extra env-export tests.
- tests/hermes_cli/test_env_loader.py → took-upstream. Dropped extra loader tests.
- tests/hermes_cli/test_gateway.py → took-upstream. Upstream already has win32 skipifs (POSIX PTY + systemd-linger); dropped ours-only tests.
- tests/hermes_cli/test_gateway_restart_loop.py → took-upstream. Dropped extra restart tests.
- tests/hermes_cli/test_gateway_s6_dispatch.py → took-upstream. Upstream's 2 tests are Windows-safe (fully mocked); dropped our `signal.pause` test (+ its skipif).
- tests/hermes_cli/test_gateway_service.py → took-upstream. Dropped 4 extra systemd-unit tests.
- tests/hermes_cli/test_gateway_windows.py → took-upstream. Dropped ~30 fork Windows-gateway tests (schtasks localized-error fallback, `_exec_schtasks` decode, task-script cd-anchor, vbs quoting/pythonpath, scheduled-task install/UAC flows, stop-drain helpers). NOTE for test phase: re-evaluate against the merged `hermes_cli/gateway_windows.py` — if the fork's Windows hardening was kept in the module, these tests should be re-added.
- tests/hermes_cli/test_gateway_wsl.py → took-upstream. Trivial formatting (one-liner vs multi-line `setattr`); dropped 2 extra `supports_systemd_services` tests (WSL without systemd, native linux).
- tests/hermes_cli/test_goals.py → took-upstream. Dropped extra goals tests.
- tests/hermes_cli/test_gui_command.py → took-upstream. Dropped extra GUI tests.
- tests/hermes_cli/test_hooks_cli.py → merged. Upstream's 8 tests + RE-ADDED `skipif(sys.platform == "win32", reason="shell hooks execute POSIX shell scripts…")` on `test_synthetic_payload_matches_production_shape` and `test_fires_real_subprocess_and_parses_block` (both write+execute `#!/usr/bin/env bash` scripts — cannot run on Windows; upstream lacks the skip) + `import sys`. Dropped our 5 extra hook tests.

## Notes for the test phase
- CN-patch test re-adds made: P-051 locale-style taskkill assertion (test_whatsapp_connect), Feishu no-thread test (CN platform, test_stream_consumer_thread_routing), win32 skipifs where upstream lacks them (apply_profile_override sudo, container_boot _posix_only, hooks_cli bash-script tests).
- Largest dropped-test groups to re-evaluate if the corresponding merged modules kept fork behavior: test_gateway_windows (~30 Windows-gateway tests), test_auth_nous_provider CA-bundle tests, test_telegram_network_reconnect AST classifier tests, test_config/test_cmd_update extra coverage.
- All orjson/pybase64 test usage switched to stdlib json/base64 per rule 4 (upstream formatting); both libs remain fork deps.

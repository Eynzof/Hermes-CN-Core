# Batch tests-4 — per-file conflict resolution report

Batch: `batches/tests-4.txt` (60 test files, `tests/hermes_cli/*` + `tests/honcho_plugin/*`).
Protocol: `records/RESOLUTION-PROTOCOL.md` (tests default = upstream base + re-add CN tests/win32 skipifs).
All files pass `python -m py_compile`; zero line-anchored conflict markers remain; all files `git add`ed.
`test_uv_tool_update.py` was deleted by upstream (#68217 "rip out brew + pip/PyPI wheel support"); the merge auto-resolved it as a clean staged deletion — no markers, nothing to resolve.

Decision keys: **took-upstream** = upstream's version used as base (fork-only additions dropped per rule 5, noted); **kept-ours** = fork side kept; **merged** = both sides composed.

| path | decision | rationale |
|---|---|---|
| tests/hermes_cli/test_install_cua_driver.py | took-upstream | ours side empty; upstream added `from types import SimpleNamespace` import — trivial, take theirs |
| tests/hermes_cli/test_kanban_boards.py | took-upstream | both hunks ours-only (boards create/switch, rm-archive tests) upstream removed; not CN — dropped, noted for test phase |
| tests/hermes_cli/test_kanban_cli.py | took-upstream | 4 hunks ours-only (run_slash CLI tests) upstream removed; dropped+noted |
| tests/hermes_cli/test_kanban_core_functionality.py | took-upstream | 11 hunks ours-only (bulk complete/archive/block, stats, known_assignees, duration, runs, resolve_workspace, worker-context, skills dedup, hallucination audit) upstream removed; dropped+noted |
| tests/hermes_cli/test_kanban_db.py | merged | h0: kept fork's module-level `skipif(win32)` ("Windows baseline: path format incompatibility") + upstream's `import hermes_state` (dup kb import removed); h1: ours-only artifact-preservation tests dropped (upstream removed) |
| tests/hermes_cli/test_kanban_decompose.py | took-upstream | ours-only decompose/fanout tests upstream removed; dropped+noted |
| tests/hermes_cli/test_kanban_notify.py | took-upstream | ours-only artifact-upload notifier test upstream removed; dropped+noted |
| tests/hermes_cli/test_kanban_promote.py | took-upstream | ours-only promote audit/bulk tests upstream removed; dropped+noted |
| tests/hermes_cli/test_kanban_specify.py | took-upstream | ours-only specify CLI tests upstream removed; dropped+noted |
| tests/hermes_cli/test_managed_uv.py | merged | h0/h2: ours-only skipif(win32) POSIX-uv-layout tests dropped (upstream removed); h1: kept fork's `test_self_update_success` (win32 skipif preserved) + upstream's fresh/stale stamp tests (restored fork test body that the 3-way merge split) |
| tests/hermes_cli/test_migrate_xai.py | took-upstream | ours-only xai-migration comment-preservation tests (errors="replace" reads) upstream removed; dropped+noted |
| tests/hermes_cli/test_model_catalog.py | merged | h0: kept fork CN-mirror PRIMARY (`desktop.hermesagent.org.cn`, China-network policy) + `test_default_url_points_to_cn_desktop_mirror` + upstream's FALLBACK chain tests (fixed shared-body split between `test_get_catalog_fetches_only_configured_url` and `test_get_catalog_uses_fallback_chain`); h1/h2: kept fork curated-accessor tests (`test_nous_returns_ids`, `test_openrouter_returns_none_when_catalog_empty`, `get_curated_nous_model_ids` fallback tests) |
| tests/hermes_cli/test_model_switch_custom_providers.py | merged | kept both offline helpers (`_disable_remote_nous_catalog` fork + `_disable_live_custom_provider_model_probe` upstream); both currently unused in-file, harmless |
| tests/hermes_cli/test_model_validation.py | took-upstream | ours-only `github_model_reasoning_efforts` catalog tests upstream removed; dropped+noted |
| tests/hermes_cli/test_models.py | merged | h0: kept fork `_use_static_curated_snapshot` fixture + `test_live_fetch_recomputes_free_tags` (offline-first, P-028 spirit — keeps OpenRouter tests off the network); h1: kept fork `TestKimiK3ContextLengthInvariant` (Kimi k3 context lengths, P-028-adjacent) + upstream `TestFormatPricePerMtok` |
| tests/hermes_cli/test_models_dev_preferred_merge.py | merged | kept fork kimi offline-catalog tests (`test_kimi_coding_offline_catalog_includes_k3`, `test_kimi_coding_live_catalog_does_not_hide_curated_k3` — P-028 offline-first) + upstream k3 live-discovery tests |
| tests/hermes_cli/test_ollama_cloud_provider.py | took-upstream | ours-only ollama-cloud cache/force-refresh/stale-cache tests upstream removed; not CN — dropped+noted |
| tests/hermes_cli/test_path_completion.py | kept-ours | h0: fork `test_home_expansion` kept (USERPROFILE env fix = P-048 Windows-compat; restored body split by 3-way merge); h1: fork `skipif(win32)` on `/etc/hosts` POSIX-only test kept |
| tests/hermes_cli/test_plugins_cmd_list.py | took-upstream | ours-only `cmd_list --json` output test upstream removed; dropped+noted |
| tests/hermes_cli/test_profile_describer.py | took-upstream | ours-only describer overwrite/malformed/missing tests upstream removed; dropped+noted |
| tests/hermes_cli/test_profiles.py | took-upstream | 4 hunks ours-only (wrapper traversal, honcho rename aiPeer, gateway-running scope checks, profile-name validation) upstream removed; dropped+noted |
| tests/hermes_cli/test_projects_db.py | merged | h0/h2/h3: ours-only POSIX-path tests (fork `skipif(win32)` markers) upstream removed — dropped+noted; h1: kept fork `test_record_discovered_repos_replace_drops_stale_rows` (skipif win32) + upstream `test_discovery_policy_change_clears_only_discovered_rows` (restored fork test tail split by 3-way merge) |
| tests/hermes_cli/test_prompt_compose_command.py | kept-ours | fork `skipif(win32)` markers on 3 editor tests ("fake editor is a bash script; POSIX-only") — genuinely cannot run on Windows-first env; upstream side empty |
| tests/hermes_cli/test_prompt_size.py | took-upstream | ours-only render_breakdown/json-serializable tests upstream removed; dropped+noted |
| tests/hermes_cli/test_proxy.py | took-upstream | ours-only NousPortalAdapter tests (206 lines) upstream removed; adapter module is byte-identical in both branches (clean merge), so fork had no divergent adapter behavior — dropped+noted |
| tests/hermes_cli/test_runtime_provider_resolution.py | took-upstream | ours-only runtime-provider resolution tests (273 lines) upstream removed; not a documented CN patch — dropped+noted |
| tests/hermes_cli/test_send_cmd.py | took-upstream | ours-only send-cmd tests (stdin/subject/json/quiet/error/skipped/list) upstream removed; dropped+noted |
| tests/hermes_cli/test_service_manager.py | took-upstream | ours-only s6 service-manager tests (`@_posix_only`, upstream removed); not CN — dropped+noted |
| tests/hermes_cli/test_session_export.py | took-upstream | ours-only CLI export tests (63 lines) upstream removed; dropped+noted |
| tests/hermes_cli/test_session_export_md.py | took-upstream | ours-only manifest-entry test upstream removed; dropped+noted |
| tests/hermes_cli/test_sessions_export_md_cli.py | took-upstream | ours-only sessions-export CLI tests (401+23 lines: qmd/trace/unknown-session etc.) upstream removed; dropped+noted — test phase should re-add if `sessions export --format qmd/trace` survives in merged code |
| tests/hermes_cli/test_setup.py | took-upstream | ours-only setup tests (222 lines, incl. one systemctl win32-skipif) upstream removed; dropped+noted |
| tests/hermes_cli/test_signal_handler_kanban_worker.py | took-upstream | trivial refactor (upstream split one-line `if` into early-return form); same behavior |
| tests/hermes_cli/test_spotify_auth.py | took-upstream | ours-only `test_spotify_logout_does_not_reset_model_provider` upstream removed; dropped+noted |
| tests/hermes_cli/test_teams_pipeline_plugin_cli.py | took-upstream | ours-only subscribe-defaults/token-health tests upstream removed; dropped+noted |
| tests/hermes_cli/test_terminal_menu_fallbacks.py | took-upstream | ours-only curses-menu fallback tests upstream removed; dropped+noted |
| tests/hermes_cli/test_tool_token_estimation.py | took-upstream | ours-only status_fn token-estimation tests upstream removed; dropped+noted |
| tests/hermes_cli/test_tools_config.py | took-upstream | ours-only context_engine toolset tests upstream removed (upstream source still has context_engine — tests dropped per rule 5; noted) |
| tests/hermes_cli/test_uninstall_node_symlinks.py | took-upstream | ours-only symlink-removal tests (win32 skipif markers) upstream removed; dropped+noted |
| tests/hermes_cli/test_update_autostash.py | took-upstream | ours-only update autostash/restore/cmd_update tests (304+ lines, some win32 skipif "git operations fail") upstream removed; note: fork tests reference `hermes_cli.main._stash_local_changes_if_needed` etc. which upstream moved to `hermes_cli/update_cmd.py` — test phase should re-point if the fork keeps the stash flow |
| tests/hermes_cli/test_update_check.py | took-upstream | ours-only banner `check_for_updates` tests (274 lines) upstream removed; note: `check_via_pypi` (used by tests) is being ripped out upstream (#68217) — dropped correctly |
| tests/hermes_cli/test_update_modified_notice.py | merged | upstream's both-modules scan (`main_mod` + `update_mod`) + fork's `errors="replace"` read hardening (P-048) composed |
| tests/hermes_cli/test_update_post_pull_syntax_guard.py | took-upstream | ours-only `_validate_critical_files_syntax` tests (win32 skipif) upstream removed; dropped+noted (the literal `<<<<<<<` strings in the file are the upstream fixture's intended test data) |
| tests/hermes_cli/test_update_stale_dashboard.py | merged | kept fork `test_wmic_returns_none_stdout_does_not_crash` (py3.14 wmic UnicodeDecodeError fix #17049, cp936 zh-CN) inside `TestWindowsWmicEncoding` + upstream's `TestSupervisedBackendRestart`/`TestManualBackendRespawn` classes |
| tests/hermes_cli/test_update_venv_health.py | took-upstream | ours-only venv-python detection tests (win32 skipif) upstream removed; dropped+noted |
| tests/hermes_cli/test_user_providers_model_switch.py | took-upstream | h0: upstream offline helper `_no_live_builtin_provider_probes`; h1: ours-only kimi private-model switch tests (132 lines) upstream removed — not a documented CN patch — dropped+noted |
| tests/hermes_cli/test_web_oauth_dispatch.py | kept-ours | P-025 (documented CN patch): fork OAuth provider-status cache tests (`test_oauth_status_cache_*`, `test_status_unknown_provider_degrades_to_logged_out`) kept — upstream has no `_oauth_status_cache` in web_server |
| tests/hermes_cli/test_web_server.py | took-upstream | all 9 hunks ours-only (honcho config API, session search lineage, profiles CRUD/setup-command/open-terminal, analytics usage, gateway health probe) upstream removed; CN-patch tests (image-upload, ops import-upload) in clean regions were auto-merged and are preserved; noted |
| tests/hermes_cli/test_web_server_files.py | took-upstream | ours-only /api/files local-mode tests (win32 skipif) upstream removed; upstream web_server has /api/files too — dropped+noted |
| tests/hermes_cli/test_web_server_git.py | took-upstream | ours-only worktree/branch lifecycle test (win32 skipif) upstream removed; dropped+noted |
| tests/hermes_cli/test_web_server_messaging_profiles.py | took-upstream | ours-only profile-scoped env/multiplex tests upstream removed; dropped+noted |
| tests/hermes_cli/test_web_server_pty_reconnect.py | took-upstream | ours-only channel-reconnect session-resume test upstream removed; dropped+noted |
| tests/hermes_cli/test_web_ui_build.py | took-upstream | upstream rewrote file to content-hash freshness (new imports/fixture/docstring/tests); fork-only additions (5 tests incl. termux/desktop npm-install) and their win32 skipif markers dropped — new upstream tests are platform-neutral; noted |
| tests/hermes_cli/test_webhook_cli.py | took-upstream | ours-only `test_file_written` upstream removed; dropped+noted |
| tests/honcho_plugin/test_async_memory.py | took-upstream | ours-only config writeFrequency tests upstream removed; honcho not a CN patch — dropped+noted |
| tests/honcho_plugin/test_client.py | took-upstream | 12 hunks ours-only `HonchoClientConfig` tests (config parsing, env fallback, depth/aliases/truncation) upstream removed; dropped+noted |
| tests/honcho_plugin/test_empty_profile_hint.py | took-upstream | ours-only hint tests upstream removed; dropped+noted |
| tests/honcho_plugin/test_pin_peer_name.py | took-upstream | ours-only pinPeerName config tests upstream removed; dropped+noted |
| tests/honcho_plugin/test_session.py | took-upstream | ours-only honcho session tests (230 lines) upstream removed; dropped+noted |
| tests/hermes_cli/test_uv_tool_update.py | took-upstream (deleted) | upstream deleted the file (#68217); merge auto-resolved as staged deletion — no markers, nothing to resolve |

## CN-patch test re-adds / Windows-compat kept (per protocol rule 5/2)
- P-025 (web_server OAuth status cache): `tests/hermes_cli/test_web_oauth_dispatch.py` — kept fork tests.
- P-028 (offline models.dev / static snapshot / Kimi curated): `test_models.py` h0 + `test_models_dev_preferred_merge.py` — kept fork tests.
- P-048 (Windows/py3.14): `errors="replace"` reads kept in `test_update_modified_notice.py`; USERPROFILE handling in `test_path_completion.py`; win32 skipif markers kept in `test_kanban_db.py` (module), `test_managed_uv.py`, `test_projects_db.py`, `test_prompt_compose_command.py`, `test_update_stale_dashboard.py` (wmic #17049).
- China-network: CN mirror URL tests kept in `test_model_catalog.py`.

## Lost-ours coverage to re-evaluate in test phase (all dropped per rule 5, none CN-documented)
Kanban CLI/DB/promote/specify/decompose/notify extras; NousPortalAdapter tests; runtime_provider_resolution; send_cmd; s6 service_manager; sessions_export_md_cli (qmd/trace formats); setup; update autostash/check/post-pull/venv-health (reference `hermes_cli.main` update helpers upstream moved to `update_cmd.py`); web_server honcho/profiles/analytics/gateway-health/search tests; web_server_files local-mode; web_ui_build fork tests; honcho_plugin config tests; spotify_auth logout; teams_pipeline subscribe; terminal_menu_fallbacks; tool_token_estimation; tools_config context_engine; uninstall_node_symlinks; webhook_cli; profiles wrapper/honcho-rename/gateway-running; profile_describer; plugins_cmd_list json; prompt_size; ollama_cloud cache tests; model_validation reasoning-efforts; migrate_xai comment tests; session_export md tests.

## Notes for merge record
- `batches/tests-4.txt` uses CRLF line endings — strip `\r` when scripting over it.
- Two 3-way-merge artifacts fixed by hand: shared "def body" split between fork/upstream tests in `test_model_catalog.py`, `test_path_completion.py`, `test_managed_uv.py`, `test_projects_db.py` — fork test bodies restored from `:2:` stage.
- `test_prompt_compose_command.py` resolves to content identical to HEAD (kept-ours); staged clean.
- All files verified: `py_compile` OK, zero `<<<<<<<`/`=======`/`>>>>>>>` at line start (the literal marker strings inside `test_update_post_pull_syntax_guard.py` are the fixture's intended test data), zero unmerged index entries.

# Batch tests-1 — conflict resolution report

Merge: `git merge upstream/main` into `dev-fix`. All files below resolved per
`records/RESOLUTION-PROTOCOL.md` rule 5 (tests: upstream base + CN-patch tests +
win32 skipif where required). Every `.py` passed `python -m py_compile`; every
file has 0 remaining conflict markers; every file is `git add`ed. **No commit
made; merge state untouched.**

Runtime test execution could not be completed in-tree: importing several kept
test files pulls in modules that are STILL unmerged by other agents
(`agent/models_dev.py`, `agent/moa_loop.py`, `agent/auxiliary_client.py`,
`agent/prompt_builder.py`, `cli.py` are all `UU`). Verify those in the test
phase once the full merge is resolved.

| # | path | decision | rationale |
|---|---|---|---|
| 1 | `skills/creative/comfyui/tests/test_common.py` | took-upstream | ours-only was an `orjson`→`json` perf swap on one test; no CN patch; upstream's `json.loads(…read_text(encoding="utf-8"))` is correct cross-platform |
| 2 | `tests/acp/test_edit_approval.py` | took-upstream | dropped ~150 lines ours-only (5 generic edit-approval tests + `orjson`/`errors="replace"` drift); `set_edit_approval_requester` + tested code exist upstream |
| 3 | `tests/acp/test_registry_manifest.py` | already-resolved (staged delete) | upstream removed the file/feature (ACP registry switched to uvx, npm launcher dropped); ours had only a 6-line adaptation; no markers; deletion already staged — left as-is |
| 4 | `tests/acp/test_session.py` | took-upstream | dropped ~438 lines ours-only (SessionManager DB-persistence / WSL-cwd / fork tests); all tested symbols (`_get_db`, `update_session_meta`, `_register_task_cwd`) exist in upstream `acp_adapter/session.py` |
| 5 | `tests/acp/test_session_db_private_access.py` | took-upstream | dropped our `test_preserves_existing_model_when_none` (COALESCE) — upstream has its own `update_session_meta` coverage; code exists upstream |
| 6 | `tests/agent/lsp/test_protocol.py` | took-upstream | dropped ours-only unicode/orjson `encode_message` test; framer is upstream code |
| 7 | `tests/agent/lsp/test_shell_linter_lsp_skip.py` | took-upstream | dropped ours-only `test_lsp_does_not_skip_non_redundant_extensions`; `_lsp_will_handle`/`_check_lint` exist upstream |
| 8 | `tests/agent/test_anthropic_adapter.py` | took-upstream | dropped 2 ours-only OAuth/static-token tests; token-resolution code exists upstream |
| 9 | `tests/agent/test_anthropic_keychain.py` | took-upstream | dropped ours-only keychain password-field test (patches `platform.system`, macOS keychain); generic |
| 10 | `tests/agent/test_anthropic_oauth_pkce.py` | took-upstream | dropped ours-only console-host fallback test; generic OAuth |
| 11 | `tests/agent/test_auxiliary_client.py` | kept-ours | **P-039 re-added (10 hunks)**: aux "auto" never probes OpenRouter/Nous, kimi-coding-cn/minimax-cn vision skips, `_try_payment_fallback`, codex JWT `_read_codex_access_token`, pool-unhealthy warnings, `_try_nous` pool entry. All patched symbols verified present in merged `agent/auxiliary_client.py` (`_read_main_provider`, `_try_openrouter`, `_try_nous`, `_is_provider_unhealthy`, `resolve_vision_provider_client`, `_get_aux_model_for_provider`, …) |
| 12 | `tests/agent/test_auxiliary_main_first.py` | kept-ours | **P-039 re-added (4 hunks)**: nous-main model reuse, non-aggregator main, main-unavailable chain fallback (`or_try`/`nous_try` never called), runtime-override-wins, vision strict-backend fallback. Symbols verified in merged module |
| 13 | `tests/agent/test_codex_app_server_persist.py` | merged | upstream's new `test_codex_user_interrupt_is_reported_and_cleared` kept; our `xfail(sys.platform=="win32", …temp file lock race)` decorator kept on the shared `test_codex_turn_persists_each_message_exactly_once` (rule 2 — flaky on Windows) |
| 14 | `tests/agent/test_codex_gpt55_autoraise_notice.py` | took-upstream | dropped ours-only idempotency test; generic |
| 15 | `tests/agent/test_coding_context.py` | took-upstream | dropped ours-only package.json verify-commands test; generic |
| 16 | `tests/agent/test_compression_concurrent_fork.py` | took-upstream | upstream rewrote file with its own lock/lease/fencing tests (incl. `_compression_lock_refresh_interval` which exists upstream); our 3 extra tests are generic coverage of upstream features; H1 comment drift → upstream comment matches following `_compression_feasibility_checked = True` |
| 17 | `tests/agent/test_context_compressor.py` | took-upstream | dropped 3 ours-only shrink/JSON-decode tests (orjson); generic unit coverage |
| 18 | `tests/agent/test_context_compressor_summary_continuity.py` | took-upstream | upstream rewrote file (~338 upstream-only lines); our hunk's `fake_generate_summary` returns "new summary from resumed turns" which contradicts the shared assertions (`"fresh replacement summary" in joined`, `count(SUMMARY_PREFIX)==1`) — kept upstream's `_capture`/`_with_summary_prefix` |
| 19 | `tests/agent/test_context_engine.py` | took-upstream | dropped ours-only orjson `handle_tool_call` error test |
| 20 | `tests/agent/test_context_references.py` | took-upstream | dropped ours-only sensitive-home-paths test; `agent/context_references.py` exists upstream |
| 21 | `tests/agent/test_copilot_acp_client.py` | took-upstream | dropped ours-only `_create_chat_completion` test |
| 22 | `tests/agent/test_credential_pool.py` | took-upstream | dropped 8 hunks of ours-only tests (DEAD-prune, load_pool non-destructive, nous/xai/codex oauth terminal-refresh, remove_index). Tested code exists upstream (`credential_pool.py`, `hermes_cli/auth.resolve_nous_runtime_credentials` etc.); H1 ours' assertion (bench persisted to disk with `last_status=exhausted`) **contradicts** merged code's upstream #70401 no-bench behavior — upstream assertion taken |
| 23 | `tests/agent/test_credential_pool_oauth_writethrough.py` | took-upstream | upstream ADDED a new test (`test_write_through_fires_on_every_refresh_not_just_first`) — kept it |
| 24 | `tests/agent/test_curator_backup.py` | took-upstream | dropped 2 ours-only snapshot/cron-jobs tests |
| 25 | `tests/agent/test_curator_classification.py` | took-upstream | dropped 6 ours-only classification/rename-summary tests |
| 26 | `tests/agent/test_curator_reports.py` | took-upstream | dropped 3 ours-only report-shape/LLM-error/cron-prune tests |
| 27 | `tests/agent/test_display.py` | took-upstream | dropped ours-only lint-error display test |
| 28 | `tests/agent/test_display_tool_failure.py` | took-upstream | dropped 4 ours-only `_detect_tool_failure` tests |
| 29 | `tests/agent/test_file_safety_credentials.py` | took-upstream | dropped ours-only relative-path-bypass test |
| 30 | `tests/agent/test_insights.py` | took-upstream | dropped ours-only tool_calls-JSON insights test |
| 31 | `tests/agent/test_learning_mutations.py` | took-upstream | dropped 2 ours-only rewrite/validate tests |
| 32 | `tests/agent/test_memory_provider.py` | took-upstream | dropped 2 ours-only registry/tool-conflict tests |
| 33 | `tests/agent/test_memory_write_bridge.py` | took-upstream | dropped 2 ours-only write-bridge tests |
| 34 | `tests/agent/test_models_dev.py` | merged | kept ours' `import agent.models_dev as _md` (needed by the P-028 cache-restore autouse fixture `_restore_models_dev_cache`); all P-028 snapshot/offline-first tests were in auto-merged regions and remain |
| 35 | `tests/agent/test_nous_rate_guard.py` | took-upstream | dropped 2 ours-only record/cooldown tests; `agent/nous_rate_guard.py` exists upstream |
| 36 | `tests/agent/test_prompt_builder.py` | kept-ours (hunk) | **P-016/P-019/P-050 re-added (1 hunk, 134 lines)**: `test_build_environment_hints_on_windows_local` + bash/ps51/pwsh hint tests using `_WINDOWS_BASH_SHELL_HINT`/`_WINDOWS_POWERSHELL_SHELL_HINT`/`_WINDOWS_PWSH_SHELL_HINT`/`_clear_backend_probe_cache` — all verified present in merged `agent/prompt_builder.py` |
| 37 | `tests/agent/test_shell_hooks.py` | merged | dropped ours-only `test_parent_session_id_used_when_no_session_id` (generic); kept ours-only `test_script_is_executable_handles_interpreter_prefix` with `skipif(win32)` (rule 2 — X_OK semantics are POSIX-only) |
| 38 | `tests/agent/test_skill_utils.py` | took-upstream | dropped ours-only config-cache invalidation test |
| 39 | `tests/agent/test_ssl_ca_guard.py` | kept-ours | **P-044 re-added (1 hunk, 85 lines)**: `HERMES_SKIP_SSL_GUARD` escape-hatch + `_ca_bundle_fingerprint` memoization/re-invalidation tests; symbols verified in merged `agent/ssl_guard.py` |
| 40 | `tests/agent/test_subdirectory_hints.py` | kept-ours | ours-only `test_terminal_cd_command` kept with `skipif(win32)` (rule 2 — path-format incompatibility on Windows baseline) |
| 41 | `tests/agent/test_tool_guardrails.py` | took-upstream | dropped ours-only lint-error-not-failure test |
| 42 | `tests/agent/test_tool_result_classification.py` | took-upstream | dropped ours-only nested-LSP-diagnostics test |
| 43 | `tests/agent/test_verification_stop.py` | kept-ours | ours-only `test_no_suite_nudge_requests_temp_script` kept with `skipif(win32)` (rule 2) |
| 44 | `tests/agent/test_vision_routing_31179.py` | took-upstream | upstream rewrote the isolation fixture (`_RELOAD_PREFIXES`/`_drop_reload_targets`/`_module_isolation`, issue #61597); the shared tests call `_fresh_modules()` → `_drop_reload_targets()` (upstream-only), so ours' `_restore_sys_modules` full-snapshot fixture is incompatible — upstream taken |
| 45 | `tests/agent/transports/test_chat_completions.py` | took-upstream | dropped ours-only gemini-flash clamp test |
| 46 | `tests/cli/test_cli_file_drop.py` | took-upstream | trivial drift: ours `if os.name=="nt": setenv(USERPROFILE)` vs upstream unconditional `setenv(USERPROFILE)` + comment — equivalent, upstream formatting preferred (rule 4) |
| 47 | `tests/cli/test_cli_image_command.py` | took-upstream | same trivial USERPROFILE drift as #46 |
| 48 | `tests/cli/test_cli_save_config_value.py` | merged | **P-027 kept**: ours' `test_save_config_value_never_writes_into_source_tree` (explicit `[CN-fork] P-027` tag) + ours' `test_preserves_readable_unicode_after_config_mutation` (CN GBK/unicode — 你好 must not be `\u4f60`-escaped; rule 2) both kept; upstream's new `TestSaveConfigValueTargetsUserConfig` class (same bug class) kept; H1 took upstream's `save_config_value`+warning body (matches the test name `test_model_write_runs_shared_cron_drift_warning`); dropped ours' comment-preservation body (generic; upstream deleted that test) |
| 49 | `tests/cli/test_cli_status_bar.py` | took-upstream | dropped ours-only spinner elapsed-format test (orjson/MagicMock; generic) |
| 50 | `tests/cli/test_tool_progress_scrollback.py` | took-upstream | dropped ours-only error-suffix test (generic) |
| 51 | `tests/computer_use/test_doctor.py` | took-upstream | dropped 2 ours-only doctor tests (text-content fallback, empty filters); H4 took upstream's assertion set — ours' full-equality `parsed == _ok_report()` would FAIL on merged `doctor.py` because Hermes adds `hermes_identity`; upstream's per-key + `hermes_identity` assertions match merged code |
| 52 | `tests/conftest.py` | merged | kept ours' `HERMES_DISABLE_MODELS_DEV_PREWARM=1` (**P-028** — never spawn prewarm thread in tests) AND upstream's `HERMES_DISABLE_LAZY_INSTALLS=1` (lazy-deps kill-switch); both are independent network kill-switches, both needed |
| 53 | `tests/cron/test_cron_context_from.py` | took-upstream | dropped ours-only context-from-reference test; `cron/jobs.py` exists upstream |
| 54 | `tests/cron/test_cron_no_agent.py` | took-upstream | dropped 3 ours-only tests (incl. 2 with `skipif(win32)` for `.sh` script execution — P-019 refuses `.sh`/`.bash` cron jobs on Windows, so they are moot on our platform); tested code exists upstream |
| 55 | `tests/cron/test_cron_script.py` | took-upstream | dropped 4 ours-only script-execution tests; `tools/cronjob_tools.py` exists upstream |
| 56 | `tests/cron/test_cron_workdir.py` | took-upstream | dropped ours-only workdir test |
| 57 | `tests/cron/test_jobs.py` | took-upstream | dropped ours-only heartbeat/interval-stale tests (generic scheduling; "stale" refs are interval fast-forward, not P-021 tick.lock); upstream has its own 183-line additions |
| 58 | `tests/cron/test_jobs_changed_notify.py` | took-upstream | dropped ours-only tool-remove-notify test |
| 59 | `tests/cron/test_scheduler.py` | took-upstream | dropped 4 ours-only tests (prefill-messages file, credential ContextVar passthrough, missing-skill warning, bump_use); none map to P-021's documented fixes (P-021 lists no test files); upstream rewrote with 729 own lines |
| 60 | `tests/docker/test_gateway_bootstrap_state.py` | took-upstream | dropped ours-only does-not-clobber-existing-state test; generic |

## Notes for the test phase
- **CN tests re-added (kept-ours/merged):** P-039 (`test_auxiliary_client.py`, `test_auxiliary_main_first.py`), P-028 (`test_models_dev.py` import + conftest prewarm kill-switch), P-044 (`test_ssl_ca_guard.py`), P-016/P-019/P-050 (`test_prompt_builder.py` shell hints), P-027 (`test_cli_save_config_value.py` source-tree + unicode tests), plus win32 skipif/xfail markers in `test_subdirectory_hints.py`, `test_verification_stop.py`, `test_shell_hooks.py`, `test_codex_app_server_persist.py`.
- **Dropped ours-only tests** (generic coverage of upstream code, per rule 5 — re-evaluate in test phase if any covered behavior is still CN-only): ACP session/edit-approval extras, anthropic adapter/keychain/oauth extras, compression/concurrency extras, credential_pool extras, curator extras, display/tool-classification extras, cron extras (~5,900 lines across `tests/cron/*` and docker), doctor/insights/context extras. All tested modules verified to exist on `upstream/main` except where noted.
- **Deliberate upstream-taken where ours would fail:** `test_credential_pool.py` H1 (ours asserts disk bench persistence; merged code implements upstream #70401 no-bench), `test_doctor.py` H4 (ours' full-equality assert vs merged `hermes_identity` output), `test_vision_routing_31179.py` (shared tests depend on upstream's `_drop_reload_targets`), `test_context_compressor_summary_continuity.py` (ours' helper contradicts shared assertions).
- Runtime runs of kept CN tests were attempted but blocked by still-unmerged (other agents') modules: `agent/models_dev.py`, `agent/moa_loop.py`, `agent/auxiliary_client.py`, `agent/prompt_builder.py`, `cli.py`. Re-run after full merge.

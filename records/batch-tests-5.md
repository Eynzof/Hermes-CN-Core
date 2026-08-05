# Batch tests-5 — conflict resolution report

Agent: tests-5 sub-agent. 60 files assigned. All conflict markers removed; all `.py`
files pass `python -m py_compile`; all resolved files staged with `git add`; no commit,
no merge-state changes. Remaining markers in batch: **0**.

## Decision summary
- Default for every test file (rule 5): take UPSTREAM's side of each conflict block as
  the base; ours-only test additions (upstream side empty) were DROPPED and noted below.
  None of the protocol-listed CN-patch test files (P-014/P-028/P-030/P-033/P-037/P-041/
  P-049/P-051/P-020/P-042/P-044) are in this batch, so no CN-patch test re-adds were
  required; dropped ours-only coverage is listed in the notes so the test phase can
  re-evaluate.
- Exceptions (merged/kept-ours) below.

## Per-file decisions
- tests/openviking_plugin/test_openviking.py → took-upstream (base) + merged import block: dropped ours-only tests (orjson batch/json-error, full-read URI, read-dedup, slug tests, test for `test_uri_slug_is_twelve_hex_chars_and_unique`); kept upstream's `# Issue #21130` section. Auto-merged ours-side hunks still call `orjson` 7× and `json` 1×, so import block = `import json` + `import orjson` + `import os` (was missing `orjson` after taking upstream).
- tests/plugins/dashboard_auth/test_self_hosted_provider.py → took-upstream: dropped ours-only test `test_discovery_follows_redirect_to_json` + auto-merged ours-only extras (83→29 tests kept upstream).
- tests/plugins/image_gen/test_fal_provider.py → took-upstream: dropped ours-only `test_generate_invalid_aspect_ratio_is_coerced`; upstream's 8 tests kept.
- tests/plugins/image_gen/test_xai_provider.py → took-upstream: dropped ours-only tests incl. win32-skipif `test_successful_url_response` and `test_api_error_preserves_real_response_status` (both ours-only; marker dropped with test).
- tests/plugins/memory/test_hindsight_provider.py → took-upstream: dropped 10 ours-only test blocks (normalize-retain-tags, local-embedded-setup, retain-with-tags, recall-max-tokens, reflect-missing-query, retain-error, sync-turn ×2, template-bank, not-available-without-config). Added `import json` (auto-merged ours hunks use `json.dumps` 1× while upstream import block had only `orjson`).
- tests/plugins/memory/test_mem0_setup.py → took-upstream: dropped ours-only `test_platform_setup_clears_stale_host`.
- tests/plugins/memory/test_mem0_v3.py → took-upstream: dropped 5 ours-only test blocks (add-returns-event-id, update/delete-missing-id, update-404, old-tool-names).
- tests/plugins/memory/test_openviking_provider.py → took-upstream: dropped 13 ours-only blocks (orjson/.local variants → upstream `json.dumps`/`.test`; CLI-config-env-override, discover-profiles, post-setup-mirror, waiter, search-sort, add-resource, forget, sync-turn retry tests dropped); kept upstream tests/helpers (`test_profile_discovery_warns_when_skipping_unsafe_ovcli_endpoint`, `test_local_setup_recommends_user_api_key_before_unauthenticated_mode`, `test_get_tool_schemas_omits_profile_and_keeps_narrow_forget_tools`, `_long_structured_turn`, prompt helper).
- tests/plugins/memory/test_supermemory_provider.py → took-upstream: dropped ours-only forget-by-query + multi-container tests.
- tests/plugins/platforms/photon/test_auth.py → took-upstream: ours `from pybase64 import b64encode` replaced by upstream imports (`stat/threading/time`); dropped ours-only tests.
- tests/plugins/platforms/photon/test_inbound.py → took-upstream + merged import: ours `import pybase64 as base64`/`import orjson` replaced by upstream `asyncio/base64/json`; auto-merged ours hunk uses `orjson` 1× → added `import orjson`.
- tests/plugins/platforms/photon/test_sidecar_lifecycle.py → took-upstream: dropped ours-only win32-skipif `test_reap_kills_verified_orphan` (test not in upstream; marker dropped with test).
- tests/plugins/platforms/photon/test_spectrum_patch.py → took-upstream: dropped ours-only `test_sidecar_applies_spectrum_patch_before_importing_sdk` + win32-skipif `test_spectrum_patch_preserves_text_at_runtime`; kept upstream `_sidecar_env` helper + tests.
- tests/plugins/test_disk_cleanup_plugin.py → took-upstream: dropped ours-only `test_quick_skips_stale_cron_output_for_cron_dir`.
- tests/plugins/test_google_meet_node.py → took-upstream: dropped 3 ours-only tests (protocol autogen-id, server bad-token, cli list-empty).
- tests/plugins/test_google_meet_plugin.py → took-upstream: dropped 6 ours-only test blocks (bot-state blank-text, meet-join safety gate, enqueue-say, unknown-node, v2 telemetry, realtime cancel-frame).
- tests/plugins/test_google_meet_realtime.py → took-upstream: dropped ours-only `test_speaker_exits_immediately_when_stop_fn_true`.
- tests/plugins/test_kanban_dashboard_plugin.py → took-upstream: dropped ours-only `test_patch_board_clears_project_directory`.
- tests/plugins/test_nemo_relay_plugin.py → took-upstream: dropped ours-only `test_nemo_relay_plugin_uses_nemo_relay_runtime` (P-048 `.as_posix()` f-string fixes lived in ours-only tests; upstream kept tests verified free of backslash TOML f-strings); added `import json` (auto-merged ours hunk uses `json.loads` 1×).
- tests/plugins/test_retaindb_plugin.py → took-upstream: dropped 2 ours-only test blocks.
- tests/run_agent/test_background_review.py → took-upstream: dropped 2 ours-only test blocks (summarizer-messages, self-improvement attribution).
- tests/run_agent/test_compression_boundary_hook.py → merged (rule 2): kept ours' module-level `pytestmark = skipif(win32)` ("Windows baseline: tempfile/path operations fail") + `import run_agent`, re-added upstream's `from agent.conversation_compression import finalize_context_engine_compression_notification`; upstream's 8 tests (incl. 3 new ones) kept.
- tests/run_agent/test_file_mutation_verifier.py → took-upstream: dropped 3 ours-only test blocks (landed-paths, v4a multi-file, record-helper-never-raises).
- tests/run_agent/test_jsondecodeerror_retryable.py → took-upstream: dropped ours-only `TestAgentLoopSourceStillHasCarveOut`.
- tests/run_agent/test_moa_loop_mode.py → took-upstream: dropped ours-only `test_moa_full_trace_written_when_enabled`.
- tests/run_agent/test_provider_parity.py → **kept-ours** (rule 1, P-039 documented CN patch "CN-specific default, won't upstream"): all 3 conflict blocks keep ours — `test_openrouter_key_is_not_implicit_fallback` and `test_nous_auth_is_not_implicit_fallback` assert auto aux resolution does NOT probe OpenRouter/Nous (returns None); upstream side asserts the opposite (openrouter/nous fallback). Kept ours, adapted to merged context (same test names, merged non-conflicted body).
- tests/run_agent/test_repair_tool_call_arguments.py → took-upstream: dropped ours-only trailing-comma/literal-newline/already-valid tests; kept upstream stage comments + tests.
- tests/run_agent/test_run_agent.py → took-upstream: dropped 7 ours-only blocks (recovers-from-history, GHSA injection-vector guard test, concurrent-executor, custom-tool-call-dict, memory-tool patches ×2, hook ordering); kept upstream's 224 tests + their-only additions.
- tests/run_agent/test_run_agent_codex_responses.py → took-upstream: dropped ours-only `test_dump_api_request_debug_redacts_request_and_error_secrets`.
- tests/run_agent/test_steer.py → merged (rule 6): fork's ReminderRegistry/SteerUserReminderProvider steer (run_agent.py steer() region settled as ours, unconflicted) AND upstream's `_pending_steer`/`_pending_redirect`/interrupt machinery both survive in the merged codebase, so `_bare_agent()` now initializes BOTH state sets and both sides' tests are kept (ours: rejects-empty/whitespace/none, strip, concat, tool-result injection, apply-pending-no-op, clear-interrupt registry asserts, steer-before/between-calls; upstream: marker-labels, multimodal-list, scopes-freshness, no-distrusted-label, drain/redirect tests via merged non-conflicted regions). Added `STEER_MARKER_OPEN` + `format_steer_marker` to the `agent.prompt_builder` import (upstream import block). Dropped upstream's alternative body of `test_steer_before_first_tool_call_lands_in_tool_result` (drain-pending_steer variant; equivalent behavior covered by ours' injection-path body — noted for test phase).
- tests/run_agent/test_streaming_tool_call_repair.py → took-upstream: dropped ours-only truncated-nested-object/valid-json-passthrough/unrepairable tests; kept upstream stage comments.
- tests/run_agent/test_tool_batch_segmentation.py → took-upstream: dropped ours-only `test_steer_remains_pending_for_unified_reminder_injection`; kept upstream `test_steer_lands_exactly_once_in_mixed_batch` + their-only segmentation tests.
- tests/run_agent/test_tool_call_guardrail_runtime.py → took-upstream: dropped ours-only `test_config_enabled_hard_stop_run_conversation_returns_controlled_guardrail_halt_without_top_level_error`; added `import json` (auto-merged ours hunks use `json.dumps` 3×).
- tests/run_agent/test_tool_executor_contextvar_propagation.py → took-upstream: dropped ours-only source-level guard test.
- tests/scripts/test_release_acp_registry.py → took-upstream (file deleted upstream; deletion already staged; no CN content noted in FORK_NOTES for this file).
- tests/skills/test_cloudflare_temporary_deploy_skill.py → took-upstream: dropped ours-only `test_main_exit_one_when_no_live_url`.
- tests/skills/test_google_workspace_api.py → took-upstream: dropped ours-only token-refresh + calendar-date-range tests.
- tests/skills/test_google_workspace_credential_files.py → took-upstream: dropped ours-only missing-token test.
- tests/skills/test_hyperliquid_skill.py → took-upstream: dropped 3 ours-only tests (candles-limit, state-env-fallback, export-contract).
- tests/skills/test_mcp_oauth_remote_gateway_skill.py → took-upstream: dropped ours-only atomic-write test.
- tests/skills/test_memento_cards.py → took-upstream: dropped ours-only quiz-batch-add + missing-cards-key tests.
- tests/skills/test_openclaw_migration.py → took-upstream: dropped 6 ours-only test blocks (messaging-cwd-skip, source-candidate-preference, signal-settings, model-config-object, cron-archive, memory-rebrand); added `import json` (auto-merged ours hunks use `json.dumps` 3×).
- tests/skills/test_openclaw_migration_hardening.py → took-upstream: dropped ours-only win32-skipif `test_dry_run_report_includes_rerun_next_step` (ours-only; marker dropped with test).
- tests/skills/test_telephony_skill.py → took-upstream: dropped ours-only twilio-seen-checkpoint test.
- tests/skills/test_unbroker_skill.py → took-upstream: dropped ours-only `test_cdp_launch_command_has_debug_flags` + win32-skipif `test_cdp_find_browser_override` (both ours-only; POSIX-only marker dropped with test).
- tests/skills/test_youtube_quiz.py → took-upstream: dropped ours-only fetch-missing-dependency tests.
- tests/test_atomic_replace_symlinks.py → took-upstream: dropped 4 ours-only test blocks (first-time-create, roundtrip-restores-owner, copy-fallback ×2).
- tests/test_batch_runner_checkpoint.py → took-upstream: dropped ours-only `test_without_lock`.
- tests/test_bitwarden_secrets.py → took-upstream: dropped 3 ours-only test blocks (disk-cache-write, disk-cache-ttl, corrupt-cache-replace); added `import json` (auto-merged ours hunks use `json.dumps` 2×).
- tests/test_hermes_constants.py → took-upstream: dropped ours-only win32-skipif `test_env_unset_returns_platform_default` + `test_npx_fallback_form_accepted`; repaired stray duplicate `class TestGetProcessHermesHome:` line (restored upstream `assert get_process_hermes_home() == home` in `test_env_set_returns_that_path`); removed now-unused `import sys`; kept auto-merged ours hunk `monkeypatch.setattr(hermes_constants.sys, "platform", "linux")` (Windows-compat, rule 2).
- tests/test_hermes_logging.py → took-upstream: dropped 2 ours-only test blocks (session-tag, win32-skipif group-writable rollover — ours-only; marker dropped with test).
- tests/test_hermes_state.py → took-upstream: dropped 3 ours-only test blocks (v16-migration-tags, lone-surrogate-title, delegate-row assert); kept upstream `TestDisplayMetadataPersistence` class + their-only tests.
- tests/test_honcho_client_config.py → took-upstream: dropped 2 ours-only tests (explicit-enabled-true, auto-enable-env-var).
- tests/test_install_sh_browser_install.py → took-upstream: dropped ours-only `@_skip_behavioral_on_windows`-decorated tests (ubuntu/fedora override-retry, operator-override) — decorator itself remains defined if referenced by shared tests.
- tests/test_lint_config.py → took-upstream (file deleted upstream; deletion already staged).
- tests/test_live_system_guard_self_test.py → took-upstream: dropped ours-only `test_systemctl_status_passes_through`; upstream's os.kill/killpg/systemctl tests kept (killpg already self-skips via `skipif(not hasattr(os, "killpg"))`).
- tests/test_model_tools.py → took-upstream: dropped ours-only exception-returns-json + coerced-strict-json-safe tests; upstream `test_registry_exception_emits_terminal_tool_hook` etc. kept.
- tests/test_onepassword_secrets.py → took-upstream: dropped 2 ours-only tests (child-env-allowlist, disk-cache-roundtrip).
- tests/test_packaging_metadata.py → took-upstream: dropped 3 ours-only test blocks (packages-find-include, manifest-skills, locale-catalogs).
- tests/test_plugin_skills.py → took-upstream: dropped 3 ours-only test blocks (invalid-namespace, nonexistent-plugin, single-skill-no-sibling-line).

## Notes for the test phase (dropped CN/ours-only coverage — re-evaluate if still needed)
1. **P-039 (test_provider_parity.py)**: ours kept — the ONLY documented-CN-patch conflict in this batch.
2. **Mass-dropped ours-only tests** (rule 5 default): the fork had 1.5–2× more tests than upstream in most of these shared files (e.g. test_run_agent.py 434→224 kept, test_hermes_state.py 381→156 kept, test_kanban_dashboard_plugin.py 112→20 kept, test_unbroker_skill.py 97→30 kept). These were fork coverage additions for shared features, not documented P-NNN patches; all were dropped with the upstream base taken. If any of them covered behavior the merged code still has and upstream does NOT test, the test phase should re-add them.
3. **win32 skipif markers**: every skipif-decorated test in ours (test_xai_provider, test_sidecar_lifecycle, test_spectrum_patch, test_openclaw_migration_hardening, test_unbroker_skill, test_hermes_constants, test_hermes_logging) was an OURS-ONLY test — markers dropped WITH the tests. No upstream-kept test required a new win32 marker (upstream's own suite has none in these files and the fork's HEAD also ran them unmarked on Windows, e.g. test_live_system_guard_self_test.py, test_bitwarden_secrets.py /bin/sh stubs are inert byte payloads).
4. **Import repairs** (auto-merged ours hunks vs upstream import blocks): added `import orjson`/`import json` to 7 files (openviking, inbound, hindsight, nemo_relay, openclaw_migration, bitwarden_secrets, tool_call_guardrail_runtime) and `STEER_MARKER_OPEN`/`format_steer_marker` to test_steer.py — the fork had converted most `json.`→`orjson.` calls but left some `json.` call sites, and upstream's import block only carried one of the two.
5. **test_compression_boundary_hook.py**: kept fork's whole-file `skipif(win32)` — upstream's 3 new tests also skip on Windows as a consequence; revisit if those tests actually pass on Windows.
6. **test_steer.py**: dual-mechanism merge — if the agent-side merge of run_agent.py/agent_init.py drops either the reminder-registry steer or the `_pending_steer` path, the corresponding kept tests will fail; re-evaluate then.
7. `tests/test_install_sh_browser_install.py`: `_skip_behavioral_on_windows` decorator definition kept (still referenced by remaining tests if any) — verified file compiles.

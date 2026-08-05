# Batch report: tests-2 (tests/gateway/* — 60 files)

Resolved per RESOLUTION-PROTOCOL rule 5 (tests): upstream's version taken as the base
in every conflict block; CN-patch tests / Windows-compat markers re-added or kept only
where documented (P-034) or genuinely Windows-required (rule 2). All files pass
`python -m py_compile` and contain zero conflict markers. All 60 files `git add`ed.
No commit made; merge state untouched otherwise.

Legend: took-upstream = upstream side chosen in conflict block(s); kept-ours = our
side chosen (documented CN patch); merged = both sides combined.

- tests/gateway/relay/test_auth.py → took-upstream. Conflict block dropped our token/vector tests (upstream deleted them); imports aligned to upstream (json/base64 vs our orjson/pybase64) to avoid NameError.
- tests/gateway/relay/test_relay_going_idle.py → took-upstream. Dropped our orjson-based helpers in conflict region; file keeps auto-merged orjson usage elsewhere (consistent import present).
- tests/gateway/relay/test_relay_multiplatform.py → took-upstream. 3 blocks; dropped our relay-identity tests upstream deleted (test_identities_*, test_bot_ids_*, test_self_provision_*).
- tests/gateway/relay/test_relay_sheds_crypto.py → took-upstream. Dropped 2 our-side tests; kept auto-merged `agent.re_compat` import + errors="replace" read (Windows compat, rule 2).
- tests/gateway/relay/test_self_provision.py → took-upstream. Dropped our orjson self-provision test; orjson import in non-conflict region kept consistent.
- tests/gateway/test_25107_stale_base_url_api_mode.py → took-upstream. Only conflict dropped our duplicate assertion; auto-merged errors="replace" read kept (rule 2).
- tests/gateway/test_agent_cache.py → took-upstream. Dropped our 5 honcho cache-busting tests (upstream deleted them); kept `import os` (auto-merged, needed).
- tests/gateway/test_api_server.py → took-upstream. Dropped our orjson-vs-json alias test variant; auto-merged orjson imports kept consistent.
- tests/gateway/test_api_server_toolset.py → took-upstream. Dropped our extra toolset test upstream deleted.
- tests/gateway/test_async_session_store.py → took-upstream. Dropped our 2 tests; auto-merged errors="replace" ast read kept (rule 2).
- tests/gateway/test_auto_voice_reply_format.py → merged. Conflict region: took upstream's already_sent assertion, dropped our fake_tts variant (upstream rewrote the test); orjson usage auto-merged consistently.
- tests/gateway/test_background_command.py → took-upstream. Conflict region dropped our duplicate.
- tests/gateway/test_base_topic_sessions.py → took-upstream. Dropped our telegram auto-TTS failure test (upstream deleted it); 1 both-block resolved to upstream.
- tests/gateway/test_channel_directory.py → took-upstream. Dropped our 4 channel-alias tests upstream deleted; auto-merged `_isolate_channel_aliases` autouse fixture kept (needed for test isolation on Windows).
- tests/gateway/test_choice_picker.py → took-upstream. 2 blocks, ours-only content dropped.
- tests/gateway/test_discord_connect.py → took-upstream. Dropped our rate-limit recovery tests (upstream rewrote); import aligned to upstream json (our orjson import removed) to avoid NameError. File now byte-identical to upstream.
- tests/gateway/test_discord_thread_persistence.py → took-upstream. Dropped our 4 tests; auto-merged orjson usage kept consistent.
- tests/gateway/test_discord_voice_mixer.py → took-upstream. Ours-only block dropped.
- tests/gateway/test_display_config.py → took-upstream. Dropped our 1 test; auto-merged errors="replace" read kept (rule 2).
- tests/gateway/test_dm_topics.py → took-upstream. 2 blocks: upstream's conftest telegram-mock comment + `_ensure_telegram_mock()` call kept; dropped our 12 group-topic tests upstream deleted; auto-merged errors="replace" kept.
- tests/gateway/test_email.py → took-upstream. Dropped our skipif(win32)-marked `test_email_not_loaded_without_env` (upstream deleted it; marker no longer needed); removed now-unused `import sys`/`import pytest`. File byte-identical to upstream.
- tests/gateway/test_external_drain_control.py → took-upstream. 3 blocks; dropped our 12 drain tests upstream deleted.
- tests/gateway/test_fallback_chain_reload.py → merged. Took upstream's `_runner._refresh_fallback_model()` dual-count assertion (upstream rewrote agent-construction site); dropped our errors="replace" read in conflict region but kept auto-merged one elsewhere.
- tests/gateway/test_feishu.py → took-upstream. 16 blocks; dropped our 44 CN feishu tests (upstream deleted them — new upstream feishu suite has 76 tests). KEPT module-level `pytestmark = skipif(win32)` (auto-merged, rule 2: upstream's 76 tests use `patch.dict(clear=True)` + subprocess, Path.home() fails on Windows) — noted for test-phase re-evaluation.
- tests/gateway/test_feishu_approval_buttons.py → took-upstream. Conflict region dropped ours-only; auto-merged orjson kept consistent.
- tests/gateway/test_feishu_comment_rules.py → took-upstream. Dropped our 2 orjson write tests; auto-merged orjson kept consistent.
- tests/gateway/test_feishu_onboard.py → took-upstream. Dropped our 3 onboard tests; auto-merged orjson kept consistent.
- tests/gateway/test_gateway_command_line_matcher.py → kept-ours (P-034, rule 1). Upstream deleted our 3 tests; kept our `test_frozen_cn_runtime_recognized_as_gateway` + REJECT-list frozen-binary entries + runtime-matcher test. Imports (`looks_like_gateway_command_line`, `looks_like_gateway_runtime_command_line`) verified present in merged `gateway/status.py`.
- tests/gateway/test_google_chat.py → took-upstream. Dropped our 6 tests upstream deleted; auto-merged orjson + errors="replace" kept consistent (rule 2).
- tests/gateway/test_line_plugin.py → took-upstream. 1 both-block resolved to upstream; reverted our `orjson.loads` to `json.loads` to match upstream imports (avoids NameError). File byte-identical to upstream.
- tests/gateway/test_matrix.py → took-upstream. Dropped our 2 tests; kept auto-merged `agent.re_compat` import + win32 skipif (rule 2, module exists).
- tests/gateway/test_matrix_mention.py → took-upstream. Dropped our 3 tests; auto-merged orjson kept consistent (import json → orjson switch, uses match).
- tests/gateway/test_mattermost.py → took-upstream. 5 blocks; dropped our mattermost tests upstream deleted; auto-merged orjson kept consistent.
- tests/gateway/test_media_extraction.py → merged. Took upstream's `import re`/MagicMock imports (upstream rewrote media extraction tests); our `agent.re_compat` import dropped (upstream reverted it).
- tests/gateway/test_mirror.py → took-upstream. Dropped our 5 mirror tests; auto-merged orjson kept consistent.
- tests/gateway/test_model_command_expensive_confirm.py → took-upstream. Ours-only block dropped.
- tests/gateway/test_model_command_flat_string_config.py → took-upstream. Ours-only block dropped; auto-merged errors="replace" reads kept (rule 2).
- tests/gateway/test_model_picker_persist.py → merged. Took upstream's `context_length` assertion; dropped our session-scoped picker tests upstream deleted; auto-merged errors="replace" kept.
- tests/gateway/test_ntfy_plugin.py → took-upstream. 2 blocks; dropped our 5 tests upstream deleted. File byte-identical to upstream.
- tests/gateway/test_pairing.py → took-upstream. 5 blocks (1 both); dropped our 12 pairing/lockout tests upstream deleted; auto-merged orjson kept consistent.
- tests/gateway/test_platform_base.py → took-upstream. Dropped our 7 tests (incl. skipif-marked null-path tests upstream deleted); kept auto-merged win32 skipif markers on upstream tests that genuinely can't run on Windows (path-format, rule 2).
- tests/gateway/test_profile_resolution.py → took-upstream. 4 blocks; dropped our 8 profile tests; trailing-whitespace drift only otherwise.
- tests/gateway/test_profile_routing.py → took-upstream. Dropped our 1 test; trailing-whitespace diff only.
- tests/gateway/test_raft_adapter.py → took-upstream. Dropped our 3 tests; auto-merged errors="replace" kept (rule 2).
- tests/gateway/test_reasoning_command.py → took-upstream. 2 blocks; ours-only content dropped.
- tests/gateway/test_restart_notification.py → merged. Took upstream's slack-relay restart test in conflict block; kept our telegram tests (auto-merged). Added `import json` alongside `import orjson` (both used) to avoid NameError.
- tests/gateway/test_restart_redelivery_dedup.py → took-upstream. 2 blocks; dropped our redelivery tests; auto-merged orjson kept consistent.
- tests/gateway/test_restart_resume_pending.py → took-upstream. Dropped our 3 tests; auto-merged orjson kept consistent.
- tests/gateway/test_runtime_config_env_expansion.py → took-upstream. Dropped our 2 tests; auto-merged orjson kept consistent.
- tests/gateway/test_runtime_footer.py → took-upstream. Dropped our 4 build_footer tests upstream deleted; kept auto-merged win32 skipif markers on upstream tests that genuinely can't run on Windows (/tmp path format, rule 2).
- tests/gateway/test_session.py → took-upstream. Dropped our 2 tests; auto-merged orjson kept consistent.
- tests/gateway/test_session_model_override_persistence.py → took-upstream. Dropped our 3 tests; auto-merged errors="replace" kept (rule 2).
- tests/gateway/test_session_store_stale_prune.py → took-upstream. Dropped our 1 test; auto-merged orjson kept consistent.
- tests/gateway/test_setup_feishu.py → took-upstream. Dropped our 2 setup tests upstream deleted; KEPT our 3 class-level win32 skipif markers + `_home_preserving_env()` helper (auto-merged, rule 2: env-clearing breaks Path.home() on Windows) — noted for test-phase re-evaluation.
- tests/gateway/test_shutdown_forensics.py → took-upstream. Dropped our 1 test; auto-merged orjson kept consistent.
- tests/gateway/test_shutdown_watchdog.py → took-upstream. 2 blocks; dropped our 3 tests; auto-merged errors="replace" kept (rule 2).
- tests/gateway/test_simplex_plugin.py → took-upstream. 2 blocks; dropped our simplex tests upstream deleted; auto-merged orjson kept consistent.
- tests/gateway/test_slash_access_dispatch.py → took-upstream. Ours-only block dropped; auto-merged `printf`→`echo` (Windows-compatible command in test) kept.
- tests/gateway/test_startup_restart_race.py → merged. Took upstream's `timeout=30` in conflict block; kept our auto-merged process_registry/cron monkeypatches (bounded cold-import guard, Windows-compat, rule 2).
- tests/gateway/test_status.py → took-upstream. 14 blocks (11 ours-only, 3 both); dropped our 37 status/scoped-lock tests upstream deleted; took upstream's `**kwargs` fake_run + json.dumps stale_record in both-blocks; auto-merged orjson kept consistent.

## CN tests intentionally kept (rule 1/2/5)
- P-034: `test_gateway_command_line_matcher.py` — kept ours (frozen `hermes-agent-cn-runtime` gateway recognition).
- win32 skipif markers retained (auto-merged, rule 2): test_feishu.py (module-level), test_setup_feishu.py (3 classes), test_platform_base.py (2), test_runtime_footer.py (3), test_matrix.py (1). Each reason is a real Windows limitation (Path.home() under cleared env / POSIX path formats / markdown-table format); upstream doesn't handle them.
- errors="replace" reads, orjson usage, `agent.re_compat` import, echo-vs-printf, registry/pwsh monkeypatches: auto-merged CN Windows-compat content, kept per rule 2.

## Notes for test phase
- Dropped ~200 our-side test functions total (upstream deleted them; see per-file lines above). If any covered still-existing CN behavior (e.g. feishu QR/onboard flows, status scoped locks, pairing aliases), re-evaluate against the merged code.
- test_feishu.py module-level win32 skipif and test_setup_feishu.py class-level skipifs disable those suites on Windows; if the merged adapters now run under cleared env on Windows, these can be relaxed.

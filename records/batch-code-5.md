# Batch code-5 — conflict resolution report

Batch: `batches/code-5.txt` (30 files, production code: tools/*, trajectory_compressor.py, tui_gateway/*)
All files resolved: 0 conflict markers remain; every `.py` passes `python -m py_compile`. All 30 files `git add`ed. NOT committed.

## Per-file decisions

- tools/homeassistant_tool.py → merged — kept `from agent.re_compat import re` (re.compile used), dropped unused `import os`; took upstream's `tool_error()` for the blocked-domain error (matches file-wide pattern).
- tools/lazy_deps.py → took-upstream — 4 pure quote-style conflicts (`encoding="utf-8", errors="replace"` already on both sides); took upstream's single-quote form.
- tools/managed_tool_gateway.py → merged — kept orjson.loads (orjson imported) + upstream's explicit `encoding="utf-8"` on read_text.
- tools/mcp_tool.py → took-upstream — 17 markers; all were orjson.dumps-error vs upstream `tool_error(...)` (tool_error imported at line 119); took upstream's tool_error form (incl. `needs_reauth=`/`server=` kwargs, `_reset_server_error` helper, and the EmbeddedResource `continue` fix). Kept `from agent.re_compat import re` + `import random`. P-014's mcp warning behavior untouched (common code).
- tools/memory_tool.py → took-upstream — 3 markers: `_read_raw_checked` strict-decode tuple contract (our errors="replace" version violated its own docstring and returned `[]` from a Tuple fn); `_detect_external_drift` upstream deleted the re-read (raw now comes from caller, matches docstring); `_write_file` uses upstream's `atomic_write_text` (already imported).
- tools/osv_check.py → merged — kept `from agent.re_compat import re` (CN shim) + added upstream's `import threading`/`import time` (both used).
- tools/patch_parser.py → merged — kept re_compat + added `import inspect` (used at 2 sites).
- tools/process_registry.py → merged — kept OUR Windows/PowerShell spawner dispatch (`_spawn_windows_powershell_local`/`_spawn_posix_local`, P-042) and taskkill locale-style decode (`errors="replace"` only, P-051 group-2); took upstream's `safe_command` (`_rewrite_bg`) in PTY branch; imports merged (`codecs` + `orjson`, dropped unused `json`).
- tools/read_terminal_tool.py → kept-ours — orjson only (file uses orjson.dumps; `os` was unused).
- tools/registry.py → merged — P-045 lazy tool index (`build_tool_index`/`load_or_build_tool_index`/fingerprint cache) kept AND upstream's `_discovery_cache_path`/`_load_discovery_cache`/`_save_discovery_cache` kept (both are referenced by common code); fixed the mis-aligned `try`/`except` seam left by concatenation; took upstream's `tool_error()` for the two error returns.
- tools/send_message_tool.py → merged — took upstream's `tool_error()` (x3) and upstream's `_resolve_slack_user_target` path (our `_open_slack_dm` no longer exists anywhere → would NameError; upstream's helper IS defined at line 1555), adapted `json.dumps`→`orjson.dumps().decode('utf-8')`; kept re_compat.
- tools/session_search_tool.py → merged — upstream's `_annotate_rebuild_status`/`_session_link`/`link_profile` params taken (all helpers exist in file), adapted `json.dumps`→orjson; orjson.loads kept.
- tools/skill_manager_tool.py → merged — took upstream's `atomic_write_text` (our `_atomic_write_text` is undefined → NameError; utils.atomic_write_text imported) and upstream's `_maybe_debounced_sync_push` hook (helper defined at 1483); adapted `json.dumps`→orjson; kept re_compat; dropped unused `import os`.
- tools/skill_usage.py → merged — kept orjson.loads + upstream's errors="replace" rationale comment (same behavior, orjson codec).
- tools/skills_hub.py → merged — took upstream's deletion of `ClaudeMarketplaceSource` class (no external references; upstream refactored it out); kept orjson codec everywhere (file imports both json+orjson; orjson is fork convention), added explicit `encoding="utf-8"` on write_text calls (P-051 hardening).
- tools/skills_sync.py → merged — orjson codec; ALSO fixed a latent `json` NameError upstream introduced at the second lock read (upstream's new `_hub_installed_install_paths` used `json.loads` but our side's `import orjson` replaced `import json`) → converted to orjson.
- tools/skills_tool.py → merged — kept orjson + upstream's `_source_path` key; dropped `ensure_ascii=False` (orjson kwarg would TypeError; orjson emits UTF-8 by default).
- tools/terminal_tool.py → merged — P-049 rtk schema (`token_kill`/`max_lines`) kept (handler `_handle_terminal` passes them); `pwsh_warnings`/`bash_fix_warnings` passthrough kept (P-016/019/037) + upstream's `failure_hint`; took upstream's `cron.lifecycle_guard` import/API (renamed from `hermes_cli.cron._contains_gateway_lifecycle_command`) + launchctl-submit check; took upstream's `tool_error()`; deduped duplicate `import json`; kept re_compat + shlex/stat.
- tools/tirith_security.py → took-upstream — 2 pure quote-style conflicts; both sides already had `encoding='utf-8', errors='replace'` (P-051).
- tools/tool_search.py → merged — took upstream's `tool_error()` (x3) + upstream's `available_sources`/`hint` on zero hits (helper `_available_source_summary` exists); adapted `json.dumps`→orjson; kept orjson closing brace on the deferrable-tool return.
- tools/transcription_tools.py → merged — took upstream's secret-scrubbing (`hermes_subprocess_env(inherit_credentials=False)` env) AND kept our `use_shell` branch + `shlex.split(command, posix=os.name == "posix")` (P-048 py3.14 shlex fix) + `env=child_env` on both; took upstream's `delegated_child_subprocess_env` env kwarg + lossy-decode comment (P-051).
- tools/tts_tool.py → merged — kept our `creationflags=windows_hide_flags()` (CN console-hide, P-038) on the ffmpeg subprocess; took upstream's `delegated_child_subprocess_env` env + comment, single-quote style, and "Run `hermes setup`" message (orjson closing kept); kept re_compat, dropped unused `import platform`.
- tools/voice_mode.py → merged — took upstream's Termux multi-probe loop + WSL PowerShell-TTS fallback branch (`_wsl_powershell_tts_available`); KEPT our `creationflags=windows_hide_flags()` on both subprocess sites; kept re_compat + shlex.
- tools/xai_http.py → merged — orjson + upstream's `encoding="utf-8"`.
- trajectory_compressor.py → merged — orjson.JSONDecodeError (file imports only orjson) + upstream's lazy `%s` logging style.
- tui_gateway/entry.py → took-upstream — `_install_signal("SIGHUP", ...)` helper (upstream's; handles Windows absence + main-thread legality; our raw `signal.signal` was the old shape).
- tui_gateway/host_supervisor.py → took-upstream — restored upstream's `encoding="utf-8"` + lossy-decode comment (#52649) that our fork had dropped; aligns with P-051.
- tui_gateway/slash_worker.py → merged — orjson (used) + logging (used); dropped upstream's `import json`.
- tui_gateway/ws.py → merged — orjson in write_async (json not imported in file), took upstream's `_safe_send_many` per-line closed-check + latch comment; dropped our unused `_safe_send` method.
- tui_gateway/server.py → merged (biggest) — upstream split ~130 inline RPC handlers into `tui_gateway/methods_*.py` (method_ctx.py mechanism; server.py tail registers them). Resolution:
  - Took upstream's side for the moved RPC blocks (pet.*, subscription/billing/session.*, spawn_tree.*/prompt.submit, image.attach_bytes/pdf.attach, cli.exec/command.resolve, command.dispatch, config.show + tools/toolsets/agents/cron/learning/skills/plugins/shell.exec) — each verified present in the new modules (methods_session/tools/prompt/complete).
  - KEPT ours inline: `provider.probe` + `provider.models` RPCs (P-011/P-036 — CN-only, NO module equivalent) with `api_mode` (P-046) + `_build_probe_url_candidates`/`_build_anthropic_probe_url_candidates`/`_parse_probe_api_mode`/`_fetch_provider_model_ids` helpers; `model.options` (slug_filter P-011) kept inline AND slug_filter ported into `methods_complete.py`'s model.options (the module handler wins registration via `_m.register` — verified install() overwrites `server._methods[name]`).
  - P-041: turn-watchdog (`_turn_watchdog_seconds`/`_start_turn_watchdog` + `watchdog_cancel` wiring + `steer_followup` P-023) merged with upstream's auto-continue (`_fail_inflight_turn`, turn_marker, `_maybe_schedule_auto_continue`, `_enqueue_prompt` with image_paths — required by common caller at line 7754).
  - Imports merged: kept `from tui_gateway import cli_delegation, git_probe` (P-047) + upstream's `describe_skill_invocation`, `INTERRUPT_WAITING_FOR_MODEL_PREFIX`, `turn_marker` imports.
  - Dropped the orphaned complete.slash tail fragment (upstream's complete.slash in methods_complete.py is a superset incl. skill ranking).
  - NOTE: our inline complete.slash/command.dispatch adaptations (if any CN-specific tweaks beyond what upstream's modules carry) were superseded by the module versions — test phase should re-verify `complete.slash`/`command.dispatch`/`cron.manage`/`shell.exec` module implementations against CN expectations (Windows/PowerShell).

## Files touched beyond the batch (needed to preserve CN patches)
- tui_gateway/methods_complete.py → added slug_filter support to `model.options` (P-011) — the module handler is the one that wins registration after upstream's split.

## Notes for the merge record
- Fork convention kept: `from agent.re_compat import re` everywhere `re` is used (CN shim; upstream lacks agent/re_compat.py); orjson as JSON codec (upstream `json.dumps/loads` adapted wherever json wasn't imported); explicit `encoding="utf-8"`/`errors="replace"` on subprocess pipes (P-051); `creationflags=windows_hide_flags()` retained at CN Windows spawn sites even where upstream dropped them.
- Upstream API renames adopted: `cron.lifecycle_guard.contains_gateway_lifecycle_command_or_referenced_script`, `atomic_write_text`, `_resolve_slack_user_target`, `_install_signal`, `_apply_managed`/`read_user_config_raw` profile-config pipeline, methods_* module split.
- No CN-only functionality knowingly dropped: provider.probe/provider.models RPCs, slug_filter, turn watchdog, pwsh/bash_fix warnings, rtk token_kill/max_lines, PowerShell spawner dispatch, WSL PowerShell TTS fallback all preserved.

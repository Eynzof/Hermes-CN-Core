# Batch code-2 merge report (34 files + 2 dependency modules)

Resolved per `records/RESOLUTION-PROTOCOL.md` + `FORK_NOTES.md`. All files py_compile-clean, 0 conflict markers, `git add`ed. No commit made.

## Per-file decisions

- cli.py → took-upstream (7/7 blocks). P-027's intent (never write package-tree cli-config.yaml) is fully covered by upstream's new always-user-config policy (its comment documents the same wake-word bug); block5 keeps upstream build_subprocess_env()+creationflags (env scrub + no console flash preserved); blocks6-7 upstream voice _activity_hold (won't count agent-mid-turn silence).
- cron/jobs.py → merged. Upstream's new `_atomic_write_counter` value logic kept, with our `errors="replace"` write hardening retained (P-021 family encoding pattern).
- cron/lifecycle_guard.py → merged. Upstream 444-line expansion (launchctl submit detection, script-reference scanning) kept; our fork-wide `from agent.re_compat import re` shim kept (upstream's stdlib `import re` replaced by it); added upstream's `os/shlex/stat` imports.
- cron/scheduler.py → merged. Config read refactored to upstream `read_user_config_raw()` (harden `errors="replace"` lives in the shared function; noted for config.py).
- gateway/dead_targets.py → took-upstream (orjson→stdlib json; not a hot path).
- gateway/platforms/api_server.py → took-upstream (`_sse_frame` helper; ours' inline SSE frame was superseded).
- gateway/platforms/base.py → took-upstream (quote style only).
- gateway/platforms/bluebubbles.py → took-upstream (upstream `compile_mention_patterns` helper in helpers.py is byte-equivalent to our inline version incl. JSON-list/comma parsing).
- gateway/platforms/webhook.py → took-upstream (quote style only).
- gateway/platforms/yuanbao.py → took-upstream (read_user_config_raw write-back round-trip).
- gateway/relay/__init__.py → took-upstream (adds display_name handling; orjson→json).
- gateway/relay/ws_transport.py → took-upstream (upstream multi-platform descriptor accumulation; orjson→json — same semantics).
- gateway/run.py → merged (32 blocks). Took upstream TurnRunner/TurnContext extraction (bodies byte-identical to old closures; verified live-status/log/verbose progress preserved) + `_load_gateway_runtime_config`/read_user_config_raw refactors + json swaps + `_primary_message_handler` + streaming-TTS finalisation; MERGED block16 (upstream `build_subprocess_env()` AND kept our `_subprocess_kwargs` + `windows_hide_flags()` — merged call still expands `**_subprocess_kwargs`) and block32 (P-021 `_validate_cron_startup()` gate kept, upstream multiplex-profile cron + can_dispatch kept); imports merged (`import queue` + `from agent.re_compat import re`; `request_hard_interrupt` + `windows_hide_flags` both kept).
- gateway/shutdown_forensics.py → took-upstream (quote style only).
- gateway/slash_commands.py → merged. 6× inline `open()+yaml` read refactored to upstream `read_user_config_raw()`; rollback handler uses upstream `_checkpoint_agent_kwargs(_load_gateway_config())`; orjson→json for pending-update file. (P-028 models-snapshot path in slash commands not touched by this file's conflicts.)
- gateway/status.py → merged. Kept ours `_get_gateway_runtime_dir()` (HERMES_GATEWAY_RUNTIME_DIR desktop override) AND `set_gateway_conflict`/`classify_port_conflict` (referenced by hermes_cli/gateway.py + feishu adapter) AND the GBK taskkill `errors="replace"` comment; added upstream `_canonical_hermes_home`/`_same_hermes_home` + `GatewayLiveness`/`resolve_gateway_liveness` (referenced by web_server/kanban).
- gateway/stream_consumer.py → merged (kept `from agent.re_compat import re` + added upstream `import threading`; body uses both).
- hermes_cli/_subprocess_compat.py → took-upstream (our `run`/`Popen` wrappers had zero live callers; upstream `noninteractive_git_env`/`bounded_git_probe` are used by coding_context/cli/mcp_catalog/plugins_cmd/profile_distribution/web_git).
- hermes_cli/auth.py → took-upstream (json + upstream fail-loud OSError guard on auth-store read).
- hermes_cli/backup.py → took-upstream (json.dump + `staging_dir` rename; matches post-conflict callers).
- hermes_cli/banner.py → took-upstream (9 blocks: formatting; #52649 git-UTF-8 comment kept; our PyPI version-check helpers had zero callers — dead code).
- hermes_cli/browser_connect.py → took-upstream (posixpath.join is equivalent to our /mnt/ backslash handling, cleaner).
- hermes_cli/claw.py → merged (blocks 2,3 kept ours: `ps_with_utf8()` PowerShell UTF-8 hardening + `**_win_kwargs` P-019/P-038; blocks 1,4 upstream quote style).
- hermes_cli/clipboard.py → merged (blocks 3,4 kept ours: `ps_with_utf8()` + `**_subprocess_kwargs`; blocks 1,2,5-8 upstream quote style).
- hermes_cli/codex_models.py → took-upstream (base64/json imports replace orjson).
- hermes_cli/commands.py → merged (kept our `swarm` CommandDef — tools/agent_swarm.py is a live CN tool — plus upstream's `busy_policy="dispatch"`; block2 upstream _SLACK_VIA_HERMES_ONLY policy superset).
- hermes_cli/config.py → merged (17 blocks). Took upstream relocation of DEFAULT_CONFIG/OPTIONAL_ENV_VARS to config_defaults.py + table-driven config_migrations + `${env:VAR}` expansion + v12 support floor; MERGED block12 (kept our `_ENV_REF_RE`/`_tree_has_env_template`/`_default_has_env_templates` — still called at load_config cache paths — plus upstream `_env_expand_match`/`_env_ref_var_name`), block13 (kept our `${` fast-path + upstream expander), block14 (kept our .env concatenation/suffix-collision hardening), block16 (kept our compression token-cap diagnostics), block15 upstream (vercel_sandbox+ssh superset); block17 upstream (clear YAML parse error). NOTE: CN content ported into `hermes_cli/config_defaults.py` (swarm DEFAULT_CONFIG key + 9 CN OPTIONAL_ENV_VARS keys: ARK_API_KEY/ARK_BASE_URL/COMPSHARE/QIANFAN/HUNYUAN/SILICONFLOW/MODELSCOPE/AI302 = P-006, LONGCAT = P-010) and `hermes_cli/config_migrations.py` (new `_migrate_to_28` CN Desktop model-catalog mirror step, P-028) — both git add'ed. Block1 list: upstream (HERMES_TOOL_PROGRESS deliberately removed by upstream's v12 floor policy).
- hermes_cli/container_boot.py → took-upstream (orjson→json, 2 blocks).
- hermes_cli/copilot_auth.py → took-upstream (quote style + upstream retry-with-backoff token exchange).
- hermes_cli/dep_ensure.py → merged (kept ours `import os` + `from platform_utils import is_windows` P-044; merged import block: `refresh_env_from_registry` P-020 + `_find_rtk` P-049 + `get_managed_tools_dir` P-049 + upstream `find_node_executable`).
- hermes_cli/doctor.py → merged (block1: upstream `check_certificates(should_fix=..., issues=...)` signature AND our `_check_windows_defender_hint()` P-044 call kept; blocks 2-10 upstream: latin-1 .env fallback, read_user_config_raw/_read_raw_* raw diagnostics, json).
- hermes_cli/dump.py → took-upstream (2 blocks).
- hermes_cli/gateway.py → took-upstream (18 blocks, all quote-style/read_user_config_raw trivia; no CN-only logic lost — P-020/P-050 bits live in non-conflict regions).

## Notes for test phase
- `HERMES_TOOL_PROGRESS` env var is no longer in config.py's known-env list (upstream v12 floor policy) — doctor flags it as ignored; our v3→4 .env migration was retired by the floor.
- `swarm` config block + `_migrate_to_28` were re-added to upstream's new modules (config_defaults.py / config_migrations.py) — verify `hermes config set swarm.max_concurrency` and the model-catalog mirror migration with a v27-era fixture.
- config.py block12 merge left both the old `_expand_env_ref` and new `_env_expand_match` — the fast-path `_expand_env_vars` now uses upstream's expander; the memoised `_default_has_env_templates()` still gates the loader cache (P-045 micro-opts intact).

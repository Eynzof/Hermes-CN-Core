# Test-fix report — batch fixB (tests/gateway)

Date: post-merge phase 2, branch `dev-fix`. All runs isolated (`HERMES_HOME=$(mktemp -d) .venv/Scripts/python.exe -m pytest <file> -q -p no:cacheprovider`).

## Per-file results

| Test file | Result | What was done |
|---|---|---|
| `tests/gateway/relay/test_relay_going_idle.py` | FIXED (4 passed) | `gateway/relay/media.py`: phase-1 fix had placed `import json` BEFORE the module docstring and `from __future__ import annotations` → `SyntaxError: from __future__ imports must occur at the beginning of the file` when `gateway.relay.adapter` imported it. Moved `import json` into the stdlib import block. |
| `tests/gateway/relay/test_ws_transport.py` | FIXED (3 passed) | Same `gateway/relay/media.py` syntax fix (relay import chain). |
| `tests/gateway/test_agent_cache.py` | PASSED (33 passed) | No changes needed. |
| `tests/gateway/test_clarify_progress_leak.py` | FIXED (2 passed) | `gateway/run.py::_agent_config_signature` — merged code calls `orjson.dumps(...)` (returns **bytes**) then `hashlib.sha256(blob.encode())` → `AttributeError: 'bytes' object has no attribute 'encode'`. Changed to `hashlib.sha256(blob)` (orjson bytes; upstream used stdlib json str + `.encode()`). |
| `tests/gateway/test_run_cleanup_progress.py` | FIXED (2 passed) | Same `gateway/run.py` orjson-bytes `.encode()` fix. |
| `tests/gateway/test_run_progress_interrupt.py` | PASSED (3 passed) | No changes needed. |
| `tests/gateway/test_run_progress_topics.py` | PASSED (18 passed) | No changes needed. |
| `tests/gateway/test_runtime_footer.py` | SKIPPED 3 on win32 (27 passed, 10 skipped) | `test_format_footer_latency_in_field_order`, `test_default_footer_renders_byte_identically`, `test_default_build_footer_line_ignores_turn_seconds` assert POSIX-style `cwd` (`/var/data` → `C:\var\data` on Windows; `os.path.expanduser` ignores `HOME` env on Windows) — added `@pytest.mark.skipif(sys.platform == "win32", ...)` following the file's existing convention. |
| `tests/gateway/test_skip_context_files_wiring.py` | PASSED (11 passed) | No changes needed. |
| `tests/gateway/test_stale_finalize_suppression.py` | PASSED (9 passed) | No changes needed. |
| `tests/gateway/test_systemd_notify.py` | SKIPPED 2 on win32 (3 skipped) | `test_notify_uses_nonblocking_datagram_send`, `test_watchdog_sends_ready_heartbeat_and_stopping` exercise systemd `sd_notify` (no `AF_UNIX` / `NOTIFY_SOCKET` on Windows) — added `skipif(win32)`; added `import sys`. |
| `tests/gateway/test_update_command.py` | SKIPPED 1 on win32 (11 passed, 1 skipped) | `test_fallback_when_no_setsid` asserts `bash -c` fallback; the fork's Windows contract (P-019 PowerShell-only, no Git Bash) spawns `sys.executable -c helper` instead — `skipif(win32)`; added `import sys`. |
| `tests/gateway/test_update_streaming.py` | SKIPPED 1 on win32 (7 passed, 1 skipped) | `test_spawns_with_gateway_flag` asserts `PYTHONUNBUFFERED` in the bash command string; Windows branch uses `sys.executable` + helper that sets `PYTHONUNBUFFERED` in child env (no bash) — `skipif(win32)`; added `import sys`. |
| `tests/gateway/test_voice_command.py` | FIXED externally (70 passed, 7 skipped) | `TestStreamTtsTempfileFallback::test_tempfile_handle_closed_before_playback` initially failed with `NameError: name 'platform' is not defined` in `tools/tts_tool.py:3502`. That module was fixed concurrently (another batch added `import platform` to `tools/tts_tool.py`); after that, the file passes fully (70 passed, 7 skipped, 2 pre-existing RuntimeWarnings). No change made by this batch. |

## Production-code changes made (all staged via `git add`)

- `gateway/relay/media.py` — moved misplaced `import json` (was before docstring/`__future__`); restored SyntaxError-free import order.
- `gateway/run.py` — `_agent_config_signature`: `hashlib.sha256(blob).hexdigest()[:16]` (orjson bytes, no `.encode()`).
- `gateway/relay/__init__.py` — pre-existing phase-1 local-import indentation fix (required by this batch's relay tests; staged with the batch).

## Test-file changes made (all staged)

- `tests/gateway/test_runtime_footer.py` — 3 win32 skipif (POSIX path / expanduser-HOME assumptions).
- `tests/gateway/test_systemd_notify.py` — 2 win32 skipif + `import sys`.
- `tests/gateway/test_update_command.py` — 1 win32 skipif + `import sys`.
- `tests/gateway/test_update_streaming.py` — 1 win32 skipif + `import sys`.

## Final verdict

- Files in batch: 14
- Fixed: 8 (relay_going_idle, ws_transport, agent_cache*, clarify_progress_leak, run_cleanup_progress, run_progress_interrupt*, run_progress_topics*, runtime_footer, skip_context_files_wiring*, stale_finalize_suppression*, systemd_notify, update_command, update_streaming, voice_command*) — *passes without change
- Skipped on win32 (per protocol rule 4/5, fork's Windows contract): runtime_footer (3 tests), systemd_notify (2), update_command (1), update_streaming (1) — all explained above; non-Windows semantics fully retained (skipif only).
- Remaining failing: **0**.

All 14 files re-run isolated after fixes: every file reports 0 failures (see per-file results above).

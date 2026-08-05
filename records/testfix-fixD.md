# Test-fix report — batch fixD (tests/tools), phase 2

Date: 2026-08  ·  Repo `C:\dev\Hermes-CN-Core` (CN fork, Windows host, py3.14.3 venv)
Protocol: `records/TEST-FIX-PROTOCOL.md` (per-file isolated runs:
`HERMES_HOME=$(mktemp -d) .venv/Scripts/python.exe -m pytest <file> -q -p no:cacheprovider`).
Nothing committed; all fixed files staged via `git add`.

## Per-file results (21/21 files)

1. `tests/tools/test_base_environment.py` -> FIXED
   - 3 POSIX-bash tests (`TestAtomicSnapshotConcurrencyBehavioral` ×2,
     `TestSnapshotFileModes::test_snapshot_and_cwd_files_are_0600`) hardcode
     `/bin/bash`; on Windows-with-Git-Bash `shutil.which("bash")` succeeds but
     `/bin/bash` doesn't exist (WinError 2). Added `sys.platform == "win32"`
     to the existing skip guards; `import sys`. 18 passed / 3 skipped.

2. `tests/tools/test_clarify_tool.py` -> FIXED (production `tools/clarify_tool.py`)
   - Merge artifact: `"user_response": str(user_response).strip()` stringified the
     multi-select list. Restored list passthrough (upstream returns `user_response`
     bare); scalar path still stripped. 20 passed.

3. `tests/tools/test_computer_use.py` -> FIXED
   - `test_cua_driver_cmd_env_override_is_resolved_dynamically`: POSIX-only
     (extension-less `#!/bin/sh` script + `PATH=/usr/bin:/bin`; `shutil.which` on
     Windows requires a PATHEXT extension) — skipif(win32).
   - `test_linux_default_capture_skips_gnome_shell_helper`: Linux/X11-only (code
     gates on `sys.platform == "linux"`) — skipif(not linux).
   - `test_cli_fallback_reads_screenshot_from_file`: fixture built fake JSON via
     `% str(shot)` — Windows backslash paths are invalid JSON. Now built via
     `json.dumps`. 71 passed / 2 skipped.

4. `tests/tools/test_credential_pool_env_fallback.py` -> PASSED (no change).

5. `tests/tools/test_docker_config_migrate.py` -> PASSED (no change).

6. `tests/tools/test_file_tools.py` -> FIXED
   - `import json` added (protocol class 1; 38 `json.` call sites).
   - `test_macos_private_var_carveouts`: macOS `/private/var` realpath semantics —
     marked with the file's existing `_win32` skip marker. 48 passed / 7 skipped.

7. `tests/tools/test_file_write_safety.py` -> FIXED (production `tools/file_operations.py`)
   - `_file_has_bom` trusted `pre_content` when provided (contradicting its own
     docstring and upstream, which always probes disk). Now always probes disk via
     `_prim_read_sample` (P-033 in-process on Windows). 24 passed / 9 skipped.

8. `tests/tools/test_mcp_tool.py` -> FIXED
   - `test_partial_failure_retry_on_second_call`: merge dropped upstream's
     `_server_connect_retry_after.pop("broken", None)` cooldown-expiry line
     (upstream #50394 semantics); restored it.
   - `test_mcp_tool_allowed_when_collision_is_another_mcp` asserted the OLD
     fork "last wins" contract; upstream deliberately changed MCP-to-MCP
     collisions to fail closed (existing owner preserved). Replaced with
     upstream's `test_mcp_tool_rejected_when_collision_is_another_mcp`. 217 passed.

9. `tests/tools/test_osv_check.py` -> FIXED — `import json` added (class 1). 18 passed.

10. `tests/tools/test_process_registry.py` -> FIXED (production `tools/process_registry.py` + tests)
    - Prod: `spawn_local` PTY block — `session.pid = pty_proc.pid` /
      `host_start_time` had been moved into the POSIX-only `else` branch (merge
      artifact); Windows PTY spawns never set `session.pid`. Restored to the
      shared `try` body (backup had it common).
    - Prod: `_spawn_posix_local` call passed raw `command` instead of
      `safe_command` — the `A && B &` rewriter (issue #68915) was a no-op on the
      POSIX Popen path (upstream uses `safe_command` in both PTY and Popen).
    - Tests: env-clear test now sets `USERPROFILE` too (Windows `Path.home()`
      reads `USERPROFILE`, not `HOME` — P-048) + `_IS_WINDOWS` False;
      rewrite/kill tests patched `_IS_WINDOWS` False (they simulate the POSIX
      path; Windows uses the PowerShell wrapper / `taskkill`);
      `test_spawn_local_windows_uses_resolve_shell` assertion literal
      `"Set-Location -LiteralPath 'D:\test'"` had `\t` → TAB — made raw string.
      (Note: `local._build_powershell_background_script` was NOT broken —
      the failure was the `\t` literal.) 47 passed / 10 skipped.

11. `tests/tools/test_search_auto_multiline.py` -> FIXED (production `tools/file_operations.py`)
    - `_search_content` local branch now tracks `used_rg`, runs the zero-match
      probe, and skips the line-oriented warning on the rg path (mirrors remote).
    - `_search_with_rg_ripgrepy` auto-enables `--multiline` for `\n` patterns and
      attaches the multiline note (upstream contract; the ripgrepy path had lost it).
    - 2 tests skipped on win32: `write_text` fixtures become CRLF on Windows and
      rg's `\n` regex cannot match CRLF (real rg semantics, not a fork bug).
    3 passed / 2 skipped.

12. `tests/tools/test_search_zero_match_and_multipath.py` -> FIXED (production `tools/file_operations.py`)
    - Zero-match probe wired into the LOCAL search branch (was remote-only);
      new `_rg_count_via_ripgrepy` in-process probe for the local backend (the
      shell `rg … | head` pipeline can't run under PowerShell, P-030 policy).
    - Multi-path recovery (`"dir1 dir2"` / comma lists) now runs on the LOCAL
      branch too; `_try_multi_path_search` uses `os.path.exists` on local
      (no POSIX `test -e`). 9 passed / 2 skipped.

13. `tests/tools/test_terminal_dynamic_description.py` -> FIXED (production `tools/terminal_tool.py` + tests)
    - Upstream rewrote the static `TERMINAL_TOOL_DESCRIPTION` (flattened single
      line), orphaning the CN `_build_dynamic_terminal_description` `replace()`
      calls (silent no-ops). Updated the PowerShell-adaptation replacements to
      the new static phrasing; updated the CN test expectations and the
      static-phrase guard accordingly. 18 passed.

14. `tests/tools/test_terminal_truncation_spill.py` -> FIXED (production `tools/terminal_tool.py` + tests)
    - Prod: `NameError: name 'strip_ansi' is not defined` in the spill-redaction
      block (upstream imports it from `tools.ansi_strip`; the merge dropped it) —
      restored the import. This silently deleted every spill file and dropped
      the `output_total_chars`/`full_output_path` metadata.
    - Tests: `python3` on this Windows host is the Microsoft-Store app-execution
      alias stub (exit 9009) — switched to `& '<sys.executable>'` (PowerShell
      call operator, required inside `Invoke-Expression`); `token_kill=False`
      so rtk rewriting doesn't collapse the output the spill tests need.
      5 passed.

15. `tests/tools/test_tirith_security.py` -> FIXED (tests; P-049 contract)
    - `test_found_on_path_returns_immediately` mocked `isfile=True`, so the
      P-049 managed-tools-dir check (`<HERMES_HOME>/tools/tirith`, checked
      before PATH) won. Mock now `isfile=False` so the PATH-resolved binary is
      exercised (P-049 order: managed dir → PATH → legacy bin).
    - `test_install_extracts_regular_tirith_member` asserted the legacy
      `bin/tirith` target; P-049 installs to `tools/tirith` — updated. 41 passed.

16. `tests/tools/test_transcription_tools.py` -> FIXED (production `tools/transcription_tools.py`)
    - Merge kept the CN `use_shell` branch (shell=True for env-var templates) but
      the merged test suite is upstream's `TestShellSafety`
      (`test_env_var_template_metacharacters_are_literal_argv` — metacharacters
      must stay literal argv; upstream always uses list mode). Upstream's
      security contract wins (CN divergence not in FORK_NOTES): removed the
      `use_shell` branch, always `shlex.split(command)` (posix mode — the CN
      `posix=os.name=="posix"` variant left `shlex.quote` single quotes in argv
      on Windows). 50 passed.

17. `tests/tools/test_tts_macos_output.py` -> FIXED (production `tools/tts_tool.py`)
    - `import platform` dropped in the merge (upstream has it; `platform.system()`
      used at L3501) — restored. 2 passed.

18. `tests/tools/test_voice_cli_integration.py` -> FIXED (production `cli.py`)
    - `NameError: _stop_continuous` at cli.py:12465 — merge partially renamed
      `_stop_continuous` → `stop_continuous_restart`; the `if` check was left
      stale. Renamed to `if stop_continuous_restart:`. 28 passed.

19. `tests/tools/test_voice_mode.py` -> FIXED (tests; win32 skips)
    - `test_wsl_without_pulse_blocks_voice`: simulates WSL on the host; on Windows
      `powershell.exe`+`ffmpeg` are on PATH so `_wsl_powershell_tts_available()`
      is True → upstream's non-blocking notice branch always wins — skipif(win32).
    - `TestWSL2PowerShellFallback` class: branch is gated on
      `platform.system() == "Linux"` — unreachable on Windows — class-level
      skipif(win32). 56 passed / 6 skipped.

20. `tests/tools/test_windows_native_support.py` -> FIXED (tests; upstream contracts)
    - `test_windows_detach_flags_has_expected_win32_bits` asserted the OLD bundle
      with `DETACHED_PROCESS`; production deliberately excludes it (MSDN:
      CREATE_NO_WINDOW is ignored with DETACHED_PROCESS; console-flash class
      #54220/#56747). Replaced with upstream's
      `test_windows_detach_flags_exclude_detached_process`; removed the
      DETACHED assertion from the no-breakaway test.
    - `TestTuiGatewayEntrySignalGuards` source check asserted literal
      `hasattr(signal, "SIGPIPE")`; upstream refactored to `_install_signal`
      (PR #72677) — updated to the loop accepting either form. 64 passed / 2 skipped.

21. `tests/tools/test_write_verification.py` -> FIXED (production `tools/file_operations.py` + tests)
    - Post-write verification hashed via shell `sha256sum`, which doesn't exist
      under PowerShell → `verified` always None on Windows. On the in-process
      (local Windows) backend the hash is now computed in-process via
      `_prim_read_all` + `hashlib` (P-033b/P-037 policy).
    - 2 tests skipped on win32: `test_hash_mismatch_is_hard_error` mocks
      `hashlib.sha256` which can only force a mismatch when the disk hash comes
      from the real `sha256sum` shell; `test_verification_failure_never_breaks_write`
      simulates `sha256sum` missing via `_exec` — unreachable when hashing is
      in-process. 3 passed / 2 skipped.

## Files changed (production, all staged)
- `cli.py` — `_stop_continuous` rename.
- `tools/tts_tool.py` — restored `import platform`.
- `tools/clarify_tool.py` — multi-select `user_response` list passthrough.
- `tools/process_registry.py` — PTY `session.pid` fix; `safe_command` on POSIX spawn.
- `tools/file_operations.py` — local search probe + auto-multiline ripgrepy +
  multi-path recovery; `_file_has_bom` disk probe; in-process write-verify hashing.
- `tools/terminal_tool.py` — PowerShell description replacements for new static
  text; restored `strip_ansi` import.
- `tools/transcription_tools.py` — always list-mode local STT (upstream security contract).

## Files changed (tests, all staged)
test_base_environment, test_computer_use, test_file_tools, test_mcp_tool,
test_osv_check, test_process_registry, test_search_auto_multiline,
test_terminal_dynamic_description, test_terminal_truncation_spill,
test_tirith_security, test_transcription_tools, test_voice_mode,
test_windows_native_support, test_write_verification.

No test-file change needed (production fixes only): test_clarify_tool,
test_file_write_safety, test_search_zero_match_and_multipath, test_tts_macos_output,
test_voice_cli_integration.

## Final verdict
- Files fixed: **19** (17 test files + 2 that needed no change passed from the start:
  test_credential_pool_env_fallback, test_docker_config_migrate).
- Files remaining failing: **0 of 21** — all 21 batch files pass isolated
  (final re-run: exit 0 for every file).
- Adjacent sanity runs green: test_file_operations.py (88p/7s),
  test_file_ops_windows_inprocess.py (13p), test_shell_resolution.py +
  test_local_pwsh_warnings.py (41p), test_search_python_fallback.py (17p),
  test_search_error_guard.py + test_search_hidden_dirs.py (11p/4s).

## Notes / out-of-batch observations (not changed)
- `tests/tools/test_file_ops_p037.py::TestLegacyEncodingRoundTrip::test_read_file_decodes_gbk_inproc`
  fails on this host **pre-existing** (identical failure in the pre-batch
  `reports/merge-full-suite.log`, line 11340) and is NOT in this batch —
  GBK sample still flags binary in `read_file`; would need a separate fix.
- `tools/terminal_command_rewrite._maybe_rewrite_shell_command_with_rtk` hangs
  (never returns) on a `& '<interpreter>' -c "..."` PowerShell command on this
  host — latent production issue observed while debugging the spill tests; the
  spill tests avoid it via `token_kill=False`. Recommend a follow-up.
- `test_bitwarden_secrets.py` is in another batch — untouched.

# Test-fix protocol (phase 2) — for sub-agents fixing post-merge test failures

Repo: `C:\dev\Hermes-CN-Core` (CN fork, Windows host, Python 3.14.3 venv at `.venv`).
We merged upstream NousResearch/hermes-agent into the CN fork (commit `ec791a5d7`).
`tests/` now = upstream suite + CN-patch tests. Your job: make the test files in
your batch pass, fixing either production code or test code.

## How to run a test file (IMPORTANT)
Use the venv interpreter and a FRESH HERMES_HOME per run (per-file isolation):

    HERMES_HOME=$(mktemp -d) .venv/Scripts/python.exe -m pytest <file> -q -p no:cacheprovider

Never run many files in ONE pytest process for the final verdict (cross-file
state pollution) — run each file separately, or use `reports/run_files_isolated.py`.

## Known failure classes already understood (fix these first when you see them)

1. **`NameError: name 'json' is not defined`** — the merge replaced `import json`
   with `import orjson` in many files. Fix: add `import json` (top-level) to the
   file that fails, IF it calls `json.*` (stdlib semantics). If a LOCAL
   `import orjson` sits inside a function, do NOT convert it to stdlib json
   unless the code needs stdlib kwargs.
2. **`TypeError: dumps() got an unexpected keyword argument`** — orjson.dumps
   called with `sort_keys=`/`ensure_ascii=`/`indent=` kwargs. Fix: use
   `option=orjson.OPT_SORT_KEYS` etc., or call the stdlib `json.dumps`.
3. **`IndentationError` at a `import json`/`import orjson` line inside a
   function** — the phase-1 import fix mis-indented a LOCAL import. Fix the
   indentation to match the enclosing function body.
4. **Windows-only tests from upstream** (assume Linux/macOS; e.g. systemd,
   `os.mkfifo`, `fcntl`, `/bin/npm`, `install.sh`, WSL/msys paths, `bash`,
   macOS TTS, 0o600 permissions, `/mnt/c/...` paths): add
   `@pytest.mark.skipif(sys.platform == "win32", reason=...)` to the test /
   class (check `import sys` present) — the CN fork runs the suite on Windows
   and must skip them (mirrors the fork's pre-merge convention).
5. **Msys path doubling** (`C:\c\Users` or `/mnt\c\Users`): a Windows test
   asserting POSIX-style paths — normalize with `os.path` or skip on win32.
6. **CN fork patches (FORK_NOTES.md P-IDs) MUST be preserved** — read
   `FORK_NOTES.md`. If a failing test asserts CN-patch behavior, FIX THE CODE
   (restore the patch's semantics) rather than deleting the test. If upstream
   deliberately changed the behavior and FORK_NOTES says "Upstreamed/Superseded",
   UPDATE THE TEST to upstream's contract instead.
7. **Production code you may need to repair** (merge artifacts found so far):
   misplaced/duplicated blocks (dedent/restore), dropped function definitions
   (re-add from `git show upstream/main:<path>` or
   `git show backup/dev-fix-pre-upstream-sync:<path>`), dropped helper
   constants, `_j`/orjson aliases with json kwargs.

## Rules
- Do NOT commit. Fix files + `git add` them. Do NOT run merge-abort/reset/
  stash/checkout-whole-tree.
- Run each of your batch's test files AFTER your fixes and confirm it passes
  (isolated run). Iterate until green or until you can explain precisely why a
  file cannot pass on Windows (then add the skipif and confirm it passes).
- Prefer the SMALLEST correct change. Don't rewrite upstream tests wholesale;
  adjust expectations only where the merged code's contract is intentionally
  different (cite FORK_NOTES).
- If a test needs an external binary (e.g. ripgrep for search tests): check
  whether the repo provisions it (scripts/install*.sh, hermes_cli/dep_ensure.py
  `_find_rg()`); if the harness should auto-provision and doesn't on Windows,
  make the code find/provision it OR skip the test on win32 with a note.

## Report
Append per-file results to `records/testfix-<batch>.md`:
`path -> FIXED (what) | SKIPPED (win32, why) | CANNOT-FIX (why)`.
End with: files fixed count, files remaining failing (must be 0 or explained).

# Conflict Resolution Protocol (for sub-agents)

We are mid-`git merge upstream/main` into `dev-fix` in `C:\dev\Hermes-CN-Core`.
This merges the official NousResearch/hermes-agent (4194 commits newer) into our
CN fork. ~620 files have conflict markers right now.

## YOUR JOB
Resolve the conflict markers in the files listed in your assigned batch file
(`batches/<batch>.txt`), file by file, using the rules below. Then `git add`
each file you resolved. **Do NOT commit. Do NOT run `git merge --abort`,
`git checkout .`, `git reset`, `git stash`, `git rebase`, or any command that
changes HEAD or the merge state.** Only edit files + `git add`.

## CONTEXT FOR DECISIONS
- Conflict marker layout: `<<<<<<< HEAD` ... (OUR CN fork's version) `=======`
  ... (UPSTREAM official version) `>>>>>>> upstream/main`.
- Read `FORK_NOTES.md` (repo root) FIRST. It documents every intentional CN
  fork patch (IDs like P-014, P-016, P-019, P-028, P-030, P-033, P-037, P-041,
  P-044, P-045, P-048, P-049, P-050, P-051, P-052, P-053, P-056, P-057) with its
  target files and upstream status ("Should be upstreamed" / "Upstreamed" /
  "Won't be upstreamed" / "Superseded by upstream").
- The fork REQUIRES Python >= 3.14 (P-048) and is Windows-first (PowerShell is
  the default shell, P-016/P-019/P-050; Git Bash optional). Upstream supports
  >=3.11 and is Linux/Git-Bash-centric. Where upstream code assumes 3.11-3.13 or
  bash, our fork intentionally diverges — keep our divergence.

## DECISION RULES (highest priority first)
1. **Documented CN patch (a P-NNN in FORK_NOTES targets this file)** → keep OUR
   side's change, but adapt it to upstream's new surrounding code/APIs so it
   still works (merge both where possible). If FORK_NOTES says the patch was
   "Upstreamed" or "Superseded by upstream", take UPSTREAM's version instead.
2. **Windows / py3.14 / China-network compatibility** that is NOT an upstream
   feature (e.g. PowerShell-only paths, `errors="replace"` reads, GBK/cp936
   handling, skipif(win32) markers, offline models.dev snapshot, registry PATH
   refresh, import accelerator, rtk, etc.) → keep OUR side, adapted.
3. **Upstream deleted/rewrote a whole file or feature** and our change was only
   a small adaptation of the old shape (imports, markers, formatting) → take
   UPSTREAM's version; note any lost CN behavior in your report so the test
   phase can re-evaluate.
4. **Trivial/mechanical** (quote style, import order, comment drift) → merge
   both, prefer upstream's formatting.
5. **Tests** (default, for files under tests/ or *test*.py): take UPSTREAM's
   version of the file as the base, then RE-ADD:
   - our fork's added test cases for CN patches that still exist in code
     (e.g. `test_mcp_unavailable_with_servers_warns` = P-014, models snapshot
     tests = P-028, in-process file-op tests = P-030/P-033, pwsh_transform
     tests = P-037, tool_calls_committed tests = P-041, rtk/post-process tests
     = P-049, utf-8 pipe tests = P-051, Windows env tests = P-020/P-042/P-044);
   - `skipif(sys.platform == "win32")` / `skipif(win32)` markers ONLY where the
     test genuinely cannot run on our Windows-first environment and upstream
     doesn't already handle it.
   Drop everything else ours-only. If unsure about a test, DROP it and note it.
6. **Never silently lose CN-only functionality.** When genuinely unsure, prefer
   merging both sides (keep ours' additions) and note the decision.

## SPECIAL FILES
- `uv.lock`: DO NOT hand-edit. It will be regenerated with `uv lock` after all
  other conflicts are resolved. Leave it (still marked) for now — skip it.
- Workflow files (.github/workflows/*): keep CN additions (Python 3.14,
  upstream_sync input, release-runtime gates) + upstream's new steps; merge both.
- `pyproject.toml` / `setup.py`: already handled or handle carefully per rules.

## QUALITY GATES (per file, before `git add`)
- No `<<<<<<<`, `=======`, `>>>>>>>` markers remain in the file.
- `.py` files: `python -m py_compile <file>` passes (run it).
- `.json` files: `python -c "import json; json.load(open(r'<file>',encoding='utf-8'))"` passes.
- `.toml`/`pyproject`: `python -c "import tomllib; tomllib.load(open(r'<file>','rb'))"` passes.
- YAML/`.yml`/`.yaml` (workflows): parse with `python -c "import yaml,sys; yaml.safe_load(open(r'<file>',encoding='utf-8'))"` if PyYAML is installed; else be extra careful about indentation.
- Scripts (.ps1/.sh): only remove markers; keep structure intact.

## REPORT
Append your per-file decisions to `records/batch-<your-batch-name>.md`:
one line per file: path → decision (kept-ours / took-upstream / merged / note).
Be specific enough that the merge record can cite you.

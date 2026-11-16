# Maintaining Hermes-CN-Core

This repository is a long-lived downstream fork of
`NousResearch/hermes-agent`. The fork exists so `Hermes-CN-Desktop` can depend on a
stable, signed runtime while we still sync official upstream changes regularly.

## Branch Model

```
origin/main          stable fork product branch
upstream/main        official NousResearch source, read-only
chore/sync-*         temporary upstream sync branches
cn/P-xxx-*           fork-only patch branches
upstream-pr/*        clean branches based on upstream/main for official PRs
runtime-v*           signed runtime release tags for desktop
```

`desktop` must consume runtime releases cut from verified `origin/main`. It should
not depend on a moving branch head or on a user's globally installed upstream
`hermes` package.

## Remote Layout

```
origin    https://github.com/Eynzof/Hermes-CN-Core.git    fetch + push
upstream  https://github.com/NousResearch/hermes-agent   fetch only
```

The local upstream push URL must be blocked:

```bash
git remote set-url --push upstream no_push
git config --unset-all remote.upstream.fetch
git config --add remote.upstream.fetch '+refs/heads/main:refs/remotes/upstream/main'
git config branch.main.remote origin
git config branch.main.merge refs/heads/main
```

## Before You Start（开发前预检）

Hermes CN 的需求与 bug 修复通常**同时横跨 Core 与 [Desktop](https://github.com/Eynzof/Hermes-CN-Desktop) 两个仓库**，**不要直接在 `main` 上改**：

1. **确认主分支已与远端同步**：两仓分别 `git fetch origin`，`git rev-list --left-right --count main...origin/main` 应为 `0  0`；落后先快进。Core 是 fork——**永远不要把 `upstream/main` 直接并进 `main`**，上游同步走 `python scripts/git_merge.py`。
2. **每个仓库开独立分支 + worktree**，两仓改动互不干扰：
   ```bash
   git -C <repo> fetch origin
   git -C <repo> worktree add ../wt/<repo>-<topic> -b <branch> origin/main
   ```
   Core 的 fork 补丁用 `cn/P-xxx-*`（登记进 `FORK_NOTES.md`），上游 PR 用 `upstream-pr/*`，杂项用 `docs/` `chore/`。
3. 不要在同一工作目录里来回 `git checkout` 切分支——双仓并行极易串味。

**收尾（每个仓库都要走完）**：改完 → 跑校验（Core：`scripts/run_tests.sh` + `ruff check .`；Desktop：`pnpm typecheck && pnpm test:unit && cargo check`）→ commit → push → 开 PR → 盯 GitHub Actions 全绿。

## This fork ships code, not documentation

The published docs site, the translated guides, the contributor-attribution data and
the recorded performance reports are **deleted on purpose** — see
[`scripts/upstream-deletions.txt`](./scripts/upstream-deletions.txt) and the "Deleted
upstream trees" section of [`AGENTS.md`](./AGENTS.md). What survives under `website/`
is runtime data only (`static/api/model-catalog.json`, `static/img/catalog/*.jpg`, the
icon set and `static/oauth/client-metadata.json`).

Two consequences for maintenance:

1. A sync must never restore a deleted path, not even a brand-new file inside a
   deleted directory. `scripts/git_merge.py` enforces this; `git merge upstream/main`
   does not.
2. A patch that adds a *core* code file is fine, but a patch that adds documentation
   belongs in a commit message, an `AGENTS.md` note or `FORK_NOTES.md`, not in a new
   docs tree.

## Routine: Upstream Sync

`upstream-watch.yml` opens or updates an issue when official upstream is ahead. When
that fires:

```bash
git switch main && git pull --ff-only origin main
python scripts/git_merge.py --branch chore/sync-upstream-$(date +%Y%m%d)
```

`scripts/git_merge.py`:

1. Refuses to run with a dirty working tree (`--allow-dirty` overrides).
2. Clones `NousResearch/hermes-agent` into `.cache/upstream/hermes-agent` on first use
   and `git fetch`es it afterwards (`--no-fetch` reuses the cache offline).
3. Reads the merge base from `.cache/upstream/merge-state.json` (the upstream commit
   the previous sync landed), or computes `git merge-base` on the first run.
4. Merges that upstream range **file by file**, three-way on content:
   both sides edited a file → `git merge-file`; only upstream did → take upstream;
   upstream deleted it → delete the local copy if it is untouched.
5. Skips every path listed in `scripts/upstream-deletions.txt`, plus — as a safety
   net — every path that existed in the base and is missing here. Those stay deleted
   even when upstream rewrote them or added files under the same tree.
6. Stages the result, writes `.cache/upstream/last-merge-report.md`, and records the
   new base. Exit code `1` means conflicts: resolve them, commit, then run it again
   (it refuses to run while the recorded conflict markers are still in the tree).

`./scripts/sync-upstream.sh` is a thin wrapper around the same script; it adds nothing
but the old environment checks.

After a clean run:

1. Run the smoke test below.
2. `git commit` the merged tree (the script stages it).
3. Push the sync branch: `git push -u origin chore/sync-upstream-YYYYMMDD`.
4. Open a PR into `main` and merge only after tests pass.

Do not merge upstream directly on `main`, and do not rebase published `main` onto
`upstream/main`.

## Triage: Issues

The tracker is https://github.com/Eynzof/Hermes-CN-Core/issues. Two scripts wrap `gh` for the
day-to-day loop; both need `gh auth login` once and never handle a token themselves.

```bash
# read: one Markdown file per issue under .cache/issues/
python scripts/fetch_issue.py                    # every issue, newest first
python scripts/fetch_issue.py 196 194            # just these
python scripts/fetch_issue.py --state open --limit 20 --index
python scripts/fetch_issue.py 196 --stdout       # print, write nothing

# write: reply to one issue, optionally closing it with that reply
python scripts/response_issue.py 196 --body-file reply.md
python scripts/response_issue.py 196 --body-file reply.md --close
python scripts/response_issue.py 196 --body-file reply.md --close --reason not-planned
python scripts/response_issue.py 196 --body-file reply.md --dry-run
git log -1 --format=%B | python scripts/response_issue.py 196 --close
```

### fetch_issue.py

Writes `issue-<number>.md`: YAML front matter (number, title, state, labels, author, dates, URL),
the body verbatim, then every comment with its author and timestamp. `--index` also writes
`INDEX.md` (number, state, title, labels, comment count, updated).

- Output defaults to `.cache/issues/`, which is gitignored: tracker text is a cache, not a document
  tree (see "This fork ships code, not documentation"). Never point `--out` at a tracked directory.
- Files are overwritten on every run — re-fetching is how new comments arrive.
- `--limit N` keeps the N most recent; pull requests are filtered out of the list endpoint.
- Exit codes: `0` every issue written, `1` some numbers failed (the rest are still written), `2` gh
  missing or unauthenticated, or the list endpoint failed — nothing was written.

### response_issue.py

Posts the reply as a comment; `--close` is the only switch that also closes the issue
(`--reason completed` by default, `not-planned` otherwise). The body reaches `gh` over stdin
(`--body-file -`), never argv, because a Windows console mangles non-ASCII arguments and a pasted
log breaks the command line: use `--body-file` (UTF-8) rather than `--body` for Chinese text. An
already closed issue is never re-closed. `--dry-run` resolves the issue and prints the reply plus
the planned calls while writing nothing — use it before a maintainer sees the text. A §3.1 security
report goes through the private channel in `SECURITY.md`, never a public reply.

Tests: `tests/scripts/test_fetch_issue.py`, `tests/scripts/test_response_issue.py` and
`tests/scripts/test__gh_issue.py`. `gh` is faked in all three, so the suite never reaches the
network.

## Smoke Test

Run after every upstream sync and before every `runtime-v*` tag.

```bash
python -m venv "${TMPDIR:-/tmp}/hermes-cn-test"
source "${TMPDIR:-/tmp}/hermes-cn-test/bin/activate"
pip install -e .
hermes dashboard --no-open &
sleep 3
TOKEN=$(curl -sS http://127.0.0.1:9119/ | grep -oE '__HERMES_SESSION_TOKEN__="[^"]+"' | sed 's/.*="\(.*\)"/\1/')
HEADER="X-Hermes-Session-Token: $TOKEN"
curl -sS -H "$HEADER" http://127.0.0.1:9119/api/mcp-servers | jq
curl -sS -H "$HEADER" http://127.0.0.1:9119/api/profiles/active | jq
curl -sS -H "$HEADER" http://127.0.0.1:9119/api/fs/list | jq
curl -sS http://127.0.0.1:9119/openapi.json | jq '.paths | has("/api/v2/events") and has("/api/v2/rpc")'
# Exercise /api/upload through desktop or the web composer when possible.
# Exercise provider.probe and model.options slug_filter when touching gateway code.
kill %1
deactivate
rm -rf "${TMPDIR:-/tmp}/hermes-cn-test"
```

If the smoke test fails, do not merge the sync PR and do not cut a runtime release.

## Fork Patches

Every fork-only change should be traceable:

1. Commit message: `[CN-fork] P-NNN: short description`.
2. Row in `FORK_NOTES.md` (the table) — that file is the registry.
3. Smoke-test coverage here when the patch has runtime behavior.

If a patch is broadly useful, create a separate `upstream-pr/*` branch from
`upstream/main` and cherry-pick only that patch's minimal change. Do not open official
upstream PRs from `origin/main`, because it carries CN-specific and desktop-runtime
changes.

## Python 3.15

The fork targets **CPython 3.15**: `requires-python = ">=3.11,<3.16"`, `.python-version` is
`3.15`, and the test / live-provider / docker lanes run 3.15 (P-068). The 3.11 floor is not a
compatibility claim — it only lets an install still on an older minor run `hermes update` and
get moved forward (see `tests/compat/`). `pm/lock.json` pins the 3.15 interpreter (`3.15.2+20261110`, SQLite 3.53.1), so every
PM-managed thing — the launcher, the dependency generation, the docker image and the sealed
runtime `release-runtime.yml` builds — runs on it (P-071).

Rebuild a checkout for it:

```bash
uv python install 3.15
uv sync --locked --python 3.15 --extra all --group dev
```

`pywin32` 311 is why 3.15 needed a lock change: it ships cp311–cp314 wheels only, so
`uv lock --upgrade-package pywin32` moved it to 312 (first cp315 release; the `>=306`
specifier is unchanged). If `uv sync` reports a Windows package "can't be installed" after a
Python bump, that is the same class of problem — check PyPI for a cp315 wheel before pinning
around it.

### JIT — opt-in, for long-lived processes only

This 3.15 build carries the experimental JIT: `sys._jit.is_available()` is True and
`is_enabled()` False, i.e. nothing enables it for you. `PYTHON_JIT=1 hermes gateway run` (or a
kanban worker) is the intended use. Measured on Windows / CPython 3.15.2, best of N:

| workload | 3.14.9 | 3.15.2 | 3.15.2 + `PYTHON_JIT=1` |
|---|---|---|---|
| isolated interpreter start (`-I -S -c pass`) | 21.6 ms | 20.5 ms | — |
| pure-Python CPU loop, 1.2 s | 2 443 reps | 3 375 reps | 3 733 reps |
| `pytest tests/scripts/test_fetch_issue.py` | — | 1 251 ms | 1 246 ms |

Reading: the upgrade alone is +38 % on sustained pure-Python work, the JIT adds +11 % on top,
and a sub-2 s process sees nothing — warm-up eats the win. Do not set `PYTHON_JIT=1` in the CLI
launcher.

### Lazy imports (PEP 810) — evaluated, deliberately not adopted

`lazy import` / `sys.set_lazy_imports("all")` were measured against the CLI entry path and
rejected. `import hermes_cli.main` is 455 ms, but both big pieces are needed before any command
runs: `hermes_cli.config` 137 ms (directory scaffolding + one config parse) and
`hermes_bootstrap` 113 ms. The deferrable remainder — subcommand parsers 19 ms, model flows
36 ms, desktop/dashboard/TUI launchers 17 ms — is re-imported by `_build_cli_parser()` before
dispatch anyway. A curated filter cut the bare import to 418 ms (−14 %) and still made every
real command slower: `hermes --help` 653 → 669 ms, `hermes status` 1 995 → 1 985 ms,
`hermes --version` 1 160 → 1 182 ms; `-X lazy_imports=all` was worse still (`--version`
1 444 ms). The CLI already defers what matters via `_forward_command` (handler imports at call
time) and `_startup_fast` (the `--version` fast path). Don't re-add a blanket lazy-import layer.

## Conflict Handling

`scripts/git_merge.py` reports conflicts per path and leaves `git merge-file` markers
in the file (`<<<<<<< ours` / `>>>>>>> upstream/<path>`). When that happens:

```bash
git log --oneline -20 -- <conflicted-file>     # the fork side
git diff <base> <upstream-head> -- <conflicted-file>   # the upstream side
```

Resolve by preserving upstream behavior first, then re-applying the fork patch intent.
If upstream added an equivalent feature, drop the local fork implementation in the sync
branch and update `FORK_NOTES.md`. When the last marker is gone, commit and re-run the
script: it clears the pending record and advances the base.

## Runtime Releases

Runtime releases are signed artifacts consumed by `Hermes-CN-Desktop`. Cut them only
from a verified `main` commit:

```bash
git switch main
git pull --ff-only origin main
git tag runtime-v0.14.0-cn.1
git push origin runtime-v0.14.0-cn.1
```

The `release-runtime.yml` workflow builds PyInstaller artifacts, signs per-platform
manifests with `RUNTIME_SIGN_PRIVATE_KEY_PEM`, and publishes them to GitHub Releases.
The manifest schema and key-rotation process live in that workflow and in
`scripts/sign_runtime_manifest.py`.

## Do Not

- Do not push to `upstream`.
- Do not rebase published `main`.
- Do not squash unrelated fork patches together.
- Do not tag `runtime-v*` from an unreviewed sync branch.
- Do not let desktop depend on a user's global upstream `hermes`.
- Do not expose MCP command, args, or env values through public API responses.
- Do not reintroduce the deleted documentation trees; add a note instead.

## References

- Upstream: https://github.com/NousResearch/hermes-agent
- Fork: https://github.com/Eynzof/Hermes-CN-Core
- Desktop consumer: https://github.com/Eynzof/Hermes-CN-Desktop
- Fork patch notes: `FORK_NOTES.md`
- Upstream merge tool: `scripts/git_merge.py`
- Issue tools: `scripts/fetch_issue.py`, `scripts/response_issue.py`
- Deleted paths: `scripts/upstream-deletions.txt`

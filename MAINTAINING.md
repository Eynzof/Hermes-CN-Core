# Maintaining hermes-agent-cn

Simplified Chinese: [`MAINTAINING.zh-CN.md`](./MAINTAINING.zh-CN.md)

This repository is a long-lived downstream fork of
`NousResearch/hermes-agent`. The fork exists so `hermes-cn-desktop-v2`
can depend on a stable, signed `hermes-agent-cn` runtime while we still
sync official upstream changes regularly.

## Branch Model

```
origin/main       stable fork product branch
upstream/main     official NousResearch source, read-only
chore/sync-*      temporary upstream sync branches
cn/P-xxx-*        fork-only patch branches
upstream-pr/*     clean branches based on upstream/main for official PRs
runtime-v*        signed runtime release tags for desktop-v2
```

`desktop-v2` must consume runtime releases cut from verified
`origin/main`. It should not depend on a moving branch head or on a
user's globally installed upstream `hermes` package.

## Remote Layout

```
origin    https://github.com/Eynzof/hermes-agent-cn.git    fetch + push
upstream  https://github.com/NousResearch/hermes-agent.git fetch only
```

The local upstream push URL must be blocked:

```bash
git remote set-url --push upstream no_push
git config --unset-all remote.upstream.fetch
git config --add remote.upstream.fetch '+refs/heads/main:refs/remotes/upstream/main'
git config branch.main.remote origin
git config branch.main.merge refs/heads/main
```

## Routine: Upstream Sync

`upstream-watch.yml` opens or updates an issue when official upstream is
ahead. When that fires:

```bash
cd ~/Documents/GithubProjects/hermes/hermes-agent-cn
./scripts/sync-upstream.sh
```

The script:

1. Refuses to run with a dirty working tree.
2. Verifies that `upstream` cannot be pushed to.
3. Fetches `origin/main` and `upstream/main`.
4. Fast-forwards local `main` from `origin/main`.
5. Creates `chore/sync-upstream-YYYYMMDD`.
6. Merges `upstream/main` into that sync branch.

After a clean merge:

1. Run the smoke test below.
2. Push the sync branch: `git push -u origin chore/sync-upstream-YYYYMMDD`.
3. Open a PR into `main`.
4. Merge only after tests pass.

Do not merge upstream directly on `main`, and do not rebase published
`main` onto `upstream/main`.

## Smoke Test

Run after every upstream sync and before every `runtime-v*` tag.

```bash
python -m venv /tmp/hermes-cn-test
source /tmp/hermes-cn-test/bin/activate
pip install -e .

hermes dashboard --no-open &
sleep 3

TOKEN=$(curl -sS http://127.0.0.1:9119/ | grep -oE '__HERMES_SESSION_TOKEN__="[^"]+"' | sed 's/.*="\(.*\)"/\1/')
HEADER="X-Hermes-Session-Token: $TOKEN"

curl -sS -H "$HEADER" http://127.0.0.1:9119/api/mcp-servers | jq
curl -sS -H "$HEADER" http://127.0.0.1:9119/api/profiles/active | jq
curl -sS -H "$HEADER" http://127.0.0.1:9119/api/fs/list | jq
curl -sS http://127.0.0.1:9119/openapi.json | jq '.paths | has("/api/v2/events") and has("/api/v2/rpc")'

# Exercise /api/upload through desktop-v2 or the web composer when possible.
# Exercise provider.probe and model.options slug_filter when touching gateway code.

kill %1
deactivate
rm -rf /tmp/hermes-cn-test
```

If the smoke test fails, do not merge the sync PR and do not cut a
runtime release.

## Fork Patches

Every fork-only change should be traceable:

1. Commit message: `[CN-fork] P-NNN: short description`.
2. Row in `FORK_NOTES.md`.
3. Per-patch detail in `FORK_NOTES.md`.
4. Smoke-test coverage here when the patch has runtime behavior.

If a patch is broadly useful, create a separate `upstream-pr/*` branch
from `upstream/main` and cherry-pick only that patch's minimal change.
Do not open official upstream PRs from `origin/main`, because it carries
CN-specific and desktop-runtime changes.

## Conflict Handling

When `git merge upstream/main` conflicts in a sync branch:

```bash
git log --oneline main..upstream/main -- <conflicted-file>
git log --oneline upstream/main..main -- <conflicted-file>
```

Resolve by preserving upstream behavior first, then re-applying the fork
patch intent. If upstream added an equivalent feature, remove the local
fork implementation in the sync branch and update `FORK_NOTES.md`.

## Runtime Releases

Runtime releases are signed artifacts consumed by `hermes-cn-desktop-v2`.
Cut them only from a verified `main` commit:

```bash
git switch main
git pull --ff-only origin main
git tag runtime-v0.14.0-cn.1
git push origin runtime-v0.14.0-cn.1
```

The `release-runtime.yml` workflow builds PyInstaller artifacts, signs
per-platform manifests with `RUNTIME_SIGN_PRIVATE_KEY_PEM`, and publishes
them to GitHub Releases. See `docs/RUNTIME_RELEASES.md` for the manifest
schema and key-rotation process.

## Do Not

- Do not push to `upstream`.
- Do not rebase published `main`.
- Do not squash unrelated fork patches together.
- Do not tag `runtime-v*` from an unreviewed sync branch.
- Do not let desktop-v2 depend on a user's global upstream `hermes`.
- Do not expose MCP command, args, or env values through public API
  responses.

## References

- Upstream: https://github.com/NousResearch/hermes-agent
- Fork: https://github.com/Eynzof/hermes-agent-cn
- Desktop consumer: https://github.com/Eynzof/hermes-cn-desktop-v2
- Runtime release docs: `docs/RUNTIME_RELEASES.md`
- Fork patch notes: `FORK_NOTES.md`

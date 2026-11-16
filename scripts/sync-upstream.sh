#!/usr/bin/env bash
# Sync this fork from NousResearch/hermes-agent upstream.
#
# Kept as the short entry point everyone already knows; the merge itself lives in
# scripts/git_merge.py, because a plain `git merge upstream/main` cannot express
# this fork's deletions: every upstream edit to a deleted path becomes a
# modify/delete conflict and every new file under a deleted tree is re-added.
# See scripts/upstream-deletions.txt and MAINTAINING.md.
#
# The script never merges upstream directly into main. It creates a
# chore/sync-upstream-* branch, merges upstream/main there, and leaves the result
# for smoke testing and a PR back into origin/main.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM_REMOTE="${UPSTREAM_REMOTE:-upstream}"
ORIGIN_REMOTE="${ORIGIN_REMOTE:-origin}"
BASE_BRANCH="${BASE_BRANCH:-main}"
SYNC_BRANCH="${SYNC_BRANCH:-chore/sync-upstream-$(date +%Y%m%d)}"
UPSTREAM_URL="${UPSTREAM_URL:-https://github.com/NousResearch/hermes-agent}"
PYTHON="${PYTHON:-python3}"

cd "$REPO_ROOT"

if ! git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
  echo "WARNING: no '$UPSTREAM_REMOTE' remote configured. scripts/git_merge.py fetches"
  echo "         upstream itself, so the sync works; add the remote for reference:"
  echo "         git remote add upstream $UPSTREAM_URL"
  echo "         git remote set-url --push upstream no_push"
fi

UPSTREAM_PUSH_URL="$(git remote get-url --push "$UPSTREAM_REMOTE" 2>/dev/null || true)"
if [[ -n "$UPSTREAM_PUSH_URL" && "$UPSTREAM_PUSH_URL" != "no_push" ]]; then
  echo "ERROR: '$UPSTREAM_REMOTE' push URL is not blocked."
  echo "Run: git remote set-url --push $UPSTREAM_REMOTE no_push"
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: working tree is not clean. Commit or stash first."
  git status --short
  exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "$BASE_BRANCH" ]]; then
  echo "ERROR: not on $BASE_BRANCH (current: $CURRENT_BRANCH). Switch to $BASE_BRANCH first."
  exit 1
fi

echo "Fetching $ORIGIN_REMOTE..."
git fetch --prune "$ORIGIN_REMOTE"
git pull --ff-only "$ORIGIN_REMOTE" "$BASE_BRANCH"

# Upstream is fetched by git_merge.py into .cache/upstream/hermes-agent; this
# remote is only used for the status line and for --base overrides.
if git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
  git fetch --prune "$UPSTREAM_REMOTE"
  AHEAD="$(git rev-list --count "$BASE_BRANCH..$UPSTREAM_REMOTE/$BASE_BRANCH")"
  echo ""
  echo "Status:"
  echo "  $UPSTREAM_REMOTE/$BASE_BRANCH is $AHEAD commits ahead of $BASE_BRANCH"
fi

if git show-ref --verify --quiet "refs/heads/$SYNC_BRANCH"; then
  echo "ERROR: branch '$SYNC_BRANCH' already exists."
  echo "Set a different branch name with: SYNC_BRANCH=chore/sync-upstream-YYYYMMDD-2 $0"
  exit 1
fi

echo ""
read -r -p "Create '$SYNC_BRANCH' and merge upstream into it? [y/N] " ANSWER
if [[ "$ANSWER" != "y" && "$ANSWER" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

set +e
"$PYTHON" scripts/git_merge.py \
  --url "$UPSTREAM_URL" \
  --branch "$SYNC_BRANCH"
STATUS=$?
set -e

echo ""
if [[ $STATUS -eq 0 ]]; then
  echo "Merge clean. NEXT STEPS:"
  echo "  1. Review the diff and run the smoke test from MAINTAINING.md"
  echo "  2. git commit -m 'chore: sync upstream $(date +%Y-%m-%d)'"
  echo "  3. git push -u origin $SYNC_BRANCH"
  echo "  4. Open a PR from $SYNC_BRANCH into $BASE_BRANCH"
elif [[ $STATUS -eq 1 ]]; then
  echo "MERGE CONFLICTS. Resolve the paths listed above, then:"
  echo "  git add -A && git commit"
  echo "  $PYTHON scripts/git_merge.py --url $UPSTREAM_URL    # advances the recorded base"
  echo "See MAINTAINING.md -> Conflict Handling."
else
  echo "git_merge.py refused or failed before changing anything (exit $STATUS)."
fi
exit $STATUS

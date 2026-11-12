#!/usr/bin/env python3
"""Merge upstream NousResearch/hermes-agent into this fork, deletions included.

This fork ships code, not documentation: the published docs site, the translated
guides, the contributor-attribution data and the recorded performance reports are
deleted on purpose (``scripts/upstream-deletions.txt``). A plain ``git merge
upstream/main`` cannot express that — every upstream edit to a deleted path comes
back as a modify/delete conflict, and every new file under a deleted tree is
re-added. This script merges the upstream tree *file by file* and keeps every
fork deletion deleted, whether or not upstream touched it.

How it works
------------
1. ``.cache/upstream/hermes-agent`` holds a mirror clone of upstream. It is
   cloned on first use and fetched afterwards, so the network cost is one
   ``git fetch`` per sync.
2. The merge base is the upstream commit recorded by the previous run
   (``.cache/upstream/merge-state.json``). On the very first run it is the
   ``git merge-base`` between ``HEAD`` and the upstream tip, computed inside the
   mirror after fetching this repo's HEAD into it.
3. ``git diff --name-status <base> <head>`` lists what upstream changed. For each
   path this script does a content-level three-way merge, with no reliance on
   local history:
      * absent from the working tree (or listed in the deletions manifest) —
        skipped, so it stays deleted even when upstream rewrote it;
      * ours unchanged since the base — upstream's version is taken;
      * upstream unchanged since the base — ours is kept;
      * both changed — ``git merge-file`` merges the two sides and leaves conflict
        markers when it cannot, exactly like ``git merge`` would;
      * upstream deleted it — the local copy is deleted when it is untouched since
        the base, and kept (and reported) when the fork has local edits.
4. The new upstream tip is written to the state file straight away, so the next
   run merges only what upstream added since — with one guard: a run that left
   conflicts records those paths as ``pending`` and refuses to run again until
   their markers are gone. Resolving them and committing is what clears it.

Deletion policy (``scripts/upstream-deletions.txt``)
----------------------------------------------------
A path is left deleted when either the manifest matches it, or the merge base had
it and the working tree does not (a fork deletion the manifest does not spell out,
e.g. one deleted in a branch this script never saw). A path upstream *added* after
the base was never here, so its absence means nothing and it is merged in unless
the manifest covers it. ``--no-auto-deleted`` turns that second rule off and
leaves the decision to the manifest alone.

Usage
-----
    python scripts/git_merge.py --dry-run # report, change nothing
    python scripts/git_merge.py --branch chore/sync-upstream-$(date +%Y%m%d)
    python scripts/git_merge.py # merge into the current branch
    python scripts/git_merge.py --base <sha> # override the recorded base
    python scripts/git_merge.py --no-fetch # use the cached mirror as-is
    python scripts/git_merge.py --list-deletions # print the protected globs

Exit codes: ``0`` merged clean, ``1`` merged with conflicts (resolve, commit, run
again), ``2`` refused or failed before touching a file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

UPSTREAM_URL = "https://github.com/NousResearch/hermes-agent"
UPSTREAM_BRANCH = "main"
CACHE_DIR = Path(".cache") / "upstream" / "hermes-agent"
STATE_FILE = Path(".cache") / "upstream" / "merge-state.json"
REPORT_FILE = Path(".cache") / "upstream" / "last-merge-report.md"
DELETIONS_FILE = Path("scripts") / "upstream-deletions.txt"

# Binary files never go through ``git merge-file``; above this size the same
# applies, because a line-based merge of a 30 MB JSON is slower than reviewing
# whichever side moved.
BINARY_SNIFF_BYTES = 8000
TEXT_MERGE_MAX_BYTES = 2 * 1024 * 1024

MISSING = object()


class MergeError(RuntimeError):
    """A precondition or a git call failed; nothing was written."""


# ─────────────────────────────────────────────────────────── subprocess helpers


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    text: bool = False,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=text,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise MergeError(f"{' '.join(args)} failed:\n{detail.strip()}")
    return proc


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["git", *args], cwd=cwd, check=check)


def _git_text(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    return _git(*args, cwd=cwd, check=check).stdout.decode("utf-8", "replace").strip()


# ──────────────────────────────────────────────────────────── deletions policy


def load_deletions(path: Path) -> list[str]:
    """The fork's deletion globs, in file order, without comments or blanks."""
    if not path.is_file():
        return []
    patterns: list[str] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate one deletion glob (``**`` = across directories) to a regex.

    ``gitignore``-style semantics, minus the directory rules this file does not
    need: ``**`` matches any run of characters including ``/``, ``*`` and ``?``
    stay inside one path segment.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("".join(out) + r"\Z")


class Deletions:
    """Answers "did this fork delete that path?" for one merge."""

    def __init__(self, patterns: list[str]) -> None:
        self._rules = [(p, glob_to_regex(p.lstrip("!"))) for p in patterns]

    def matches(self, path: str) -> bool:
        """Last matching rule wins, so a leading ``!`` re-includes a path."""
        verdict = False
        for pattern, regex in self._rules:
            if regex.match(path):
                verdict = not pattern.startswith("!")
        return verdict

    def __bool__(self) -> bool:
        return bool(self._rules)


# ───────────────────────────────────────────────────────────── change parsing


@dataclass(frozen=True)
class Change:
    """One entry of ``git diff --name-status <base> <head>``."""

    status: str  # A, M, D, T (type change), or the raw R/C prefix
    path: str
    old_path: str | None = None

    @property
    def deleted(self) -> bool:
        return self.status == "D"

    @property
    def renamed(self) -> bool:
        return self.status.startswith("R") or self.status.startswith("C")


def parse_name_status(payload: bytes) -> list[Change]:
    """Parse ``git diff --name-status -z -M`` output.

    The ``-z`` form separates every field with NUL: ``STATUS\\0path\\0`` and, for a
    rename or copy, ``STATUS\\0old\\0new\\0``.
    """
    fields = payload.decode("utf-8", "surrogateescape").split("\0")
    changes: list[Change] = []
    i = 0
    while i < len(fields):
        status = fields[i].strip()
        i += 1
        if not status:
            continue
        if status.startswith(("R", "C")):
            old_path, new_path = fields[i], fields[i + 1]
            i += 2
            changes.append(Change(status=status, path=new_path, old_path=old_path))
        else:
            changes.append(Change(status=status, path=fields[i]))
            i += 1
    return changes


# ──────────────────────────────────────────────────────────────── the mirror


class Mirror:
    """The cached upstream clone plus the git reads a merge needs."""

    def __init__(self, root: Path, url: str, branch: str) -> None:
        self.root = root
        self.url = url
        self.branch = branch
        self.dir = root / CACHE_DIR

    # — lifecycle ---------------------------------------------------------
    def exists(self) -> bool:
        return (self.dir / "HEAD").is_file() or (self.dir / "config").is_file()

    def ensure(self, fetch: bool) -> None:
        if self.dir.exists() and not self.exists():
            print(f"[git_merge] removing the incomplete cache at {self.dir}")
            shutil.rmtree(self.dir, ignore_errors=True)
        if not self.exists():
            self._clone()
        elif fetch:
            self._fetch()

    def _clone(self) -> None:
        print(f"[git_merge] cloning {self.url} -> {self.dir}")
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        base = ["git", "clone", "--mirror", "--quiet"]
        # A partial mirror keeps .cache/ small; git fetches a missing blob on
        # demand, which the merge needs for every changed file anyway.
        attempt = _run([*base, "--filter=blob:none", self.url, str(self.dir)], cwd=self.root, check=False)
        if attempt.returncode != 0:
            shutil.rmtree(self.dir, ignore_errors=True)
            _run([*base, self.url, str(self.dir)], cwd=self.root)

    def _fetch(self) -> None:
        print(f"[git_merge] fetching {self.url} -> {self.dir}")
        _git("fetch", "--quiet", "--prune", "--prune-tags", "origin",
             "+refs/heads/*:refs/heads/*", cwd=self.dir)

    # — reads -------------------------------------------------------------
    def head(self) -> str:
        for ref in (f"refs/heads/{self.branch}", f"origin/{self.branch}", "HEAD"):
            proc = _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=self.dir, check=False)
            if proc.returncode == 0:
                return proc.stdout.decode().strip()
        raise MergeError(f"the mirror at {self.dir} has no {self.branch} branch; try --no-fetch or delete {self.dir}")

    def has_commit(self, rev: str) -> bool:
        return _git("cat-file", "-e", f"{rev}^{{commit}}", cwd=self.dir, check=False).returncode == 0

    def is_ancestor(self, older: str, newer: str) -> bool:
        return _git("merge-base", "--is-ancestor", older, newer, cwd=self.dir, check=False).returncode == 0

    def merge_base_with_worktree(self, ours: str = "HEAD") -> str:
        """merge-base(HEAD, upstream tip), computed inside the mirror.

        The fork shares upstream's history, so the answer exists in both repos;
        the mirror just needs this repo's HEAD to be able to see it.
        """
        _git("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", str(self.root),
             f"{ours}:refs/hermes-merge/head", cwd=self.dir)
        return _git_text("merge-base", "refs/hermes-merge/head", self.branch_ref(), cwd=self.dir)

    def branch_ref(self) -> str:
        for ref in (f"refs/heads/{self.branch}", f"origin/{self.branch}"):
            if _git("rev-parse", "--verify", "--quiet", ref, cwd=self.dir, check=False).returncode == 0:
                return ref
        return "HEAD"

    def diff(self, base: str, head: str) -> list[Change]:
        proc = _git("diff", "--name-status", "-z", "-M", base, head, cwd=self.dir)
        return parse_name_status(proc.stdout)

    def blob(self, rev: str, path: str) -> bytes | None:
        proc = _git("cat-file", "blob", f"{rev}:{path}", cwd=self.dir, check=False)
        return proc.stdout if proc.returncode == 0 else None

    def mode(self, rev: str, path: str) -> str:
        proc = _git("ls-tree", "-z", rev, "--", path, cwd=self.dir, check=False)
        entry = proc.stdout.decode("utf-8", "surrogateescape").split("\0")[0]
        return entry.split(" ", 1)[0] if " " in entry else "100644"


# ──────────────────────────────────────────────────────────── the merge itself


@dataclass
class Outcome:
    """Every path this run touched, grouped by what happened to it."""

    updated: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    conflict_hunks: dict[str, int] = field(default_factory=dict)
    binary_conflicts: list[str] = field(default_factory=list)
    kept_deleted: list[str] = field(default_factory=list)
    kept_modified: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.updated or self.added or self.deleted or self.merged or self.conflicts
                    or self.binary_conflicts)


def _looks_binary(data: bytes) -> bool:
    return b"\0" in data[:BINARY_SNIFF_BYTES]


def _write(path: Path, data: bytes, mode: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        os.chmod(path, 0o755 if mode.endswith("755") else 0o644)
    except OSError:  # pragma: no cover - exotic filesystems
        pass


def _merge_text(ours: bytes, base: bytes, theirs: bytes, labels: tuple[str, str, str]) -> tuple[int, bytes]:
    """``git merge-file`` on three byte strings; returns (conflict count, result)."""
    with tempfile.TemporaryDirectory(prefix="hermes-merge-") as tmp:
        tmp_path = Path(tmp)
        paths = []
        for name, data in zip(("ours", "base", "theirs"), (ours, base, theirs)):
            target = tmp_path / name
            target.write_bytes(data)
            paths.append(str(target))
        proc = _run(
            ["git", "merge-file", "-p", "-L", labels[0], "-L", labels[1], "-L", labels[2],
             paths[0], paths[1], paths[2]],
            check=False,
        )
        # 0 = clean, 1..127 = number of conflicts, <0 = error
        if proc.returncode < 0 or proc.returncode > 127:
            raise MergeError(f"git merge-file failed on {labels[0]}")
        return proc.returncode, proc.stdout


def merge_one(
    change: Change,
    *,
    repo: Path,
    mirror: Mirror,
    base: str,
    head: str,
    deletions: Deletions,
    auto_deleted: bool,
) -> tuple[str, int]:
    """Merge one changed path; returns (outcome bucket, conflict count)."""
    path = change.path
    target = repo / path

    if deletions.matches(path):
        return "kept_deleted", 0

    ours: bytes | None = target.read_bytes() if target.is_file() else None
    base_blob = mirror.blob(base, change.old_path or path)
    theirs_blob = mirror.blob(head, path)

    # A path the base had and the worktree does not was deleted by this fork;
    # treat that as deliberate and never resurrect it. A path *added* upstream
    # after the base was never here, so its absence says nothing (base_blob is
    # None there and the addition below applies).
    if ours is None and base_blob is not None and auto_deleted and not change.deleted:
        return "kept_deleted", 0

    if change.deleted:
        if ours is None:
            return "unchanged", 0
        if base_blob is None or ours == base_blob:
            target.unlink()
            return "deleted", 0
        return "kept_modified", 0

    if theirs_blob is None:  # a type change upstream turned into a non-file
        return "kept_modified", 0

    if ours == theirs_blob:
        return "unchanged", 0

    if base_blob is None:  # upstream added the file
        _write(target, theirs_blob, mirror.mode(head, path))
        return "added", 0

    if ours is None:
        # Deleted here, unchanged upstream (auto_deleted=False), so honour the
        # working tree: nothing to do.
        return "kept_deleted", 0

    if ours == base_blob:  # only upstream moved
        _write(target, theirs_blob, mirror.mode(head, path))
        return "updated", 0

    if theirs_blob == base_blob:  # only we moved
        return "unchanged", 0

    if _looks_binary(ours) or _looks_binary(base_blob) or _looks_binary(theirs_blob) or \
            max(len(ours), len(theirs_blob)) > TEXT_MERGE_MAX_BYTES:
        return "binary_conflict", 1

    conflicts, merged = _merge_text(
        ours, base_blob, theirs_blob, ("ours", "base", f"upstream/{path}"),
    )
    _write(target, merged, mirror.mode(head, path))
    return ("conflict", conflicts) if conflicts else ("merged", 0)


def apply_changes(
    changes: list[Change],
    *,
    repo: Path,
    mirror: Mirror,
    base: str,
    head: str,
    deletions: Deletions,
    auto_deleted: bool,
    dry_run: bool,
) -> Outcome:
    outcome = Outcome()
    for change in changes:
        if dry_run:
            bucket, conflicts = _classify(change, repo=repo, mirror=mirror, base=base,
                                          head=head, deletions=deletions,
                                          auto_deleted=auto_deleted)
        else:
            bucket, conflicts = merge_one(change, repo=repo, mirror=mirror, base=base, head=head,
                                          deletions=deletions, auto_deleted=auto_deleted)
        if change.renamed and change.old_path:
            # Upstream moved the path: the old one is a deletion, the new one an
            # addition (both already went through the deletion policy above).
            rule = _drop_renamed_source(change.old_path, repo=repo, mirror=mirror, base=base,
                                        deletions=deletions, dry_run=dry_run)
            if rule is not None:
                getattr(outcome, rule).append(change.old_path)
        if bucket == "conflict":
            outcome.conflicts.append(change.path)
            outcome.conflict_hunks[change.path] = conflicts
        elif bucket == "binary_conflict":
            outcome.binary_conflicts.append(change.path)
        else:
            getattr(outcome, bucket).append(change.path)
    return outcome


def _drop_renamed_source(
    path: str,
    *,
    repo: Path,
    mirror: Mirror,
    base: str,
    deletions: Deletions,
    dry_run: bool,
) -> str | None:
    """Delete the source of an upstream rename; returns its outcome bucket."""
    if deletions.matches(path):
        return "kept_deleted"
    target = repo / path
    if not target.is_file():
        return None
    base_blob = mirror.blob(base, path)
    if base_blob is not None and target.read_bytes() != base_blob:
        return "kept_modified"
    if not dry_run:
        target.unlink()
    return "deleted"


def _classify(
    change: Change,
    *,
    repo: Path,
    mirror: Mirror,
    base: str,
    head: str,
    deletions: Deletions,
    auto_deleted: bool,
) -> tuple[str, int]:
    """Dry-run twin of :func:`merge_one` — same rules, no writes."""
    path = change.path
    if deletions.matches(path):
        return "kept_deleted", 0
    target = repo / path
    ours = target.read_bytes() if target.is_file() else None
    base_blob = mirror.blob(base, change.old_path or path)
    theirs_blob = mirror.blob(head, path)
    if change.deleted:
        if ours is None:
            return "unchanged", 0
        return ("deleted", 0) if base_blob is None or ours == base_blob else ("kept_modified", 0)
    if ours is None and base_blob is not None and auto_deleted:
        return "kept_deleted", 0
    if theirs_blob is None:
        return "kept_modified", 0
    if ours == theirs_blob:
        return "unchanged", 0
    if base_blob is None:
        return "added", 0
    if ours is None or ours == base_blob:
        return "updated", 0
    if theirs_blob == base_blob:
        return "unchanged", 0
    if _looks_binary(ours) or _looks_binary(base_blob) or _looks_binary(theirs_blob) or \
            max(len(ours), len(theirs_blob)) > TEXT_MERGE_MAX_BYTES:
        return "binary_conflict", 1
    conflicts, _ = _merge_text(ours, base_blob, theirs_blob, ("ours", "base", f"upstream/{path}"))
    return ("conflict", conflicts) if conflicts else ("merged", 0)


# ─────────────────────────────────────────────────────────────────── plumbing


def repo_root() -> Path:
    proc = _git("rev-parse", "--show-toplevel", cwd=Path.cwd(), check=False)
    if proc.returncode != 0:
        raise MergeError("not inside a git repository")
    return Path(proc.stdout.decode().strip().replace("\\", "/"))


def ensure_clean(repo: Path, allow_dirty: bool) -> None:
    proc = _git("status", "--porcelain", cwd=repo, check=False)
    dirty = [line for line in proc.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    if not dirty:
        return
    listing = "\n".join(dirty[:10])
    more = "" if len(dirty) <= 10 else f"\n... and {len(dirty) - 10} more"
    if allow_dirty:
        print(f"[git_merge] warning: working tree is not clean\n{listing}{more}")
        return
    raise MergeError(
        "the working tree is not clean — commit or stash first, or pass --allow-dirty\n"
        f"{listing}{more}"
    )


def checkout_branch(repo: Path, name: str) -> None:
    exists = _git("show-ref", "--verify", "--quiet", f"refs/heads/{name}", cwd=repo, check=False)
    if exists.returncode == 0:
        raise MergeError(f"branch '{name}' already exists; pick another with --branch")
    _git("switch", "--create", name, cwd=repo)
    print(f"[git_merge] on a new branch '{name}'")


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(
    path: Path,
    *,
    base: str,
    head: str,
    branch: str,
    pending: list[str],
) -> None:
    """Record the upstream tip whose tree this working tree now represents.

    ``pending`` holds the paths left conflicted. The base advances either way:
    a re-run must merge only what upstream added since, and the recorded paths
    are what a re-run refuses to accept while their markers are still there.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "base": head,
        "previous_base": base,
        "upstream": UPSTREAM_URL,
        "branch": branch,
        "merged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pending": pending,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


CONFLICT_MARKERS = ("<<<<<<<", ">>>>>>>")


def unresolved_conflicts(repo: Path, pending: list[str]) -> list[str]:
    """Recorded conflicted paths that still carry conflict markers."""
    still = []
    for rel in pending:
        target = repo / rel
        try:
            text = target.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if all(marker in text for marker in CONFLICT_MARKERS):
            still.append(rel)
    return still


def stage(repo: Path, outcome: Outcome) -> None:
    paths = [*outcome.updated, *outcome.added, *outcome.deleted, *outcome.merged,
             *outcome.conflicts, *outcome.binary_conflicts]
    if not paths:
        return
    # ``git add -A`` also records the deletions and the conflict markers, so the
    # tree is exactly what a review (or `git merge --continue`) expects.
    for start in range(0, len(paths), 200):
        _git("add", "-A", "--", *paths[start:start + 200], cwd=repo)


def write_report(path: Path, *, base: str, head: str, outcome: Outcome, upstream: str) -> None:
    lines = [
        "# Upstream merge report",
        "",
        f"- upstream: {upstream}",
        f"- base: `{base}`",
        f"- head: `{head}`",
        f"- generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "",
    ]
    sections = [
        ("Conflicts (resolve by hand)",
         [f"{p} ({outcome.conflict_hunks.get(p, 1)} hunk(s))" for p in outcome.conflicts]),
        ("Binary conflicts (pick a side)", outcome.binary_conflicts),
        ("Merged textually", outcome.merged),
        ("Updated from upstream", outcome.updated),
        ("Added from upstream", outcome.added),
        ("Deleted (upstream deleted)", outcome.deleted),
        ("Kept deleted (fork policy)", outcome.kept_deleted),
        ("Kept local edits (upstream deleted)", outcome.kept_modified),
        ("Already identical", outcome.unchanged),
    ]
    for title, items in sections:
        lines.append(f"## {title} ({len(items)})")
        lines.append("")
        lines.extend(f"- `{item}`" for item in items)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def summarise(outcome: Outcome, *, dry_run: bool) -> None:
    rows = [
        ("merged (both sides edited)", outcome.merged),
        ("updated from upstream", outcome.updated),
        ("added from upstream", outcome.added),
        ("deleted (upstream deleted)", outcome.deleted),
        ("kept deleted (fork policy)", outcome.kept_deleted),
        ("kept local edits", outcome.kept_modified),
        ("already identical", outcome.unchanged),
        ("CONFLICTS", outcome.conflicts),
        ("BINARY CONFLICTS", outcome.binary_conflicts),
    ]
    verb = "would be" if dry_run else ""
    print()
    for label, items in rows:
        if items:
            print(f"  {label:<32} {len(items):>5} {verb}")
    if outcome.conflicts or outcome.binary_conflicts:
        print("\n  Conflicted paths:")
        for item in outcome.conflicts:
            hunks = outcome.conflict_hunks.get(item, 1)
            print(f"    {item} ({hunks} hunk(s))")
        for item in outcome.binary_conflicts:
            print(f"    {item} (binary — pick a side)")
        print("\n  Resolve them, then `git add -A && git merge --continue`"
              " (or `git commit`).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge upstream NousResearch/hermes-agent into this fork, keeping fork deletions deleted.",
    )
    parser.add_argument("--url", default=os.environ.get("HERMES_UPSTREAM_URL", UPSTREAM_URL),
                        help=f"upstream repository (default: {UPSTREAM_URL})")
    parser.add_argument("--branch", default="",
                        help="create and switch to this branch before merging")
    parser.add_argument("--base", default="",
                        help="merge base commit (default: the previous run's upstream commit)")
    parser.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    parser.add_argument("--no-fetch", action="store_true", help="use the cached mirror without fetching")
    parser.add_argument("--allow-dirty", action="store_true", help="merge into a dirty working tree")
    parser.add_argument("--no-auto-deleted", action="store_true",
                        help="ignore paths that are merely absent here; only the manifest decides")
    parser.add_argument("--no-stage", action="store_true", help="leave changes unstaged")
    parser.add_argument("--no-report", action="store_true", help="do not write the markdown report")
    parser.add_argument("--list-deletions", action="store_true",
                        help="print the deletion globs from scripts/upstream-deletions.txt and exit")
    args = parser.parse_args(argv)

    try:
        repo = repo_root()
        deletions = Deletions(load_deletions(repo / DELETIONS_FILE))
        if args.list_deletions:
            for pattern in load_deletions(repo / DELETIONS_FILE):
                print(pattern)
            return 0

        mirror = Mirror(repo, args.url, UPSTREAM_BRANCH)
        if args.no_fetch:
            if not mirror.exists():
                raise MergeError(f"{mirror.dir} does not exist yet; run once without --no-fetch")
        else:
            ensure_clean(repo, args.allow_dirty)
            mirror.ensure(fetch=True)

        state_path = repo / STATE_FILE
        state = load_state(state_path)
        pending = list(state.get("pending") or [])
        if pending:
            leftover = unresolved_conflicts(repo, pending)
            if leftover:
                raise MergeError(
                    "the previous merge left conflict markers in:\n  "
                    + "\n  ".join(leftover)
                    + "\nresolve them, commit, then run again"
                )
            print(f"[git_merge] previous merge's {len(pending)} conflict(s) are resolved")
            pending = []

        head = mirror.head()
        base = args.base or state.get("base", "")
        if base and not mirror.has_commit(base):
            raise MergeError(
                f"the recorded base {base} is not in the mirror. Re-run without --base to"
                " fall back to the merge base, or pass a commit the mirror knows."
            )
        if not base:
            base = mirror.merge_base_with_worktree()
            print(f"[git_merge] no state file: using merge-base {base[:12]}")
        if base == head:
            if not args.dry_run and state.get("pending"):
                save_state(state_path, base=base, head=head, branch=state.get("branch", UPSTREAM_BRANCH),
                           pending=[])
            print(f"[git_merge] already up to date with {args.url} at {head[:12]}")
            return 0
        if not mirror.is_ancestor(base, head):
            print(f"[git_merge] warning: {base[:12]} is not an ancestor of {head[:12]};"
                  " merging a diverged pair (content drift is reported as conflicts)")

        if args.branch and not args.dry_run:
            checkout_branch(repo, args.branch)

        changes = mirror.diff(base, head)
        print(f"[git_merge] {len(changes)} path(s) changed upstream between {base[:12]} and {head[:12]}")
        outcome = apply_changes(
            changes,
            repo=repo,
            mirror=mirror,
            base=base,
            head=head,
            deletions=deletions,
            auto_deleted=not args.no_auto_deleted,
            dry_run=args.dry_run,
        )

        conflicted = [*outcome.conflicts, *outcome.binary_conflicts]
        if not args.dry_run:
            if not args.no_stage:
                stage(repo, outcome)
            if not args.no_report:
                write_report(repo / REPORT_FILE, base=base, head=head, outcome=outcome,
                             upstream=args.url)
            save_state(state_path, base=base, head=head,
                       branch=args.branch or state.get("branch", UPSTREAM_BRANCH),
                       pending=conflicted)

        summarise(outcome, dry_run=args.dry_run)
        if args.dry_run:
            print("\n  Dry run: nothing was written.")
            return 0
        if conflicted:
            print(f"\n  {len(conflicted)} path(s) need a human. The base is recorded as"
                  f" {head[:12]}, so the next run merges only new upstream work and"
                  " refuses to run while these markers are still there.")
            return 1
        print(f"\n  Merge state advanced to {head[:12]}. Review the diff, run the tests, commit.")
        return 0
    except MergeError as exc:
        print(f"[git_merge] error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("[git_merge] interrupted", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

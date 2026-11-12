"""Contract: an upstream sync never resurrects a path this fork deleted.

``scripts/git_merge.py`` is the only sanctioned path for pulling
NousResearch/hermes-agent into this fork, because ``git merge upstream/main``
cannot distinguish "the fork deleted the docs site" from "a merge left these
files deleted": every upstream edit to a deleted path becomes a
modify/delete conflict, and every new file under a deleted tree is re-added.

These tests drive the script against real (local) git repositories, so the
merge is exercised end to end: clone-or-fetch, the recorded base, the three-way
content merge, the deletion policy and the conflict path.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "git_merge.py"

MANIFEST = """\
# test manifest
website/docs/**
contributors/**
"""


def _load():
    spec = importlib.util.spec_from_file_location("git_merge", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["git_merge"] = module
    spec.loader.exec_module(module)
    return module


git_merge = _load()


# ────────────────────────────────────────────────────────── unit-level pieces


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("website/docs/**", "website/docs/guide.md", True),
        ("website/docs/**", "website/docs/a/b/c.md", True),
        ("website/docs/**", "website/static/api/model-catalog.json", False),
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("plugins/*/README.md", "plugins/memory/README.md", True),
        ("plugins/*/README.md", "plugins/memory/deep/README.md", False),
        ("plugins/**/README.md", "plugins/memory/deep/README.md", True),
        ("evals/**/SCORECARD-*.md", "evals/compaction/results/SCORECARD-2026-08-15.md", True),
        ("evals/**/SCORECARD-*.md", "evals/compaction/results/SUMMARY.md", False),
    ],
)
def test_deletion_globs_match_like_gitignore(pattern, path, expected):
    assert bool(git_merge.Deletions([pattern]).matches(path)) is expected


def test_a_negated_rule_re_includes_a_path():
    deletions = git_merge.Deletions(["plugins/**/README.md", "!plugins/keep/README.md"])
    assert deletions.matches("plugins/memory/README.md")
    assert not deletions.matches("plugins/keep/README.md")


def test_name_status_parses_renames_and_deletions():
    payload = b"M\0agent/core.py\0D\0agent/legacy.py\0R087\0old/name.py\0new/name.py\0A\0a/b.md\0"
    changes = git_merge.parse_name_status(payload)
    assert [(c.status, c.path, c.old_path) for c in changes] == [
        ("M", "agent/core.py", None),
        ("D", "agent/legacy.py", None),
        ("R087", "new/name.py", "old/name.py"),
        ("A", "a/b.md", None),
    ]


def test_manifest_is_the_shipped_file():
    patterns = git_merge.load_deletions(ROOT / "scripts" / "upstream-deletions.txt")
    assert "website/docs/**" in patterns
    assert "contributors/**" in patterns
    deletions = git_merge.Deletions(patterns)
    # the data the fork keeps under website/ must stay mergeable
    assert not deletions.matches("website/static/api/model-catalog.json")
    assert not deletions.matches("website/static/oauth/client-metadata.json")
    assert deletions.matches("website/docs/reference/slash-commands.md")
    # code and agent instructions are never protected
    assert not deletions.matches("agent/prompt_builder.py")
    assert not deletions.matches("AGENTS.md")


def test_manifest_does_not_protect_a_file_the_fork_still_ships():
    """The manifest is a deletion list, not a freeze: a tracked path it matched
    would be silently dropped from every future sync."""
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False,
    )
    if tracked.returncode != 0:
        pytest.skip("not a git checkout")
    files = [p for p in tracked.stdout.decode().split("\0") if p]
    deletions = git_merge.Deletions(git_merge.load_deletions(ROOT / "scripts" / "upstream-deletions.txt"))
    still_here = [p for p in files if deletions.matches(p)]
    assert still_here == [], f"the manifest protects paths this fork still ships: {still_here[:5]}"


# ────────────────────────────────────────────────── an end-to-end merge fixture


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False,
        env={
            "PATH": str(Path(sys.executable).parent) + ";" + str(Path(sys.executable).parent),
            "HOME": str(repo.parent),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed:\n{proc.stderr}"
    return proc.stdout.strip()


def _init(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", branch)
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "config", "core.filemode", "false")
    return path


def _commit(repo: Path, files: dict[str, str | None], message: str) -> None:
    for rel, content in files.items():
        target = repo / rel
        if content is None:
            _git(repo, "rm", "-q", "--", rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _git(repo, "add", "--", rel)
    _git(repo, "commit", "-q", "-m", message)


def _run_merge(fork: Path, upstream: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(fork / "scripts" / "git_merge.py"), "--url", str(upstream), *args],
        cwd=fork, capture_output=True, text=True, check=False,
    )


@pytest.fixture
def fork(tmp_path: Path):
    """A fork that deleted the docs tree and patched one code file."""
    upstream = _init(tmp_path / "upstream")
    _commit(upstream, {
        "agent/core.py": "def run():\n    return 1\n\n\ndef helper():\n    return 0\n",
        "agent/legacy.py": "gone = True\n",
        "website/docs/guide.md": "# Guide\n\nold body\n",
        "contributors/emails/a@b.c": "alice\n",
        "README.md": "# Hermes\n",
    }, "initial")

    fork = tmp_path / "fork"
    _git(tmp_path, "clone", "-q", str(upstream), str(fork))
    _git(fork, "config", "user.email", "test@example.com")
    _git(fork, "config", "user.name", "Test")
    _git(fork, "config", "commit.gpgsign", "false")
    (fork / ".gitignore").write_text(".cache/\n", encoding="utf-8")
    _git(fork, "add", ".gitignore")
    # the fork's own additions/deletions live in the repo, as in the real tree
    (fork / "scripts").mkdir(exist_ok=True)
    script_source = SCRIPT.read_text(encoding="utf-8-sig")
    (fork / "scripts" / "git_merge.py").write_text(script_source, encoding="utf-8")
    (fork / "scripts" / "upstream-deletions.txt").write_text(MANIFEST, encoding="utf-8")
    _git(fork, "add", "scripts")
    _git(fork, "rm", "-q", "-r", "website", "contributors")
    _commit(fork, {"agent/core.py": "def run():\n    return 1\n\n\ndef helper():\n    # fork patch\n    return 5\n"}, "fork: drop docs, patch core")
    return fork, upstream


def test_merge_keeps_deletions_and_applies_upstream_code(fork):
    fork_repo, upstream = fork
    _commit(upstream, {
        "agent/core.py": "def run():\n    return 2\n\n\ndef helper():\n    return 0\n",  # both sides edited
        "agent/legacy.py": None,                                  # upstream deleted
        "agent/new_module.py": "NEW = 1\n",                        # upstream added
        "website/docs/guide.md": "# Guide\n\nrewritten body\n",    # deleted here
        "website/docs/brand-new.md": "# New page\n",               # new under a deleted tree
        "contributors/emails/b@c.d": "bob\n",                      # new under a deleted tree
        "README.md": "# Hermes\n\nupstream line\n",
    }, "upstream work")

    result = _run_merge(fork_repo, upstream)
    assert result.returncode == 0, result.stdout + result.stderr

    # 1. code upstream changed lands, with the fork's own edit preserved
    core = (fork_repo / "agent" / "core.py").read_text(encoding="utf-8-sig")
    assert "<<<<<<<" not in core
    assert "return 2" in core and "# fork patch" in core and "return 5" in core
    assert (fork_repo / "agent" / "new_module.py").is_file()
    assert not (fork_repo / "agent" / "legacy.py").exists()
    assert "upstream line" in (fork_repo / "README.md").read_text(encoding="utf-8-sig")

    # 2. every deleted path stays deleted, including brand-new upstream files
    assert not (fork_repo / "website" / "docs" / "guide.md").exists()
    assert not (fork_repo / "website" / "docs" / "brand-new.md").exists()
    assert not (fork_repo / "contributors" / "emails" / "b@c.d").exists()

    # 3. the merge advanced the recorded base and wrote a report
    state = json.loads((fork_repo / ".cache" / "upstream" / "merge-state.json").read_text(encoding="utf-8-sig"))
    assert state["base"] == _git(upstream, "rev-parse", "HEAD")
    report = (fork_repo / ".cache" / "upstream" / "last-merge-report.md").read_text(encoding="utf-8-sig")
    assert "website/docs/guide.md" in report
    assert "Kept deleted (fork policy)" in report

    # 4. committing that merge and running again is a no-op
    _git(fork_repo, 'commit', '-q', '-m', 'sync upstream')
    again = _run_merge(fork_repo, upstream)
    assert again.returncode == 0
    assert "already up to date" in again.stdout


def test_conflict_stops_the_merge_and_does_not_advance_state(fork):
    fork_repo, upstream = fork
    _commit(upstream, {"agent/core.py": "def run():\n    return 99\n\n\ndef helper():\n    return 0\n"}, "upstream rewrite")
    # make the fork's own edit collide on the same line
    (fork_repo / "agent" / "core.py").write_text(
        "def run():\n    return 7\n\n\ndef helper():\n    # fork patch\n    return 5\n",
        encoding="utf-8",
    )
    _git(fork_repo, "add", "agent/core.py")
    _git(fork_repo, "commit", "-q", "-m", "fork: change the return value")

    result = _run_merge(fork_repo, upstream)
    assert result.returncode == 1, result.stdout + result.stderr
    merged = (fork_repo / "agent" / "core.py").read_text(encoding="utf-8-sig")
    assert "<<<<<<< ours" in merged and ">>>>>>> upstream/agent/core.py" in merged

    # the base is recorded, together with the paths still needing a human, so a
    # re-run neither re-merges this commit nor accepts the markers as resolved
    state = json.loads((fork_repo / ".cache" / "upstream" / "merge-state.json").read_text(encoding="utf-8-sig"))
    assert state["base"] == _git(upstream, "rev-parse", "HEAD")
    assert state["pending"] == ["agent/core.py"]

    _git(fork_repo, "add", "-A")
    _git(fork_repo, "commit", "-q", "-m", "conflicted merge")
    unresolved = _run_merge(fork_repo, upstream)
    assert unresolved.returncode == 2
    assert "conflict markers" in unresolved.stderr

    # resolving and re-running clears the pending flag and stays up to date
    (fork_repo / "agent" / "core.py").write_text(
        "def run():\n    return 99\n\n\ndef helper():\n    # fork patch\n    return 5\n",
        encoding="utf-8",
    )
    _git(fork_repo, "add", "-A")
    _git(fork_repo, "commit", "-q", "-m", "resolve")
    second = _run_merge(fork_repo, upstream)
    assert second.returncode == 0, second.stdout + second.stderr
    state = json.loads((fork_repo / ".cache" / "upstream" / "merge-state.json").read_text(encoding="utf-8-sig"))
    assert state["pending"] == []


def test_dry_run_changes_nothing(fork):
    fork_repo, upstream = fork
    _commit(upstream, {"agent/new_module.py": "NEW = 1\n"}, "upstream work")
    before = _git(fork_repo, "status", "--porcelain")

    result = _run_merge(fork_repo, upstream, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dry run" in result.stdout
    assert _git(fork_repo, "status", "--porcelain") == before
    assert not (fork_repo / "agent" / "new_module.py").exists()
    assert not (fork_repo / ".cache" / "upstream" / "merge-state.json").exists()


def test_dirty_worktree_is_refused(fork):
    fork_repo, upstream = fork
    _commit(upstream, {"agent/new_module.py": "NEW = 1\n"}, "upstream work")
    (fork_repo / "agent" / "core.py").write_text("dirty\n", encoding="utf-8")

    result = _run_merge(fork_repo, upstream)
    assert result.returncode == 2
    assert "not clean" in result.stderr
    assert not (fork_repo / "agent" / "new_module.py").exists()

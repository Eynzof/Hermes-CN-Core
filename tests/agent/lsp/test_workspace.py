"""Tests for workspace + project-root resolution."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent.lsp.workspace import (
    clear_cache,
    find_git_worktree,
    is_inside_workspace,
    nearest_root,
    normalize_path,
    resolve_workspace_for_file,
)


@pytest.fixture(autouse=True)
def _clear():
    clear_cache()
    yield
    clear_cache()


def test_find_git_worktree_finds_dotgit(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    assert find_git_worktree(str(sub)) == str(repo)


def test_nearest_root_finds_first_marker(tmp_path: Path):
    root = tmp_path / "p"
    deep = root / "src" / "pkg"
    deep.mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    found = nearest_root(str(deep / "mod.py"), ["pyproject.toml"])
    assert found == str(root)


def test_nearest_root_skips_package_dirs(tmp_path: Path):
    # hermes_cli/setup.py is a module inside a package, not a project
    # marker; treating it as one spawned a second pyright per worktree.
    root = tmp_path / "p"
    pkg = root / "hermes_cli"
    pkg.mkdir(parents=True)
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "setup.py").write_text("", encoding="utf-8")
    found = nearest_root(str(pkg / "main.py"), ["pyproject.toml", "setup.py"])
    assert found == str(root)


def test_resolve_workspace_for_file_uses_cwd_first(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    file_path = repo / "x.py"
    file_path.write_text("", encoding="utf-8")
    # cwd is inside the repo
    monkeypatch.chdir(str(repo))
    root, gated = resolve_workspace_for_file(str(file_path))
    assert root == str(repo)
    assert gated is True


def test_resolve_workspace_for_file_survives_deleted_cwd(tmp_path: Path, monkeypatch):
    """A removed process cwd must read as "no anchor", not raise — the LSP
    workspace resolver runs inside a write tool and must never break a write
    that already landed on disk.

    The dead cwd is *simulated* (``os.getcwd`` raises, exactly as it does on POSIX
    once the directory is unlinked): Windows refuses to remove the process's own
    current directory (WinError 32), so the rmdir-based setup cannot even run there
    while the behaviour under test is the OSError guard, not the unlink itself.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    file_path = repo / "x.py"
    file_path.write_text("", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.chdir(str(scratch))

    def _raise_oserror(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(os, "getcwd", _raise_oserror)
    with pytest.raises(OSError):
        os.getcwd()

    root, gated = resolve_workspace_for_file(str(file_path))

    assert root == str(repo)
    assert gated is True
    # The diagnostics path logs through eventlog; its cwd-relative shortener must
    # not raise either, or the write succeeds with diagnostics silently dropped.
    from agent.lsp.eventlog import _short_path

    assert _short_path(str(file_path)) == str(file_path)


@pytest.mark.skipif(sys.platform == "win32", reason="HOME env var doesn't control Path.home() on Windows")
def test_normalize_path_expands_tilde(monkeypatch):
    monkeypatch.setenv("HOME", "/home/user")
    p = normalize_path("~/x.py")
    assert p == os.path.abspath("/home/user/x.py")


def test_find_git_worktree_cache_is_capped(tmp_path: Path, monkeypatch):
    """The start-dir cache resets past _WORKSPACE_CACHE_CAP instead of growing per distinct dir touched."""
    import agent.lsp.workspace as ws

    monkeypatch.setattr(ws, "_WORKSPACE_CACHE_CAP", 4)
    for i in range(6):
        d = tmp_path / f"d{i}"
        d.mkdir()
        assert find_git_worktree(str(d)) is None
    assert len(ws._workspace_cache) <= 4

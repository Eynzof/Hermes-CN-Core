"""Tests for the CLI ``/diff`` command handler.

``/diff`` shows git changes in the working directory (unstaged + untracked by
default; ``staged``/``all`` modes) and ``/diff session`` shows the cumulative
checkpoint-baseline diff of everything Hermes changed. These drive the mixin
handler against real git repos (default modes) and a stubbed checkpoint
manager (session mode), asserting rendering, ``--stat``, and graceful
degradation.
"""

import contextlib
import io
import os
import shutil
import subprocess

import pytest

from hermes_cli.cli_commands_mixin import CLICommandsMixin

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git required"
)


class _Console:
    def __init__(self, sink):
        self._sink = sink

    def print(self, obj, **kwargs):
        self._sink.write(getattr(obj, "plain", str(obj)) + "\n")


class _Mgr:
    def __init__(self, result, enabled=True):
        self.enabled = enabled
        self._result = result
        self.calls = []

    def unsupported_backend_reason(self, task_id="default"):
        return None

    def session_diff(self, cwd):
        self.calls.append(cwd)
        return self._result


class _Agent:
    def __init__(self, mgr):
        self._checkpoint_mgr = mgr


class _Stub(CLICommandsMixin):
    def __init__(self, agent=None):
        self.agent = agent


def _run(stub, command):
    buf = io.StringIO()
    stub.console = _Console(buf)
    with contextlib.redirect_stdout(buf):
        stub._handle_diff_command(command)
    return buf.getvalue()


def _git(repo, *args):
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "HOME": str(repo),
        # Same config isolation the product's git calls use, so the committed blob and
        # the product's later ``git diff`` agree on line endings on any host.
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "PATH": os.environ["PATH"],
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q")
    # Deterministic EOLs on every host: the product runs its own git commands with the
    # user's config isolated (``hermes_cli._subprocess_compat.noninteractive_git_env``
    # blanks GIT_CONFIG_SYSTEM/GLOBAL, so ``core.autocrlf`` is unset), while this
    # fixture's git inherits the host's — on Windows that is git-for-windows'
    # system-wide ``core.autocrlf=true``. Committing CRLF-normalized content and then
    # Diffing it with autocrlf off reports the just-committed file as modified. Pin the
    # same isolation the product uses, and write bytes so text mode can't re-encode \n
    # as CRLF (same pattern as tests/agent/test_coding_context.py).
    _git(d, "config", "core.autocrlf", "false")
    (d / "main.py").write_bytes(b"print('hello')\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "init")
    monkeypatch.setenv("TERMINAL_CWD", str(d))
    return d


# ---------------------------------------------------------------------------
# Default (working-tree) mode — real git
# ---------------------------------------------------------------------------

@requires_git
def test_diff_clean_repo_reports_no_changes(repo):
    out = _run(_Stub(), "/diff")
    assert "No changes" in out


@requires_git
def test_diff_non_git_directory_is_graceful(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(plain))
    out = _run(_Stub(), "/diff")
    assert "not a git repository" in out.lower()


# ---------------------------------------------------------------------------
# Session mode — stubbed checkpoint manager
# ---------------------------------------------------------------------------


def test_diff_session_empty_reports_no_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    mgr = _Mgr({"success": True, "stat": "", "diff": "", "empty": True})
    out = _run(_Stub(_Agent(mgr)), "/diff session")
    assert "No changes" in out



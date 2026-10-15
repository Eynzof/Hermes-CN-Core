"""Repository stages recover an unmerged index without losing local work (#4735)."""
import os
import shutil
import subprocess

import pytest

from tests.scripts.install.test_install_autostash_conflict_recovery import (
    INSTALL_PS1,
    INSTALL_SH,
    _git,
    _make_conflicted_managed_checkout,
)


@pytest.mark.platforms("windows", "posix")
@pytest.mark.live_system_guard_bypass
@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_repository_stage_clears_unmerged_index_and_preserves_changes(tmp_path):
    repo = _make_conflicted_managed_checkout(tmp_path)
    _git(repo, "commit", "-am", "local edit")
    _git(repo, "checkout", "-b", "feature", "HEAD~1")
    (repo / "tracked.txt").write_text("feature edit\n", encoding="utf-8")
    _git(repo, "commit", "-am", "feature edit")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "feature", check=False)
    assert _git(repo, "ls-files", "--unmerged").stdout.strip()
    conflict = (repo / "tracked.txt").read_text(encoding="utf-8")
    env = dict(os.environ, HERMES_HOME=str(tmp_path / "home"))
    if os.name == "nt":
        powershell = shutil.which("powershell")
        assert powershell, "Windows PowerShell is required on the Windows lane"
        command = [powershell, "-NoProfile", "-File", str(INSTALL_PS1),
                   "-Stage", "repository", "-NonInteractive", "-InstallDir", str(repo),
                   "-HermesHome", env["HERMES_HOME"]]
    else:
        command = ["bash", str(INSTALL_SH), "--stage", "repository", "--non-interactive", "--dir", str(repo)]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not _git(repo, "ls-files", "--unmerged").stdout.strip()
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "upstream edit\n"
    assert _git(repo, "show", "stash@{0}:tracked.txt").stdout == conflict

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="install.sh is POSIX-only")


def _write_fake_git(fake_bin: Path, log_path: Path, *, fail_ssh_clone: bool = False) -> None:
    fake_git = fake_bin / "git"
    fail_flag = "1" if fail_ssh_clone else "0"
    fake_git.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$*" >> "{log_path}"
if [ "$1" = "--version" ]; then
  echo "git version 2.50.0"
  exit 0
fi
if [ "$1" = "clone" ]; then
  url=""
  dest=""
  for arg in "$@"; do
    dest="$arg"
    case "$arg" in
      git@*|https://*) url="$arg" ;;
    esac
  done
  if [ "{fail_flag}" = "1" ] && [ "${{url#git@}}" != "$url" ]; then
    exit 1
  fi
  mkdir -p "$dest/.git"
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)


def _run_repository_stage(tmp_path: Path, *, fail_ssh_clone: bool = False):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_log = tmp_path / "git.log"
    _write_fake_git(fake_bin, git_log, fail_ssh_clone=fail_ssh_clone)

    install_dir = tmp_path / "install"
    hermes_home = tmp_path / "home"
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "HOME": str(tmp_path),
            "HERMES_HOME": str(hermes_home),
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(INSTALL_SH),
            "--stage",
            "repository",
            "--dir",
            str(install_dir),
            "--hermes-home",
            str(hermes_home),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    calls = git_log.read_text(encoding="utf-8").splitlines()
    return result, calls


def test_repository_stage_clones_hermes_cn_core_over_ssh(tmp_path: Path) -> None:
    result, calls = _run_repository_stage(tmp_path)

    assert result.returncode == 0, result.stderr
    clone_calls = [call for call in calls if call.startswith("clone ")]
    assert len(clone_calls) == 1
    assert "git@github.com:Eynzof/Hermes-CN-Core.git" in clone_calls[0]


def test_repository_stage_falls_back_to_hermes_cn_core_https(tmp_path: Path) -> None:
    result, calls = _run_repository_stage(tmp_path, fail_ssh_clone=True)

    assert result.returncode == 0, result.stderr
    clone_calls = [call for call in calls if call.startswith("clone ")]
    assert len(clone_calls) == 2
    assert "git@github.com:Eynzof/Hermes-CN-Core.git" in clone_calls[0]
    assert "https://github.com/Eynzof/Hermes-CN-Core.git" in clone_calls[1]

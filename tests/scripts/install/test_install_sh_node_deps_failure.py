"""Behavioral coverage for required Node dependency installation."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _shell_path(path: Path) -> str:
    """The POSIX spelling of ``path`` in the bash that runs install.sh.

    The stage child records ``$PWD``, which on Windows is the MSYS/WSL form
    (``%TEMP%`` shows up as ``/tmp/...``). Ask the shell to translate instead of
    guessing the mount table.
    """
    if os.name != "nt":
        return str(path)
    probe = subprocess.run(
        [
            "bash",
            "-c",
            'p="$1"; if command -v cygpath >/dev/null 2>&1; then cygpath -u "$p"; '
            'elif command -v wslpath >/dev/null 2>&1; then wslpath -u "$p"; '
            'else printf %s "$p"; fi',
            "hermes-shell-path",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.stdout.strip() or str(path)


def _run_node_deps_stage(
    tmp_path: Path,
    *,
    fail_directory: str | None,
) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
    install_dir = tmp_path / "install"
    tui_dir = install_dir / "ui-tui"
    bin_dir = tmp_path / "bin"
    hermes_home = tmp_path / "home"
    managed_bin = hermes_home / "bin"
    npm_calls = tmp_path / "npm-calls"

    tui_dir.mkdir(parents=True)
    bin_dir.mkdir()
    managed_bin.mkdir(parents=True)
    (install_dir / "package.json").write_text(
        '{"name":"installer-regression-probe","private":true}\n',
        encoding="utf-8",
    )
    (tui_dir / "package.json").write_text(
        '{"name":"tui-regression-probe","private":true}\n',
        encoding="utf-8",
    )
    _write_executable(bin_dir / "node", "#!/bin/sh\necho v26.0.0\n")
    # install.sh walks away on an MSYS/Cygwin host ("Windows detected. Please
    # use the PowerShell installer") before any stage can run. This test is
    # about the node-deps stage's failure semantics, which only exist in the
    # POSIX installer, so it runs the stage with the platform the stub tools
    # already claim (the node_probe driver fakes OS/DISTRO the same way).
    _write_executable(bin_dir / "uname", "#!/bin/sh\necho Linux\n")
    _write_executable(
        bin_dir / "npm",
        """#!/bin/sh
if [ "${1:-}" = "--version" ]; then
    echo 12.0.0
    exit 0
fi
printf '%s\\n' "$PWD" >> "$NPM_CALLS"
if [ -n "${NPM_FAIL_DIRECTORY:-}" ] && [ "$PWD" = "$NPM_FAIL_DIRECTORY" ]; then
    echo "simulated npm lifecycle failure" >&2
    exit 37
fi
exit 0
""",
    )
    _write_executable(managed_bin / "uv", "#!/bin/sh\necho 'uv probe'\n")

    env = os.environ.copy()
    env.update(
        {
            "HERMES_HOME": str(hermes_home),
            "HERMES_INSTALL_DIR": str(install_dir),
            "NPM_CALLS": str(npm_calls),
            # The npm stub compares against its own `$PWD`, so hand it the same
            # (POSIX) spelling the shell will report.
            "NPM_FAIL_DIRECTORY": (
                _shell_path(Path(fail_directory)) if fail_directory else ""
            ),
            # os.pathsep, not ":": install.sh runs under the host's bash, and on
            # Windows a hard-coded ":" turns the inherited PATH into one bogus
            # entry, so the `node`/`npm` stubs above are the only commands the
            # stage can find.
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )
    proc = subprocess.run(
        [
            "bash",
            str(INSTALL_SH),
            "--stage",
            "node-deps",
            "--json",
            "--skip-browser",
            "--skip-computer-use",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = npm_calls.read_text(encoding="utf-8").splitlines()
    return proc, install_dir, calls


def _stage_result(proc: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(proc.stdout.splitlines()[-1])


def test_root_node_dependency_failure_is_fatal(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    proc, actual_install_dir, calls = _run_node_deps_stage(
        tmp_path,
        fail_directory=str(install_dir),
    )

    assert actual_install_dir == install_dir
    assert proc.returncode != 0
    assert _stage_result(proc) == {
        "ok": False,
        "stage": "node-deps",
        "skipped": False,
        "reason": "exit code 1",
    }
    assert calls == [_shell_path(install_dir)]
    assert "Node.js dependencies installed" not in proc.stdout
    assert "TUI dependencies installed" not in proc.stdout
    assert not (install_dir / "node_modules").exists()


def test_tui_node_dependency_failure_is_fatal(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    tui_dir = install_dir / "ui-tui"
    proc, _, calls = _run_node_deps_stage(
        tmp_path,
        fail_directory=str(tui_dir),
    )

    assert proc.returncode != 0
    assert _stage_result(proc)["ok"] is False
    assert calls == [_shell_path(install_dir), _shell_path(tui_dir)]
    assert "Node.js dependencies installed" in proc.stdout
    assert "TUI dependencies installed" not in proc.stdout


def test_node_dependency_success_remains_successful(tmp_path: Path) -> None:
    proc, install_dir, calls = _run_node_deps_stage(
        tmp_path,
        fail_directory=None,
    )

    assert proc.returncode == 0, proc.stderr
    assert _stage_result(proc) == {
        "ok": True,
        "stage": "node-deps",
        "skipped": False,
    }
    assert calls == [
        _shell_path(install_dir),
        _shell_path(install_dir / "ui-tui"),
    ]
    assert "Node.js dependencies installed" in proc.stdout
    assert "TUI dependencies installed" in proc.stdout

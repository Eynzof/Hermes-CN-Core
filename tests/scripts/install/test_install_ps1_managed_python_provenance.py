"""The real installer waits for bootstrap uv before it delegates to PM."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.platforms("windows")
INSTALLER = Path(__file__).resolve().parents[3] / "scripts" / "install.ps1"


_INSTALL_PS1 = Path(__file__).resolve().parents[3] / "scripts" / "install.ps1"


def _pinned_python_minor() -> str:
    """The Python minor install.ps1 pins (``$PythonVersion``).

    Read from the script instead of hard-coding it: the CN fork pins 3.14
    (FORK_NOTES P-048, ``requires-python >=3.14``) where upstream pins 3.11, and
    these tests are about *provenance* (which interpreter the stage adopts), not
    about the pinned minor itself.
    """
    match = re.search(
        r'^\$PythonVersion\s*=\s*"([0-9.]+)"',
        _INSTALL_PS1.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match is not None, "install.ps1 no longer declares $PythonVersion"
    return match.group(1)


_PINNED_PYTHON = _pinned_python_minor()


def test_fresh_install_manifest_orders_repo_before_checkout_scoped_python(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell")
    assert powershell
    install = tmp_path / "install with spaces"
    package = install / "pm"
    package.mkdir(parents=True)
    (package / "lock.json").write_text(json.dumps({"packages": {"python": {"version": "3.12.8+fixture"}}}), encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(
        "import json, os, pathlib, sys\n"
        "assert not pathlib.Path(os.environ['UV_BUSY']).exists()\n"
        "pathlib.Path(os.environ['PM_LOG']).write_text(json.dumps(sys.argv[1:]))\n"
        "sys.exit(int(os.environ['PROBE_EXIT']))\n", encoding="utf-8",
    )
    existing = install / "venv" / "sentinel"
    existing.parent.mkdir()
    existing.write_text("previous generation", encoding="utf-8")
    log = tmp_path / "uv-calls.jsonl"
    pm_log = tmp_path / "pm-call.json"
    wrapper = tmp_path / "boundary.ps1"
    wrapper.write_text(r'''
param([string]$Installer, [string]$InstallDir, [string]$HomeDir, [string]$Log)
$ErrorActionPreference = 'Stop'
function uv {
    Set-Content -Path $env:UV_BUSY -Value 'running'
    try {
        ConvertTo-Json -Compress -InputObject @($args) | Add-Content -Encoding UTF8 $Log
        switch ($args[1]) {
            'install' { }
            'find' { Write-Output $env:BOOTSTRAP_PYTHON }
            default { throw 'PM must not be a child of the bootstrap uv' }
        }
        $global:LASTEXITCODE = 0
    } finally { Remove-Item $env:UV_BUSY }
}
function Invoke-WebRequest { throw 'network access outside test boundary' }
$installerArgs = @{ InstallDir = $InstallDir; HermesHome = $HomeDir }
if ($env:PROBE_SKIP_BROWSER) { $installerArgs.SkipBrowser = $true }
. $Installer @installerArgs
# Dot-sourcing defines Get-Uv, so override only that acquisition boundary.
function Get-Uv { return 'uv' }
Invoke-StageByName $env:PROBE_STAGE
exit $LASTEXITCODE
''', encoding="utf-8-sig")
    # uv python find returns the distribution interpreter, not a venv launcher.
    env = dict(os.environ, PROBE_STAGE=stage, PROBE_EXIT=str(exit_code), BOOTSTRAP_PYTHON=sys._base_executable,
               PM_LOG=str(pm_log), UV_BUSY=str(tmp_path / "uv-busy"), PATHEXT=".COM;.EXE;.BAT;.CMD")
    env.pop("PYTHONPATH", None)
    env.pop("PROBE_SKIP_BROWSER", None)
    if skip_browser:
        env["PROBE_SKIP_BROWSER"] = "1"
    run = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(wrapper),
                          "-Installer", str(INSTALLER), "-InstallDir", str(install), "-HomeDir", str(tmp_path / "home"), "-Log", str(log)],
                         cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=120)
    assert (run.returncode == 0) == (exit_code == 0), run.stdout + run.stderr
    from hermes_platform.host.facts import native_arch

    # The request names the machine's architecture: a bare version lets uv
    # pick an emulated x86_64 build on Windows-on-ARM.
    uv_arch = "aarch64" if native_arch() == "arm64" else "x86_64"
    assert [json.loads(line) for line in log.read_text(encoding="utf-8-sig").splitlines()] == [
        ["python", "find", "--managed-python", "--no-project", f"cpython-3.12-windows-{uv_arch}-none"],
    ]
    if stage == "python-deps":
        assert json.loads(pm_log.read_text(encoding="utf-8-sig")) == (
            ["install", "--without", "agent-browser"] if skip_browser else ["install"])
    else:
        assert not pm_log.exists()
    assert existing.read_text(encoding="utf-8-sig") == "previous generation"
    assert run.returncode == 0, installer_stdout + installer_stderr
    assert frames[-1]["ok"] is True
    commands = log.read_text(encoding="utf-8").splitlines()
    assert any(
        command.startswith(f"python find {_PINNED_PYTHON}")
        and "--managed-python" in command
        for command in commands
    )
    venv_command = next(command for command in commands if command.startswith("venv venv"))
    assert f"--python {managed_python}" in venv_command
    assert "--managed-python" in venv_command
    assert "--no-python-downloads" in venv_command


def test_fallback_minor_is_reported_from_resolved_managed_interpreter(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is required")

    hermes_home = tmp_path / "hermes-home"
    install_dir = tmp_path / "install"
    managed_python = (
        install_dir / ".hermes-runtime" / "python" / "cpython-3.12" / "python.exe"
    )
    uv = hermes_home / "bin" / "uv.exe"
    uv.parent.mkdir(parents=True)
    managed_python.parent.mkdir(parents=True)
    install_dir.mkdir(exist_ok=True)
    compile_fake_uv(powershell, uv)
    shutil.copy2(uv, managed_python)
    env = {
        **os.environ,
        "FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "FAKE_MANAGED_PYTHON": str(managed_python),
        "FAKE_MANAGED_PYTHON_VERSION": "3.12",
        "FAKE_PYTHON_VERSION": "Python 3.12.13",
    }

    run = _run_venv_stage(powershell, tmp_path, hermes_home, install_dir, env)

    assert run.returncode == 0, run.stdout + run.stderr
    assert "Creating virtual environment with Python 3.12" in run.stdout
    assert "Virtual environment ready (Python 3.12)" in run.stdout
    created_python = install_dir / "venv" / "Scripts" / "python.exe"
    version = subprocess.run(
        [str(created_python), "--version"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert version.stdout.strip() == "Python 3.12.13"


def test_python_find_drains_large_stderr_without_deadlock(tmp_path: Path) -> None:
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is required")

    hermes_home = tmp_path / "hermes-home"
    install_dir = tmp_path / "install"
    managed_python = (
        install_dir
        / ".hermes-runtime"
        / "python"
        / f"cpython-{_PINNED_PYTHON}"
        / "python.exe"
    )
    uv = hermes_home / "bin" / "uv.exe"
    uv.parent.mkdir(parents=True)
    managed_python.parent.mkdir(parents=True)
    compile_fake_uv(powershell, uv)
    shutil.copy2(uv, managed_python)
    env = {
        **os.environ,
        "FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "FAKE_MANAGED_PYTHON": str(managed_python),
        "FAKE_UV_FIND_STDERR_BYTES": str(1024 * 1024),
    }

    run = _run_venv_stage(powershell, tmp_path, hermes_home, install_dir, env)

    assert run.returncode == 0, run.stdout + run.stderr
    assert (
        f"Creating virtual environment with Python {_PINNED_PYTHON}" in run.stdout
    )


def test_python_find_timeout_kills_uv_and_fails_stage(tmp_path: Path) -> None:
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is required")

    hermes_home = tmp_path / "hermes-home"
    install_dir = tmp_path / "install"
    uv = hermes_home / "bin" / "uv.exe"
    uv.parent.mkdir(parents=True)
    install_dir.mkdir()
    compile_fake_uv(powershell, uv)
    env = {
        **os.environ,
        "FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "FAKE_UV_FIND_DELAY_MS": "60000",
    }

    run = _run_venv_stage(powershell, tmp_path, hermes_home, install_dir, env)

    assert run.returncode != 0
    assert (
        f"uv python find {_PINNED_PYTHON} timed out after 30000 ms"
        in (run.stdout + run.stderr)
    )


def test_venv_failure_fails_stage_and_restores_existing_environment(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is required")

    hermes_home = tmp_path / "hermes-home"
    install_dir = tmp_path / "install"
    managed_python = (
        install_dir / ".hermes-runtime" / "python" / "cpython-3.11" / "python.exe"
    )
    old_python = install_dir / "venv" / "Scripts" / "python.exe"
    uv = hermes_home / "bin" / "uv.exe"
    for directory in (uv.parent, managed_python.parent, old_python.parent):
        directory.mkdir(parents=True, exist_ok=True)
    old_python.write_text("previous environment", encoding="ascii")
    compile_fake_uv(powershell, uv)
    shutil.copy2(uv, managed_python)
    env = {
        **os.environ,
        "FAKE_UV_LOG": str(tmp_path / "uv.log"),
        "FAKE_MANAGED_PYTHON": str(managed_python),
        "FAKE_UV_VENV_EXIT": "37",
    }

    run = _run_venv_stage(powershell, tmp_path, hermes_home, install_dir, env)

    assert run.returncode != 0
    assert "Failed to create virtual environment (uv venv exited with 37)" in (
        run.stdout + run.stderr
    )
    assert old_python.read_text(encoding="ascii") == "previous environment"
    frames = [json.loads(line) for line in run.stdout.splitlines() if line.startswith("{")]
    assert frames[-1]["ok"] is False

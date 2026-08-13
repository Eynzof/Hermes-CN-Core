import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch




@pytest.mark.linux_only
def test_find_install_script_from_checkout(tmp_path):
    """_find_install_script finds scripts/install.sh in a git checkout.

    ``linux_only``: the POSIX arm picks ``install.sh`` + ``bash``, which is
    already what ``_IS_WINDOWS`` reports here — nothing needs faking.
    """
    from hermes_cli.dep_ensure import _find_install_script
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "install.sh").write_text("#!/bin/bash", encoding="utf-8")
    path, shell = _find_install_script(package_dir=tmp_path / "hermes_cli", repo_root=tmp_path)
    assert path is not None
    assert path.name == "install.sh"
    assert shell == "bash"








# ── _has_ripgrepy ────────────────────────────────────────────────────


def test_has_ripgrepy_returns_true_when_installed():
    """_has_ripgrepy returns True when ripgrepy is importable."""
    from hermes_cli.dep_ensure import _has_ripgrepy
    # ripgrepy is installed in the dev venv, so this should be True
    assert _has_ripgrepy() is True


def test_has_ripgrepy_returns_false_when_not_installed():
    """_has_ripgrepy returns False when import fails."""
    import builtins
    from hermes_cli.dep_ensure import _has_ripgrepy
    orig_import = builtins.__import__

    def blocking_import(name, *args, **kwargs):
        if name == "ripgrepy" or name.startswith("ripgrepy."):
            raise ImportError("No module named ripgrepy")
        return orig_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=blocking_import):
        assert _has_ripgrepy() is False


def test_ripgrep_dep_check_requires_ripgrepy():
    """The ripgrep dep check requires both rg binary AND ripgrepy package."""
    from hermes_cli.dep_ensure import _DEP_CHECKS
    check_fn = _DEP_CHECKS["ripgrep"]
    with patch("hermes_cli.dep_ensure._find_rg", return_value=None), \
         patch("hermes_cli.dep_ensure._has_ripgrepy", return_value=False):
        assert check_fn() is False


def test_ripgrep_dep_check_passes_with_both():
    """The ripgrep dep check passes when both rg AND ripgrepy are present."""
    from hermes_cli.dep_ensure import _DEP_CHECKS
    check_fn = _DEP_CHECKS["ripgrep"]
    with patch("hermes_cli.dep_ensure._find_rg", return_value="/usr/bin/rg"), \
         patch("hermes_cli.dep_ensure._has_ripgrepy", return_value=True):
        assert check_fn() is True


def test_ripgrep_dep_check_fails_with_rg_but_no_ripgrepy():
    """The ripgrep dep check fails when rg is present but ripgrepy is missing."""
    from hermes_cli.dep_ensure import _DEP_CHECKS
    check_fn = _DEP_CHECKS["ripgrep"]
    with patch("hermes_cli.dep_ensure._find_rg", return_value="/usr/bin/rg"), \
         patch("hermes_cli.dep_ensure._has_ripgrepy", return_value=False):
        assert check_fn() is False


# ── _find_rg ─────────────────────────────────────────────────────────


def _completed(*args, **kwargs):
    return CompletedProcess(args=args, returncode=0, stdout=b"ripgrep 14.1.1")


def test_find_rg_prefers_managed_on_windows(tmp_path):
    """On Windows, _find_rg returns the managed rg.exe when it runs."""
    managed = tmp_path / "tools" / "rg.exe"
    managed.parent.mkdir(parents=True)
    managed.write_text("fake")
    from hermes_cli.dep_ensure import _find_rg
    with patch("hermes_cli.dep_ensure._IS_WINDOWS", True), \
         patch("hermes_cli.dep_ensure.get_managed_tools_dir", return_value=managed.parent), \
         patch("hermes_cli.dep_ensure.subprocess.run", side_effect=_completed) as mock_run, \
         patch("hermes_cli.dep_ensure.shutil.which", return_value=None):
        result = _find_rg()
        assert result == str(managed)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == str(managed)


def test_find_rg_prefers_managed_on_posix(tmp_path):
    """On POSIX, _find_rg returns the managed rg when it runs."""
    managed = tmp_path / "tools" / "rg"
    managed.parent.mkdir(parents=True)
    managed.write_text("fake")
    from hermes_cli.dep_ensure import _find_rg
    with patch("hermes_cli.dep_ensure._IS_WINDOWS", False), \
         patch("hermes_cli.dep_ensure.get_managed_tools_dir", return_value=managed.parent), \
         patch("hermes_cli.dep_ensure.subprocess.run", side_effect=_completed) as mock_run, \
         patch("hermes_cli.dep_ensure.shutil.which", return_value="/usr/bin/rg"):
        result = _find_rg()
        assert result == str(managed)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == str(managed)


def test_find_rg_falls_back_to_path_when_managed_broken(tmp_path):
    """When managed rg is broken, _find_rg falls back to PATH rg."""
    managed = tmp_path / "tools" / "rg.exe"
    managed.parent.mkdir(parents=True)
    managed.write_text("fake")
    from hermes_cli.dep_ensure import _find_rg

    def _run_side_effect(cmd, **kwargs):
        if str(cmd[0]) == str(managed):
            raise subprocess.CalledProcessError(1, cmd)
        return _completed(cmd)

    with patch("hermes_cli.dep_ensure._IS_WINDOWS", True), \
         patch("hermes_cli.dep_ensure.get_managed_tools_dir", return_value=managed.parent), \
         patch("hermes_cli.dep_ensure.subprocess.run", side_effect=_run_side_effect) as mock_run, \
         patch("hermes_cli.dep_ensure.shutil.which", return_value="C:\\Program Files\\rg.exe"):
        result = _find_rg()
        assert result == "C:\\Program Files\\rg.exe"
        assert mock_run.call_count == 2


def test_find_rg_returns_none_when_all_broken(tmp_path):
    """_find_rg returns None when both managed and PATH rg are unusable."""
    managed = tmp_path / "tools" / "rg.exe"
    managed.parent.mkdir(parents=True)
    managed.write_text("fake")
    from hermes_cli.dep_ensure import _find_rg
    with patch("hermes_cli.dep_ensure._IS_WINDOWS", True), \
         patch("hermes_cli.dep_ensure.get_managed_tools_dir", return_value=managed.parent), \
         patch("hermes_cli.dep_ensure.subprocess.run", side_effect=subprocess.CalledProcessError(1, "rg")), \
         patch("hermes_cli.dep_ensure.shutil.which", return_value="C:\\bad\\rg.exe"):
        assert _find_rg() is None


def test_find_rg_falls_back_to_legacy_bin(tmp_path):
    """_find_rg falls back to the legacy HERMES_HOME/bin location."""
    legacy = tmp_path / "bin" / "rg"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("fake")
    from hermes_cli.dep_ensure import _find_rg
    with patch("hermes_cli.dep_ensure._IS_WINDOWS", False), \
         patch("hermes_cli.dep_ensure.get_managed_tools_dir", return_value=tmp_path / "tools"), \
         patch("hermes_constants.get_hermes_home", return_value=tmp_path), \
         patch("hermes_cli.dep_ensure.subprocess.run", side_effect=_completed) as mock_run, \
         patch("hermes_cli.dep_ensure.shutil.which", return_value=None):
        result = _find_rg()
        assert result == str(legacy)
        args = mock_run.call_args[0][0]
        assert args[0] == str(legacy)


def test_find_rg_verifies_path_candidate_runs():
    """_find_rg returns None when PATH rg exists but cannot run --version."""
    from hermes_cli.dep_ensure import _find_rg
    with patch("hermes_cli.dep_ensure._IS_WINDOWS", False), \
         patch("hermes_cli.dep_ensure.get_managed_tools_dir", return_value=Path("/nonexistent/tools")), \
         patch("hermes_cli.dep_ensure.shutil.which", return_value="/broken/rg"), \
         patch("hermes_cli.dep_ensure.subprocess.run", side_effect=subprocess.CalledProcessError(127, "rg")):
        assert _find_rg() is None


def test_ripgrep_description_mentions_ripgrepy():
    """The ripgrep dep description should mention ripgrepy."""
    from hermes_cli.dep_ensure import _DEP_DESCRIPTIONS
    assert "ripgrepy" in _DEP_DESCRIPTIONS["ripgrep"]


# ── Original test_ensure_dependency_uses_powershell_on_windows ──────────


@pytest.mark.windows_only
def test_ensure_dependency_uses_powershell_on_windows(tmp_path):
    """``windows_only``: the assertion is that we shell out to a real
    PowerShell. Faking ``_IS_WINDOWS`` on Linux also required faking
    ``shutil.which`` into inventing a powershell.exe that isn't there."""
    from hermes_cli.dep_ensure import ensure_dependency
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "install.ps1").write_text("# fake")
    with patch("hermes_cli.dep_ensure._DEP_CHECKS", {"node": lambda: False}), \
         patch("hermes_cli.dep_ensure._find_install_script", return_value=(scripts_dir / "install.ps1", "powershell")), \
         patch("hermes_cli.dep_ensure.shutil") as mock_shutil, \
         patch("hermes_constants.get_hermes_home", return_value=tmp_path / "fakehome"), \
         patch("subprocess.run") as mock_run, \
         patch("sys.stdin") as mock_stdin:
        mock_shutil.which.side_effect = lambda name: "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" if name == "powershell" else None
        mock_stdin.isatty.return_value = False
        mock_run.return_value = type("R", (), {"returncode": 0})()
        ensure_dependency("node", interactive=False)
        cmd = mock_run.call_args[0][0]
        assert "powershell" in cmd[0].lower()
        assert "-Ensure" in cmd
        assert cmd[cmd.index("-Ensure") + 1] == "node"
        assert "-HermesHome" in cmd
        assert str(tmp_path / "fakehome") in cmd
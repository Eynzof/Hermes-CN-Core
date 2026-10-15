"""Git recovery finds the selected PM tree even outside standard install roots."""

import pytest


@pytest.mark.parametrize("arch", ["mingw64", "clangarm64"])
def test_recovery_uses_the_selected_pm_git_core(tmp_path, monkeypatch, arch):
    import pm
    from hermes_cli import update_cmd_git
    from pm.install import InstalledPackage

    root = tmp_path / "custom-store" / "selected-git"
    shim = root / "cmd" / "git.exe"
    core = root / arch / "libexec" / "git-core" / "git.exe"
    shim.parent.mkdir(parents=True)
    core.parent.mkdir(parents=True)
    shim.touch()
    core.touch()

    def installed(name, *, allow_outdated=False):
        assert name == "git" and allow_outdated
        return InstalledPackage(root, "previous-pin", shim)

    monkeypatch.setattr(pm, "installed_package", installed)
    # Model the broken launcher at the subprocess boundary, without faking
    # the host OS; the Windows live suite executes the real PM executables.
    monkeypatch.setattr(update_cmd_git, "_probe_fork_bomb", lambda argv: argv != [str(core)])

    assert update_cmd_git._locate_real_git() == core

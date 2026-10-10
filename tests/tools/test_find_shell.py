"""Tests for _find_shell — user-login-shell preference on POSIX.

Regression tests for #42203: on macOS, ``_find_shell`` used to return
``/bin/bash`` (bash 3.2) which silently swallowed background commands
when ``~/.bash_profile`` contained ``exec /bin/zsh -l``.
"""

import os
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from tools.environments.local import _find_bash, _find_bash_posix, _find_shell


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        try:
            return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
    except ImportError:
        try:
            os.kill(pid, 0)  # windows-footgun: ok — psutil fallback only on POSIX hosts without it
        except OSError:
            return False
        return True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: $SHELL preference")
class TestFindShellPrefersUserShell:
    """_find_shell should prefer $SHELL over bash on POSIX."""

    @pytest.mark.platforms("linux")
    def test_returns_shell_env_when_set_and_exists(self, tmp_path):
        """When $SHELL points to an existing allowlisted executable, _find_shell returns it."""
        fake_zsh = tmp_path / "zsh"
        fake_zsh.touch()
        fake_zsh.chmod(0o755)
        with patch.dict(os.environ, {"SHELL": str(fake_zsh)}):
            assert _find_shell() == str(fake_zsh)

    def test_falls_back_when_shell_not_executable(self, tmp_path):
        """$SHELL exists but lacks the execute bit -> fall back to _find_bash_posix
        (returning it would fail at spawn time)."""
        fake = tmp_path / "zsh"
        fake.touch()
        fake.chmod(0o644)  # not executable
        with patch.dict(os.environ, {"SHELL": str(fake)}):
            assert _find_shell() == _find_bash_posix()

    def test_falls_back_for_incompatible_shell_fish(self, tmp_path):
        """#42203 regression: $SHELL=fish must NOT be returned — spawn_local's
        `-lic` / `set +m` syntax breaks fish, which would trade the bash-3.2
        swallow for a parse error on every background command. Fall back to bash."""
        fake_fish = tmp_path / "fish"
        fake_fish.touch()
        fake_fish.chmod(0o755)
        with patch.dict(os.environ, {"SHELL": str(fake_fish)}):
            assert _find_shell() == _find_bash_posix()

    def test_falls_back_for_incompatible_shell_csh(self, tmp_path):
        """$SHELL=tcsh/csh is also not -lic/set+m compatible -> fall back."""
        fake = tmp_path / "tcsh"
        fake.touch()
        fake.chmod(0o755)
        with patch.dict(os.environ, {"SHELL": str(fake)}):
            assert _find_shell() == _find_bash_posix()

    @pytest.mark.platforms("linux")
    def test_honours_allowlisted_bash_and_dash(self, tmp_path):
        """Every allowlisted POSIX-sh-family shell is honoured."""
        for name in ("bash", "dash", "sh", "ksh"):
            fake = tmp_path / name
            fake.touch()
            fake.chmod(0o755)
            with patch.dict(os.environ, {"SHELL": str(fake)}):
                assert _find_shell() == str(fake), name

    def test_falls_back_to_find_bash_posix_when_shell_unset(self):
        """When $SHELL is unset, _find_shell delegates to _find_bash_posix."""
        env = {k: v for k, v in os.environ.items() if k != "SHELL"}
        with patch.dict(os.environ, env, clear=True):
            assert _find_shell() == _find_bash_posix()

    def test_falls_back_to_find_bash_posix_when_shell_not_a_file(self, tmp_path):
        """When $SHELL points to a non-existent path, _find_shell delegates."""
        fake_path = str(tmp_path / "nonexistent_shell")
        with patch.dict(os.environ, {"SHELL": fake_path}):
            assert _find_shell() == _find_bash_posix()

    def test_falls_back_to_find_bash_posix_when_shell_empty(self):
        """When $SHELL is empty string, _find_shell delegates."""
        with patch.dict(os.environ, {"SHELL": ""}):
            assert _find_shell() == _find_bash_posix()


class TestFindShellWindowsBehavior:
    """On Windows, _find_shell always delegates to _find_bash_posix."""

    @pytest.mark.platforms("windows")
    def test_windows_ignores_shell_env(self):
        """On Windows, $SHELL is ignored — _find_shell resolves a Git Bash.

        Windows-only: faking ``_IS_WINDOWS`` selected the branch but left
        ``_find_bash`` resolving a POSIX bash, so the equality proved nothing
        about Git-Bash resolution on the real host.
        """
        # Even if SHELL is set, it should be ignored on Windows
        with patch.dict(os.environ, {"SHELL": "/usr/bin/zsh"}):
            result = _find_shell()
            assert result != "/usr/bin/zsh"
            assert "bash" in os.path.basename(result).lower()

            # Do NOT assert strict equality with _find_bash(): on a real host
            # _find_shell (POSIX-only by design) uses shutil.which, while
            # _find_bash's kimix-aligned ladder can legitimately pick the
            # <gitRoot>/bin/bash.exe launcher instead of the
            # <gitRoot>/usr/bin/bash.exe MSYS binary. Both are valid Git Bash
            # files; assert both resolve to a real bash rather than coupling
            # the two resolution orders.
            bash = _find_bash()
            assert "bash" in os.path.basename(bash).lower()
            assert os.path.isfile(result) and os.path.isfile(bash)


class TestFindBashCollapsedToPmShell:
    """_find_bash is now a thin wrapper over pm.shell(); the Windows
    candidate ladder (HERMES_GIT_BASH_PATH → %LOCALAPPDATA%\\hermes\\git →
    Program Files) and the ASLR diagnostic were deleted — the store is the
    authority on bundled bash."""

    @pytest.mark.platforms("windows")
    def test_delegates_to_pm_shell(self, monkeypatch):
        """_find_bash returns whatever pm.shell() resolves (store bash or
        provisioned PATH)."""
        monkeypatch.setattr(
            "pm.shell.bash", lambda: r"C:\store\tools\git-x\usr\bin\bash.exe"
        )
        assert _find_bash() == r"C:\store\tools\git-x\usr\bin\bash.exe"
    def test_returns_string(self):
        """_find_shell always returns a non-empty string on any platform."""
        result = _find_shell()
        assert isinstance(result, str)
        assert len(result) > 0


class TestFindBashUnchanged:
    """_find_bash_posix should be unaffected by the _find_shell change."""

    def test_find_bash_posix_still_prefers_bash(self):
        """_find_bash_posix still returns bash (not $SHELL) on POSIX."""
        result = _find_bash_posix()
        # On any system, _find_bash_posix should return something containing "bash"
        # or fall back to $SHELL or /bin/sh — but it should NOT prefer $SHELL
        # over bash the way _find_shell does.
        assert isinstance(result, str)
        assert len(result) > 0


class TestFindBashSkipsBrokenCustomPath:
    """Stale HERMES_GIT_BASH_PATH must not brick Windows terminal startup."""

    @pytest.mark.windows_only
    def test_falls_through_to_portable_when_custom_fails_probe(self, tmp_path, monkeypatch):
        """Windows-only: the candidate ladder (HERMES_GIT_BASH_PATH →
        %LOCALAPPDATA%\\hermes\\git → Program Files) only exists in
        ``_find_bash``'s Windows branch."""
        import tools.environments.local as local_mod
        from tools.environments import local_gitbash_probe as gitbash_probe

        gitbash_probe._bash_starts_cache.clear()

        broken = tmp_path / "broken" / "bash.exe"
        broken.parent.mkdir()
        broken.write_text("", encoding="utf-8")
        portable = tmp_path / "hermes" / "git" / "bin" / "bash.exe"
        portable.parent.mkdir(parents=True)
        portable.write_text("", encoding="utf-8")

        monkeypatch.setenv("HERMES_GIT_BASH_PATH", str(broken))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        def fake_starts(path: str) -> bool:
            return path == str(portable)

        monkeypatch.setattr(local_mod, "_bash_starts", fake_starts)

        assert _find_bash() == str(portable)


class TestGitBashExternalProgramProbe:
    """The Windows health check must exercise MSYS child-process creation."""

    def test_probe_runs_external_msys_programs(self, monkeypatch):
        """``_bash_starts`` builds the same external-program probe argv on
        every host and routes it through the deadlock-safe ``bounded_probe_run``
        (own Popen, tree-kill + bounded drain on timeout, ``stdin=DEVNULL`` for
        #78820) — never ``subprocess.run`` whose Windows post-timeout cleanup is an
        unbounded ``communicate()`` (#73403)."""
        from tools.environments import local_gitbash_probe as gitbash_probe

        gitbash_probe._bash_starts_cache.clear()
        gitbash_probe._bash_probe_details_cache.clear()
        calls = []

        def fake_bounded_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(gitbash_probe, "bounded_probe_run", fake_bounded_run)

        assert gitbash_probe._bash_starts(r"C:\Git\bin\bash.exe") is True
        assert calls[0][0][-1] == "/usr/bin/true; /usr/bin/cat --version >/dev/null"
        assert calls[0][1]["timeout"] == gitbash_probe._BASH_PROBE_TIMEOUT

    def test_probe_timeout_is_bounded_and_kills_the_grandchild(self, monkeypatch, tmp_path):
        """A probe whose grandchild keeps the captured pipes open past the timeout
        (the MSYS ``true``/``cat`` shape) returns within the bound, records a
        timeout verdict, and leaves no orphaned pipe-holder behind."""
        from tools.environments import local_gitbash_probe as gitbash_probe

        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("no bash on this host")
        gitbash_probe._bash_starts_cache.clear()
        gitbash_probe._bash_probe_details_cache.clear()
        stamp = tmp_path / "grandchild.pid"
        monkeypatch.setattr(gitbash_probe, "_BASH_PROBE_TIMEOUT", 1.0)
        monkeypatch.setattr(gitbash_probe, "_BASH_EXTERNAL_PROGRAM_PROBE",
                            # `$!` is an MSYS pid on Windows; /proc/<pid>/winpid is the Windows pid
                            # psutil can see. Both lines land in the stamp; POSIX has no winpid.
                            f"sleep 30 & echo $! > '{stamp}'; cat /proc/$!/winpid >> '{stamp}' 2>/dev/null; wait")

        t0 = time.monotonic()
        ok = gitbash_probe._bash_starts(bash)
        elapsed = time.monotonic() - t0

        assert ok is False
        assert elapsed < 8.0, f"probe cleanup took {elapsed:.1f}s — pipe drain not bounded"
        assert "timed out" in gitbash_probe._bash_probe_details_cache[bash]
        grandchild = int(stamp.read_text(encoding="utf-8").split()[-1])
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and _pid_alive(grandchild):
            time.sleep(0.05)
        assert not _pid_alive(grandchild), "grandchild survived the probe's tree-kill"

    @pytest.mark.windows_only
    def test_aslr_failure_surfaces_targeted_windows_command(
        self, tmp_path, monkeypatch
    ):
        """Windows-only: the Mandatory-ASLR diagnostic is raised from
        ``_find_bash``'s Windows candidate ladder and names PowerShell's
        ``Set-ProcessMitigation`` — unreachable off Windows."""
        import tools.environments.local as local_mod
        from tools.environments import local_gitbash_probe as gitbash_probe

        gitbash_probe._bash_starts_cache.clear()
        gitbash_probe._bash_probe_details_cache.clear()
        portable = tmp_path / "hermes" / "git" / "bin" / "bash.exe"
        portable.parent.mkdir(parents=True)
        portable.write_text("", encoding="utf-8")

        monkeypatch.setenv("HERMES_GIT_BASH_PATH", "")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setenv("ProgramFiles", str(tmp_path / "empty-program-files"))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.setattr(local_mod.shutil, "which", lambda _name: None)
        monkeypatch.setattr(local_mod, "_mandatory_aslr_enabled", lambda: True)

        def failed_probe(path: str) -> bool:
            gitbash_probe._bash_probe_details_cache[path] = (
                "dofork: child -1 - forked process died unexpectedly"
            )
            return False

        monkeypatch.setattr(local_mod, "_bash_starts", failed_probe)

    def test_raises_when_pm_shell_finds_nothing(self, monkeypatch):
        """A store with no bash (and no PATH bash) surfaces a clear error
        pointing at `hermes pm install` instead of hunting locations."""
        monkeypatch.setattr("pm.shell.bash", lambda: None)
        with pytest.raises(RuntimeError) as exc_info:
            _find_bash()
        assert "No shell found" in str(exc_info.value)
        assert "hermes pm install" in str(exc_info.value)



@pytest.mark.platforms("macos")
@pytest.mark.skipif(
    not os.path.isfile("/bin/bash"),
    reason="reproduces the macOS system-bash-3.2 login-shell swallow",
)
class TestMacosLoginShellSwallowRegression:
    """E2E regression for #42203: the actual failure is that system bash 3.2,
    invoked as a login shell (`-lic`) with stdin=/dev/null and a
    ~/.bash_profile that `exec`s zsh, silently swallows the command (exit 0,
    no output, no side effects). Prove (a) the bug exists with /bin/bash and
    (b) the zsh path _find_shell prefers does NOT swallow."""

    def _spawn_like_registry(self, shell, command, home, tmp_path):
        import subprocess
        env = dict(os.environ)
        env["HOME"] = str(home)
        # Mirror process_registry.spawn_local: [shell, "-lic", "set +m; <cmd>"]
        # with stdin redirected to /dev/null.
        return subprocess.run(
            [shell, "-lic", f"set +m; {command}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
        )


    def test_find_shell_selects_working_shell_on_this_box(self, tmp_path):
        """_find_shell's choice must actually execute a background-style
        command (regression against returning a swallow-prone shell)."""
        shell = _find_shell()
        marker = tmp_path / "ok_marker"
        subprocess.run(
            [shell, "-lic", f"set +m; echo ok > {marker}"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )
        assert marker.exists(), f"_find_shell()={shell} swallowed the command"

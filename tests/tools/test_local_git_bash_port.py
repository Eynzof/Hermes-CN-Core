"""Tests for the kimix ``bash_tool`` win32 features ported into
``tools/environments/local.py``.

Covers (bash_report.md Section B):
- B.1 ``_encode_startup_script`` — base64+gzip self-decoding one-liner.
- B.2 ``_with_msystem_neutralized`` + ``_MSYSTEM_NEUTRALIZE_PREFIX``.
- B.3 ``_is_git_bash_install`` — ``<root>/cmd/git.exe`` marker probe.
- B.4 git.exe discovery chain — ``_where_git_executables``,
  ``_git_bash_candidate_from_git_path``, ``_git_exec_path``,
  ``_git_install_root_from_exec_path``, ``_git_bash_candidates_from_exec_path``
  plus their wiring into ``_find_bash``.
- B.6 macOS bash candidates — ``_bash_candidates_macos``,
  ``_bash_candidates_system``, ``_git_bash_for_macos`` and the darwin branch
  of ``_find_bash_posix``.
- Wiring: MSYSTEM neutralization inside ``LocalEnvironment._wrap_command``,
  and ``_run_bash`` spawning the resolved ``self._shell_path``.

Path-shape note: ``_is_git_bash_install`` and the discovery chain are Windows
path parsers (backslash split), so the marker-probe tests pass Windows-form
paths and patch ``os.path.isfile`` — this keeps them platform-independent the
same way the bash_fix suite patches ``sys.platform``.
"""

import base64
import gzip
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.environments import local as local_mod
from tools.environments.local import (
    LocalEnvironment,
    _MSYSTEM_NEUTRALIZE_PREFIX,
    _bash_candidates_macos,
    _bash_candidates_system,
    _encode_startup_script,
    _find_bash,
    _find_bash_posix,
    _git_bash_candidate_from_git_path,
    _git_bash_candidates_from_exec_path,
    _git_bash_for_macos,
    _git_exec_path,
    _git_install_root_from_exec_path,
    _is_git_bash_install,
    _where_git_executables,
    _with_msystem_neutralized,
)


class _FakeProc:
    """Minimal Popen stand-in used by the _run_bash wiring tests."""

    def __init__(self):
        self.pid = 12345

    def poll(self):
        return None


def _payload(encoded: str) -> str:
    """Extract the base64 payload from an ``_encode_startup_script`` one-liner."""
    start = encoded.index("'%s' '") + len("'%s' '")
    end = encoded.index("' | base64 -d | gzip -d)\"")
    return encoded[start:end]


# ---------------------------------------------------------------------------
# B.1 — _encode_startup_script
# ---------------------------------------------------------------------------

class TestEncodeStartupScript:
    def test_roundtrip_decodes_to_original(self):
        script = "export FOO=1\nbar() {\n  echo hi\n}\necho $FOO\n"
        encoded = _encode_startup_script(script)
        payload = _payload(encoded)
        assert gzip.decompress(base64.b64decode(payload)).decode("utf-8") == script

    def test_single_line_safe_ascii_payload(self):
        encoded = _encode_startup_script("a\nb\n")
        assert "\n" not in encoded
        assert encoded.startswith('eval "$(printf \'%s\' \'')
        assert encoded.endswith("' | base64 -d | gzip -d)\"")
        payload = _payload(encoded)
        # base64 alphabet — no single quotes/spaces that argv quoting would mangle
        assert "'" not in payload
        assert " " not in payload
        assert payload == payload.strip()

    def test_empty_script(self):
        encoded = _encode_startup_script("")
        payload = _payload(encoded)
        assert gzip.decompress(base64.b64decode(payload)) == b""


# ---------------------------------------------------------------------------
# B.3 — _is_git_bash_install
# ---------------------------------------------------------------------------

class TestIsGitBashInstall:
    def test_bin_layout_true(self, monkeypatch):
        monkeypatch.setattr(
            local_mod.os.path, "isfile", lambda p: p == r"C:\git\cmd\git.exe"
        )
        assert _is_git_bash_install(r"C:\Git\bin\bash.exe") is True

    def test_usr_bin_layout_true(self, monkeypatch):
        monkeypatch.setattr(
            local_mod.os.path, "isfile", lambda p: p == r"C:\git\cmd\git.exe"
        )
        assert _is_git_bash_install(r"C:\Git\usr\bin\bash.exe") is True

    def test_missing_marker_false(self):
        # No cmd\git.exe marker anywhere -> not a Git for Windows install.
        assert _is_git_bash_install(r"C:\Git\bin\bash.exe") is False

    def test_non_bash_path_false(self):
        assert _is_git_bash_install(r"C:\Program Files\PowerShell\7\pwsh.exe") is False

    def test_empty_path_false(self):
        assert _is_git_bash_install("") is False

    def test_drive_anchor_not_drive_relative(self, monkeypatch):
        """The marker probe must be drive-anchored (C:\\...), never the
        drive-relative ``C:...`` form that resolves against the per-drive CWD."""
        seen = []

        def fake_isfile(p):
            seen.append(p)
            return False

        monkeypatch.setattr(local_mod.os.path, "isfile", fake_isfile)
        assert _is_git_bash_install(r"C:\Git\bin\bash.exe") is False
        assert seen and seen[0].startswith("C:\\"), seen


# ---------------------------------------------------------------------------
# B.2 — _with_msystem_neutralized
# ---------------------------------------------------------------------------

class TestWithMsystemNeutralized:
    def test_prefix_constant(self):
        assert _MSYSTEM_NEUTRALIZE_PREFIX == "export MSYSTEM=; "

    def test_win32_git_bash_gets_prefix(self, monkeypatch):
        monkeypatch.setattr(local_mod.sys, "platform", "win32")
        monkeypatch.setattr(
            local_mod.os.path, "isfile", lambda p: p == r"C:\git\cmd\git.exe"
        )
        result = _with_msystem_neutralized("echo hi", r"C:\Git\bin\bash.exe")
        assert result == "export MSYSTEM=; echo hi"

    def test_non_git_bash_unchanged(self, monkeypatch):
        monkeypatch.setattr(local_mod.sys, "platform", "win32")
        monkeypatch.setattr(local_mod.os.path, "isfile", lambda p: False)
        # Real MSYS2 bash: no cmd\git.exe marker -> MSYSTEM left alone.
        assert (
            _with_msystem_neutralized("echo hi", r"C:\MSYS2\usr\bin\bash.exe")
            == "echo hi"
        )

    def test_non_win32_unchanged_even_for_git_bash(self, monkeypatch):
        monkeypatch.setattr(local_mod.sys, "platform", "linux")
        monkeypatch.setattr(local_mod.os.path, "isfile", lambda p: True)
        assert _with_msystem_neutralized("echo hi", r"C:\Git\bin\bash.exe") == "echo hi"

    def test_none_bash_path_unchanged(self, monkeypatch):
        monkeypatch.setattr(local_mod.sys, "platform", "win32")
        assert _with_msystem_neutralized("echo hi", None) == "echo hi"

    def test_empty_command_still_gets_prefix(self, monkeypatch):
        monkeypatch.setattr(local_mod.sys, "platform", "win32")
        monkeypatch.setattr(
            local_mod.os.path, "isfile", lambda p: p == r"C:\git\cmd\git.exe"
        )
        assert _with_msystem_neutralized("", r"C:\Git\bin\bash.exe") == "export MSYSTEM=; "


# ---------------------------------------------------------------------------
# B.4 — git.exe discovery chain (pure path derivation)
# ---------------------------------------------------------------------------

class TestGitExeDiscoveryChain:
    def test_git_bash_candidate_from_git_path(self):
        candidate = _git_bash_candidate_from_git_path(r"C:\Program Files\Git\cmd\git.exe")
        assert str(candidate) == r"C:\Program Files\Git\bin\bash.exe"

    def test_git_install_root_from_exec_path(self):
        assert (
            _git_install_root_from_exec_path(
                r"C:\Program Files\Git\mingw64\libexec\git-core"
            )
            == r"C:\Program Files\Git"
        )
        assert (
            _git_install_root_from_exec_path(
                r"C:\Program Files\Git\mingw32\libexec\git-core"
            )
            == r"C:\Program Files\Git"
        )

    def test_git_install_root_from_exec_path_no_mingw(self):
        assert _git_install_root_from_exec_path(r"C:\Git\libexec\git-core") is None

    def test_git_bash_candidates_from_exec_path_mingw(self):
        paths = _git_bash_candidates_from_exec_path(
            r"C:\Program Files\Git\mingw64\libexec\git-core"
        )
        assert [str(p) for p in paths] == [r"C:\Program Files\Git\bin\bash.exe"]

    def test_git_bash_candidates_from_exec_path_plain(self):
        paths = _git_bash_candidates_from_exec_path(r"C:\Git\libexec\git-core")
        assert [str(p) for p in paths] == [r"C:\Git\bin\bash.exe"]


class TestWhereGitExecutables:
    def test_returns_where_lines(self, monkeypatch):
        seen = []

        def fake_run(argv, **kwargs):
            seen.append((argv, kwargs))
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    "C:\\Program Files\\Git\\cmd\\git.exe\n"
                    "D:\\scoop\\apps\\git\\cmd\\git.exe\n\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(local_mod.subprocess, "run", fake_run)
        assert _where_git_executables() == [
            r"C:\Program Files\Git\cmd\git.exe",
            r"D:\scoop\apps\git\cmd\git.exe",
        ]
        assert seen[0][0] == ["where.exe", "git"]

    def test_nonzero_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            local_mod.subprocess,
            "run",
            lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, stdout="", stderr=""),
        )
        assert _where_git_executables() == []

    def test_oserror_returns_empty(self, monkeypatch):
        def raise_oserror(argv, **kwargs):
            raise OSError("where.exe not found")

        monkeypatch.setattr(local_mod.subprocess, "run", raise_oserror)
        assert _where_git_executables() == []


class TestGitExecPath:
    def test_returns_first_nonempty_line(self, monkeypatch):
        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    "C:\\Program Files\\Git\\mingw64\\libexec\\git-core\n"
                    "C:\\Program Files\\Git\\mingw64\\libexec\\git-core\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(local_mod.subprocess, "run", fake_run)
        assert (
            _git_exec_path(r"C:\Program Files\Git\cmd\git.exe")
            == r"C:\Program Files\Git\mingw64\libexec\git-core"
        )

    def test_timeout_returns_none(self, monkeypatch):
        def raise_timeout(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 5)

        monkeypatch.setattr(local_mod.subprocess, "run", raise_timeout)
        assert _git_exec_path(r"C:\Git\cmd\git.exe") is None

    def test_nonzero_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            local_mod.subprocess,
            "run",
            lambda argv, **kwargs: subprocess.CompletedProcess(argv, 128, stdout="", stderr=""),
        )
        assert _git_exec_path(r"C:\Git\cmd\git.exe") is None

    def test_oserror_returns_none(self, monkeypatch):
        def raise_oserror(argv, **kwargs):
            raise OSError("git cannot run")

        monkeypatch.setattr(local_mod.subprocess, "run", raise_oserror)
        assert _git_exec_path(r"C:\Git\cmd\git.exe") is None


# ---------------------------------------------------------------------------
# B.4 wiring — _find_bash uses the git.exe discovery chain
# ---------------------------------------------------------------------------

class TestFindBashUsesGitExeChain:
    def _clear_win_env(self, monkeypatch, tmp_path):
        monkeypatch.setattr(local_mod, "_IS_WINDOWS", True)
        monkeypatch.setenv("HERMES_GIT_BASH_PATH", "")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no-local-appdata"))
        monkeypatch.setenv("ProgramFiles", str(tmp_path / "no-program-files"))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.setattr(local_mod.shutil, "which", lambda _name: None)

    def test_where_git_chain_finds_bash(self, tmp_path, monkeypatch):
        self._clear_win_env(monkeypatch, tmp_path)
        install = tmp_path / "Git"
        (install / "cmd").mkdir(parents=True)
        (install / "cmd" / "git.exe").write_text("", encoding="utf-8")
        (install / "bin").mkdir()
        bash_exe = install / "bin" / "bash.exe"
        bash_exe.write_text("", encoding="utf-8")

        monkeypatch.setattr(
            local_mod, "_where_git_executables", lambda: [str(install / "cmd" / "git.exe")]
        )
        monkeypatch.setattr(local_mod, "_git_exec_path", lambda git_path: None)
        monkeypatch.setattr(
            local_mod, "_bash_starts", lambda p: p == str(bash_exe)
        )

        assert _find_bash() == str(bash_exe)

    def test_exec_path_chain_finds_bash(self, tmp_path, monkeypatch):
        """When the where.exe derivation (../bin/bash.exe) misses but
        ``git --exec-path`` resolves a mingw64 install root, the exec-path
        candidate must be found."""
        self._clear_win_env(monkeypatch, tmp_path)
        install = tmp_path / "Git"
        (install / "mingw64" / "libexec" / "git-core").mkdir(parents=True)
        (install / "bin").mkdir()
        bash_exe = install / "bin" / "bash.exe"
        bash_exe.write_text("", encoding="utf-8")
        # git.exe lives in an odd layout whose ../bin/bash.exe does NOT exist.
        odd_git = install / "odd" / "cmd" / "git.exe"

        monkeypatch.setattr(local_mod, "_where_git_executables", lambda: [str(odd_git)])
        monkeypatch.setattr(
            local_mod,
            "_git_exec_path",
            lambda git_path: str(install / "mingw64" / "libexec" / "git-core"),
        )
        monkeypatch.setattr(
            local_mod, "_bash_starts", lambda p: p == str(bash_exe)
        )

        assert _find_bash() == str(bash_exe)

    def test_no_candidates_raises_helpful_error(self, tmp_path, monkeypatch):
        self._clear_win_env(monkeypatch, tmp_path)
        monkeypatch.setattr(local_mod, "_where_git_executables", lambda: [])
        monkeypatch.setattr(local_mod, "_bash_starts", lambda p: False)
        monkeypatch.setattr(local_mod, "_mandatory_aslr_enabled", lambda: False)

        with pytest.raises(RuntimeError, match="Git Bash is not found"):
            _find_bash()


# ---------------------------------------------------------------------------
# B.6 — macOS bash candidates
# ---------------------------------------------------------------------------

class TestMacosBashCandidates:
    def test_candidates_macos(self):
        assert _bash_candidates_macos() == [
            Path("/opt/homebrew/bin/bash"),
            Path("/usr/local/bin/bash"),
            Path("/opt/local/bin/bash"),
        ]

    def test_candidates_system(self):
        assert _bash_candidates_system() == [Path("/bin/bash"), Path("/usr/bin/bash")]

    def test_git_bash_for_macos_finds_usr_bin_bash(self, tmp_path, monkeypatch):
        git_root = tmp_path / "git"
        (git_root / "usr" / "bin").mkdir(parents=True)
        bash = git_root / "usr" / "bin" / "bash"
        bash.write_text("", encoding="utf-8")
        bash.chmod(0o755)
        monkeypatch.setattr(
            local_mod.shutil,
            "which",
            lambda name: str(git_root / "bin" / "git") if name == "git" else None,
        )
        assert _git_bash_for_macos() == str(bash.resolve())

    def test_git_bash_for_macos_none_without_git(self, monkeypatch):
        monkeypatch.setattr(local_mod.shutil, "which", lambda name: None)
        assert _git_bash_for_macos() is None


class TestFindBashPosixMacosPreference:
    def test_prefers_homebrew_bash_on_darwin(self, tmp_path, monkeypatch):
        homebrew = tmp_path / "homebrew" / "bin" / "bash"
        homebrew.parent.mkdir(parents=True)
        homebrew.write_text("", encoding="utf-8")
        homebrew.chmod(0o755)
        monkeypatch.setattr(local_mod.sys, "platform", "darwin")
        monkeypatch.setattr(local_mod, "_bash_candidates_macos", lambda: [homebrew])
        monkeypatch.setattr(local_mod.shutil, "which", lambda name: None)
        assert _find_bash_posix() == str(homebrew.resolve())

    def test_falls_back_to_git_bash_for_macos(self, tmp_path, monkeypatch):
        monkeypatch.setattr(local_mod.sys, "platform", "darwin")
        monkeypatch.setattr(
            local_mod,
            "_bash_candidates_macos",
            lambda: [Path(tmp_path / "missing" / "bash")],
        )
        monkeypatch.setattr(
            local_mod, "_git_bash_for_macos", lambda: "/usr/local/git/bin/bash"
        )
        assert _find_bash_posix() == "/usr/local/git/bin/bash"

    def test_linux_order_unchanged(self, monkeypatch):
        monkeypatch.setattr(local_mod.sys, "platform", "linux")
        with patch("shutil.which", return_value="/usr/bin/bash"):
            assert _find_bash_posix() == "/usr/bin/bash"

    def test_darwin_misses_everything_falls_to_common(self, tmp_path, monkeypatch):
        monkeypatch.setattr(local_mod.sys, "platform", "darwin")
        monkeypatch.setattr(
            local_mod,
            "_bash_candidates_macos",
            lambda: [Path(tmp_path / "missing" / "bash")],
        )
        monkeypatch.setattr(local_mod, "_git_bash_for_macos", lambda: None)
        monkeypatch.setattr(local_mod.os.path, "isfile", lambda p: False)
        monkeypatch.setattr(local_mod.shutil, "which", lambda name: None)
        monkeypatch.delenv("SHELL", raising=False)
        assert _find_bash_posix() == "/bin/sh"


# ---------------------------------------------------------------------------
# Wiring — _wrap_command applies MSYSTEM neutralization on win32
# ---------------------------------------------------------------------------

class TestWrapCommandMsystemNeutralized:
    def _make_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(local_mod, "_IS_WINDOWS", True)
        monkeypatch.setattr(local_mod.sys, "platform", "win32")
        with patch.object(
            LocalEnvironment, "init_session", autospec=True, return_value=None
        ):
            env = LocalEnvironment(cwd=str(tmp_path), timeout=10)
        env._shell_type = "bash"
        env._shell_path = r"C:\Git\bin\bash.exe"
        return env

    def test_win32_git_bash_embeds_msystem_prefix(self, tmp_path, monkeypatch):
        env = self._make_env(tmp_path, monkeypatch)
        seen = {}

        def fake_neutralize(cmd, bash_path):
            seen["cmd"] = cmd
            seen["bash"] = bash_path
            return "export MSYSTEM=; " + cmd

        monkeypatch.setattr(local_mod, "_with_msystem_neutralized", fake_neutralize)
        wrapped = env._wrap_command("echo hi", str(tmp_path))

        # The prefix lands inside the eval'd region of the base wrapper.
        assert "export MSYSTEM=; echo hi" in wrapped
        assert seen["bash"] == r"C:\Git\bin\bash.exe"
        assert seen["cmd"] == "echo hi"

    def test_non_win32_skips_msystem(self, tmp_path, monkeypatch):
        env = self._make_env(tmp_path, monkeypatch)
        monkeypatch.setattr(local_mod.sys, "platform", "linux")
        called = []
        monkeypatch.setattr(
            local_mod,
            "_with_msystem_neutralized",
            lambda cmd, bash: called.append(cmd) or cmd,
        )
        env._wrap_command("echo hi", str(tmp_path))
        assert called == []

    def test_powershell_shell_skips_msystem(self, tmp_path, monkeypatch):
        env = self._make_env(tmp_path, monkeypatch)
        env._shell_type = "powershell"
        called = []
        monkeypatch.setattr(
            local_mod,
            "_with_msystem_neutralized",
            lambda cmd, bash: called.append(cmd) or cmd,
        )
        with patch.object(
            LocalEnvironment,
            "_wrap_command_powershell",
            return_value="<pwsh wrapper>",
        ):
            wrapped = env._wrap_command("echo hi", str(tmp_path))
        assert wrapped == "<pwsh wrapper>"
        assert called == []

    def test_real_neutralize_guards_per_command(self, tmp_path, monkeypatch):
        """Integration: with a real Git Bash marker on disk the REAL
        _with_msystem_neutralized produces the prefix; without the marker it
        is a no-op."""
        install = tmp_path / "Git"
        (install / "cmd").mkdir(parents=True)
        (install / "cmd" / "git.exe").write_text("", encoding="utf-8")
        (install / "bin").mkdir()

        env = self._make_env(tmp_path, monkeypatch)
        env._shell_path = str(install / "bin" / "bash.exe")
        wrapped = env._wrap_command("echo hi", str(tmp_path))
        assert "export MSYSTEM=; echo hi" in wrapped

        env2 = self._make_env(tmp_path, monkeypatch)
        env2._shell_path = str(tmp_path / "plain" / "bin" / "bash.exe")
        wrapped2 = env2._wrap_command("echo hi", str(tmp_path))
        assert "export MSYSTEM=; " not in wrapped2


# ---------------------------------------------------------------------------
# Wiring — _run_bash spawns the resolved self._shell_path (git.exe discovery
# must drive execution, not just init discovery)
# ---------------------------------------------------------------------------

class TestRunBashUsesShellPath:
    def _run(self, monkeypatch, shell_path="/resolved/git/bash.exe"):
        with patch.object(
            LocalEnvironment, "init_session", autospec=True, return_value=None
        ):
            env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
        env._shell_type = "bash"
        env._shell_path = shell_path
        monkeypatch.setattr(local_mod, "_find_bash_posix", lambda: "/evil/path/bash")
        monkeypatch.setattr(local_mod, "_prepare_bash_cmd", lambda c: c)
        monkeypatch.setattr(local_mod, "_resolve_shell_init_files", lambda: [])
        monkeypatch.setattr(local_mod, "_resolve_safe_cwd", lambda c: c)
        monkeypatch.setattr(local_mod, "_make_run_env", lambda env: {})
        with patch.object(
            local_mod.subprocess, "Popen", return_value=_FakeProc()
        ) as popen_mock:
            env._run_bash("echo hi")
        return popen_mock

    def test_spawns_resolved_shell_path_not_find_bash_posix(self, monkeypatch):
        popen = self._run(monkeypatch)
        args = popen.call_args.args[0]
        assert args[0] == "/resolved/git/bash.exe"
        assert args[1] == "-c"
        assert args[2] == "echo hi"

    def test_login_probe_uses_resolved_shell_path(self, monkeypatch):
        with patch.object(
            LocalEnvironment, "init_session", autospec=True, return_value=None
        ):
            env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
        env._shell_type = "bash"
        env._shell_path = "/resolved/git/bash.exe"
        monkeypatch.setattr(local_mod, "_find_bash_posix", lambda: "/evil/path/bash")
        monkeypatch.setattr(local_mod, "_prepare_bash_cmd", lambda c: c)
        monkeypatch.setattr(local_mod, "_resolve_shell_init_files", lambda: [])
        monkeypatch.setattr(local_mod, "_resolve_safe_cwd", lambda c: c)
        monkeypatch.setattr(local_mod, "_make_run_env", lambda env: {})
        with patch.object(
            local_mod.subprocess, "Popen", return_value=_FakeProc()
        ) as popen_mock:
            env._run_bash("echo hi", login=True)
        args = popen_mock.call_args.args[0]
        assert args[0] == "/resolved/git/bash.exe"
        assert args[1] == "-l"
        assert "echo hi" in args[args.index("-c") + 1]

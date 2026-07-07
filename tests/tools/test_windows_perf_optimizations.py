"""Windows-specific performance optimizations (P-042, plan 10).

Covers the four deliverables from the "Windows-Specific Optimizations" plan,
plus the ``FILE_ATTRIBUTE_TEMPORARY`` helper:

* ``test_registry_cache_ttl``          — registry PATH-refresh caching (#1)
* ``test_persistent_powershell_session`` — PowerShell session reuse (#2)
* ``test_cmd_fallback``                — cmd.exe fast-path classifier/routing (#3)
* ``test_crc_verification``            — CRC-32 post-write integrity (#4)
* ``TestMarkAsTemporary``              — FILE_ATTRIBUTE_TEMPORARY helper (#5)

The registry-cache, classifier, resolver-gating and CRC tests are behaviour
contracts that mock the platform / force the in-process path, so they run on
any host (including the Linux CI slices).  The live PowerShell / cmd.exe /
Win32-attribute tests ``skipif`` when PowerShell or Windows is unavailable.
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest


# ==========================================================================
# #1 — Registry PATH refresh caching  (test_registry_cache_ttl)
# ==========================================================================


def test_registry_cache_ttl(monkeypatch):
    """The registry refresh is cached: an unchanged Environment-key signature
    collapses a burst of calls into one real read, and the belt-and-suspenders
    max-age forces a re-read once the window elapses even if nothing changed.

    Cross-platform: the platform, clock, signature and real read are all mocked,
    so this asserts the caching *contract* without touching a real registry.
    """
    import tools.environments.windows_env as we

    we._reset_registry_env_cache()
    reads = {"n": 0}
    clock = {"t": 1000.0}

    monkeypatch.setattr(we.sys, "platform", "win32")
    monkeypatch.setattr(
        we, "_do_refresh_env_from_registry", lambda: reads.__setitem__("n", reads["n"] + 1)
    )
    # Signature never changes — only the TTL can force a re-read here.
    monkeypatch.setattr(we, "_registry_env_signature", lambda: ("sigA", "sigB"))
    monkeypatch.setattr(we.time, "monotonic", lambda: clock["t"])

    try:
        # Cold call → one real read.
        we.refresh_env_from_registry()
        assert reads["n"] == 1

        # A burst within the TTL with an unchanged signature → all cache hits.
        for _ in range(25):
            we.refresh_env_from_registry()
        assert reads["n"] == 1, "unchanged signature within TTL must not re-read"

        # Advance the clock past the max-age → forced re-read (staleness bound)
        # even though the signature is identical.
        clock["t"] += we._REGISTRY_ENV_MAX_AGE + 1.0
        we.refresh_env_from_registry()
        assert reads["n"] == 2, "max-age must force a refresh even if unchanged"

        # Back within the (new) TTL window → cache hits again.
        for _ in range(5):
            we.refresh_env_from_registry()
        assert reads["n"] == 2

        # force=True always reads regardless of signature/TTL.
        we.refresh_env_from_registry(force=True)
        assert reads["n"] == 3
    finally:
        we._reset_registry_env_cache()


# ==========================================================================
# #2 — PowerShell session reuse  (test_persistent_powershell_session)
# ==========================================================================

_PS_PATH = None
if sys.platform == "win32":
    _PS_PATH = shutil.which("pwsh.exe") or shutil.which("powershell.exe")

_live_ps = pytest.mark.skipif(
    not _PS_PATH, reason="requires Windows PowerShell / pwsh"
)


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


@_live_ps
def test_persistent_powershell_session(tmp_path, monkeypatch):
    """One long-lived interpreter is reused across ``execute`` calls: the same
    session object serves every command, shell state (variables, cwd) persists
    between them, and ``cleanup`` tears it down."""
    monkeypatch.setenv("HERMES_PWSH_SESSION_REUSE", "1")
    monkeypatch.delenv("HERMES_CMD_FAST_PATH", raising=False)
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    try:
        assert env._pwsh_session_reuse is True

        r1 = env.execute("Write-Output first")
        assert r1["returncode"] == 0 and "first" in r1["output"]
        assert env._pwsh_session is not None and env._pwsh_session.is_alive()
        session_id = id(env._pwsh_session)

        # Second call reuses the SAME interpreter (no fresh spawn).
        r2 = env.execute("Write-Output second")
        assert "second" in r2["output"]
        assert id(env._pwsh_session) == session_id

        # Shell state persists across the two reused calls.
        env.execute("$reuse_probe = 20260706")
        r3 = env.execute('Write-Output "probe=$reuse_probe"')
        assert _norm(r3["output"]) == "probe=20260706"

        session = env._pwsh_session
        env.cleanup()
        assert env._pwsh_session is None
        assert not session.is_alive()
    finally:
        env.cleanup()


def test_persistent_powershell_session_pure():
    """Cross-platform reuse primitives: batching combiner + result semantics.

    Guards the reuse building blocks on every host (the live test above only
    runs on Windows)."""
    from tools.environments.powershell_session import (
        PSResult,
        combine_commands,
    )

    # Batching many commands into one round-trip drops blanks, keeps order.
    assert combine_commands(["a", "", "  ", "b", "c"]) == "a; b; c"
    # Only a clean, non-interrupted zero exit counts as success.
    assert PSResult("ok", 0).success is True
    assert PSResult("", 1).success is False
    assert PSResult("", 124, timed_out=True).success is False
    assert PSResult("", 0, session_died=True).success is False


# ==========================================================================
# #3 — cmd.exe fast path  (test_cmd_fallback)
# ==========================================================================


def test_cmd_fallback():
    """Simple builtins are classified for the cmd.exe fast path, while anything
    with PowerShell-divergent syntax (pipes, redirection, quoting, globbing,
    cwd/env mutation) is rejected so only cmd-identical commands ever route.

    Cross-platform: pure classifier + strict-gate contract."""
    from tools.environments.local import is_simple_command, _cmd_fast_path_eligible

    # Coarse classifier — recognises bare builtins by their leading verb.
    for ok in ("dir", "echo hello", "  TYPE a.txt", "copy a b", "whoami", "ver"):
        assert is_simple_command(ok), ok
    for no in ("", "git status", "Get-ChildItem", "python x.py", "node -v"):
        assert not is_simple_command(no), no

    # Strict executor gate — cmd-safe simple commands pass.
    for ok in ("dir foo", "echo hello world", "type a.txt", "whoami", "del a.tmp"):
        assert _cmd_fast_path_eligible(ok), ok

    # ...and everything that could behave differently under cmd.exe is rejected
    # (falls through to the unchanged PowerShell path).
    for no in (
        "echo hi | findstr x",     # pipe
        "echo hi > out.txt",       # redirection
        "echo %PATH%",             # cmd var expansion
        "dir *.py",                # glob
        'type "a b.txt"',          # quoting
        "del a && del b",          # chaining
        "cd subdir",               # cwd mutation
        "set FOO=bar",             # env mutation
        "echo `whoami`",           # backtick
        "echo $(whoami)",          # subexpr
    ):
        assert not _cmd_fast_path_eligible(no), no


def test_cmd_fallback_resolver_gating(monkeypatch):
    """The opt-in resolver is Windows+PowerShell only, env overrides config, and
    it defaults OFF.  Cross-platform via a mocked ``_IS_WINDOWS``."""
    import tools.environments.local as local
    import hermes_cli.config as cfg

    # Off when not Windows, or for a non-PowerShell shell.
    monkeypatch.setattr(local, "_IS_WINDOWS", False)
    assert local._resolve_cmd_fast_path("powershell") is False
    monkeypatch.setattr(local, "_IS_WINDOWS", True)
    assert local._resolve_cmd_fast_path("bash") is False

    # Env var enables (and wins over config), for both PS flavours.
    monkeypatch.setenv("HERMES_CMD_FAST_PATH", "1")
    assert local._resolve_cmd_fast_path("powershell") is True
    assert local._resolve_cmd_fast_path("pwsh") is True

    # Explicit env "0" disables even when config says true.
    monkeypatch.setenv("HERMES_CMD_FAST_PATH", "0")
    monkeypatch.setattr(cfg, "load_config", lambda: {"terminal": {"cmd_fast_path": True}})
    assert local._resolve_cmd_fast_path("powershell") is False

    # No env → config drives it.
    monkeypatch.delenv("HERMES_CMD_FAST_PATH", raising=False)
    monkeypatch.setattr(cfg, "load_config", lambda: {"terminal": {"cmd_fast_path": True}})
    assert local._resolve_cmd_fast_path("powershell") is True
    monkeypatch.setattr(cfg, "load_config", lambda: {"terminal": {}})
    assert local._resolve_cmd_fast_path("powershell") is False


_HAS_PS = sys.platform == "win32" and _PS_PATH
_live_win = pytest.mark.skipif(not _HAS_PS, reason="requires Windows PowerShell / pwsh")


@_live_win
def test_cmd_fallback_routes_live(tmp_path, monkeypatch):
    """With the opt-in on, an eligible command is served by ``_execute_via_cmd``
    (correct output + exit code); an ineligible one bypasses it and still runs
    correctly on the PowerShell spawn path."""
    monkeypatch.setenv("HERMES_CMD_FAST_PATH", "1")
    monkeypatch.delenv("HERMES_PWSH_SESSION_REUSE", raising=False)
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    try:
        assert env._cmd_fast_path is True

        calls: list[str] = []
        real = env._execute_via_cmd

        def _spy(command, cwd, *, timeout):
            calls.append(command)
            return real(command, cwd, timeout=timeout)

        monkeypatch.setattr(env, "_execute_via_cmd", _spy)

        # Eligible → routed via cmd.exe, correct output + rc.
        r = env.execute("echo routed_via_cmd")
        assert r["returncode"] == 0
        assert "routed_via_cmd" in r["output"]
        assert calls == ["echo routed_via_cmd"]

        # Nonzero exit propagates (cmd ``dir`` on a missing entry → rc 1).
        r = env.execute("dir __surely_missing_entry__")
        assert r["returncode"] != 0

        # Ineligible (PowerShell) command is NOT routed to cmd; spawn handles it.
        calls.clear()
        r = env.execute("Write-Output ps_only")
        assert r["returncode"] == 0 and "ps_only" in r["output"]
        assert calls == [], "ineligible command must not hit the cmd path"

        # cwd is preserved across eligible commands (they can't mutate it).
        assert os.path.normcase(os.path.normpath(env.cwd)) == os.path.normcase(
            os.path.normpath(str(tmp_path))
        )
    finally:
        env.cleanup()


# ==========================================================================
# #4 — CRC-32 post-write integrity  (test_crc_verification)
# ==========================================================================


@pytest.fixture()
def win_local_ops(monkeypatch):
    """A ShellFileOperations forced onto the in-process Windows disk path,
    regardless of host OS (so CRC verification is exercised on Linux CI too)."""
    import tools.file_operations as fops_mod
    from tools.file_operations import ShellFileOperations
    from tools.environments.local import LocalEnvironment

    monkeypatch.setattr(fops_mod, "_IS_WINDOWS", True)
    ops = ShellFileOperations(LocalEnvironment())
    assert ops._use_inproc_io() is True
    return ops


def test_crc_verification(win_local_ops, tmp_path, monkeypatch):
    """A CRC/size mismatch on the just-written temp aborts the atomic write
    BEFORE the rename, so a corrupt write can never clobber the good original;
    a clean write still succeeds and the disabled toggle skips the check."""
    import tools.file_operations as fops_mod

    # ---- happy path: real content verifies and lands ----
    good = tmp_path / "good.txt"
    res = win_local_ops.write_file(str(good), "integrity ok")
    assert res.error is None
    assert good.read_bytes() == b"integrity ok"

    # ---- corrupt CRC: mismatch aborts, original file untouched ----
    target = tmp_path / "keep.txt"
    target.write_text("ORIGINAL", encoding="utf-8")

    monkeypatch.setattr(fops_mod, "_WRITE_VERIFY_CRC", True)
    monkeypatch.setattr(
        fops_mod, "_crc32_of_file", lambda path, chunk_size=1 << 20: (0xDEADBEEF, 4)
    )
    r = win_local_ops._local_atomic_write(str(target), "NEWDATA!")
    assert r.exit_code == 1
    assert "verification failed" in r.stdout.lower()
    # Corrupt swap was refused → the original bytes survive intact.
    assert target.read_text(encoding="utf-8") == "ORIGINAL"
    # No leftover temp files beside the target.
    assert not any(p.name.startswith(".hermes-tmp.") for p in tmp_path.iterdir())

    # ---- size mismatch is also caught ----
    def _wrong_size(path, chunk_size=1 << 20):
        import zlib

        data = b"NEWDATA!"
        return zlib.crc32(data) & 0xFFFFFFFF, len(data) + 1  # correct crc, wrong size

    monkeypatch.setattr(fops_mod, "_crc32_of_file", _wrong_size)
    r = win_local_ops._local_atomic_write(str(target), "NEWDATA!")
    assert r.exit_code == 1
    assert "verification failed" in r.stdout.lower()
    assert target.read_text(encoding="utf-8") == "ORIGINAL"

    # ---- toggle OFF: the (still-lying) CRC helper is never consulted ----
    monkeypatch.setattr(fops_mod, "_WRITE_VERIFY_CRC", False)
    r = win_local_ops._local_atomic_write(str(target), "NEWDATA!")
    assert r.exit_code == 0
    assert target.read_text(encoding="utf-8") == "NEWDATA!"


def test_crc_verification_resolver(monkeypatch):
    """``HERMES_WRITE_VERIFY_CRC`` toggles the default; unset defaults ON."""
    import tools.file_operations as fops_mod

    monkeypatch.delenv("HERMES_WRITE_VERIFY_CRC", raising=False)
    assert fops_mod._resolve_write_verify_crc() is True
    for off in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("HERMES_WRITE_VERIFY_CRC", off)
        assert fops_mod._resolve_write_verify_crc() is False
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("HERMES_WRITE_VERIFY_CRC", on)
        assert fops_mod._resolve_write_verify_crc() is True


def test_crc32_helper_matches_zlib(tmp_path):
    """``_crc32_of_file`` streams the same digest as an in-memory ``zlib.crc32``."""
    import zlib
    from tools.file_operations import _crc32_of_file

    data = ("héllo 世界\n".encode("utf-8")) * 500  # multi-chunk-ish, non-ASCII
    p = tmp_path / "blob.bin"
    p.write_bytes(data)
    crc, size = _crc32_of_file(str(p), chunk_size=64)
    assert crc == (zlib.crc32(data) & 0xFFFFFFFF)
    assert size == len(data)


# ==========================================================================
# #5 — FILE_ATTRIBUTE_TEMPORARY helper
# ==========================================================================


class TestMarkAsTemporary:
    def test_noop_off_windows(self, monkeypatch, tmp_path):
        """Off Windows the helpers are inert no-ops returning False."""
        import tools.environments.windows_env as we

        monkeypatch.setattr(we.sys, "platform", "linux")
        p = tmp_path / "f.txt"
        p.write_text("x", encoding="utf-8")
        assert we.mark_as_temporary(str(p)) is False
        assert we.set_file_temporary(str(p), False) is False

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 attribute API")
    def test_mark_and_clear_live(self, tmp_path):
        """On Windows the temporary bit can be set and cleared, preserving the
        file's other attributes and its contents."""
        import ctypes
        from tools.environments.windows_env import (
            _FILE_ATTRIBUTE_TEMPORARY,
            mark_as_temporary,
            set_file_temporary,
        )

        p = tmp_path / "scratch.txt"
        p.write_text("data", encoding="utf-8")

        assert mark_as_temporary(str(p)) is True
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
        assert attrs & _FILE_ATTRIBUTE_TEMPORARY

        assert set_file_temporary(str(p), False) is True
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
        assert not (attrs & _FILE_ATTRIBUTE_TEMPORARY)
        # Contents untouched by attribute juggling.
        assert p.read_text(encoding="utf-8") == "data"

    def test_missing_file_when_clearing_returns_false(self, monkeypatch, tmp_path):
        """Clearing the bit on a non-existent path is a graceful False."""
        import tools.environments.windows_env as we

        if sys.platform != "win32":
            monkeypatch.setattr(we.sys, "platform", "linux")
        assert we.set_file_temporary(str(tmp_path / "nope.txt"), False) is False


def test_mark_temp_files_write_path(tmp_path, monkeypatch):
    """The opt-in ``_MARK_TEMP_FILES`` wiring in ``_local_atomic_write`` still
    writes correctly, and on Windows the final (permanent) file is NOT left
    marked temporary — the bit is cleared before the rename.  Cross-platform:
    forces the in-process path; off Windows the attribute calls are inert."""
    import tools.file_operations as fops_mod
    from tools.file_operations import ShellFileOperations
    from tools.environments.local import LocalEnvironment

    monkeypatch.setattr(fops_mod, "_IS_WINDOWS", True)
    monkeypatch.setattr(fops_mod, "_MARK_TEMP_FILES", True)
    ops = ShellFileOperations(LocalEnvironment())
    assert ops._use_inproc_io() is True

    p = tmp_path / "marked.txt"
    res = ops.write_file(str(p), "temp-hint content")
    assert res.error is None
    assert p.read_text(encoding="utf-8") == "temp-hint content"
    # No leftover staging temp files.
    assert not any(x.name.startswith(".hermes-tmp.") for x in tmp_path.iterdir())

    if sys.platform == "win32":
        import ctypes
        from tools.environments.windows_env import _FILE_ATTRIBUTE_TEMPORARY

        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
        assert attrs != 0xFFFFFFFF
        assert not (attrs & _FILE_ATTRIBUTE_TEMPORARY), (
            "the permanent file must not keep the temporary hint"
        )

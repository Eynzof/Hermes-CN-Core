"""[CN-fork] P-033 — in-process disk I/O for the local Windows backend.

On Windows the fork forces Windows PowerShell 5.1 as the only shell, which has
none of the POSIX tools (``wc``/``sed``/``head``/``mktemp``/``cat``) that
``ShellFileOperations`` shelled out to.  That made ``read_file`` unusable
(issue #53) and made ``write_file`` silently report success while writing
nothing (issue #54).  These tests force the in-process path (``_IS_WINDOWS``
monkeypatched True + a real ``LocalEnvironment``) so the Linux/macOS CI runner
exercises exactly the code that runs on a Windows desktop, and assert the two
behaviours that were broken: reads work, and writes actually land on disk with
a truthful byte count.
"""

import pytest

import tools.file_operations as fops_mod
from tools.file_operations import ExecuteResult, ShellFileOperations
from tools.environments.local import LocalEnvironment


@pytest.fixture()
def win_local_ops(monkeypatch):
    """A ShellFileOperations wired to a local backend, forced onto the
    in-process Windows disk path regardless of the host OS."""
    monkeypatch.setattr(fops_mod, "_IS_WINDOWS", True)
    ops = ShellFileOperations(LocalEnvironment())
    assert ops._use_inproc_io() is True
    return ops


class TestWindowsInProcessWrite:
    def test_write_actually_creates_file_with_real_size(self, win_local_ops, tmp_path):
        """#54: the file must really exist on disk, and bytes_written must be
        the real on-disk size — not a fabricated len(content)."""
        p = tmp_path / "note.txt"
        res = win_local_ops.write_file(str(p), "hello")
        assert res.error is None
        assert p.exists(), "write_file reported success but wrote no file (#54)"
        assert p.read_bytes() == b"hello"
        assert res.bytes_written == 5

    def test_overwrite_replaces_content(self, win_local_ops, tmp_path):
        p = tmp_path / "ow.txt"
        p.write_text("old-old-old", encoding="utf-8")
        res = win_local_ops.write_file(str(p), "new")
        assert res.error is None
        assert p.read_text(encoding="utf-8", errors="replace") == "new"

    def test_write_creates_parent_dirs(self, win_local_ops, tmp_path):
        p = tmp_path / "a" / "b" / "c.txt"
        res = win_local_ops.write_file(str(p), "deep")
        assert res.error is None
        assert p.read_text(encoding="utf-8", errors="replace") == "deep"

    def test_failed_write_is_not_silent_success(self, win_local_ops, tmp_path, monkeypatch):
        """#54 core: when the underlying write fails, write_file must surface an
        error and must NOT fabricate a success payload."""
        def boom(path, content):
            return ExecuteResult(stdout="atomic write failed: boom", exit_code=1)

        monkeypatch.setattr(win_local_ops, "_local_atomic_write", boom)
        res = win_local_ops.write_file(str(tmp_path / "x.txt"), "data")
        assert res.error is not None
        assert res.bytes_written == 0
        assert not (tmp_path / "x.txt").exists()

    def test_verification_catches_silent_noop(self, win_local_ops, tmp_path, monkeypatch):
        """If _atomic_write reports success but never persisted the bytes,
        write_file must surface an error instead of a fabricated success."""
        target = tmp_path / "silent.txt"

        def noop(path, content):
            return ExecuteResult(stdout="5", exit_code=0)

        monkeypatch.setattr(win_local_ops, "_atomic_write", noop)
        res = win_local_ops.write_file(str(target), "hello")
        assert res.error is not None
        assert "verification failed" in res.error.lower()
        assert res.bytes_written == 0
        assert not target.exists()

    def test_unicode_roundtrip(self, win_local_ops, tmp_path):
        p = tmp_path / "u.txt"
        text = "中文 αβ 🚀\n"
        res = win_local_ops.write_file(str(p), text)
        assert res.error is None
        assert p.read_bytes().decode("utf-8") == text
        assert res.bytes_written == len(text.encode("utf-8"))


class TestWindowsInProcessRead:
    def test_read_after_write_roundtrip(self, win_local_ops, tmp_path):
        p = tmp_path / "round.txt"
        win_local_ops.write_file(str(p), "line1\nline2\nline3\n")
        r = win_local_ops.read_file(str(p))
        assert r.error is None
        assert "line1" in r.content and "line3" in r.content
        assert r.total_lines == 3

    def test_read_pagination_and_truncation(self, win_local_ops, tmp_path):
        p = tmp_path / "big.txt"
        p.write_text("\n".join(f"L{i}" for i in range(1, 11)) + "\n", encoding="utf-8")
        r = win_local_ops.read_file(str(p), offset=3, limit=2)
        assert r.error is None
        assert "3|L3" in r.content
        assert "4|L4" in r.content
        assert "5|L5" not in r.content
        assert r.truncated is True

    def test_read_file_raw_returns_full_content(self, win_local_ops, tmp_path):
        p = tmp_path / "raw.txt"
        # Write with explicit newline so Windows text mode doesn't translate
        # LF to CRLF; the assertion checks the bytes round-trip verbatim.
        p.write_text("alpha\nbeta\n", encoding="utf-8", newline="\n")
        r = win_local_ops.read_file_raw(str(p))
        assert r.error is None
        assert r.content == "alpha\nbeta\n"

    def test_missing_file_suggests_similar(self, win_local_ops, tmp_path):
        (tmp_path / "config.yaml").write_text("x", encoding="utf-8")
        r = win_local_ops.read_file(str(tmp_path / "config.yml"))
        assert r.error is not None and "not found" in r.error.lower()
        assert any("config.yaml" in s for s in r.similar_files)

    def test_bom_stripped_on_first_page(self, win_local_ops, tmp_path):
        p = tmp_path / "bom.txt"
        p.write_bytes(b"\xef\xbb\xbffirst\nsecond\n")
        r = win_local_ops.read_file(str(p))
        assert r.error is None
        assert "﻿" not in r.content
        assert "1|first" in r.content


class TestInProcessGate:
    def test_non_windows_local_uses_shell_path(self, monkeypatch):
        """With _IS_WINDOWS False (Linux/macOS default) the proven shell path is
        preserved — the in-process branch is Windows-only by design."""
        monkeypatch.setattr(fops_mod, "_IS_WINDOWS", False)
        ops = ShellFileOperations(LocalEnvironment())
        assert ops._use_inproc_io() is False

    def test_remote_backend_never_uses_inprocess(self, monkeypatch):
        """Even on Windows, a non-local (remote/sandbox) backend keeps the shell
        path — the POSIX tools exist inside those environments."""
        monkeypatch.setattr(fops_mod, "_IS_WINDOWS", True)
        from unittest.mock import MagicMock
        env = MagicMock()
        env.cwd = "/work"
        ops = ShellFileOperations(env)
        assert ops._use_inproc_io() is False

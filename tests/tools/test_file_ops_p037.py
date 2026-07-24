"""[CN-fork] P-037 — Windows in-process I/O correctness follow-ups to P-033.

Two gaps that P-030/P-033 left in the shell→in-process migration:

* ``patch_replace`` / its post-write verify / ``_check_lint`` still shelled out
  to ``cat … 2>/dev/null``, bypassing the in-process primitives — broken under
  PowerShell 5.1 (no POSIX ``cat``; ``2>/dev/null`` writes a literal file).
* the in-process reads hard-decoded bytes as ``utf-8`` with ``errors="replace"``,
  turning every non-ASCII byte of a GBK/cp936 file (the common case on Chinese
  Windows) into U+FFFD — and a read→patch→write round-trip then *persisted* that
  corruption.

These tests force the in-process Windows path on the Linux/macOS CI runner
(``_IS_WINDOWS`` monkeypatched True + a real ``LocalEnvironment``) and, for the
encoding case, substitute a concrete ``gbk`` fallback codec (the production
``mbcs`` alias exists only on Windows).
"""

import pytest

import tools.file_operations as fops_mod
from tools.file_operations import _decode_file_bytes, ShellFileOperations
from tools.environments.local import LocalEnvironment


@pytest.fixture()
def win_local_ops(monkeypatch):
    """ShellFileOperations forced onto the in-process Windows disk path."""
    monkeypatch.setattr(fops_mod, "_IS_WINDOWS", True)
    ops = ShellFileOperations(LocalEnvironment())
    assert ops._use_inproc_io() is True
    return ops


# ---------------------------------------------------------------------------
# #3 — _decode_file_bytes: encoding-aware decode (unit)
# ---------------------------------------------------------------------------

class TestDecodeFileBytes:
    def test_utf8_decodes_directly(self):
        assert _decode_file_bytes("héllo 中文".encode("utf-8")) == "héllo 中文"

    def test_legacy_fallback_decodes_cleanly(self):
        data = "你好世界".encode("gbk")
        # GBK bytes are not valid UTF-8, so the fallback codec is what decodes.
        assert _decode_file_bytes(data, fallbacks=("gbk",)) == "你好世界"

    def test_no_fallback_is_lossy_but_never_raises(self):
        data = "你好世界".encode("gbk")
        out = _decode_file_bytes(data, fallbacks=())
        assert isinstance(out, str)
        assert "�" in out  # replacement chars, not an exception

    def test_unknown_fallback_codec_is_skipped(self):
        data = "你好世界".encode("gbk")
        # A codec that doesn't exist on this platform must be skipped (LookupError
        # swallowed), falling through to lossy replacement rather than raising.
        out = _decode_file_bytes(data, fallbacks=("definitely-not-a-codec",))
        assert isinstance(out, str)

    def test_utf8_preferred_over_fallback(self):
        # Valid UTF-8 must decode as UTF-8 even when a fallback is offered.
        data = "数据".encode("utf-8")
        assert _decode_file_bytes(data, fallbacks=("latin-1",)) == "数据"


# ---------------------------------------------------------------------------
# #2 — patch_replace / _check_lint read via the in-process primitive
# ---------------------------------------------------------------------------

class TestPatchReplaceUsesInProcessReads:
    def test_patch_replace_roundtrip_inproc(self, win_local_ops, tmp_path):
        p = tmp_path / "f.txt"
        win_local_ops.write_file(str(p), "alpha\nbeta\ngamma\n")
        res = win_local_ops.patch_replace(str(p), "beta", "BETA")
        assert res.error is None
        assert p.read_text(encoding="utf-8", errors="replace") == "alpha\nBETA\ngamma\n"

    def test_patch_replace_does_not_shell_out_for_reads(self, win_local_ops, tmp_path, monkeypatch):
        """Reads must go through _prim_read_all, never a raw ``cat … 2>/dev/null``
        ``_exec`` (which fails under PowerShell 5.1)."""
        p = tmp_path / "f.txt"
        win_local_ops.write_file(str(p), "alpha\nbeta\ngamma\n")

        calls: list[str] = []
        orig_exec = win_local_ops._exec

        def spy(cmd, *a, **k):
            calls.append(cmd)
            return orig_exec(cmd, *a, **k)

        monkeypatch.setattr(win_local_ops, "_exec", spy)

        res = win_local_ops.patch_replace(str(p), "beta", "BETA")
        assert res.error is None
        assert not any(c.lstrip().startswith("cat ") for c in calls), (
            f"patch_replace still shells out for reads: {calls}"
        )

    def test_patch_replace_missing_file_fails_cleanly(self, win_local_ops, tmp_path):
        res = win_local_ops.patch_replace(str(tmp_path / "nope.txt"), "x", "y")
        assert res.error is not None  # clean error, not a shell crash


# ---------------------------------------------------------------------------
# #2 + #3 — the silent-data-loss scenario: patch a GBK-encoded file
# ---------------------------------------------------------------------------

class TestLegacyEncodingRoundTrip:
    def test_read_file_decodes_gbk_inproc(self, win_local_ops, tmp_path, monkeypatch):
        monkeypatch.setattr(fops_mod, "_INPROC_FALLBACK_ENCODINGS", ("gbk",))
        p = tmp_path / "gbk.txt"
        p.write_bytes("数据库\n连接配置\n".encode("gbk"))
        r = win_local_ops.read_file(str(p))
        assert r.error is None
        assert "数据库" in r.content and "连接配置" in r.content
        assert "�" not in r.content

    def test_patch_on_gbk_file_preserves_content(self, win_local_ops, tmp_path, monkeypatch):
        """Read→patch→write on a GBK file must not corrupt the untouched text.

        Before P-037 the read went through ``cat`` whose output was decoded as
        ``utf-8``-replace, so the GBK bytes became U+FFFD and the fuzzy match for
        the (now-mojibake) target failed — silent data loss / failed edit.
        """
        monkeypatch.setattr(fops_mod, "_INPROC_FALLBACK_ENCODINGS", ("gbk",))
        p = tmp_path / "doc.txt"
        p.write_bytes("标题行\n正文内容\n结尾\n".encode("gbk"))

        res = win_local_ops.patch_replace(str(p), "正文内容", "更新后的内容")
        assert res.error is None

        out = p.read_bytes().decode("utf-8")  # write-back normalizes to UTF-8
        assert "标题行" in out and "结尾" in out
        assert "更新后的内容" in out
        assert "�" not in out

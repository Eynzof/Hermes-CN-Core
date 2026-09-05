"""Regression test for UTF-8 subprocess output with hostile bytes.

Bug class: ``subprocess.run(..., text=True)`` without ``encoding=``/``errors=``
decodes the child's pipes with ``locale.getpreferredencoding(False)`` —
cp936/GBK on zh-CN Windows.  Modern cross-platform children (git, python,
node, uv, docker, gh, …) write UTF-8 regardless of the ANSI codepage, so one
byte that is illegal in GBK (real-world log: "UnicodeDecodeError: 'gbk' codec
can't decode byte 0xae") raises inside CPython's daemon ``_readerthread``
(``subprocess.py: buffer.append(fh.read())``), is printed only via
``threading.excepthook`` ("Exception in thread Thread-N (_readerthread)"),
and leaves ``result.stdout is None`` — the caller never sees an exception.
First surfaced when the session-start workspace snapshot
(``agent/coding_context.py::_git`` running ``git log -3`` over a repo with
Chinese commit subjects) died exactly this way right after the first
conversation turn.

Fix (P-051): every text-mode subprocess pipe in shipped code pins
``errors="replace"`` so a bad byte can never kill the reader thread, and
UTF-8-by-contract children additionally pin ``encoding="utf-8"`` so the text
is decoded correctly rather than as locale mojibake.  Windows-native tools
that emit the OEM/ANSI codepage (tasklist, taskkill, netstat, where) keep
locale decoding (no ``encoding=``) plus ``errors="replace"``.

The behavioral test proves ``coding_context._git`` survives hostile bytes.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Bytes illegal in BOTH cp936/GBK and UTF-8, followed by UTF-8 CJK whose UTF-8
# trail bytes (e.g. 0xAE in 实) are exactly what kills a GBK reader thread.
# Built via bytes([...]) so the source stays pure ASCII (hex escapes get
# mangled by file-write pipelines).
_CHILD_SCRIPT = (
    "import sys; "
    "sys.stdout.buffer.write(bytes([0xFF, 0xFE, 0x81, 0x30, 0x20]) "
    "+ '实现'.encode('utf-8') + bytes([0x0A])); "
    "sys.stdout.buffer.flush()"
)


def test_coding_context_git_decodes_utf8_and_survives_hostile_bytes(monkeypatch):
    from agent import coding_context
    from hermes_cli import _subprocess_compat

    captured: dict = {}
    real_popen = _subprocess_compat.subprocess.Popen

    def fake_popen(argv, **kwargs):
        captured.update(kwargs)
        # Swap git for a child that emits hostile bytes on stdout.
        return real_popen([sys.executable, "-c", _CHILD_SCRIPT], **kwargs)

    monkeypatch.setattr(_subprocess_compat.subprocess, "Popen", fake_popen)

    result = coding_context._git(Path("C:/repo"), "log", "-3")

    # Contract: git's UTF-8 stdout is decoded as UTF-8, and a byte that is
    # illegal in any codec is replaced — never raised inside _readerthread.
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "replace"
    assert "实现" in result  # CJK survived the round-trip

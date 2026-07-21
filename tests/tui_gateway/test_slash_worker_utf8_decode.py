"""Regression test: _SlashWorker drain threads must survive non-locale bytes.

Bug: _SlashWorker spawned the slash worker with ``text=True`` but no
``encoding=``/``errors=``.  The worker child inherits PYTHONIOENCODING=utf-8
(hermes_cli/__init__.py setdefault), so its stdio is UTF-8 — but text=True
decodes with ``locale.getpreferredencoding(False)``, which is cp936/GBK on
zh-CN Windows.  One byte sequence that is illegal in GBK (real-world log:
"UnicodeDecodeError: 'gbk' codec can't decode byte 0xa0") raised inside the
daemon ``_drain_stderr`` thread, killing it via threading.excepthook
("[gateway-crash] thread ... (_drain_stderr) raised UnicodeDecodeError") and
silently starving the slash-command pipeline.

Fix: pin ``encoding="utf-8", errors="replace"`` on the Popen so the drains
decode what the child actually writes and a bad byte can never kill them.
"""

import subprocess
import sys
import time

# Bytes that are illegal in BOTH cp936/GBK and UTF-8 (0xff/0x81-0x30), followed
# by UTF-8 CJK text.  Pre-fix this kills the drain thread on every locale;
# post-fix errors="replace" keeps the thread alive and the CJK text lands in
# stderr_tail.
_CHILD_SCRIPT = (
    "import sys, time; "
    "sys.stderr.buffer.write(b'\\xff\\xfe\\x81\\x30 \\xe4\\xb8\\xad\\xe6\\x96\\x87\\n'); "
    "sys.stderr.buffer.flush(); "
    "time.sleep(0.5)"
)


def _make_worker(monkeypatch):
    from tui_gateway import server

    real_popen = subprocess.Popen
    captured: dict = {}

    def fake_popen(argv, **kwargs):
        # NOTE: server.subprocess is the global subprocess module — only
        # hijack the slash-worker spawn; pass everything else (e.g. the
        # update-check daemon's git calls) through untouched.
        if "tui_gateway.slash_worker" not in argv:
            return real_popen(argv, **kwargs)
        captured.update(kwargs)
        # Swap the real slash_worker for a tiny child that emits hostile bytes.
        return real_popen([sys.executable, "-c", _CHILD_SCRIPT], **kwargs)

    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)
    worker = server._SlashWorker(session_key="utf8-test", model="")
    return worker, captured


def test_slash_worker_decodes_child_stdio_as_utf8_with_replace(monkeypatch):
    worker, captured = _make_worker(monkeypatch)
    try:
        # Contract: decode the child's UTF-8 stdio as UTF-8, and never let a
        # bad byte raise inside the drain threads.
        assert captured.get("encoding") == "utf-8"
        assert captured.get("errors") == "replace"

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not worker.stderr_tail:
            time.sleep(0.05)

        assert worker.stderr_tail, (
            "_drain_stderr collected nothing — the drain thread died "
            "(UnicodeDecodeError regression)"
        )
        assert any("中文" in line for line in worker.stderr_tail)
    finally:
        worker.close()

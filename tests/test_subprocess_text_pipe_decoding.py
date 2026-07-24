"""Regression guard: text-mode subprocess pipes must pin error handling.

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

This file guards the class two ways:
1. A static AST invariant over shipped code — no text-mode subprocess call
   (kwarg form *or* ``**kwargs``-dict splat form) without explicit ``errors=``.
2. A behavioral test proving ``coding_context._git`` survives hostile bytes.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Scanned trees — everything shipped.  tests/ is excluded (not runtime code;
# CI runs a UTF-8 locale), as are vendored/frontend trees.
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "website", "ui-tui",
    "__pycache__", "tests", "dist", "build", ".hermes",
}
SUBPROCESS_NAMES = {"run", "Popen", "call", "check_call", "check_output"}


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_true_const(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _dict_has_text_without_errors(d: ast.Dict) -> bool:
    keys = [k.value for k in d.keys if isinstance(k, ast.Constant)]
    if not any(k in ("text", "universal_newlines") for k in keys):
        return False
    # Only flag when the text-mode value is literally True.
    for k, v in zip(d.keys, d.values):
        if isinstance(k, ast.Constant) and k.value in ("text", "universal_newlines"):
            if not _is_true_const(v):
                return False
    return "errors" not in keys


def _violations(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out: list[str] = []
    # kwargs-dict form: kw = {"text": True, ...}; subprocess.run(..., **kw)
    text_dict_vars: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            if _dict_has_text_without_errors(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        text_dict_vars.add(target.id)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) not in SUBPROCESS_NAMES:
            continue
        kws = {k.arg for k in node.keywords if k.arg}
        texty = any(
            k.arg in ("text", "universal_newlines") and _is_true_const(k.value)
            for k in node.keywords
        )
        if texty and "errors" not in kws:
            out.append(f"{path}:{node.lineno} text-mode call without errors=")
        for k in node.keywords:
            if k.arg is not None:
                continue
            # **splat forms
            if isinstance(k.value, ast.Name) and k.value.id in text_dict_vars:
                out.append(
                    f"{path}:{node.lineno} text-mode **{k.value.id} dict without \"errors\""
                )
            elif isinstance(k.value, ast.Dict) and _dict_has_text_without_errors(k.value):
                out.append(f"{path}:{node.lineno} text-mode inline **dict without \"errors\"")
    return out


@pytest.mark.skipif(sys.platform == 'win32', reason="Windows baseline: subprocess pipe encoding differs")
def test_no_text_mode_subprocess_pipe_without_errors_kwarg():
    violations: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        violations.extend(_violations(path))
    assert violations == [], (
        "text-mode subprocess pipes must pin errors= (and encoding= for "
        "UTF-8-by-contract children) — see module docstring:\n"
        + "\n".join(violations)
    )


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

    captured: dict = {}
    real_run = subprocess.run

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        # Swap git for a child that emits hostile bytes on stdout.
        return real_run([sys.executable, "-c", _CHILD_SCRIPT], **kwargs)

    monkeypatch.setattr(coding_context.subprocess, "run", fake_run)

    result = coding_context._git(Path("C:/repo"), "log", "-3")

    # Contract: git's UTF-8 stdout is decoded as UTF-8, and a byte that is
    # illegal in any codec is replaced — never raised inside _readerthread.
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "replace"
    assert "实现" in result  # CJK survived the round-trip

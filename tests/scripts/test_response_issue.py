"""Contract: scripts/response_issue.py posts one reply, and closes only when told to.

The write half of the issue workflow. The properties worth fixing are the ones that
would damage the tracker rather than fail loudly: the reply travels over stdin (a Windows
console mangles non-ASCII argv, and a pasted log breaks the command line), ``--close`` is
the only thing that closes an issue, an already-closed issue is never re-closed, and a
``--dry-run`` writes nothing at all.

``gh`` is replaced by a recorded fake — the suite must never reach GitHub
(tests/AGENTS.md: no network).
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "response_issue.py"
REPO = "Eynzof/Hermes-CN-Core"
URL = f"https://github.com/{REPO}/issues/196"


def _load():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("response_issue", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["response_issue"] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


response_issue = _load()


class FakeGh:
    """Records the gh calls a run makes and answers them like gh would."""

    def __init__(self, state: str = "OPEN", error: Exception | None = None):
        self.meta = {"number": 196, "title": "[Bug] 桌面壳静默退出", "state": state, "stateReason": None, "url": URL}
        self.error = error
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: list[str], *, stdin: str | None = None) -> str:
        self.calls.append((list(args), stdin))
        if self.error is not None:
            raise self.error
        verb = args[:2]
        if verb == ["issue", "view"]:
            return json.dumps(self.meta)
        if verb == ["issue", "comment"]:
            return URL + "#issuecomment-42"
        if verb == ["issue", "close"]:
            return ""
        raise AssertionError(f"unexpected gh call: {args}")

    def verbs(self) -> list[list[str]]:
        return [args[:2] for args, _ in self.calls]


class FakeStdin:
    """A pipe: bytes on ``.buffer``, no tty."""

    def __init__(self, data: bytes, tty: bool = False):
        self.buffer = io.BytesIO(data)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_reply_is_piped_through_stdin_not_argv(tmp_path, monkeypatch, capsys):
    fake = FakeGh()
    monkeypatch.setattr(response_issue, "run_gh", fake)
    reply = tmp_path / "reply.md"
    reply.write_bytes("第一行\r\n\r\n```text\n日志\n```\r\n".encode())

    assert response_issue.main([str(196), "--body-file", str(reply)]) == 0

    comment = next(call for call in fake.calls if call[0][:2] == ["issue", "comment"])
    assert comment[0] == ["issue", "comment", "196", "-R", REPO, "--body-file", "-"]
    assert comment[1] == "第一行\n\n```text\n日志\n```", "CRLF normalised, BOM tolerated, text verbatim"
    assert not any("第一行" in arg for args, _ in fake.calls for arg in args), "never on the command line"
    assert "✓ reply posted" in capsys.readouterr().out


def test_close_posts_the_reply_first_then_closes(monkeypatch, capsys):
    fake = FakeGh()
    monkeypatch.setattr(response_issue, "run_gh", fake)

    assert response_issue.main([str(196), "--body", "已修复", "--close"]) == 0

    assert fake.verbs() == [["issue", "view"], ["issue", "comment"], ["issue", "close"]]
    assert fake.calls[-1][0] == ["issue", "close", "196", "-R", REPO, "--reason", "completed"]
    assert "✓ closed #196" in capsys.readouterr().out


def test_not_planned_reason_uses_the_spelling_gh_expects(monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(response_issue, "run_gh", fake)

    assert response_issue.main([str(196), "--body", "无法复现", "--close", "--reason", "not-planned"]) == 0

    assert fake.calls[-1][0][-1] == "not planned"


def test_dry_run_reads_the_issue_and_posts_nothing(monkeypatch, capsys):
    fake = FakeGh()
    monkeypatch.setattr(response_issue, "run_gh", fake)

    assert response_issue.main([str(196), "--body", "预演文本", "--close", "--dry-run"]) == 0

    assert fake.verbs() == [["issue", "view"]]
    out = capsys.readouterr().out
    assert "预演文本" in out and "would then close #196" in out


def test_already_closed_issue_is_never_re_closed(monkeypatch, capsys):
    fake = FakeGh(state="CLOSED")
    monkeypatch.setattr(response_issue, "run_gh", fake)

    assert response_issue.main([str(196), "--body", "补充说明", "--close"]) == 0

    assert ["issue", "close"] not in fake.verbs()
    assert "already closed" in capsys.readouterr().out


def test_no_close_flag_leaves_the_issue_open(monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(response_issue, "run_gh", fake)

    assert response_issue.main([str(196), "--body", "谢谢反馈"]) == 0

    assert fake.verbs() == [["issue", "view"], ["issue", "comment"]]


def test_empty_reply_is_refused_before_any_gh_call(monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(response_issue, "run_gh", fake)

    with pytest.raises(SystemExit) as excinfo:
        response_issue.main([str(196), "--body", "   \n\t"])

    assert excinfo.value.code == 2
    assert fake.calls == []


def test_body_and_body_file_are_mutually_exclusive(monkeypatch, tmp_path):
    fake = FakeGh()
    monkeypatch.setattr(response_issue, "run_gh", fake)

    with pytest.raises(SystemExit) as excinfo:
        response_issue.main([str(196), "--body", "x", "--body-file", str(tmp_path / "r.md")])

    assert excinfo.value.code == 2
    assert fake.calls == []


def test_piped_stdin_is_the_default_body_source(monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(response_issue, "run_gh", fake)
    monkeypatch.setattr(response_issue.sys, "stdin", FakeStdin("来自 stdin 的回复".encode()))

    assert response_issue.main([str(196), "--close"]) == 0

    comment = next(call for call in fake.calls if call[0][:2] == ["issue", "comment"])
    assert comment[1] == "来自 stdin 的回复"


def test_tty_without_a_body_source_is_an_error(monkeypatch):
    monkeypatch.setattr(response_issue.sys, "stdin", FakeStdin(b"", tty=True))
    monkeypatch.setattr(response_issue, "run_gh", FakeGh())

    with pytest.raises(SystemExit) as excinfo:
        response_issue.main([str(196)])

    assert excinfo.value.code == 2


def test_stdin_bytes_that_are_not_utf8_fall_back_to_the_locale_codec(monkeypatch):
    monkeypatch.setattr(response_issue.locale, "getpreferredencoding", lambda *_: "latin-1")
    monkeypatch.setattr(response_issue.sys, "stdin", FakeStdin(b"caf\xe9 au lait"))

    assert response_issue._read_stdin() == "café au lait"


def test_gh_failure_exits_2_without_claiming_success(monkeypatch, capsys):
    from _gh_issue import GhError

    monkeypatch.setattr(
        response_issue,
        "run_gh",
        FakeGh(error=GhError("gh issue view 196 failed (exit 1):\nHTTP 401: Bad credentials")),
    )

    assert response_issue.main([str(196), "--body", "hi"]) == 2

    captured = capsys.readouterr()
    assert "HTTP 401" in captured.err
    assert "✓" not in captured.out


def test_non_positive_issue_number_is_refused(monkeypatch):
    monkeypatch.setattr(response_issue, "run_gh", FakeGh())

    with pytest.raises(SystemExit) as excinfo:
        response_issue.main(["0", "--body", "hi"])

    assert excinfo.value.code == 2

"""Contract: scripts/_gh_issue.py is the one place the issue scripts run ``gh``.

Shared plumbing, so the three properties the two entry points rely on are pinned here:
every call is UTF-8 (a Chinese Windows console would decode gh's JSON as cp936), stdin
is piped rather than inherited (so ``--body-file -`` works and gh cannot block on the
terminal), and ``gh api --paginate`` output — one JSON document per page — is decoded
into one list instead of blowing up in ``json.loads``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "_gh_issue.py"


def _load():
    spec = importlib.util.spec_from_file_location("_gh_issue_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_gh_issue_under_test"] = module
    spec.loader.exec_module(module)
    return module


gh_issue = _load()


def test_decode_pages_walks_every_concatenated_document():
    text = '[{"number": 196}]\n[{"number": 194}]'

    assert gh_issue.decode_pages(text) == [[{"number": 196}], [{"number": 194}]]


def test_decode_pages_keeps_a_single_object_whole():
    assert gh_issue.decode_pages('  {"number": 196}\n') == [{"number": 196}]


def test_decode_pages_of_empty_output_is_empty():
    assert gh_issue.decode_pages("   \n") == []


@pytest.mark.parametrize("value", ["Eynzof/Hermes-CN-Core", "owner/name"])
def test_split_repo_accepts_owner_name(value):
    assert gh_issue.split_repo(value) == tuple(value.split("/"))


@pytest.mark.parametrize("value", ["", "nope", "owner/", "/name", "a/b/c"])
def test_split_repo_rejects_anything_else(value):
    with pytest.raises(gh_issue.GhError, match="OWNER/NAME"):
        gh_issue.split_repo(value)


def _capture(monkeypatch, returncode=0, stdout="", stderr=""):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(gh_issue.shutil, "which", lambda name: "C:/gh.exe")
    monkeypatch.setattr(gh_issue.subprocess, "run", fake_run)
    return seen


def test_run_gh_forces_utf8_and_pipes_stdin(monkeypatch):
    seen = _capture(monkeypatch, stdout="{}")

    assert gh_issue.run_gh(["api", "/x"]) == "{}"

    assert seen["kwargs"]["encoding"] == "utf-8"
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert "input" not in seen["kwargs"]


def test_run_gh_sends_a_body_over_stdin_instead_of_argv(monkeypatch):
    seen = _capture(monkeypatch, stdout="")

    gh_issue.run_gh(["issue", "comment", "1", "--body-file", "-"], stdin="中文回复")

    assert seen["kwargs"]["input"] == "中文回复"
    assert "中文回复" not in " ".join(seen["argv"])
    assert "stdin" not in seen["kwargs"]


def test_run_gh_surfaces_gh_stderr_in_one_error(monkeypatch):
    _capture(monkeypatch, returncode=1, stderr="HTTP 401: Bad credentials")

    with pytest.raises(gh_issue.GhError) as excinfo:
        gh_issue.run_gh(["api", "/x"])

    assert "exit 1" in str(excinfo.value)
    assert "HTTP 401: Bad credentials" in str(excinfo.value)


def test_missing_gh_names_the_install_and_login_step(monkeypatch):
    monkeypatch.setattr(gh_issue.shutil, "which", lambda name: None)

    with pytest.raises(gh_issue.GhError, match="gh auth login"):
        gh_issue.run_gh(["api", "/x"])


def test_gh_json_flattens_pages_into_one_list(monkeypatch):
    monkeypatch.setattr(
        gh_issue,
        "run_gh",
        lambda args, **kwargs: '[{"number": 196}]\n[{"number": 194}]\n',
    )

    assert gh_issue.gh_json("/repos/o/r/issues", paginate=True) == [{"number": 196}, {"number": 194}]


def test_gh_json_returns_one_object_without_paginate(monkeypatch):
    monkeypatch.setattr(gh_issue, "run_gh", lambda args, **kwargs: '{"number": 196}')

    assert gh_issue.gh_json("/repos/o/r/issues/196") == {"number": 196}

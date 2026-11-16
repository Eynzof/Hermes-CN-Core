"""Contract: scripts/fetch_issue.py turns tracker JSON into one faithful Markdown file.

The script is the read half of the issue workflow documented in AGENTS.md; its output is
what a maintainer greps, diffs and pastes into a model prompt. These tests fix the parts
that matter and would otherwise rot silently: the front matter stays parseable YAML, the
body and comments survive verbatim (CJK, code fences, long logs), pull requests never
show up as issues, and a fetch failure is reported per issue instead of aborting the run.

``gh`` is replaced by a recorded fake — the suite must never reach GitHub
(tests/AGENTS.md: no network).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "fetch_issue.py"
REPO = "Eynzof/Hermes-CN-Core"


def _load():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("fetch_issue", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_issue"] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


fetch_issue = _load()


def api_issue(**overrides):
    """A REST issue object shaped like the fork's own bug reports (CJK, fence, labels)."""
    data = {
        "number": 196,
        "title": '[Bug] 桌面壳「hermes-agent-cn-desktop.exe」反复 0xC0000409 静默退出',
        "state": "open",
        "state_reason": None,
        "user": {"login": "wuyuze9462", "type": "User"},
        "created_at": "2026-11-08T15:18:31Z",
        "updated_at": "2026-11-08T16:02:00Z",
        "closed_at": None,
        "labels": [{"name": "bug"}],
        "milestone": None,
        "assignees": [],
        "comments": 1,
        "html_url": f"https://github.com/{REPO}/issues/196",
        "body": "复现步骤：\n\n```text\nFAST_FAIL_FATAL_APP_EXIT 0xC0000409\n```\n",
    }
    data.update(overrides)
    return data


def api_comment(**overrides):
    data = {
        "user": {"login": "Eynzof", "type": "User"},
        "created_at": "2026-11-09T09:00:00Z",
        "updated_at": "2026-11-09T09:00:00Z",
        "body": "已在 0.9.1 修复。",
        "html_url": f"https://github.com/{REPO}/issues/196#issuecomment-1",
    }
    data.update(overrides)
    return data


class FakeGh:
    """Records ``gh api`` endpoints and answers them from a routing table."""

    def __init__(self, routes: dict[str, object]):
        self.routes = dict(sorted(routes.items(), key=lambda item: len(item[0]), reverse=True))
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, endpoint: str, *, paginate: bool = False):
        self.calls.append((endpoint, paginate))
        for prefix, reply in self.routes.items():
            if prefix in endpoint:
                if isinstance(reply, Exception):
                    raise reply
                return copy.deepcopy(reply)
        raise AssertionError(f"unexpected gh api endpoint: {endpoint}")


def _front_matter(markdown: str) -> dict:
    """Parse the front matter the way a consumer would: every value is JSON, hence YAML.

    No PyYAML here on purpose — the script's contract is that each value is ``json.dumps``
    output, so this parser is the invariant, not a re-implementation of one.
    """
    assert markdown.startswith("---\n"), markdown[:20]
    block = markdown.split("\n---\n", 1)[0][len("---\n") :]
    fields: dict = {}
    for line in block.splitlines():
        key, separator, value = line.partition(": ")
        assert separator, line
        fields[key] = json.loads(value)
    return fields


def test_front_matter_is_parseable_yaml_with_the_issue_identity():
    issue = fetch_issue.issue_from_api(api_issue(labels=[{"name": "bug"}, {"name": "windows"}]))

    markdown = fetch_issue.render_issue(issue, [], fetched_at="2026-11-11T00:00:00Z")
    front = _front_matter(markdown)

    assert front["number"] == 196
    assert front["title"] == api_issue()["title"]
    assert front["state"] == "open"
    assert front["labels"] == ["bug", "windows"]
    assert front["comments"] == 1
    assert front["url"] == f"https://github.com/{REPO}/issues/196"
    assert front["fetched_at"] == "2026-11-11T00:00:00Z"


def test_body_and_comments_survive_verbatim_including_a_code_fence():
    issue = fetch_issue.issue_from_api(api_issue())
    comment = fetch_issue.comment_from_api(api_comment(body="日志：\n\n```text\nNameError: InProcessCronScheduler\n```"))

    markdown = fetch_issue.render_issue(issue, [comment], fetched_at="2026-11-11T00:00:00Z")

    assert "FAST_FAIL_FATAL_APP_EXIT 0xC0000409" in markdown
    assert "```text\nNameError: InProcessCronScheduler\n```" in markdown
    assert "### 1. @Eynzof · 2026-11-09 09:00:00 UTC" in markdown


def test_comment_heading_flags_edits_and_bots():
    comment = fetch_issue.comment_from_api(
        api_comment(user={"login": "github-actions[bot]", "type": "Bot"}, updated_at="2026-11-09T10:00:00Z")
    )

    markdown = fetch_issue.render_issue(fetch_issue.issue_from_api(api_issue()), [comment], fetched_at="x")

    assert "@github-actions[bot] [bot] · 2026-11-09 09:00:00 UTC · edited" in markdown


def test_issue_without_a_body_says_so_instead_of_leaving_a_blank_section():
    issue = fetch_issue.issue_from_api(api_issue(body=None, comments=0))

    markdown = fetch_issue.render_issue(issue, [], fetched_at="x")

    assert fetch_issue.NO_BODY in markdown
    assert "_No comments._" in markdown


def test_comments_skipped_reports_the_count_that_was_left_behind():
    issue = fetch_issue.issue_from_api(api_issue(comments=4))

    markdown = fetch_issue.render_issue(issue, [], fetched_at="x", comments_fetched=False)

    assert "`--no-comments`" in markdown
    assert "4 on GitHub" in markdown


def test_pull_requests_are_not_issues(monkeypatch):
    routes = {
        f"/repos/{REPO}/issues?": [
            api_issue(number=197, pull_request={"url": "..."}),
            api_issue(number=196),
            api_issue(number=194),
        ]
    }
    monkeypatch.setattr(fetch_issue, "gh_json", FakeGh(routes))

    issues = fetch_issue.fetch_issue_list(REPO, state="open", limit=0)

    assert [issue.number for issue in issues] == [196, 194]


def test_limit_keeps_the_newest_issues_the_endpoint_returned(monkeypatch):
    routes = {f"/repos/{REPO}/issues?": [api_issue(number=n) for n in (196, 194, 193, 191)]}
    fake = FakeGh(routes)
    monkeypatch.setattr(fetch_issue, "gh_json", fake)

    issues = fetch_issue.fetch_issue_list(REPO, state="open", limit=2)

    assert [issue.number for issue in issues] == [196, 194]
    assert fake.calls[0][1] is True, "list endpoints must paginate"


def test_main_writes_one_file_per_issue_plus_an_index(tmp_path, capsys, monkeypatch):
    routes = {
        f"/repos/{REPO}/issues/196/comments": [api_comment()],
        f"/repos/{REPO}/issues/196": api_issue(),
        f"/repos/{REPO}/issues/194": api_issue(number=194, title="[Feature] 子 Agent 委派", comments=0, labels=[]),
    }
    fake = FakeGh(routes)
    monkeypatch.setattr(fetch_issue, "gh_json", fake)

    out = tmp_path / "issues"
    assert fetch_issue.main(["--out", str(out), "--index", "196", "194"]) == 0

    first = (out / "issue-196.md").read_text(encoding="utf-8-sig")
    second = (out / "issue-194.md").read_text(encoding="utf-8-sig")
    index = (out / "INDEX.md").read_text(encoding="utf-8-sig")
    assert "@Eynzof" in first and "已在 0.9.1 修复。" in first
    assert "# [Feature] 子 Agent 委派" in second
    assert "[196](issue-196.md)" in index and "[194](issue-194.md)" in index
    assert "2 issues fetched" in index
    assert not [call for call in fake.calls if "/194/comments" in call[0]], "no comment call without comments"


def test_index_escapes_pipes_so_a_title_cannot_break_the_table(tmp_path, monkeypatch):
    fake = FakeGh({f"/repos/{REPO}/issues/7": api_issue(number=7, title="a | b", comments=0)})
    monkeypatch.setattr(fetch_issue, "gh_json", fake)

    assert fetch_issue.main(["--out", str(tmp_path), "--index", "7"]) == 0
    index = (tmp_path / "INDEX.md").read_text(encoding="utf-8-sig")

    assert "| [7](issue-7.md) | open | a \\| b |" in index


def test_stdout_prints_and_writes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(fetch_issue, "gh_json", FakeGh({f"/repos/{REPO}/issues/196": api_issue(comments=0)}))

    out = tmp_path / "issues"
    assert fetch_issue.main(["--out", str(out), "--stdout", "196"]) == 0

    assert "# " + api_issue()["title"] in capsys.readouterr().out
    assert not out.exists()


def test_one_bad_number_does_not_abort_the_rest(tmp_path, capsys, monkeypatch):
    from _gh_issue import GhError

    routes = {
        f"/repos/{REPO}/issues/9999": GhError("gh api /repos/x/issues/9999 failed (exit 1):\nHTTP 404: Not Found"),
        f"/repos/{REPO}/issues/196": api_issue(comments=0),
    }
    monkeypatch.setattr(fetch_issue, "gh_json", FakeGh(routes))

    assert fetch_issue.main(["--out", str(tmp_path), "9999", "196"]) == 1

    assert (tmp_path / "issue-196.md").is_file()
    assert "HTTP 404" in capsys.readouterr().err


def test_gh_failure_on_the_list_endpoint_writes_nothing_and_exits_2(tmp_path, capsys, monkeypatch):
    from _gh_issue import GhError

    monkeypatch.setattr(
        fetch_issue, "gh_json", FakeGh({f"/repos/{REPO}/issues?": GhError("gh not found on PATH")})
    )

    out = tmp_path / "issues"
    assert fetch_issue.main(["--out", str(out)]) == 2

    assert not out.exists()
    assert "gh not found on PATH" in capsys.readouterr().err


def test_bad_repo_is_refused_before_any_call(tmp_path, capsys, monkeypatch):
    fake = FakeGh({})
    monkeypatch.setattr(fetch_issue, "gh_json", fake)

    assert fetch_issue.main(["--out", str(tmp_path), "--repo", "nope", "196"]) == 2

    assert fake.calls == []
    assert "OWNER/NAME" in capsys.readouterr().err


def test_negative_limit_is_refused():
    assert fetch_issue.main(["--limit", "-1"]) == 2


def test_crlf_bodies_from_the_api_are_normalised_to_lf():
    issue = fetch_issue.issue_from_api(api_issue(body="第一行\r\n第二行\r\n"))
    comment = fetch_issue.comment_from_api(api_comment(body="回复\r\n\r\n```text\r\nx\r\n```"))

    markdown = fetch_issue.render_issue(issue, [comment], fetched_at="x")

    assert "\r" not in markdown
    assert "第一行\n第二行" in markdown
    assert "```text\nx\n```" in markdown

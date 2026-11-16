#!/usr/bin/env python3
"""Download issues from the fork's tracker with ``gh`` and write them out as Markdown.

    python scripts/fetch_issue.py                  # every issue, open and closed
    python scripts/fetch_issue.py 196 194          # just those two
    python scripts/fetch_issue.py --state open --limit 20
    python scripts/fetch_issue.py 196 --stdout     # print it, write nothing
    python scripts/fetch_issue.py --limit 50 --index

Why this exists: triaging Eynzof/Hermes-CN-Core means reading long Chinese bug reports
with logs, stack traces and screenshots inline. The web UI paginates, hides comment
timestamps behind a click and wrecks copy-paste, so each issue is dumped as ONE
Markdown file: metadata in YAML front matter, the body verbatim, then every comment in
order. The result can be grepped, diffed, attached to a model prompt or reviewed in an
editor without a browser.

Output defaults to ``.cache/issues/issue-<number>.md`` — a cache, not a document tree.
This fork ships code (AGENTS.md § Deleted upstream trees), so fetched tracker text must
never land in a tracked directory; ``.cache/`` is gitignored. Files are overwritten on
every run, which is the point: re-fetch to pick up new comments.

Credentials are whatever ``gh auth login`` stored; this script never sees a token.

Exit codes: ``0`` every requested issue was written, ``1`` some of them failed (the
rest are still written), ``2`` gh is missing/unauthenticated or nothing could be
fetched — nothing was written.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _gh_issue import DEFAULT_REPO, GhError, gh_json, split_repo

DEFAULT_OUT = Path(".cache") / "issues"
NO_BODY = "_No description provided._"


@dataclass(frozen=True)
class Comment:
    """One issue comment, reduced to what the Markdown needs."""

    author: str
    created_at: str
    body: str
    url: str
    edited: bool
    bot: bool


@dataclass(frozen=True)
class Issue:
    """One issue, reduced to what the Markdown and the index need."""

    number: int
    title: str
    state: str
    state_reason: str | None
    author: str
    author_bot: bool
    created_at: str
    updated_at: str
    closed_at: str | None
    labels: tuple[str, ...]
    milestone: str | None
    assignees: tuple[str, ...]
    comment_count: int
    url: str
    body: str


def _text(value: str | None) -> str:
    """LF-normalise body text: GitHub hands comment bodies back with CRLF, and a cache
    file that mixes both endings shows every line as changed in a diff."""
    return (value or "").replace("\r\n", "\n")


def issue_from_api(data: dict[str, Any]) -> Issue:
    """Map a REST issue object onto :class:`Issue` (absent fields become empty)."""
    user = data.get("user") or {}
    milestone = data.get("milestone") or {}
    return Issue(
        number=int(data["number"]),
        title=data.get("title") or "",
        state=data.get("state") or "OPEN",
        state_reason=data.get("state_reason"),
        author=user.get("login") or "ghost",
        author_bot=(user.get("type") == "Bot"),
        created_at=data.get("created_at") or "",
        updated_at=data.get("updated_at") or "",
        closed_at=data.get("closed_at"),
        labels=tuple(label.get("name") or "" for label in data.get("labels") or []),
        milestone=milestone.get("title"),
        assignees=tuple(user.get("login") or "" for user in data.get("assignees") or []),
        comment_count=int(data.get("comments") or 0),
        url=data.get("html_url") or "",
        body=_text(data.get("body")),
    )


def comment_from_api(data: dict[str, Any]) -> Comment:
    """Map a REST issue-comment object onto :class:`Comment`."""
    user = data.get("user") or {}
    return Comment(
        author=user.get("login") or "ghost",
        created_at=data.get("created_at") or "",
        body=_text(data.get("body")),
        url=data.get("html_url") or "",
        edited=data.get("updated_at") != data.get("created_at"),
        bot=(user.get("type") == "Bot"),
    )


def _quote(value: Any) -> str:
    """Serialise a front-matter value as JSON, which is also valid YAML.

    ``json.dumps`` keeps ``#``, ``:`` and CJK titles intact, and ``null``/``false`` are
    YAML too, so the front matter round-trips through ``yaml.safe_load`` unchanged.
    """
    return json.dumps(value, ensure_ascii=False)


def _stamp(timestamp: str | None) -> str:
    """``2026-11-08T15:18:31Z`` → ``2026-11-08 15:18:31 UTC``; no parsing, no locale."""
    if not timestamp:
        return "—"
    return timestamp.replace("T", " ").replace("Z", " UTC")


def _who(login: str, bot: bool) -> str:
    return f"@{login} [bot]" if bot else f"@{login}"


def render_issue(
    issue: Issue,
    comments: Sequence[Comment],
    *,
    fetched_at: str,
    comments_fetched: bool = True,
) -> str:
    """Render one issue as Markdown: front matter, header, body, then comments."""
    lines = [
        "---",
        f"number: {issue.number}",
        f"title: {_quote(issue.title)}",
        f"state: {_quote(issue.state)}",
        f"state_reason: {_quote(issue.state_reason)}",
        f"author: {_quote(issue.author)}",
        f"author_is_bot: {'true' if issue.author_bot else 'false'}",
        f"created_at: {_quote(issue.created_at)}",
        f"updated_at: {_quote(issue.updated_at)}",
        f"closed_at: {_quote(issue.closed_at)}",
        f"labels: {_quote(list(issue.labels))}",
        f"milestone: {_quote(issue.milestone)}",
        f"assignees: {_quote(list(issue.assignees))}",
        f"comments: {issue.comment_count}",
        f"url: {_quote(issue.url)}",
        f"fetched_at: {_quote(fetched_at)}",
        "---",
        "",
        f"# {issue.title}",
        "",
        f"- **Issue**: [#{issue.number}]({issue.url})",
        f"- **State**: `{issue.state}`" + (f" · {issue.state_reason}" if issue.state_reason else ""),
        f"- **Author**: {_who(issue.author, issue.author_bot)}",
        f"- **Created**: {_stamp(issue.created_at)}",
        f"- **Updated**: {_stamp(issue.updated_at)}",
    ]
    if issue.closed_at:
        lines.append(f"- **Closed**: {_stamp(issue.closed_at)}")
    lines.append("- **Labels**: " + (", ".join(f"`{label}`" for label in issue.labels) or "_none_"))
    if issue.milestone:
        lines.append(f"- **Milestone**: {issue.milestone}")
    if issue.assignees:
        lines.append("- **Assignees**: " + ", ".join(f"@{login}" for login in issue.assignees))
    lines += ["- **Comments**: " + str(issue.comment_count), "", "## Description", ""]
    lines += [issue.body.strip() or NO_BODY, "", "## Comments", ""]
    lines += _render_comments(issue, comments, comments_fetched=comments_fetched)
    return "\n".join(lines).rstrip("\n") + "\n"


def _render_comments(issue: Issue, comments: Sequence[Comment], *, comments_fetched: bool) -> list[str]:
    if not comments:
        if not comments_fetched and issue.comment_count:
            return ["_Comments not fetched (`--no-comments`)._" + f" {issue.comment_count} on GitHub.", ""]
        return ["_No comments._", ""]
    lines: list[str] = []
    for index, comment in enumerate(comments, start=1):
        edited = " · edited" if comment.edited else ""
        heading = f"### {index}. {_who(comment.author, comment.bot)} · {_stamp(comment.created_at)}{edited}"
        lines += [heading, "", comment.body.strip() or "_Empty comment._", ""]
    return lines


def render_index(issues: Sequence[Issue], *, repo: str, fetched_at: str) -> str:
    """Render the ``INDEX.md`` table for the issues fetched in this run."""
    open_count = sum(1 for issue in issues if issue.state.upper() == "OPEN")
    lines = [
        f"# Issues — {repo}",
        "",
        f"{len(issues)} issues fetched {_stamp(fetched_at)} — {open_count} open, {len(issues) - open_count} closed.",
        "",
        "| # | State | Title | Labels | Comments | Updated |",
        "|---|---|---|---|---|---|",
    ]
    for issue in sorted(issues, key=lambda item: item.number, reverse=True):
        title = issue.title.replace("|", "\\|")
        labels = ", ".join(label.replace("|", "\\|") for label in issue.labels) or "—"
        updated = _stamp(issue.updated_at)[:10]
        lines.append(
            f"| [{issue.number}](issue-{issue.number}.md) | {issue.state} | {title} "
            f"| {labels} | {issue.comment_count} | {updated} |"
        )
    return "\n".join(lines) + "\n"


def fetch_comments(repo: str, number: int) -> list[Comment]:
    """Every comment on one issue, oldest first."""
    rows = gh_json(f"/repos/{repo}/issues/{number}/comments?per_page=100", paginate=True)
    return [comment_from_api(row) for row in rows]


def fetch_issue(repo: str, number: int) -> Issue:
    """One issue by number; gh reports a clear error when it does not exist."""
    return issue_from_api(gh_json(f"/repos/{repo}/issues/{number}"))


def fetch_issue_list(repo: str, *, state: str, limit: int) -> list[Issue]:
    """The most recent ``limit`` issues in ``state``, pull requests filtered out."""
    endpoint = f"/repos/{repo}/issues?state={state}&per_page=100&sort=created&direction=desc"
    rows = gh_json(endpoint, paginate=True)
    issues = [issue_from_api(row) for row in rows if "pull_request" not in row]
    return issues[:limit] if limit else issues


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download issues from the fork's tracker and write them as Markdown files.",
        epilog=f"Output defaults to {DEFAULT_OUT}/ (gitignored). Credentials come from 'gh auth login'.",
    )
    parser.add_argument("numbers", nargs="*", type=int, metavar="NUMBER", help="issue numbers; omit to list by --state")
    parser.add_argument("--repo", default=DEFAULT_REPO, metavar="OWNER/NAME", help=f"tracker to read (default: {DEFAULT_REPO})")
    parser.add_argument("--state", choices=("open", "closed", "all"), default="all", help="state filter when listing (default: all)")
    parser.add_argument("--limit", type=int, default=0, metavar="N", help="keep only the N most recent issues when listing")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, metavar="DIR", help=f"output directory (default: {DEFAULT_OUT})")
    parser.add_argument("--no-comments", action="store_true", help="skip the comment fetch (one request per issue saved)")
    parser.add_argument("--stdout", action="store_true", help="print the Markdown instead of writing files")
    parser.add_argument("--index", action="store_true", help="also write INDEX.md for what was fetched (ignored with --stdout)")
    parser.add_argument("--quiet", action="store_true", help="only report failures")
    return parser


def _collect(args: argparse.Namespace) -> tuple[list[tuple[Issue, list[Comment]]], list[str]]:
    """Fetch what the arguments ask for; return the issues plus one message per failure."""
    failures: list[str] = []
    if args.numbers:
        rows: list[tuple[Issue, list[Comment]]] = []
        for number in args.numbers:
            try:
                issue = fetch_issue(args.repo, number)
            except GhError as exc:
                failures.append(f"#{number}: {exc}")
                continue
            rows.append((issue, _comments_for(args, issue)))
        return rows, failures
    issues = fetch_issue_list(args.repo, state=args.state, limit=args.limit)
    return [(issue, _comments_for(args, issue)) for issue in issues], failures


def _comments_for(args: argparse.Namespace, issue: Issue) -> list[Comment]:
    if args.no_comments or not issue.comment_count:
        return []
    return fetch_comments(args.repo, issue.number)


def _write_files(args: argparse.Namespace, rows: Sequence[tuple[Issue, list[Comment]]], fetched_at: str) -> None:
    if args.stdout:
        rendered = [
            render_issue(issue, comments, fetched_at=fetched_at, comments_fetched=not args.no_comments)
            for issue, comments in rows
        ]
        print("\n".join(rendered), end="")
        return
    args.out.mkdir(parents=True, exist_ok=True)
    for issue, comments in rows:
        markdown = render_issue(issue, comments, fetched_at=fetched_at, comments_fetched=not args.no_comments)
        target = args.out / f"issue-{issue.number}.md"
        target.write_text(markdown, encoding="utf-8")
        if not args.quiet:
            print(f"  #{issue.number} {issue.state.lower():6} {_clip(issue.title)} → {target}", file=sys.stderr)
    if args.index:
        index = args.out / "INDEX.md"
        index.write_text(render_index([issue for issue, _ in rows], repo=args.repo, fetched_at=fetched_at), encoding="utf-8")
        if not args.quiet:
            print(f"  index → {index}", file=sys.stderr)


def _clip(text: str, width: int = 60) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _utf8_stdio() -> None:
    """Force UTF-8 on the console streams: Windows would raise on the CJK titles."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _utf8_stdio()
    if args.limit < 0:
        print("✗ --limit must be >= 0", file=sys.stderr)
        return 2
    try:
        split_repo(args.repo)
        fetched_at = _now()
        rows, failures = _collect(args)
    except GhError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    for failure in failures:
        print(f"✗ {failure}", file=sys.stderr)
    if not rows and not failures:
        print(f"✗ no issues matched (state={args.state}) in {args.repo}", file=sys.stderr)
        return 2
    if rows:
        _write_files(args, rows, fetched_at)
    if not args.quiet:
        open_count = sum(1 for issue, _ in rows if issue.state.upper() == "OPEN")
        where = "stdout" if args.stdout else str(args.out)
        print(f"{len(rows)} issues ({open_count} open, {len(rows) - open_count} closed) → {where}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

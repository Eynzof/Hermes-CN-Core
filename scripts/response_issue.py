#!/usr/bin/env python3
"""Reply to one issue on the fork's tracker, optionally closing it with that reply.

    python scripts/response_issue.py 196 --body "0.9.1 已修复，见 #188。"
    python scripts/response_issue.py 196 --body-file reply.md --close
    git log -1 --format=%B | python scripts/response_issue.py 196 --close --reason not-planned
    python scripts/response_issue.py 196 --body-file reply.md --dry-run

The reply is always posted as a comment; ``--close`` is the switch that also closes the
issue afterwards (``--reason completed`` by default, or ``not-planned``). Two calls, not
``gh issue close --comment``, because that flag takes the text on the command line — a
long Chinese reply or a pasted log would not survive a Windows console. Here the body
travels over stdin (``--body-file -``), so it arrives byte-exact whatever the code page.

``--dry-run`` resolves the issue and prints the reply plus the calls that would run,
writing nothing: use it before a maintainer sees the text.

Exit codes: ``0`` posted (and closed), ``2`` refused or gh failed — nothing was posted.
"""

from __future__ import annotations

import argparse
import json
import locale
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _gh_issue import DEFAULT_REPO, GhError, run_gh, split_repo

REASONS = {"completed": "completed", "not-planned": "not planned"}


def _utf8_stdio() -> None:
    """Force UTF-8 on the console streams: Windows would raise on the CJK titles."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _read_stdin() -> str:
    """Decode a piped reply: UTF-8 when it is UTF-8, else the console's own code page.

    PowerShell's ``$OutputEncoding`` is not UTF-8 on every host, and a reply in Chinese
    arrives as GBK bytes there; trying the locale codec second keeps that working while
    a UTF-8 pipe (``gh`` output, ``git log``, WSL) stays byte-exact.
    """
    raw = sys.stdin.buffer.read()
    for encoding in ("utf-8-sig", locale.getpreferredencoding(False)):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _resolve_body(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    """Return the reply text from --body, --body-file (``-`` = stdin) or a pipe."""
    if args.body is not None and args.body_file is not None:
        parser.error("--body and --body-file are mutually exclusive")
    if args.body is not None:
        text = args.body
    elif args.body_file is not None:
        text = _read_stdin() if args.body_file == "-" else Path(args.body_file).read_text(encoding="utf-8-sig")
    elif not sys.stdin.isatty():
        text = _read_stdin()
    else:
        parser.error("no reply text: pass --body, --body-file, or pipe it on stdin")
    text = text.replace("\r\n", "\n").strip()
    if not text:
        parser.error("the reply is empty; refusing to post nothing")
    return text


def _issue_meta(repo: str, number: int) -> dict[str, Any]:
    """Title/state/url for the target issue; gh's error already names a missing one."""
    raw = run_gh(["issue", "view", str(number), "-R", repo, "--json", "number,title,state,stateReason,url"])
    return json.loads(raw)


def _post_reply(repo: str, number: int, body: str) -> str:
    """Post the comment and return the URL gh prints for it."""
    return run_gh(
        ["issue", "comment", str(number), "-R", repo, "--body-file", "-"],
        stdin=body,
    ).strip()


def _close_issue(repo: str, number: int, reason: str) -> None:
    run_gh(["issue", "close", str(number), "-R", repo, "--reason", reason])


def _header(meta: dict[str, Any]) -> str:
    return f"#{meta.get('number')} {meta.get('title') or ''} ({meta.get('state') or '?'})"


def _print_plan(args: argparse.Namespace, meta: dict[str, Any], body: str, reason: str) -> None:
    print(_header(meta))
    print(f"[dry-run] would post this reply ({len(body)} chars) as a comment:")
    print("----- 8< -----")
    print(body)
    print("----- >8 -----")
    if args.close:
        if str(meta.get("state", "")).upper() == "CLOSED":
            print(f"[dry-run] #{args.number} is already closed — the close step would be skipped")
        else:
            print(f"[dry-run] would then close #{args.number} with reason '{reason}'")
    else:
        print(f"[dry-run] #{args.number} would stay OPEN — no --close given")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Post a reply to one issue on the fork's tracker and optionally close it.",
        epilog=f"Credentials come from 'gh auth login'. Default repo: {DEFAULT_REPO}.",
    )
    parser.add_argument("number", type=int, metavar="ISSUE", help="issue number to reply to")
    parser.add_argument("--repo", default=DEFAULT_REPO, metavar="OWNER/NAME", help=f"tracker to write to (default: {DEFAULT_REPO})")
    parser.add_argument("--body", metavar="TEXT", help="the reply text (prefer --body-file for anything non-ASCII)")
    parser.add_argument("--body-file", metavar="FILE", help="read the reply from FILE ('-' = stdin)")
    parser.add_argument("--close", action="store_true", help="close the issue after posting the reply")
    parser.add_argument("--reason", choices=tuple(REASONS), default="completed", help="close reason (default: completed)")
    parser.add_argument("--dry-run", action="store_true", help="print the reply and the planned calls, write nothing")
    parser.add_argument("--quiet", action="store_true", help="only print failures")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _utf8_stdio()
    if args.number <= 0:
        parser.error("ISSUE must be a positive issue number")
    body = _resolve_body(args, parser)
    reason = REASONS[args.reason]
    try:
        split_repo(args.repo)
        meta = _issue_meta(args.repo, args.number)
        if args.dry_run:
            _print_plan(args, meta, body, reason)
            return 0
        if not args.quiet:
            print(_header(meta))
        comment_url = _post_reply(args.repo, args.number, body)
        print(f"✓ reply posted: {comment_url or meta.get('url') or ''}")
        if not args.close:
            return 0
        if str(meta.get("state", "")).upper() == "CLOSED":
            print(f"· #{args.number} was already closed — close step skipped")
            return 0
        _close_issue(args.repo, args.number, reason)
        print(f"✓ closed #{args.number} (reason: {reason})")
    except GhError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

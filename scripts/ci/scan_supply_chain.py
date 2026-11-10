#!/usr/bin/env python3
"""Scan a PR's added lines for critical supply-chain indicators and unbounded PyPI deps.

Run by two steps of ``.github/workflows/repo-guards.yml`` on pull requests, with the PR's base
and head commits in ``_HERMES_PR_BASE_SHA`` and ``_HERMES_PR_HEAD_SHA``. Each subcommand appends ``found=true|false`` to
``$GITHUB_OUTPUT`` and, on a finding, writes the report that
``scripts/ci/review_status.py emit`` publishes:

    python3 scripts/ci/scan_supply_chain.py patterns            # $RUNNER_TEMP/supply-chain-findings.md
    python3 scripts/ci/scan_supply_chain.py dependency-bounds   # $RUNNER_TEMP/supply-chain-unbounded.txt

``patterns`` is a narrow, high-signal scanner: only critical indicators of supply chain attacks
(the litellm-style payloads). Low-signal heuristics were removed on purpose because they fired
on nearly every PR and trained reviewers to ignore the scanner; keep it ruthlessly narrow.
Both diff base...head, from the merge base, so only changes this PR introduces are judged, not
changes that landed on main after the PR branched off.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LOCKFILE_EXCLUDES = (":!uv.lock", ":!*.lock", ":!pnpm-lock.yaml", ":!yarn.lock")
MAX_MATCHES = 10


@dataclass(frozen=True)
class Indicator:
    heading: str
    explanation: str
    patterns: tuple[re.Pattern[str], ...]  # every pattern must match the added line


INDICATORS = (
    Indicator(
        "### 🚨 CRITICAL: base64 decode + exec/eval combo",
        "Base64-decoded strings passed directly to exec/eval — the signature of hidden "
        "credential-stealing payloads.",
        (re.compile(r"base64\.(b64decode|decodebytes|urlsafe_b64decode)", re.IGNORECASE),
         re.compile(r"exec\(|eval\(", re.IGNORECASE)),
    ),
    Indicator(
        "### 🚨 CRITICAL: subprocess with encoded/obfuscated command",
        "Subprocess calls whose command strings are base64- or hex-encoded are a strong "
        "indicator of payload execution.",
        (re.compile(r"subprocess\.(Popen|call|run)\s*\("),
         re.compile(r"base64|\\x[0-9a-f]{2}|chr\(", re.IGNORECASE)),
    ),
)

# A dep spec with ``>=`` and no ``,<`` ceiling (the pattern cannot match one); git+ URLs (commit
# SHAs) and comments never match.
# The workflow's ERE spelled the extras group ``\[[^\]]*\]``; in a POSIX bracket the backslash is
# literal, so that is one non-backslash character then ``]*``. Kept as is: an extras group matches
# only when it holds one character (``"a[b]>=2"``, not ``"pkg[extra]>=1"``).
UNBOUNDED_DEP = re.compile(r'"[a-zA-Z0-9_-]+(?:\[[^\\]\]*\])?>=[ 0-9.]+"')


def pr_diff(*pathspec: str) -> str:
    """Three-dot diff of the PR; a git failure yields an empty diff, as ``|| true`` did."""
    commits = f"{os.environ['_HERMES_PR_BASE_SHA']}...{os.environ['_HERMES_PR_HEAD_SHA']}"
    result = subprocess.run(("git", "diff", commits, "--", *pathspec), stdout=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, timeout=300, check=False)
    return result.stdout.decode("utf-8", "surrogateescape")


def numbered_added_lines(diff: str) -> list[str]:
    """``grep -n '^+'`` over the diff."""
    return [f"{number}:{line}" for number, line in enumerate(diff.split("\n"), 1)
            if line.startswith("+")]


def critical_findings(diff: str) -> str:
    added = numbered_added_lines(diff)
    findings = ""
    for indicator in INDICATORS:
        hits = [line for line in added if all(p.search(line) for p in indicator.patterns)]
        if hits:
            matches = "\n".join(hits[:MAX_MATCHES])
            findings += (f"\n{indicator.heading}\n{indicator.explanation}\n\n**Matches:**\n"
                         f"```\n{matches}\n```\n")
    return findings


def unbounded_dependencies(diff: str) -> list[str]:
    added = [line for line in diff.split("\n") if line.startswith("+") and not line.startswith("+++")]
    return [match for line in added for match in UNBOUNDED_DEP.findall(line)]


def _set_found(found: bool) -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        fh.write(f"found={'true' if found else 'false'}\n")


def _write_report(name: str, text: str) -> None:
    Path(os.environ["RUNNER_TEMP"], name).write_text(text + "\n", encoding="utf-8",
                                                     errors="surrogateescape")


RECORDS = Path(__file__).with_name("supply_chain_reviewed.json")
# Preserve both checks from supply-chain-audit.yml, including their case sensitivity.
PATTERNS = {
    "base64-exec": (
        re.compile(r"base64\.(b64decode|decodebytes|urlsafe_b64decode)", re.IGNORECASE),
        re.compile(r"exec\(|eval\(", re.IGNORECASE),
    ),
    "encoded-subprocess": (
        re.compile(r"subprocess\.(Popen|call|run)\s*\("),
        re.compile(r"base64|\\x[0-9a-f]{2}|chr\(", re.IGNORECASE),
    ),
}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def added_lines(diff: str) -> tuple[set[str], list[tuple[str, int, str]]]:
    """Keep Git's path and HEAD line number; quoted paths cannot match a review record."""
    paths, added = set(), []
    path, number = "", None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, number = "", None
        elif line.startswith("@@ "):
            number = int(re.search(r"\+(\d+)", line)[1])
        elif number is not None:
            if line.startswith("+"):
                added.append((path, number, line[1:]))
                number += 1
            elif line.startswith(" "):
                number += 1
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
            paths.add(path)
    return paths, added


def reviewed_positions(paths: set[str], read_head, records: list[dict]) -> tuple[set, list]:
    """A changed context or duplicate revokes the exact occurrence's approval."""
    approved, revoked = set(), []
    contents = {path: read_head(path).splitlines() for path in paths
                if any(record["path"] == path for record in records)}
    for record in records:
        path, rule = record["path"], record["rule"]
        lines = contents.get(path, [])
        matches = [i for i, line in enumerate(lines) if digest(line) == record["line_sha256"]]
        context = record["context"]
        for index in matches:
            start, end = index - context["before"], index + context["after"] + 1
            valid = (len(matches) == record["expected_occurrences"] and start >= 0
                     and end <= len(lines)
                     and digest("\n".join(lines[start:end])) == context["sha256"])
            key = (path, index + 1, rule)
            if valid:
                approved.add(key)
            else:
                # Re-review the occurrence even if only its source context changed.
                revoked.append((*key, lines[index]))
    return approved, revoked


def scan(diff: str, read_head, records: list[dict]) -> tuple[list[tuple], set]:
    paths, added = added_lines(diff)
    approved, revoked = reviewed_positions(paths, read_head, records)
    findings = {item[:3]: item for item in revoked}
    for path, number, line in added:
        for rule, patterns in PATTERNS.items():
            key = (path, number, rule)
            if all(pattern.search(line) for pattern in patterns) and key not in approved:
                findings[key] = (*key, line)
    return sorted(findings.values()), approved


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        text=True, encoding="utf-8", errors="replace", timeout=120,
    ).stdout


def reviewed_findings(diff: str) -> str:
    records = json.loads(RECORDS.read_text(encoding="utf-8-sig"))
    head = os.environ["_HERMES_PR_HEAD_SHA"]
    findings, _ = scan(diff, lambda path: git("show", f"{head}:{path}"), records)
    reports = []
    for rule, indicator in zip(PATTERNS, INDICATORS, strict=True):
        matches = [f"- `{path}:{line}`: `{text.strip()}`"
                   for path, line, kind, text in findings if kind == rule]
        if matches:
            reports.extend([indicator.heading, indicator.explanation, *matches])
    return "\n".join(reports)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan a PR diff for supply-chain risks.")
    parser.add_argument("scan", choices=("patterns", "dependency-bounds"))
    args = parser.parse_args(argv)
    if args.scan == "patterns":
        report = reviewed_findings(pr_diff(".", *LOCKFILE_EXCLUDES))
        report_name = "supply-chain-findings.md"
    else:
        report = "\n".join(unbounded_dependencies(pr_diff("pyproject.toml")))
        report_name = "supply-chain-unbounded.txt"
    _set_found(bool(report))
    if report:
        _write_report(report_name, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

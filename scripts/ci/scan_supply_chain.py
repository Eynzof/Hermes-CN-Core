#!/usr/bin/env python3
"""Scan added lines for critical payload patterns, with exact reviewed transport records."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

RECORDS = Path(__file__).with_name("supply_chain_reviewed.json")
# Preserve both checks from supply-chain-audit.yml, including their case sensitivity.
PATTERNS = {
    "base64-exec": (
        re.compile(r"base64\.(b64decode|decodebytes|urlsafe_b64decode)", re.I),
        re.compile(r"exec\(|eval\(", re.I),
    ),
    "encoded-subprocess": (
        re.compile(r"subprocess\.(Popen|call|run)\s*\("),
        re.compile(r"base64|\\x[0-9a-f]{2}|chr\(", re.I),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        diff = git("-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff", "--no-renames",
                   "--unified=0", f"{args.base}...{args.head}", "--", ".", ":!uv.lock",
                   ":!*.lock", ":!package-lock.json", ":!yarn.lock")
        records = json.loads(RECORDS.read_text(encoding="utf-8-sig"))
        findings, approved = scan(diff, lambda path: git("show", f"{args.head}:{path}"), records)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"Supply-chain scan could not read the PR: {exc}")
        return 2
    report = ["### Critical supply-chain patterns", ""] if findings else []
    report.extend(f"- `{path}:{line}` ({rule}): `{text.strip()}`"
                  for path, line, rule, text in findings)
    args.output.write_text("\n".join(report), encoding="utf-8")
    print(f"Supply-chain scan: {len(approved)} reviewed occurrences, {len(findings)} critical findings")
    for path, line, rule in sorted(approved):
        print(f"  reviewed: {path}:{line} ({rule})")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

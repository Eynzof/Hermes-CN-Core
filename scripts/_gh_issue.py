#!/usr/bin/env python3
"""Shared ``gh`` plumbing for scripts/fetch_issue.py and scripts/response_issue.py.

The two entry points read and write the fork's issue tracker through the GitHub CLI,
never through HTTP directly: ``gh`` owns the credentials (``gh auth login``), the host
resolution and pagination. This module is the single place that runs it, decodes its
output and turns a failed call into one readable error, so the two scripts cannot drift.

Two details are load-bearing on Windows:

* every call passes ``encoding="utf-8"`` — the console code page (cp936 on a Chinese
  install) would otherwise decode issue titles and JSON payloads into mojibake;
* ``gh api --paginate`` prints one JSON document per page (``[{...}][{...}]``), which
  ``json.loads`` rejects. ``decode_pages`` walks the concatenated documents instead of
  relying on ``--slurp``, so an older ``gh`` behaves the same as a current one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

DEFAULT_REPO = "Eynzof/Hermes-CN-Core"
GH_TIMEOUT_SECONDS = 120


class GhError(RuntimeError):
    """``gh`` is missing, unauthenticated, or the requested call failed."""


def _gh_executable() -> str:
    executable = shutil.which("gh")
    if executable is None:
        raise GhError(
            "gh not found on PATH — install the GitHub CLI (https://cli.github.com), "
            "then run 'gh auth login'."
        )
    return executable


def run_gh(args: list[str], *, stdin: str | None = None) -> str:
    """Run ``gh <args>`` and return stdout text, or raise GhError with gh's own message.

    ``stdin`` is the body piped to commands that read it (``--body-file -``); it keeps
    multi-line, non-ASCII text off the command line, where a Windows console would
    mangle it.
    """
    argv = [_gh_executable(), *args]
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": GH_TIMEOUT_SECONDS,
    }
    if stdin is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = stdin
    try:
        proc = subprocess.run(argv, check=False, **kwargs)
    except subprocess.TimeoutExpired:
        raise GhError(f"gh {' '.join(args)} timed out after {GH_TIMEOUT_SECONDS}s") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "no output"
        raise GhError(f"gh {' '.join(args)} failed (exit {proc.returncode}):\n{detail}")
    return proc.stdout


def decode_pages(text: str) -> list[Any]:
    """Decode one or more concatenated JSON documents read from gh's stdout."""
    decoder = json.JSONDecoder()
    documents: list[Any] = []
    body = text.strip()
    index = 0
    while index < len(body):
        document, index = decoder.raw_decode(body, index)
        documents.append(document)
        while index < len(body) and body[index].isspace():
            index += 1
    return documents


def gh_json(endpoint: str, *, paginate: bool = False) -> Any:
    """Call ``gh api <endpoint>`` and return the decoded body.

    With ``paginate`` the endpoint's pages are flattened into one list, which is what
    every list endpoint here wants; a single-page call returns the object itself.
    """
    documents = decode_pages(run_gh(["api", endpoint, *(["--paginate"] if paginate else [])]))
    if not paginate:
        return documents[0] if documents else None
    rows: list[Any] = []
    for document in documents:
        rows.extend(document if isinstance(document, list) else [document])
    return rows


def split_repo(repo: str) -> tuple[str, str]:
    """Validate ``OWNER/NAME`` and return the two halves."""
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        raise GhError(f"repo must look like OWNER/NAME, got {repo!r}")
    return owner, name

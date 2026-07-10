#!/usr/bin/env python3
"""
File Operations Module

Provides file manipulation capabilities (read, write, patch, search) that work
across all terminal backends (local, docker, ssh, singularity, modal, daytona).

The key insight is that all file operations can be expressed as shell commands,
so we wrap the terminal backend's execute() interface to provide a unified file API.

Usage:
    from tools.file_operations import ShellFileOperations
    from tools.terminal_tool import _active_environments
    
    # Get file operations for a terminal environment
    file_ops = ShellFileOperations(terminal_env)
    
    # Read a file
    result = file_ops.read_file("/path/to/file.py")
    
    # Write a file
    result = file_ops.write_file("/path/to/new.py", "print('hello')")
    
    # Search for content
    result = file_ops.search("TODO", path=".", file_glob="*.py")
"""

import os
from agent.re_compat import re
import shutil
import stat as _stat
import subprocess
import sys
import tempfile
import threading
import zlib
import difflib
import fnmatch
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, ClassVar
from pathlib import Path
from tools.binary_extensions import BINARY_EXTENSIONS

from agent.file_safety import (
    build_write_denied_paths,
    build_write_denied_prefixes,
    is_write_denied as _shared_is_write_denied,
)

# [CN-fork] P-033: On Windows the fork forces Windows PowerShell 5.1 as the
# ONLY shell (git-bash was removed by P-016/P-019), and PowerShell has none of
# the POSIX tools (``wc``/``sed``/``head``/``mktemp``/``cat``) that
# ShellFileOperations shells out to.  That made read_file unusable (#53) and
# made write_file silently report success while writing nothing (#54).  The
# disk primitives below therefore do their I/O *in-process* (the Hermes process
# is itself Python, always present, no interpreter-on-PATH dependency) on a
# LOCAL Windows backend, and run the IDENTICAL shell command everywhere else so
# the proven POSIX path (Linux/macOS local, and all remote docker/ssh/modal
# backends) is byte-for-byte unchanged.  Module-level so tests can monkeypatch.
_IS_WINDOWS = os.name == "nt"


def _parse_optional_int(value: Optional[str]) -> Optional[int]:
    """Parse an int from command/stat output, or ``None`` if non-numeric."""
    try:
        return int((value or "").strip())
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# [CN-fork] P1 (concurrent tool dispatch): striped per-file write locks
# ---------------------------------------------------------------------------
#
# The agent runs concurrent tool calls on a ThreadPoolExecutor, so two writers
# can target the same path at once.  The in-process atomic write below is
# crash-safe on its own (temp-file + ``os.replace``, an atomic rename on POSIX
# *and* Windows), but the surrounding stat-mode / getsize steps — and any
# concurrent reader — are only consistent when same-path writes don't
# interleave.
#
# A single global lock would serialize ALL file I/O (the exact contention this
# work set out to remove).  A per-path dict of locks removes cross-file
# contention but grows unbounded over a long-lived gateway's lifetime.  Lock
# *striping* gets both properties: a fixed pool of locks indexed by the path
# hash.  Distinct files almost always land on distinct stripes and run fully in
# parallel; the same file always maps to the same stripe and is serialized.
# Memory is O(_FILE_LOCK_STRIPES) for the life of the process.
_FILE_LOCK_STRIPES = 64
_file_lock_stripes: tuple = tuple(threading.Lock() for _ in range(_FILE_LOCK_STRIPES))


def _get_file_lock(path: str) -> "threading.Lock":
    """Return the stripe lock guarding writes to ``path``.

    Keyed on the normalized absolute path so two spellings of the same file
    (``a/b.txt`` vs ``a/./b.txt``, or case variants on Windows) share a lock.
    Different files may collide on a stripe — harmless, just a rare and brief
    serialization — but the same file never splits across stripes.
    """
    try:
        key = os.path.normcase(os.path.abspath(path))
    except Exception:  # noqa: BLE001 - never let path canonicalization break a write
        key = path
    return _file_lock_stripes[hash(key) % _FILE_LOCK_STRIPES]


# ---------------------------------------------------------------------------
# In-process write verification (P-042 #4 — CRC-32 integrity check)
# ---------------------------------------------------------------------------
#
# The local Windows in-process atomic write (P-033) encodes the content to a
# temp file and ``os.replace()``s it over the target.  Before P-042 the only
# integrity signal was the post-rename size stat at the write_file caller
# (P-033b): it catches truncation / silent no-ops but not silent *corruption*
# (a flipped byte still has the right length).  A streamed CRC-32 of the temp
# file, compared against the CRC of the bytes we meant to write, closes that
# gap cheaply — it re-reads the just-written temp (warm in the page cache) and
# compares two 4-byte digests instead of building + normalizing a second full
# copy of the content for a string ``==`` (what a naive "re-read and compare"
# costs).  It runs *before* the atomic rename, so a mismatch aborts with the
# original file still intact — never a corrupt swap.
#
# Gated by ``_WRITE_VERIFY_CRC`` (env ``HERMES_WRITE_VERIFY_CRC``) so a caller
# that prizes raw throughput over the read-back can opt out; default ON because
# the cost is negligible and the safety is real.  Resolved once at import
# (writes are hot); tests flip the module attribute directly.


def _resolve_write_verify_crc() -> bool:
    val = os.environ.get("HERMES_WRITE_VERIFY_CRC")
    if val is not None:
        return val.strip().lower() not in ("0", "false", "no", "off", "")
    return True


_WRITE_VERIFY_CRC = _resolve_write_verify_crc()


def _crc32_of_file(path: str, chunk_size: int = 1 << 20) -> "tuple[int, int]":
    """Return ``(crc32, byte_count)`` for *path*, streamed so a large file is
    never held in memory twice.  ``crc32`` is masked to 32 bits so it compares
    equal to ``zlib.crc32(data) & 0xFFFFFFFF``."""
    crc = 0
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            crc = zlib.crc32(chunk, crc)
            size += len(chunk)
    return crc & 0xFFFFFFFF, size


# Opt-in (env ``HERMES_MARK_TEMP_FILES``): tag the ``.hermes-tmp`` staging file
# of an atomic write ``FILE_ATTRIBUTE_TEMPORARY`` so Windows keeps it in cache /
# AV deprioritises it, then clear the bit before the rename so the permanent
# file it becomes is not left marked temporary.  OFF by default — zero syscalls
# added on the hot path unless a deployment measures a Defender win and enables
# it (mirrors the ``powershell_session_reuse`` opt-in).


def _resolve_mark_temp_files() -> bool:
    val = os.environ.get("HERMES_MARK_TEMP_FILES")
    if val is not None:
        return val.strip().lower() in ("1", "true", "yes", "on")
    return False


_MARK_TEMP_FILES = _resolve_mark_temp_files()


def _set_temp_attr(path: str, temporary: bool) -> bool:
    """Best-effort ``FILE_ATTRIBUTE_TEMPORARY`` toggle (lazy import; never raises)."""
    try:
        from tools.environments.windows_env import set_file_temporary

        return set_file_temporary(path, temporary)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Write-path deny list — blocks writes to sensitive system/credential files
# ---------------------------------------------------------------------------

_HOME = str(Path.home())

WRITE_DENIED_PATHS = build_write_denied_paths(_HOME)

WRITE_DENIED_PREFIXES = build_write_denied_prefixes(_HOME)


_OSC_SEQUENCE_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_FENCE_MARKER_RE = re.compile(r"'?\x07?__HERMES_FENCE_[A-Za-z0-9]+__\x07?'?")


def _strip_terminal_fence_leaks(text: str) -> str:
    """Strip leaked terminal fence wrappers from file read output."""
    if not text:
        return text

    cleaned_lines: List[str] = []
    for line in text.splitlines(keepends=True):
        had_terminal_wrapper = "__HERMES_FENCE_" in line or "\x1b]" in line
        cleaned = _OSC_SEQUENCE_RE.sub("", line)
        cleaned = _FENCE_MARKER_RE.sub("", cleaned)
        cleaned = cleaned.replace("\x07", "")
        if had_terminal_wrapper and cleaned.strip("'\r\n\t ") == "":
            continue
        cleaned_lines.append(cleaned)
    return "".join(cleaned_lines)


def _detect_line_ending(sample: str) -> Optional[str]:
    """Return the dominant line ending in ``sample`` or None if undetermined.

    Looks at the first few line breaks and picks ``\\r\\n`` if any are
    present (Windows / DOS), otherwise ``\\n`` (Unix).  Returns ``None``
    for empty / single-line content where we can't tell.  Used to
    preserve the file's original line endings across write_file and
    patch operations — without this the agent's bare-LF tool args
    silently normalize Windows-line-ending files, and patch produces
    mixed endings when only a substituted region changes.
    """
    if not sample:
        return None
    # Look at the first chunk — enough to tell, cheap to scan.
    head = sample[:4096]
    if "\r\n" in head:
        return "\r\n"
    if "\n" in head:
        return "\n"
    return None


def _normalize_line_endings(text: str, target: str) -> str:
    """Convert all line endings in ``text`` to ``target`` (``\\n`` or ``\\r\\n``).

    Idempotent: ``_normalize_line_endings(_normalize_line_endings(x, "\\r\\n"), "\\r\\n") == _normalize_line_endings(x, "\\r\\n")``.
    Strips lone ``\\r`` characters as well, so mixed-ending content is
    homogenized in a single pass.
    """
    # First collapse to LF (handle CRLF and lone CR), then expand if target
    # is CRLF.  Order matters: doing the replacements separately would
    # double-convert a CRLF -> LFLF.
    lf_normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if target == "\n":
        return lf_normalized
    if target == "\r\n":
        return lf_normalized.replace("\n", "\r\n")
    return text


# UTF-8 byte order mark. Some Windows editors (Notepad, older Visual Studio,
# some PowerShell redirects) prepend this invisible 3-byte marker
# (EF BB BF == U+FEFF) to UTF-8 text files. It renders as nothing but is a
# real character at the start of the decoded string, so without handling it:
#   - read_file would surface a stray U+FEFF as the first character (the
#     model sees a phantom char before `import ...`), and
#   - patch matches against the true first line would miss, and write_file
#     would silently drop or double the marker on rewrite.
# We strip it on read so the model sees clean content, and restore it on
# write when the original file had one — exactly mirroring the line-ending
# preservation above (detect on disk, preserve across the edit).
_UTF8_BOM = "\ufeff"


def _strip_bom(text: str) -> tuple[str, bool]:
    """Return (text-without-leading-BOM, had_bom).

    Only a single leading BOM is stripped; a BOM appearing mid-content is
    left alone (it's legitimate data there, not a file marker).
    """
    if text and text.startswith(_UTF8_BOM):
        return text[len(_UTF8_BOM):], True
    return text, False


def _has_bom(text: Optional[str]) -> bool:
    """True if ``text`` begins with a UTF-8 BOM."""
    return bool(text) and text.startswith(_UTF8_BOM)


# [CN-fork] P-037: legacy fallback codecs for in-process whole-file reads.
#
# The P-033 in-process read primitives replaced PowerShell ``Get-Content``,
# which decodes via the system code page.  Hard-coding ``utf-8`` turned every
# non-ASCII byte of a GBK/cp936 file — the common case on Chinese Windows, this
# fork's audience — into U+FFFD, and a read→patch→write round-trip then
# *persisted* that corruption (write_file re-encodes as UTF-8): silent data
# loss.  On Windows ``"mbcs"`` maps to the active ANSI code page (``GetACP()``,
# e.g. cp936/GBK on zh-CN) regardless of PYTHONUTF8.  Exposed at module level so
# tests can substitute a concrete codec (the ``"mbcs"`` alias exists only on
# Windows).
_INPROC_FALLBACK_ENCODINGS: tuple[str, ...] = ("mbcs",) if _IS_WINDOWS else ()


def _decode_file_bytes(
    data: bytes, fallbacks: Optional[tuple[str, ...]] = None
) -> str:
    """Decode raw file bytes to text, tolerating non-UTF-8 legacy encodings.

    Try UTF-8 strictly first (the modern default; strict decoding doubles as a
    cheap "is this UTF-8?" probe).  On failure, try each legacy fallback codec
    (the Windows ANSI code page by default), and only as a last resort fall back
    to lossy ``utf-8`` replacement so a genuinely binary blob still returns
    *something* rather than raising.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for enc in (_INPROC_FALLBACK_ENCODINGS if fallbacks is None else fallbacks):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _is_write_denied(path: str) -> bool:
    """Return True if path is on the write deny list."""
    return _shared_is_write_denied(path)


# =============================================================================
# Result Data Classes
# =============================================================================

@dataclass
class ReadResult:
    """Result from reading a file."""
    content: str = ""
    total_lines: int = 0
    file_size: int = 0
    truncated: bool = False
    hint: Optional[str] = None
    is_binary: bool = False
    is_image: bool = False
    base64_content: Optional[str] = None
    mime_type: Optional[str] = None
    dimensions: Optional[str] = None  # For images: "WIDTHxHEIGHT"
    error: Optional[str] = None
    similar_files: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None and v != []}


@dataclass
class WriteResult:
    """Result from writing a file."""
    bytes_written: int = 0
    dirs_created: bool = False
    lint: Optional[Dict[str, Any]] = None
    # Semantic diagnostics from the LSP layer, when applicable.  Kept in
    # its own field (not folded into ``lint``) so the model and any
    # downstream parsers can read syntax errors and semantic errors as
    # separate signals.  ``None`` when LSP is disabled, when the file
    # isn't in a git workspace, or when no diagnostics were introduced
    # by this edit.
    lsp_diagnostics: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class PatchResult:
    """Result from patching a file."""
    success: bool = False
    diff: str = ""
    files_modified: List[str] = field(default_factory=list)
    files_created: List[str] = field(default_factory=list)
    files_deleted: List[str] = field(default_factory=list)
    lint: Optional[Dict[str, Any]] = None
    # See :class:`WriteResult.lsp_diagnostics`.
    lsp_diagnostics: Optional[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        result = {"success": self.success}
        if self.diff:
            result["diff"] = self.diff
        if self.files_modified:
            result["files_modified"] = self.files_modified
        if self.files_created:
            result["files_created"] = self.files_created
        if self.files_deleted:
            result["files_deleted"] = self.files_deleted
        if self.lint:
            result["lint"] = self.lint
        if self.lsp_diagnostics:
            result["lsp_diagnostics"] = self.lsp_diagnostics
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class SearchMatch:
    """A single search match."""
    path: str
    line_number: int
    content: str
    mtime: float = 0.0  # Modification time for sorting


@dataclass
class SearchResult:
    """Result from searching."""
    matches: List[SearchMatch] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    total_count: int = 0
    truncated: bool = False
    limit_reason: Optional[str] = None
    warning: Optional[str] = None
    error: Optional[str] = None
    
    # Densify content-mode matches into a path-grouped text block above this
    # many matches. Below it, the verbose array is already compact enough that
    # the path-grouping header costs more than it saves.
    _DENSIFY_MIN_MATCHES: ClassVar[int] = 5

    def _densify_matches(self) -> Optional[str]:
        """Render content-mode matches as a compact, path-grouped text block.

        The verbose form repeats the ``{"path","line","content"}`` keys and the
        full path string for every match. This groups consecutive matches by
        path (path printed once, then ``  <line>: <content>`` rows), which is
        lossless — every path, line number, and content byte is preserved — and
        readable by the model without any decode step.

        Returns ``None`` when densification is not worthwhile (too few matches),
        so the caller falls back to the verbose array.
        """
        if len(self.matches) < self._DENSIFY_MIN_MATCHES:
            return None
        # ripgrep emits matches path-ordered (all hits in a file are
        # consecutive), so grouping on path change collapses each file to a
        # single header without reordering results.
        lines: list[str] = []
        current_path: Optional[str] = None
        for m in self.matches:
            if m.path != current_path:
                lines.append(m.path)
                current_path = m.path
            # rstrip trailing whitespace only; leading indentation in code is
            # meaningful and preserved verbatim after the "<line>: " prefix.
            lines.append(f"  {m.line_number}: {m.content.rstrip()}")
        return "\n".join(lines)

    def to_dict(self, densify: bool = False) -> dict:
        result: dict[str, object] = {"total_count": self.total_count}
        if self.matches:
            dense = self._densify_matches() if densify else None
            if dense is not None:
                # Self-describing: the format key tells the model how to read
                # the block so it never has to guess the shape.
                result["matches_format"] = (
                    "path-grouped: each file path on its own line, followed by "
                    "indented '<line>: <content>' rows for matches in that file"
                )
                result["matches_text"] = dense
            else:
                result["matches"] = [
                    {"path": m.path, "line": m.line_number, "content": m.content}
                    for m in self.matches
                ]
        if self.files:
            result["files"] = self.files
        if self.counts:
            result["counts"] = self.counts
        if self.truncated:
            result["truncated"] = True
        if self.limit_reason:
            result["limit_reason"] = self.limit_reason
        if self.warning:
            result["warning"] = self.warning
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class LintResult:
    """Result from linting a file."""
    success: bool = True
    skipped: bool = False
    output: str = ""
    message: str = ""
    
    def to_dict(self) -> dict:
        if self.skipped:
            return {"status": "skipped", "message": self.message}
        result = {"status": "ok" if self.success else "error", "output": self.output}
        if self.message:
            result["message"] = self.message
        return result


@dataclass
class ExecuteResult:
    """Result from executing a shell command."""
    stdout: str = ""
    exit_code: int = 0


_SEARCH_TIMEOUT_MARKER_RE = re.compile(r"\n?\[Command timed out after \d+s\]\s*$")


def _search_stdout_and_limit(result: ExecuteResult) -> tuple[str, Optional[str]]:
    """Return stdout cleaned for parsing and a limit reason for search timeouts."""
    if result.exit_code == 124:
        return _SEARCH_TIMEOUT_MARKER_RE.sub("", result.stdout), "search_timeout"
    return result.stdout, None


def _split_tool_diagnostics(output: str) -> tuple[str, str]:
    """Separate rg/grep diagnostic lines from real match output.

    ``_exec`` runs commands with ``stderr=subprocess.STDOUT``, so error and
    warning text from ``rg``/``grep`` is interleaved with match lines in a
    single stream. Diagnostics must not be parsed as matches, and on a hard
    failure they are the error message to surface.

    Returns ``(diagnostics, payload)`` where ``payload`` contains only lines
    that look like real search output — a match line (``file:line:content``),
    a files-only path, a count line, or a context line/separator. Everything
    else (tool-prefixed errors, rg's multi-line ``regex parse error`` block
    with its indented carets, blank lines) is folded into ``diagnostics``.

    Classifying by *shape* rather than by error prefix is what lets the
    exit-2 guard distinguish a pure failure (no usable payload → surface the
    error) from a partial failure (some files matched, one was unreadable →
    keep the matches). It also means error text can never be mis-parsed as a
    match, a latent bug that predates the exit-code fix.
    """
    diagnostics: list[str] = []
    payload: list[str] = []
    for line in output.split('\n'):
        if not line.strip():
            continue
        # Tool diagnostics always carry the "<tool>: " prefix (e.g.
        # "rg: <file>: Permission denied", "grep: Invalid regular
        # expression", "rg: regex parse error:"). Check this first: a real
        # match path can legitimately contain "-<digit>" (e.g. a tmp dir like
        # ".../pytest-686/..."), which the shape regex would otherwise treat
        # as a match line.
        stripped = line.lstrip()
        if stripped.startswith("rg: ") or stripped.startswith("grep: "):
            diagnostics.append(line)
            continue
        # Otherwise classify by output shape. rg's regex-parse-error block
        # also emits an indented caret line and a trailing "error: ..." line
        # with no tool prefix; neither matches a search-output shape, so they
        # fall through to diagnostics.
        #   match / count : "<path>:<...>"   (has a colon; rg -c uses path:count)
        #   files_only    : "<path>"         (no whitespace, no leading colon)
        #   context line  : "<path>-<line>-" or the "--" group separator
        if line == "--" or _SEARCH_OUTPUT_RE.match(line):
            payload.append(line)
        else:
            diagnostics.append(line)
    return '\n'.join(diagnostics), '\n'.join(payload)


# A real rg/grep output line starts with a path token and is followed by a
# ``:`` (match/count), a ``-`` (context), or nothing (files_only). Tool
# diagnostics ("rg: ...", "grep: ...", "error: ...", indented carets) never
# match because the path token forbids whitespace and a leading tool prefix
# like "rg" is followed by ": " (space) which the negated class rejects.
_SEARCH_OUTPUT_RE = re.compile(r'^([A-Za-z]:)?[^\s:][^\n]*?[:\-]\d|^[^\s:][^\s]*$')


# --- In-process search fallback (local backend without ripgrep) ---------------
# The shell-command search paths assume a POSIX shell + GNU rg/grep/find. On the
# Windows/PowerShell local backend none of that holds (the very `command -v`
# probe in `_has_command` can't run), so search hard-errored even with rg on
# PATH. These constants bound a portable os.walk-based fallback used only on the
# local backend. It can't honor .gitignore the way rg does, so we prune the
# usual vendored/cache dirs to approximate it and keep wide trees fast.
_FALLBACK_PRUNE_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", ".hg", ".svn", ".venv", "venv",
    "dist", "build", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".idea", ".gradle", ".next", ".cache",
})
# Hard cap on files visited per search so a pathological tree can't hang the
# agent. When hit, results are marked truncated with a limit_reason.
_FALLBACK_MAX_FILES_SCANNED = 50_000
# Skip files larger than this for content search (binaries/blobs); matches the
# spirit of rg's default large-file handling and avoids reading huge artifacts.
_FALLBACK_MAX_CONTENT_BYTES = 8 * 1024 * 1024


def _parse_search_context_line(line: str) -> tuple[str, int, str] | None:
    """Parse grep/rg context output in ``path-line-content`` format.

    Context lines are ambiguous because filenames may legitimately contain
    ``-<digits>-`` segments. Prefer the rightmost numeric separator so a path
    like ``dir/file-12-name.py-8-context`` resolves to
    ``dir/file-12-name.py`` line ``8`` instead of truncating at ``file``.
    """
    if not line or line == "--":
        return None

    match = None
    for candidate in re.finditer(r'-(\d+)-', line):
        match = candidate

    if match is None:
        return None

    path = line[:match.start()]
    if not path:
        return None

    return path, int(match.group(1)), line[match.end():]


def _parse_search_content_output(
    stdout: str,
    output_mode: str,
    context: int,
    limit: int,
    offset: int,
    limit_reason: Optional[str] = None,
) -> SearchResult:
    """Parse rg/grep content-search output into a SearchResult.

    Handles ``content`` (default), ``files_only``, and ``count`` output modes.
    Context lines (dash-separated) are parsed only when ``context > 0``.
    """
    if output_mode == "files_only":
        all_files = [f for f in stdout.strip().split('\n') if f]
        total = len(all_files)
        page = all_files[offset:offset + limit]
        return SearchResult(
            files=page,
            total_count=total,
            truncated=bool(limit_reason),
            limit_reason=limit_reason,
        )

    if output_mode == "count":
        counts = {}
        for line in stdout.strip().split('\n'):
            if ':' in line:
                parts = line.rsplit(':', 1)
                if len(parts) == 2:
                    try:
                        counts[parts[0]] = int(parts[1])
                    except ValueError:
                        pass
        return SearchResult(
            counts=counts,
            total_count=sum(counts.values()),
            truncated=bool(limit_reason),
            limit_reason=limit_reason,
        )

    # Content mode: parse match lines and optional context lines.
    # rg match lines:   "file:lineno:content"  (colon separator)
    # rg context lines: "file-lineno-content"   (dash separator)
    # rg group seps:    "--"
    # Note: on Windows, paths contain drive letters (e.g. C:\path),
    # so naive split(":") breaks. Use regex to handle both platforms.
    _match_re = re.compile(r'^([A-Za-z]:)?(.*?):(\d+):(.*)$')
    matches = []
    for line in stdout.strip().split('\n'):
        if not line or line == "--":
            continue

        # Try match line first (colon-separated: file:line:content)
        m = _match_re.match(line)
        if m:
            matches.append(SearchMatch(
                path=(m.group(1) or '') + m.group(2),
                line_number=int(m.group(3)),
                content=m.group(4)[:500]
            ))
            continue

        # Try context line (dash-separated: file-line-content)
        # Only attempt if context was requested to avoid false positives
        if context > 0:
            parsed = _parse_search_context_line(line)
            if parsed:
                matches.append(SearchMatch(
                    path=parsed[0],
                    line_number=parsed[1],
                    content=parsed[2][:500]
                ))

    total = len(matches)
    page = matches[offset:offset + limit]
    return SearchResult(
        matches=page,
        total_count=total,
        truncated=total > offset + limit or bool(limit_reason),
        limit_reason=limit_reason,
    )


# =============================================================================
# Abstract Interface
# =============================================================================

class FileOperations(ABC):
    """Abstract interface for file operations across terminal backends."""
    
    @abstractmethod
    def read_file(self, path: str, offset: int = 1, limit: int = 500) -> ReadResult:
        """Read a file with pagination support."""
        ...

    @abstractmethod
    def read_file_raw(self, path: str) -> ReadResult:
        """Read the complete file content as a plain string.

        No pagination, no line-number prefixes, no per-line truncation.
        Returns ReadResult with .content = full file text, .error set on
        failure. Always reads to EOF regardless of file size.
        """
        ...

    @abstractmethod
    def write_file(self, path: str, content: str) -> WriteResult:
        """Write content to a file, creating directories as needed."""
        ...

    @abstractmethod
    def patch_replace(self, path: str, old_string: str, new_string: str,
                      replace_all: bool = False) -> PatchResult:
        """Replace text in a file using fuzzy matching."""
        ...

    @abstractmethod
    def patch_v4a(self, patch_content: str) -> PatchResult:
        """Apply a V4A format patch."""
        ...

    @abstractmethod
    def delete_file(self, path: str) -> WriteResult:
        """Delete a file. Returns WriteResult with .error set on failure."""
        ...

    def delete_path(self, path: str, recursive: bool = False) -> WriteResult:
        """Cross-platform delete that handles files and (with recursive=True)
        directory trees. Default implementation delegates to ``delete_file``
        for the non-recursive case; backends with native recursive support
        should override.
        """
        if recursive:
            return WriteResult(error="Recursive delete not implemented for this backend")
        return self.delete_file(path)

    @abstractmethod
    def move_file(self, src: str, dst: str) -> WriteResult:
        """Move/rename a file from src to dst. Returns WriteResult with .error set on failure."""
        ...

    @abstractmethod
    def search(self, pattern: str, path: str = ".", target: str = "content",
               file_glob: Optional[str] = None, limit: int = 50, offset: int = 0,
               output_mode: str = "content", context: int = 0) -> SearchResult:
        """Search for content or files."""
        ...


# =============================================================================
# Shell-based Implementation
# =============================================================================

# Image extensions (subset of binary that we can return as base64)
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico'}

# Shell-based linters by file extension.  Invoked via _exec() with the
# filesystem path.  Cover languages where a compile/type check needs an
# external toolchain (py_compile, node, tsc, go vet, rustfmt).
LINTERS = {
    '.py': 'python -m py_compile {file} 2>&1',
    '.js': 'node --check {file} 2>&1',
    '.ts': 'npx tsc --noEmit {file} 2>&1',
    '.go': 'go vet {file} 2>&1',
    '.rs': 'rustfmt --check {file} 2>&1',
}

# Extensions where the per-file shell linter is structurally weaker than
# a real LSP server AND produces phantom errors on real-world projects:
#
# - ``.ts``: ``tsc --noEmit FILE.ts`` ignores ``tsconfig.json`` and
#   defaults to no-lib / ES5, so every ES2015+ stdlib reference
#   (``Promise``, ``Map``, ``Set``, ``ReadonlySet``, ``Iterable``,
#   ``Math.imul``, ``Number.isFinite``, etc.) reports as missing.  This
#   floods the agent's lint field with 20K+ tokens of false positives on
#   every edit.  No supported tsc flag fixes the single-file invocation;
#   the canonical replacement is ``tsserver`` via LSP, which respects
#   tsconfig and gives true diagnostics.
#
#   ``.tsx`` is intentionally NOT in ``LINTERS`` (and therefore not
#   here): it has no shell linter entry, so it falls through to the
#   ``ext not in LINTERS`` skip case unchanged.  Pre-PR behavior:
#   ``.tsx`` was implicitly ``skipped``.  Keeping it that way means
#   ``.tsx`` edits with LSP disabled get no per-file syntax check
#   (same as before this PR) instead of the broken ``tsc`` invocation
#   that ``.ts`` used to get.  When LSP is enabled, ``.tsx`` is covered
#   by the LSP tier via ``_maybe_lsp_diagnostics`` exactly as ``.ts``.
#
# - ``.go``: ``go vet FILE.go`` fails outside a module / GOPATH with
#   "cannot find package" — already partially handled by
#   ``_LINTER_UNUSABLE_PATTERNS`` but only when the package error is the
#   ONLY output; mixed real+phantom output still leaks through.
#   ``gopls`` is the canonical replacement.
#
# - ``.rs``: ``rustfmt --check FILE.rs`` is style, not type-checking, and
#   rejects non-Cargo project files.  ``rust-analyzer`` is the canonical
#   replacement.
#
# When the LSP service is configured AND ``enabled_for(path)`` for this
# extension's file, ``_check_lint`` skips the shell linter for these
# extensions — the ``lsp_diagnostics`` channel carries the real signal.
# Everything else in ``LINTERS`` (Python ``py_compile``, ``node --check``)
# is fast, file-local, and correct, so it runs unconditionally.
_SHELL_LINTER_LSP_REDUNDANT = frozenset({'.ts', '.go', '.rs'})


# Patterns that indicate the linter base command exists on PATH but
# couldn't actually run — e.g. ``npx tsc`` when tsc isn't installed in
# node_modules, or rustfmt complaining there's no Cargo project.  When
# any of these substrings appears in the linter output, ``_check_lint``
# returns ``skipped`` instead of ``error`` so:
#
# 1. The write isn't flagged for a tooling problem the agent can't fix.
# 2. The LSP semantic tier still runs (it gates on success/skipped).
#
# Patterns are matched case-insensitively against linter stdout.
_LINTER_UNUSABLE_PATTERNS = {
    'npx': (
        # npx prints this banner when the package isn't installed locally
        # AND it can't auto-install (no internet, registry off, etc.) or
        # when the binary it tried to run is the wrong one.
        'this is not the tsc command you are looking for',
        # npx with --no-install resolution failures
        'could not determine executable to run',
        'not found in npm registry',
    ),
    'rustfmt': (
        # rustfmt outside a Cargo project
        'no input filename given',
        'error: not a workspace',
    ),
    'go': (
        # ``go vet`` on a file outside a module / GOPATH
        'cannot find package',
        'go: cannot find main module',
    ),
}


def _looks_like_linter_unusable(base_cmd: str, output: str) -> bool:
    """Return True iff ``output`` from ``base_cmd`` indicates the linter
    itself couldn't run (a tooling gap), as opposed to a real lint error
    in the file being checked.

    ``base_cmd`` is the first word of the linter command line (``npx``,
    ``rustfmt``, ``go``, ...).  ``output`` is the stdout/stderr captured
    from running it.
    """
    patterns = _LINTER_UNUSABLE_PATTERNS.get(base_cmd)
    if not patterns:
        return False
    lower = output.lower()
    return any(p in lower for p in patterns)


def _lint_json_inproc(content: str) -> tuple[bool, str]:
    """In-process JSON syntax check.  Returns (ok, error_message)."""
    import orjson as _json
    try:
        _json.loads(content)
        return True, ""
    except _json.JSONDecodeError as e:
        return False, f"JSONDecodeError: {e.msg} (line {e.lineno}, column {e.colno})"
    except Exception as e:  # noqa: BLE001 — any parse failure is a lint failure
        return False, f"{type(e).__name__}: {e}"


def _lint_yaml_inproc(content: str) -> tuple[bool, str]:
    """In-process YAML syntax check.  Returns (ok, error_message).

    Skipped gracefully if PyYAML isn't installed — YAML parsing is optional.
    """
    try:
        import yaml as _yaml
    except ImportError:
        # PyYAML not available — skip silently, caller treats as no linter.
        return True, "__SKIP__"
    try:
        _yaml.safe_load(content)
        return True, ""
    except _yaml.YAMLError as e:
        return False, f"YAMLError: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _lint_toml_inproc(content: str) -> tuple[bool, str]:
    """In-process TOML syntax check (stdlib tomllib, Python 3.11+)."""
    try:
        import tomllib as _toml
    except ImportError:
        # Pre-3.11 fallback via tomli, if installed.
        try:
            import tomli as _toml  # type: ignore[no-redef]
        except ImportError:
            return True, "__SKIP__"
    try:
        _toml.loads(content)
        return True, ""
    except Exception as e:  # tomllib raises TOMLDecodeError, a ValueError subclass
        return False, f"{type(e).__name__}: {e}"


def _lint_python_inproc(content: str) -> tuple[bool, str]:
    """In-process Python syntax check via ast.parse.

    Catches SyntaxError, IndentationError, and everything else the
    ast module rejects — matching py_compile's scope but with no
    subprocess overhead and no dependency on a ``python`` in PATH.
    """
    import ast as _ast
    try:
        _ast.parse(content)
        return True, ""
    except SyntaxError as e:
        loc = f" (line {e.lineno}, column {e.offset})" if e.lineno else ""
        return False, f"{type(e).__name__}: {e.msg}{loc}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# In-process linters by file extension.  Preferred over shell linters when
# present — no subprocess overhead, microseconds per call.  Each callable
# takes file content (str) and returns (ok: bool, error: str).  An error
# string of ``"__SKIP__"`` signals the linter isn't available (missing
# dependency) and should be treated as "no linter".
LINTERS_INPROC = {
    '.py': _lint_python_inproc,
    '.json': _lint_json_inproc,
    '.yaml': _lint_yaml_inproc,
    '.yml': _lint_yaml_inproc,
    '.toml': _lint_toml_inproc,
}

# Max limits for read operations
MAX_LINES = 2000
MAX_LINE_LENGTH = 2000
MAX_FILE_SIZE = 50 * 1024  # 50KB
DEFAULT_READ_OFFSET = 1
DEFAULT_READ_LIMIT = 500
DEFAULT_SEARCH_OFFSET = 0
DEFAULT_SEARCH_LIMIT = 50


def _coerce_int(value: Any, default: int) -> int:
    """Best-effort integer coercion for tool pagination inputs."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_read_pagination(offset: Any = DEFAULT_READ_OFFSET,
                              limit: Any = DEFAULT_READ_LIMIT) -> tuple[int, int]:
    """Return safe read_file pagination bounds.

    Tool schemas declare minimum/maximum values, but not every caller or
    provider enforces schemas before dispatch. Clamp here so invalid values
    cannot leak into sed ranges like ``0,-1p``.

    The upper bound on ``limit`` comes from ``tool_output.max_lines`` in
    config.yaml (defaults to the module-level ``MAX_LINES`` constant).
    """
    from tools.tool_output_limits import get_max_lines
    max_lines = get_max_lines()
    normalized_offset = max(1, _coerce_int(offset, DEFAULT_READ_OFFSET))
    normalized_limit = _coerce_int(limit, DEFAULT_READ_LIMIT)
    normalized_limit = max(1, min(normalized_limit, max_lines))
    return normalized_offset, normalized_limit


def normalize_search_pagination(offset: Any = DEFAULT_SEARCH_OFFSET,
                                limit: Any = DEFAULT_SEARCH_LIMIT) -> tuple[int, int]:
    """Return safe search pagination bounds for shell head/tail pipelines."""
    normalized_offset = max(0, _coerce_int(offset, DEFAULT_SEARCH_OFFSET))
    normalized_limit = max(1, _coerce_int(limit, DEFAULT_SEARCH_LIMIT))
    return normalized_offset, normalized_limit


_REGEX_NEWLINE_ESCAPE_RE = re.compile(r"(?<!\\)(?:\\\\)*\\n")


def _pattern_has_regex_newline(pattern: str) -> bool:
    """Return True when a content-search regex tries to match a newline.

    ``search_files`` runs rg/grep in line-oriented mode, not rg
    ``-U``/``--multiline`` mode, so newline regexes cannot match across
    lines.  Detect both a literal newline already decoded into the tool
    argument and a regex ``\n`` escape (odd number of backslashes before
    ``n``).  Even backslashes, e.g. ``\\n``, mean a literal backslash+n
    search and should not warn.
    """
    return "\n" in pattern or bool(_REGEX_NEWLINE_ESCAPE_RE.search(pattern))


def _is_line_oriented_newline_error(error: Optional[str]) -> bool:
    """Return True for rg's hard error when multiline mode is required."""
    if not error:
        return False
    return "literal \"\\n\" is not allowed" in error and "--multiline" in error


def _maybe_warn_line_oriented_newline_pattern(result: SearchResult, pattern: str) -> SearchResult:
    """Attach a newline-regex warning only when search found no usable results."""
    if result.total_count != 0 or not _pattern_has_regex_newline(pattern):
        return result
    if result.error and not _is_line_oriented_newline_error(result.error):
        return result
    result.error = None
    result.warning = (
        "0 results found. Note: search_files content search is line-oriented "
        "and does not run ripgrep with -U/--multiline, so `\\n` in the regex "
        "does not match line breaks. Use context=N to inspect neighboring "
        "lines, or escape as `\\\\n` when searching for a literal backslash+n."
    )
    return result


class ShellFileOperations(FileOperations):
    """
    File operations implemented via shell commands.
    
    Works with ANY terminal backend that has execute(command, cwd) method.
    This includes local, docker, singularity, ssh, modal, and daytona environments.
    """
    
    def __init__(self, terminal_env, cwd: str = None):
        """
        Initialize file operations with a terminal environment.

        Args:
            terminal_env: Any object with execute(command, cwd) method.
                         Returns {"output": str, "returncode": int}
            cwd: Optional explicit fallback cwd when the terminal env has
                 no cwd attribute (rare — most backends track cwd live).

        Note:
            Every _exec() call prefers the LIVE ``terminal_env.cwd`` over
            ``self.cwd`` so ``cd`` commands run via the terminal tool are
            picked up immediately.  ``self.cwd`` is only used as a fallback
            when the env has no cwd at all — it is NOT the authoritative
            cwd, despite being settable at init time.

            Historical bug (fixed): prior versions of this class used the
            init-time cwd for every _exec() call, which caused relative
            paths passed to patch/read/write to target the wrong directory
            after the user ran ``cd`` in the terminal.  Patches would
            claim success and return a plausible diff but land in the
            original directory, producing apparent silent failures.
        """
        self.env = terminal_env
        # Determine cwd from various possible sources.
        # IMPORTANT: do NOT fall back to os.getcwd() -- that's the HOST's local
        # path which doesn't exist inside container/cloud backends (modal, docker).
        # If nothing provides a cwd, use "/" as a safe universal default.
        self.cwd = cwd or getattr(terminal_env, 'cwd', None) or \
                   getattr(getattr(terminal_env, 'config', None), 'cwd', None) or "/"

        # Cache for command availability checks
        self._command_cache: Dict[str, bool] = {}
    
    def _exec(self, command: str, cwd: str = None, timeout: int = None,
              stdin_data: str = None) -> ExecuteResult:
        """Execute command via terminal backend.

        Args:
            stdin_data: If provided, piped to the process's stdin instead of
                        embedding in the command string. Bypasses ARG_MAX.

        Cwd resolution order (critical — see class docstring):
          1. Explicit ``cwd`` arg (if provided)
          2. Live ``self.env.cwd`` (tracks ``cd`` commands run via terminal)
          3. Init-time ``self.cwd`` (fallback when env has no cwd attribute)

        This ordering ensures relative paths in file operations follow the
        terminal's current directory — not the directory this file_ops was
        originally created in.  See test_file_ops_cwd_tracking.py.
        """
        # On Windows, LocalEnvironment runs commands via PowerShell which
        # doesn't understand POSIX ``/dev/null`` redirect syntax.  Translate
        # the most common patterns to their PowerShell equivalents.
        if sys.platform == "win32":
            command = command.replace(">/dev/null 2>&1", "*>$null")
            command = command.replace("2>/dev/null", "2>$null")
            command = command.replace(">/dev/null", ">$null")
        kwargs = {}
        if timeout:
            kwargs['timeout'] = timeout
        if stdin_data is not None:
            kwargs['stdin_data'] = stdin_data

        # Resolve cwd from the live env so `cd` commands are picked up.
        # Fall through to init-time self.cwd only if the env doesn't track cwd.
        effective_cwd = cwd or getattr(self.env, 'cwd', None) or self.cwd
        result = self.env.execute(command, cwd=effective_cwd, **kwargs)
        return ExecuteResult(
            stdout=result.get("output", ""),
            exit_code=result.get("returncode", 0)
        )
    
    def _has_command(self, cmd: str) -> bool:
        """Check if a command exists in the environment (cached)."""
        if cmd not in self._command_cache:
            result = self._exec(f"command -v {cmd} >/dev/null 2>&1 && echo 'yes'")
            self._command_cache[cmd] = result.stdout.strip() == 'yes'
        return self._command_cache[cmd]
    
    def _is_likely_binary(self, path: str, content_sample: str = None) -> bool:
        """
        Check if a file is likely binary.
        
        Uses extension check (fast) + content analysis (fallback).
        """
        ext = os.path.splitext(path)[1].lower()
        if ext in BINARY_EXTENSIONS:
            return True
        
        # Content analysis: >30% non-printable chars = binary
        if content_sample:
            non_printable = sum(1 for c in content_sample[:1000]
                               if ord(c) < 32 and c not in '\n\r\t')
            return non_printable / min(len(content_sample), 1000) > 0.30
        
        return False
    
    def _is_image(self, path: str) -> bool:
        """Check if file is an image we can return as base64."""
        ext = os.path.splitext(path)[1].lower()
        return ext in IMAGE_EXTENSIONS
    
    def _add_line_numbers(self, content: str, start_line: int = 1) -> str:
        """Add line numbers to content in ``LINE_NUM|CONTENT`` format.

        The gutter uses a compact ``<n>|`` prefix (e.g. ``34|foo``) rather
        than a fixed-width zero/space-padded one (``    34|foo``). The
        padding was pure token overhead: on dense source the padded gutter
        cost ~48% more tokens than the bare content and ~16% more than the
        compact form, because the leading spaces + zero-padding tokenize
        into extra tokens on every single line. An A/B (Sonnet 4.6, 2
        passes) showed the compact gutter matches the padded gutter on
        line-reference / patch / value-lookup / structure tasks (4/4 both),
        while dropping line numbers entirely regressed line-referencing
        (the model hand-counted and was off-by-one, 3/4) — so we keep the
        numbers, just not the padding.
        """
        from tools.tool_output_limits import get_max_line_length
        max_line_length = get_max_line_length()
        lines = content.split('\n')
        numbered = []
        for i, line in enumerate(lines, start=start_line):
            # Truncate long lines
            if len(line) > max_line_length:
                line = line[:max_line_length] + "... [truncated]"
            numbered.append(f"{i}|{line}")
        return '\n'.join(numbered)
    
    def _expand_path(self, path: str) -> str:
        """
        Expand shell-style paths like ~ and ~user to absolute paths.
        
        This must be done BEFORE shell escaping, since ~ doesn't expand
        inside single quotes.
        """
        if not path:
            return path
        
        # Handle ~ and ~user
        if path.startswith('~'):
            # Get home directory via the terminal environment
            result = self._exec("echo $HOME")
            if result.exit_code == 0 and result.stdout.strip():
                home = result.stdout.strip()
                if path == '~':
                    return home
                elif path.startswith('~/'):
                    return home + path[1:]  # Replace ~ with home
                # ~username format - extract and validate username before
                # letting shell expand it (prevent shell injection via
                # paths like "~; rm -rf /").
                rest = path[1:]  # strip leading ~
                slash_idx = rest.find('/')
                username = rest[:slash_idx] if slash_idx >= 0 else rest
                if username and re.fullmatch(r'[a-zA-Z0-9._-]+', username):
                    # Only expand ~username (not the full path) to avoid shell
                    # injection via path suffixes like "~user/$(malicious)".
                    expand_result = self._exec(f"echo ~{username}")
                    if expand_result.exit_code == 0 and expand_result.stdout.strip():
                        user_home = expand_result.stdout.strip()
                        suffix = path[1 + len(username):]  # e.g. "/rest/of/path"
                        return user_home + suffix
        
        return path
    
    def _escape_shell_arg(self, arg: str) -> str:
        """Escape a string for safe use in shell commands."""
        # Use single quotes and escape any single quotes in the string
        return "'" + arg.replace("'", "'\"'\"'") + "'"

    # =====================================================================
    # [CN-fork] P-033: cross-platform disk primitives
    #
    # Each primitive does in-process Python I/O on a LOCAL Windows backend
    # (where the POSIX shell tools don't exist under PowerShell 5.1) and runs
    # the IDENTICAL shell command everywhere else.  read_file / read_file_raw /
    # write_file call these instead of inlining ``self._exec(f"wc -c ...")`` so
    # the high-level logic (BOM/line-ending/lint/LSP/pagination) is untouched.
    # =====================================================================

    def _use_inproc_io(self) -> bool:
        """True iff disk I/O must bypass the shell (local Windows backend).

        References the module-level ``_IS_WINDOWS`` at call time so tests can
        monkeypatch it to exercise the in-process path on non-Windows CI.
        """
        return _IS_WINDOWS and self._is_local_env()

    def _abs_local(self, path: str) -> str:
        """Resolve ``path`` against the live tracked cwd for in-process I/O.

        Mirrors :meth:`_exec`'s cwd resolution (live ``env.cwd`` → init-time
        ``self.cwd``) so a relative path follows ``cd`` exactly like the shell
        path does.  ``path`` has already been ``~``-expanded by the caller.
        """
        if os.path.isabs(path):
            return path
        base = getattr(self.env, "cwd", None) or getattr(self, "cwd", None) or "."
        return os.path.join(base, path)

    def _prim_stat_size(self, path: str) -> "ExecuteResult":
        """Byte size of ``path`` (POSIX: ``wc -c``). Non-zero exit if missing."""
        if self._use_inproc_io():
            try:
                return ExecuteResult(stdout=str(os.path.getsize(self._abs_local(path))), exit_code=0)
            except OSError:
                return ExecuteResult(stdout="", exit_code=1)
        return self._exec(f"wc -c < {self._escape_shell_arg(path)} 2>/dev/null")

    def _prim_read_sample(self, path: str, n: int) -> "ExecuteResult":
        """First ``n`` bytes of ``path`` (POSIX: ``head -c n``), decoded text."""
        if self._use_inproc_io():
            abs_path = self._abs_local(path)
            try:
                with open(abs_path, "rb") as fh:
                    data = fh.read(n)
                return ExecuteResult(stdout=data.decode("utf-8", errors="replace"), exit_code=0)
            except OSError:
                return ExecuteResult(stdout="", exit_code=1)
        return self._exec(f"head -c {int(n)} {self._escape_shell_arg(path)} 2>/dev/null")

    def _prim_read_all(self, path: str, suppress_stderr: bool = True) -> "ExecuteResult":
        """Full file text (POSIX: ``cat``)."""
        if self._use_inproc_io():
            abs_path = self._abs_local(path)
            try:
                with open(abs_path, "rb") as fh:
                    return ExecuteResult(stdout=_decode_file_bytes(fh.read()), exit_code=0)
            except OSError as exc:
                return ExecuteResult(stdout="" if suppress_stderr else str(exc), exit_code=1)
        redir = " 2>/dev/null" if suppress_stderr else ""
        return self._exec(f"cat {self._escape_shell_arg(path)}{redir}")

    def _prim_read_page(self, path: str, offset: int, end: int) -> "ExecuteResult":
        """Lines ``offset..end`` 1-indexed inclusive (POSIX: ``sed -n``)."""
        if self._use_inproc_io():
            abs_path = self._abs_local(path)
            try:
                with open(abs_path, "rb") as fh:
                    text = _decode_file_bytes(fh.read())
            except OSError as exc:
                return ExecuteResult(stdout=str(exc), exit_code=1)
            lines = text.split("\n")
            # A trailing newline yields a final empty element that ``sed`` does
            # not treat as a line — drop it so pagination matches POSIX.
            if lines and lines[-1] == "" and text.endswith("\n"):
                lines = lines[:-1]
            return ExecuteResult(stdout="\n".join(lines[offset - 1:end]), exit_code=0)
        return self._exec(f"sed -n '{offset},{end}p' {self._escape_shell_arg(path)}")

    def _prim_count_lines(self, path: str) -> "ExecuteResult":
        """Newline count (POSIX: ``wc -l``) — matches the truncation logic."""
        if self._use_inproc_io():
            abs_path = self._abs_local(path)
            try:
                count = 0
                with open(abs_path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        count += chunk.count(b"\n")
                return ExecuteResult(stdout=str(count), exit_code=0)
            except OSError:
                return ExecuteResult(stdout="0", exit_code=1)
        return self._exec(f"wc -l < {self._escape_shell_arg(path)}")

    def _prim_list_dir(self, dir_path: str) -> "ExecuteResult":
        """Up to 50 entry names of ``dir_path`` (POSIX: ``ls -1 | head -50``)."""
        if self._use_inproc_io():
            try:
                names = sorted(os.listdir(self._abs_local(dir_path)))[:50]
                return ExecuteResult(stdout="\n".join(names), exit_code=0)
            except OSError:
                return ExecuteResult(stdout="", exit_code=1)
        return self._exec(f"ls -1 {self._escape_shell_arg(dir_path)} 2>/dev/null | head -50")

    def _prim_mkdirs(self, parent: str) -> "ExecuteResult":
        """Create ``parent`` and ancestors (POSIX: ``mkdir -p``)."""
        if self._use_inproc_io():
            try:
                os.makedirs(self._abs_local(parent), exist_ok=True)
                return ExecuteResult(stdout="", exit_code=0)
            except OSError as exc:
                return ExecuteResult(stdout=str(exc), exit_code=1)
        return self._exec(f"mkdir -p {self._escape_shell_arg(parent)}")

    def _local_atomic_write(self, path: str, content: str) -> "ExecuteResult":
        """In-process atomic write for the local Windows backend.

        Streams to a temp file in the target's own directory, verifies the
        just-written bytes (CRC-32 + size, P-042) unless disabled, preserves the
        existing file's mode, then ``os.replace()`` (atomic same-dir rename) —
        the cross-platform equivalent of the POSIX ``mktemp``/``mv -f`` script.
        The integrity check runs BEFORE the rename, so a corrupt/short write
        aborts with the original file intact instead of clobbering it.  On
        success ``stdout`` carries the verified on-disk byte count (via
        ``os.path.getsize`` after the replace), so write_file never has to
        fabricate a size (the root cause of the silent-success bug #54).
        """
        abs_path = self._abs_local(path)
        parent = os.path.dirname(abs_path) or "."
        data = content.encode("utf-8")
        expected_crc = zlib.crc32(data) & 0xFFFFFFFF
        expected_size = len(data)
        # Serialize concurrent writers targeting the SAME file (striped, so
        # distinct files still run fully in parallel). The temp-file +
        # os.replace swap is atomic on its own; the lock keeps the surrounding
        # stat-mode / getsize steps and any concurrent reader from observing a
        # half-applied write when two calls hit the same path at once.
        lock = _get_file_lock(abs_path)
        try:
            with lock:
                os.makedirs(parent, exist_ok=True)
                mode: Optional[int] = None
                try:
                    mode = _stat.S_IMODE(os.stat(abs_path).st_mode)
                except OSError:
                    mode = None
                fd, tmp = tempfile.mkstemp(prefix=".hermes-tmp.", dir=parent)
                if _MARK_TEMP_FILES:
                    _set_temp_attr(tmp, True)
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(data)
                    # Integrity gate (P-042): re-read the flushed temp and
                    # confirm CRC-32 + length match what we meant to write.
                    # Cheap (the temp is page-cache warm; two 4-byte digests)
                    # and it catches a corrupt/short write BEFORE it can replace
                    # the good original — the size stat at the caller can't see a
                    # same-length bit flip.
                    if _WRITE_VERIFY_CRC:
                        actual_crc, actual_size = _crc32_of_file(tmp)
                        if (
                            actual_size != expected_size
                            or actual_crc != expected_crc
                        ):
                            raise OSError(
                                "write verification failed: CRC/size mismatch "
                                f"for {abs_path} (expected {expected_crc:08x}/"
                                f"{expected_size}B, got {actual_crc:08x}/"
                                f"{actual_size}B)"
                            )
                    if mode is not None:
                        try:
                            os.chmod(tmp, mode)
                        except OSError:
                            pass
                    if _MARK_TEMP_FILES:
                        # Clear the hint so the renamed-into-place file (now
                        # permanent user data) isn't left marked temporary.
                        _set_temp_attr(tmp, False)
                    os.replace(tmp, abs_path)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
                if not os.path.exists(abs_path):
                    raise OSError(f"File did not appear after atomic rename: {abs_path}")

                return ExecuteResult(stdout=str(os.path.getsize(abs_path)), exit_code=0)
        except OSError as exc:
            return ExecuteResult(stdout=f"atomic write failed: {exc}", exit_code=1)

    def _atomic_write(self, path: str, content: str) -> "ExecuteResult":
        """Write ``content`` to ``path`` atomically via temp-file + rename.

        Streams ``content`` over stdin into a temp file in the SAME
        directory as ``path`` (so the final ``mv`` is a real rename on the
        same filesystem, not a non-atomic cross-device copy), preserves the
        existing file's mode if it exists, then renames over the target.
        On any failure the temp file is removed so we never leak a partial
        ``.hermes-tmp`` file next to the user's data, and the original file
        is left untouched. Content rides stdin so there is no ARG_MAX limit.

        Returns an :class:`ExecuteResult`; ``exit_code == 0`` means the file
        was swapped into place atomically. A non-zero exit means nothing was
        renamed and the original (if any) is intact.
        """
        # [CN-fork] P-033: local Windows backend can't run the POSIX script
        # (mktemp/cat/mv) under PowerShell 5.1 — do the atomic write in-process.
        if self._use_inproc_io():
            return self._local_atomic_write(path, content)

        q_path = self._escape_shell_arg(path)
        parent = os.path.dirname(path) or "."
        q_parent = self._escape_shell_arg(parent)
        # template basename: hidden so it doesn't show up in casual `ls`,
        # carries a marker so an orphaned temp (only possible on a hard
        # crash *between* cat and mv) is identifiable.
        tmpl = self._escape_shell_arg(".hermes-tmp.XXXXXX")

        # One shell script, fully quoted. Notes:
        #  - `mktemp` lands the temp in the target's own dir (-p) so `mv` is
        #    same-FS atomic; we fall back to a PID-stamped name if the
        #    backend lacks mktemp (rare; busybox/macOS/Linux all ship it).
        #  - `chmod --reference` is GNU-only, so we read the octal mode with
        #    `stat` (GNU `-c%a` or BSD `-f%Lp`) and `chmod` it explicitly;
        #    silent best-effort — a perms-copy failure must not abort the
        #    write, the file still lands with default umask perms.
        #  - `trap ... EXIT` guarantees the temp is removed on every error
        #    path (cat failure, mv failure, signal) but NOT after a
        #    successful mv (the temp no longer exists by then).
        #  - we `cat >` the temp, then `mv -f` it over the target.
        script = (
            "set -e; "
            f"d={q_parent}; t={q_path}; "
            'tmp="$(mktemp -p "$d" ' + tmpl + ' 2>/dev/null '
            '|| mktemp "$d/.hermes-tmp.$$.XXXXXX" 2>/dev/null '
            '|| { tmp="$d/.hermes-tmp.$$"; : > "$tmp" && echo "$tmp"; })"; '
            '[ -n "$tmp" ] || { echo "atomic write: could not create temp file" >&2; exit 1; }; '
            "trap 'rm -f \"$tmp\"' EXIT; "
            # preserve mode of an existing target (best-effort, never fatal)
            'if [ -e "$t" ]; then '
            'm="$(stat -c%a "$t" 2>/dev/null || stat -f%Lp "$t" 2>/dev/null || true)"; '
            '[ -n "$m" ] && chmod "$m" "$tmp" 2>/dev/null || true; '
            "fi; "
            'cat > "$tmp"; '
            'mv -f "$tmp" "$t"; '
            "trap - EXIT"
        )
        return self._exec(script, stdin_data=content)

    def _detect_file_line_ending(self, path: str, pre_content: Optional[str] = None) -> Optional[str]:
        """Detect the dominant line ending of a file on disk.

        If ``pre_content`` is already available (we just read the file
        for lint/LSP purposes), inspect that — zero extra exec calls.
        Otherwise issue a tiny ``head -c 4096`` to sample the first 4KB.

        Returns ``"\\r\\n"`` for CRLF (Windows), ``"\\n"`` for LF (Unix),
        or ``None`` if undetermined (new file, empty file, single-line
        file with no line break in the first chunk).
        """
        if pre_content:
            return _detect_line_ending(pre_content)
        # File may not exist (new write) — `head` exits 0 with empty
        # stdout in that case which yields None below.  Cheap probe.
        head_result = self._prim_read_sample(path, 4096)
        if head_result.exit_code != 0 or not head_result.stdout:
            return None
        return _detect_line_ending(head_result.stdout)

    def _file_has_bom(self, path: str, pre_content: Optional[str] = None) -> bool:
        """Whether the file on disk starts with a UTF-8 BOM.

        Uses ``pre_content`` if we already read the file (zero extra exec
        calls); otherwise issues a tiny ``head -c 3`` to sample just the
        marker. A missing/empty file returns False (new writes get no BOM
        unless the caller explicitly includes one).
        """
        if pre_content is not None:
            return _has_bom(pre_content)
        head_result = self._prim_read_sample(path, 3)
        if head_result.exit_code != 0 or not head_result.stdout:
            return False
        return _has_bom(head_result.stdout)


    def _unified_diff(self, old_content: str, new_content: str, filename: str) -> str:
        """Generate unified diff between old and new content."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}"
        )
        return ''.join(diff)
    
    # =========================================================================
    # READ Implementation
    # =========================================================================
    
    def read_file(self, path: str, offset: int = 1, limit: int = 500) -> ReadResult:
        """
        Read a file with pagination, binary detection, and line numbers.
        
        Args:
            path: File path (absolute or relative to cwd)
            offset: Line number to start from (1-indexed, default 1)
            limit: Maximum lines to return (default 500, max 2000)
        
        Returns:
            ReadResult with content, metadata, or error info
        """
        # Expand ~ and other shell paths
        path = self._expand_path(path)
        
        offset, limit = normalize_read_pagination(offset, limit)
        
        # Check if file exists and get size (POSIX ``wc -c``; in-process on
        # a local Windows backend — see _prim_stat_size / P-033).
        stat_result = self._prim_stat_size(path)

        if stat_result.exit_code != 0:
            # File not found - try to suggest similar files
            return self._suggest_similar_files(path)
        
        stat_output = _strip_terminal_fence_leaks(stat_result.stdout)
        try:
            file_size = int(stat_output.strip())
        except ValueError:
            file_size = 0
        
        # Check if file is too large
        if file_size > MAX_FILE_SIZE:
            # Still try to read, but warn
            pass
        
        # Images are never inlined — redirect to the vision tool
        if self._is_image(path):
            return ReadResult(
                is_image=True,
                is_binary=True,
                file_size=file_size,
                hint=(
                    "Image file detected. Automatically redirected to vision_analyze tool. "
                    "Use vision_analyze with this file path to inspect the image contents."
                ),
            )
        
        # Read a sample to check for binary content
        sample_result = self._prim_read_sample(path, 1000)
        sample_output = _strip_terminal_fence_leaks(sample_result.stdout)

        if self._is_likely_binary(path, sample_output):
            return ReadResult(
                is_binary=True,
                file_size=file_size,
                error="Binary file - cannot display as text. Use appropriate tools to handle this file type."
            )

        # Read with pagination (POSIX ``sed -n``; in-process on local Windows)
        end_line = offset + limit - 1
        read_result = self._prim_read_page(path, offset, end_line)

        if read_result.exit_code != 0:
            return ReadResult(error=f"Failed to read file: {read_result.stdout}")
        read_output = _strip_terminal_fence_leaks(read_result.stdout)
        # Strip a leading UTF-8 BOM so the model never sees a phantom U+FEFF
        # before the first real character. Only meaningful on the first
        # chunk (the marker lives at byte 0); later pages can't carry it.
        if offset == 1:
            read_output, _ = _strip_bom(read_output)
        
        # Get total line count (POSIX ``wc -l``; in-process on local Windows)
        wc_result = self._prim_count_lines(path)
        wc_output = _strip_terminal_fence_leaks(wc_result.stdout)
        try:
            total_lines = int(wc_output.strip())
        except ValueError:
            total_lines = 0
        
        # Check if truncated
        truncated = total_lines > end_line
        hint = None
        if truncated:
            hint = f"Use offset={end_line + 1} to continue reading (showing {offset}-{end_line} of {total_lines} lines)"
        
        return ReadResult(
            content=self._add_line_numbers(read_output, offset),
            total_lines=total_lines,
            file_size=file_size,
            truncated=truncated,
            hint=hint
        )
    
    def _suggest_similar_files(self, path: str) -> ReadResult:
        """Suggest similar files when the requested file is not found."""
        dir_path = os.path.dirname(path) or "."
        filename = os.path.basename(path)
        basename_no_ext = os.path.splitext(filename)[0]
        ext = os.path.splitext(filename)[1].lower()
        lower_name = filename.lower()

        # List files in the target directory (in-process on local Windows)
        ls_result = self._prim_list_dir(dir_path)

        scored: list = []  # (score, filepath) — higher is better
        if ls_result.exit_code == 0 and ls_result.stdout.strip():
            for f in ls_result.stdout.strip().split('\n'):
                if not f:
                    continue
                lf = f.lower()
                score = 0

                # Exact match (shouldn't happen, but guard)
                if lf == lower_name:
                    score = 100
                # Same base name, different extension (e.g. config.yml vs config.yaml)
                elif os.path.splitext(f)[0].lower() == basename_no_ext.lower():
                    score = 90
                # Target is prefix of candidate or vice-versa
                elif lf.startswith(lower_name) or lower_name.startswith(lf):
                    score = 70
                # Substring match (candidate contains query)
                elif lower_name in lf:
                    score = 60
                # Reverse substring (query contains candidate name)
                elif lf in lower_name and len(lf) > 2:
                    score = 40
                # Same extension with some overlap
                elif ext and os.path.splitext(f)[1].lower() == ext:
                    common = set(lower_name) & set(lf)
                    if len(common) >= max(len(lower_name), len(lf)) * 0.4:
                        score = 30

                if score > 0:
                    scored.append((score, os.path.join(dir_path, f)))

        scored.sort(key=lambda x: -x[0])
        similar = [fp for _, fp in scored[:5]]

        return ReadResult(
            error=f"File not found: {path}",
            similar_files=similar
        )
    
    def read_file_raw(self, path: str) -> ReadResult:
        """Read the complete file content as a plain string.

        No pagination, no line-number prefixes, no per-line truncation.
        Uses cat so the full file is returned regardless of size.
        """
        path = self._expand_path(path)
        stat_result = self._prim_stat_size(path)
        if stat_result.exit_code != 0:
            return self._suggest_similar_files(path)
        stat_output = _strip_terminal_fence_leaks(stat_result.stdout)
        try:
            file_size = int(stat_output.strip())
        except ValueError:
            file_size = 0
        if self._is_image(path):
            return ReadResult(is_image=True, is_binary=True, file_size=file_size)
        sample_result = self._prim_read_sample(path, 1000)
        sample_output = _strip_terminal_fence_leaks(sample_result.stdout)
        if self._is_likely_binary(path, sample_output):
            return ReadResult(
                is_binary=True, file_size=file_size,
                error="Binary file — cannot display as text."
            )
        cat_result = self._prim_read_all(path, suppress_stderr=False)
        if cat_result.exit_code != 0:
            return ReadResult(error=f"Failed to read file: {cat_result.stdout}")
        # Strip a leading UTF-8 BOM so patch's fuzzy matcher operates on
        # clean content (a phantom U+FEFF before line 1 would defeat an
        # exact first-line match). write_file restores the BOM on the way
        # back out — it re-probes the on-disk file, which still has the
        # marker — so the round-trip preserves it.
        raw_content, _ = _strip_bom(_strip_terminal_fence_leaks(cat_result.stdout))
        return ReadResult(
            content=raw_content,
            file_size=file_size,
        )

    def delete_file(self, path: str) -> WriteResult:
        """Delete a single file.

        Cross-platform: runs via ``python -c`` against the terminal env's
        Python so it works on Windows shells (``cmd.exe``/PowerShell) that
        don't ship ``rm``. Directories are rejected here — use
        ``delete_path(recursive=True)`` for trees.
        """
        return self._python_delete(path, recursive=False)

    def delete_path(self, path: str, recursive: bool = False) -> WriteResult:
        """Cross-platform delete that handles files and (with recursive=True)
        directory trees. Always preferred over emitting ``rm -rf`` /
        ``Remove-Item -Recurse`` directly so the same tool call works on
        every backend (local / docker / ssh / Windows).
        """
        return self._python_delete(path, recursive=recursive)

    def _python_delete(self, path: str, recursive: bool) -> WriteResult:
        path = self._expand_path(path)
        if _is_write_denied(path):
            return WriteResult(error=f"Delete denied: {path} is a protected path")

        # We can't shell out to ``rm`` here — it doesn't exist on Windows
        # ``cmd.exe`` or PowerShell, so this code path is what's left when
        # the backend's terminal is a Windows shell. Path is baked into the
        # snippet via ``repr()`` so quoting is correct on every shell.
        snippet = (
            "import shutil, pathlib, sys\n"
            f"p = pathlib.Path({path!r})\n"
            f"recursive = {bool(recursive)!r}\n"
            "try:\n"
            "    if p.is_dir() and not p.is_symlink():\n"
            "        if recursive:\n"
            "            shutil.rmtree(p)\n"
            "        else:\n"
            "            print('is a directory: ' + str(p), file=sys.stderr); sys.exit(2)\n"
            "    else:\n"
            # NOTE: avoid ``unlink(missing_ok=True)`` — that kwarg lands in
            # Python 3.8 and the remote interpreter (docker/ssh) may still
            # be 3.7 on older distros. The FileNotFoundError handler below
            # covers the same case and works back to 3.4.
            "        p.unlink()\n"
            "except FileNotFoundError:\n"
            "    pass\n"
            "except Exception as exc:\n"
            "    print(str(exc), file=sys.stderr); sys.exit(1)\n"
        )

        result = self._exec(f"python3 -c {self._escape_shell_arg(snippet)}")

        # Fall back to ``python`` (Windows / older systems where there's no
        # ``python3`` symlink but a ``python`` binary is on PATH).
        if result.exit_code != 0 and "python3" in (result.stdout or ""):
            result = self._exec(f"python -c {self._escape_shell_arg(snippet)}")

        if result.exit_code != 0:
            return WriteResult(error=f"Failed to delete {path}: {(result.stdout or '').strip() or 'unknown error'}")

        return WriteResult()

    def move_file(self, src: str, dst: str) -> WriteResult:
        """Move a file via mv."""
        src = self._expand_path(src)
        dst = self._expand_path(dst)
        for p in (src, dst):
            if _is_write_denied(p):
                return WriteResult(error=f"Move denied: {p} is a protected path")
        result = self._exec(
            f"mv {self._escape_shell_arg(src)} {self._escape_shell_arg(dst)}"
        )
        if result.exit_code != 0:
            return WriteResult(error=f"Failed to move {src} -> {dst}: {result.stdout}")
        return WriteResult()

    # =========================================================================
    # WRITE Implementation
    # =========================================================================

    def write_file(self, path: str, content: str) -> WriteResult:
        """
        Write content to a file, creating parent directories as needed.

        Pipes content through stdin to avoid OS ARG_MAX limits on large
        files. The content never appears in the shell command string —
        only the file path does.

        After the write, runs a post-first / pre-lazy lint check via
        ``_check_lint_delta()``.  If the new content is clean, the lint
        call is O(one parse).  If the new content has errors, the pre-write
        content is linted too and only errors newly introduced by this
        write are surfaced — pre-existing problems are filtered out so
        the agent isn't distracted chasing them.

        Args:
            path: File path to write
            content: Content to write

        Returns:
            WriteResult with bytes written, lint summary, or error.
        """
        # Expand ~ and other shell paths
        path = self._expand_path(path)

        # Block writes to sensitive paths
        if _is_write_denied(path):
            return WriteResult(error=f"Write denied: '{path}' is a protected system/credential file.")

        # Capture pre-write content.  Two consumers want it:
        #
        #   1. The lint-delta layer (for in-process linters like ast.parse
        #      and json.loads) needs the previous content to compute the
        #      set of NEW lint errors introduced by this write.
        #   2. The LSP layer needs pre/post content to build a line-shift
        #      map — pre-existing diagnostics below the edit point shift
        #      when lines are added/removed, and the shift map remaps
        #      baseline diagnostics into post-edit coordinates so the
        #      strict (range-aware) delta key matches.
        #
        # The set of extensions we capture pre_content for is therefore
        # the UNION of in-process lint coverage and LSP coverage.  For
        # extensions outside both sets (binaries, opaque formats),
        # skipping the read keeps the hot path fast.
        ext = os.path.splitext(path)[1].lower()
        pre_content: Optional[str] = None
        want_pre = ext in LINTERS_INPROC or self._lsp_handles_extension(ext)
        if want_pre:
            # Best-effort read; failure (file missing, permission) leaves
            # pre_content as None which makes both downstream consumers
            # degrade gracefully (lint reports all errors; LSP skips the
            # shift map).
            read_result = self._prim_read_all(path)
            if read_result.exit_code == 0 and read_result.stdout:
                pre_content = read_result.stdout

        # ── Line-ending preservation (Roo Code pattern) ──────────────
        # If the file existed with CRLF endings and the agent's content
        # has bare LFs, convert to CRLF before writing.  Otherwise the
        # write silently normalizes a Windows-line-ending file (and patch
        # produces mixed endings when only a substituted region changes).
        # Detect from a small head sample to avoid reading the full file
        # for line-ending purposes alone.
        original_ending = self._detect_file_line_ending(path, pre_content)
        if original_ending == "\r\n":
            content = _normalize_line_endings(content, "\r\n")

        # ── BOM preservation ──────────────────────────────────────────
        # If the file on disk started with a UTF-8 BOM, keep it. read_file
        # strips the BOM so the agent never sees it, which means the
        # content it hands back to write_file / patch has no BOM either —
        # without restoring it here a round-trip would silently strip the
        # marker and change the file's byte signature (some Windows
        # toolchains key on it). Only prepend when the original had a BOM
        # and the new content doesn't already carry one (guards against
        # double-BOM if a caller passed raw bytes).
        if self._file_has_bom(path, pre_content) and not _has_bom(content):
            content = _UTF8_BOM + content

        # Snapshot LSP diagnostics for this file (best-effort) so the
        # post-write LSP layer can return only diagnostics introduced
        # by this specific edit.  Mirrors claude-code's
        # ``beforeFileEdited`` pattern but wired to the local LSP
        # rather than an external IDE.
        self._snapshot_lsp_baseline(path)

        # Create parent directories
        parent = os.path.dirname(path)
        dirs_created = False

        if parent:
            mkdir_result = self._prim_mkdirs(parent)
            if mkdir_result.exit_code == 0:
                dirs_created = True

        # Write atomically: stream into a temp file in the SAME directory,
        # then ``mv`` it over the target. The rename is atomic on POSIX
        # (and on every backend FS we run on), so a crash / power loss /
        # truncated pipe mid-write leaves the original file intact instead
        # of a half-written corrupt file. Same-directory is load-bearing —
        # ``mv`` across filesystems degrades to copy+unlink, which is NOT
        # atomic; keeping the temp beside the target guarantees a real
        # rename. Content still rides stdin so there's no ARG_MAX limit.
        #
        # The temp file is created with ``mktemp`` (collision-safe) when the
        # backend has it, falling back to a PID-stamped name otherwise. We
        # then chmod the temp to match the existing file's mode (if any) so
        # the atomic swap doesn't silently widen or narrow permissions, and
        # clean the temp up on any failure so we never leak a ``.hermes-tmp``
        # turd next to the user's file.
        write_result = self._atomic_write(path, content)

        if write_result.exit_code != 0:
            return WriteResult(error=f"Failed to write file: {write_result.stdout}")

        # Post-write verification: confirm the file exists and its size matches
        # what we intended to write. This catches silent persistence failures
        # (backend FS oddities, truncated pipe, race, PowerShell script no-op,
        # etc.) without the cost of re-reading the entire file content.
        expected_bytes = len(content.encode("utf-8"))
        stat_result = self._prim_stat_size(path)
        if stat_result.exit_code != 0:
            return WriteResult(
                error=f"Post-write verification failed: could not stat {path}"
            )
        bytes_written = _parse_optional_int(stat_result.stdout)
        if bytes_written is None or bytes_written != expected_bytes:
            return WriteResult(
                error=(
                    f"Post-write verification failed for {path}: on-disk size "
                    f"({bytes_written} bytes) differs from intended write "
                    f"({expected_bytes} bytes). The write did not persist. "
                    "Re-read the file and try again."
                )
            )

        # Post-write lint with delta refinement.
        lint_result = self._check_lint_delta(path, pre_content=pre_content, post_content=content)

        # Semantic diagnostics from the LSP layer — separate channel.
        # Only fired when the syntax tier reported clean (no point asking
        # an LSP for a file that won't even parse).  Pass pre/post
        # content so the LSP layer can build a line-shift map and
        # remap baseline diagnostics into post-edit coordinates.
        # Best-effort: ``""`` is returned for any failure path.
        lsp_diagnostics: Optional[str] = None
        if lint_result.success or lint_result.skipped:
            block = self._maybe_lsp_diagnostics(
                path, pre_content=pre_content, post_content=content
            )
            if block:
                lsp_diagnostics = block

        return WriteResult(
            bytes_written=bytes_written,
            dirs_created=dirs_created,
            lint=lint_result.to_dict() if lint_result else None,
            lsp_diagnostics=lsp_diagnostics,
        )
    
    # =========================================================================
    # PATCH Implementation (Replace Mode)
    # =========================================================================
    
    def patch_replace(self, path: str, old_string: str, new_string: str,
                      replace_all: bool = False) -> PatchResult:
        """
        Replace text in a file using fuzzy matching.

        Args:
            path: File path to modify
            old_string: Text to find (must be unique unless replace_all=True)
            new_string: Replacement text
            replace_all: If True, replace all occurrences

        Returns:
            PatchResult with diff and lint results
        """
        # Expand ~ and other shell paths
        path = self._expand_path(path)

        # Block writes to sensitive paths
        if _is_write_denied(path):
            return PatchResult(error=f"Write denied: '{path}' is a protected system/credential file.")

        # Read current content. Route through the in-process primitive so the
        # read works on a local Windows backend (PowerShell 5.1 has no POSIX
        # ``cat`` and ``2>/dev/null`` redirects to a literal ``\dev\null`` file);
        # every other backend runs the identical ``cat … 2>/dev/null`` shell read.
        read_result = self._prim_read_all(path)

        if read_result.exit_code != 0:
            return PatchResult(error=f"Failed to read file: {path}")
        
        content = read_result.stdout
        # Strip a leading UTF-8 BOM before matching so the fuzzy matcher and
        # the diff operate on clean content (a phantom U+FEFF before line 1
        # defeats an exact first-line match). write_file restores the BOM on
        # the way back out by re-probing the on-disk file, so the round-trip
        # preserves the marker.
        content, _ = _strip_bom(content)

        # Import and use fuzzy matching
        from tools.fuzzy_match import fuzzy_find_and_replace
        
        new_content, match_count, _strategy, error = fuzzy_find_and_replace(
            content, old_string, new_string, replace_all
        )
        
        if error or match_count == 0:
            err_msg = error or f"Could not find match for old_string in {path}"
            try:
                from tools.fuzzy_match import format_no_match_hint
                err_msg += format_no_match_hint(err_msg, match_count, old_string, content)
            except Exception:
                pass
            return PatchResult(error=err_msg)

        # ── Line-ending preservation ──────────────────────────────────
        # Models nearly always send old_string/new_string with bare LF
        # in tool args (JSON-encoded), but the file may have CRLF on
        # disk.  After fuzzy_find_and_replace, ``new_content`` is a
        # mixed-ending string: the substituted region is LF, surrounding
        # text keeps the file's CRLF.  Normalize the whole thing to the
        # file's detected line ending so the on-disk file is consistent
        # and the unified diff below reflects the actual change.
        file_ending = _detect_line_ending(content)
        if file_ending:
            new_content = _normalize_line_endings(new_content, file_ending)

        # Write back
        write_result = self.write_file(path, new_content)
        if write_result.error:
            return PatchResult(error=f"Failed to write changes: {write_result.error}")

        # Post-write verification — re-read the file and confirm the bytes we
        # intended to write actually landed. Catches silent persistence
        # failures (backend FS oddities, race with another task, truncated
        # pipe, etc.) that would otherwise return success-with-diff while the
        # file is unchanged on disk.
        verify_result = self._prim_read_all(path)
        if verify_result.exit_code != 0:
            return PatchResult(error=f"Post-write verification failed: could not re-read {path}")
        # Normalize line endings before comparing.  On Windows, Python's
        # default text-mode ``open()`` translates ``\n`` → ``\r\n`` on
        # write, so the file on disk legitimately holds CRLFs while our
        # ``new_content`` string has bare LFs.  Without this normalization
        # every patch on Windows returns a bogus "wrote 39, read 42"
        # false-negative even though the edit landed correctly.  POSIX
        # backends don't translate, so this is a no-op there.  We also
        # strip a leading BOM from the re-read: write_file restored the
        # marker on disk but ``new_content`` is the BOM-less string we
        # matched against, so the comparison must drop it to stay
        # apples-to-apples.
        _verify_bomless, _ = _strip_bom(verify_result.stdout)
        _verify_stdout_normalized = _verify_bomless.replace("\r\n", "\n").replace("\r", "\n")
        _new_content_normalized = new_content.replace("\r\n", "\n").replace("\r", "\n")
        if _verify_stdout_normalized != _new_content_normalized:
            return PatchResult(error=(
                f"Post-write verification failed for {path}: on-disk content "
                f"differs from intended write "
                f"(wrote {len(_new_content_normalized)} chars, read back "
                f"{len(_verify_stdout_normalized)} chars after normalizing line endings). "
                "The patch did not persist. Re-read the file and try again."
            ))

        # Generate diff
        diff = self._unified_diff(content, new_content, path)

        # Auto-lint with delta refinement: only surface errors introduced
        # by this patch, filtering out pre-existing lint failures so the
        # agent isn't distracted by problems that were already there.
        lint_result = self._check_lint_delta(path, pre_content=content, post_content=new_content)

        return PatchResult(
            success=True,
            diff=diff,
            files_modified=[path],
            lint=lint_result.to_dict() if lint_result else None,
            # Propagate the LSP diagnostics already captured by the
            # internal ``write_file`` call.  Its baseline was the
            # pre-patch content (taken at the start of write_file via
            # ``_snapshot_lsp_baseline``) so the delta is correct for
            # the patch as a whole.  Keep the field separate from the
            # syntax-check ``lint`` so the agent can read both signals.
            lsp_diagnostics=write_result.lsp_diagnostics,
        )
    
    def patch_v4a(self, patch_content: str) -> PatchResult:
        """
        Apply a V4A format patch.
        
        V4A format:
            *** Begin Patch
            *** Update File: path/to/file.py
            @@ context hint @@
             context line
            -removed line
            +added line
            *** End Patch
        
        Args:
            patch_content: V4A format patch string
        
        Returns:
            PatchResult with changes made
        """
        # Import patch parser
        from tools.patch_parser import parse_v4a_patch, apply_v4a_operations
        
        operations, parse_error = parse_v4a_patch(patch_content)
        if parse_error:
            return PatchResult(error=f"Failed to parse patch: {parse_error}")
        
        # Apply operations
        result = apply_v4a_operations(operations, self)
        return result
    
    def _check_lint(self, path: str, content: Optional[str] = None) -> LintResult:
        """
        Run syntax check on a file after editing.

        Prefers the in-process linter for structured formats (JSON, YAML,
        TOML) when possible — those parse via the Python stdlib in
        microseconds and don't require a subprocess.  Falls back to the
        shell linter table for compiled/type-checked languages
        (py_compile, node --check, tsc, go vet, rustfmt).

        Args:
            path: File path (used to select the linter + for shell invocation).
            content: Optional file content.  If provided AND an in-process
                     linter matches the extension, we lint the content
                     directly without re-reading the file from disk.  Ignored
                     for shell linters.

        Returns:
            LintResult with status and any errors.
        """
        ext = os.path.splitext(path)[1].lower()

        # Prefer in-process linter when available.
        inproc = LINTERS_INPROC.get(ext)
        if inproc is not None:
            # Need content — either passed in or read from disk.
            if content is None:
                read_result = self._prim_read_all(path)
                if read_result.exit_code != 0:
                    return LintResult(skipped=True, message=f"Failed to read {path} for lint")
                content = read_result.stdout
            ok, err = inproc(content)
            if err == "__SKIP__":
                return LintResult(skipped=True, message=f"No linter available for {ext} (missing dependency)")
            return LintResult(success=ok, output="" if ok else err)

        # Fall back to shell linter.
        if ext not in LINTERS:
            return LintResult(skipped=True, message=f"No linter for {ext} files")

        # If a real LSP server is active and claims this file, skip the
        # shell linter for extensions whose per-file shell invocation is
        # structurally weaker / floods phantom errors.  See
        # ``_SHELL_LINTER_LSP_REDUNDANT`` above for the rationale per ext.
        # The LSP tier runs separately via ``_maybe_lsp_diagnostics`` and
        # carries the real diagnostics in ``lsp_diagnostics`` on the
        # WriteResult / PatchResult.
        if ext in _SHELL_LINTER_LSP_REDUNDANT and self._lsp_will_handle(path):
            return LintResult(
                skipped=True,
                message=f"LSP server handles {ext} — shell linter skipped",
            )

        linter_cmd = LINTERS[ext]
        # Extract the base command (first word)
        base_cmd = linter_cmd.split()[0]

        if not self._has_command(base_cmd):
            return LintResult(skipped=True, message=f"{base_cmd} not available")

        # Run linter
        cmd = linter_cmd.replace("{file}", self._escape_shell_arg(path))
        result = self._exec(cmd, timeout=30)

        if result.exit_code != 0 and _looks_like_linter_unusable(base_cmd, result.stdout):
            # The linter command exists on PATH but couldn't actually run
            # (e.g. ``npx tsc`` when tsc isn't in node_modules; ``rustfmt
            # --check`` without a Cargo project).  This is a tooling gap,
            # not a real lint failure — surface it as ``skipped`` so the
            # write doesn't get flagged AND so the LSP tier still runs.
            from tools.ansi_strip import strip_ansi
            cleaned = strip_ansi(result.stdout).strip()
            # Collapse to a single line — the npx banner is multi-line ASCII.
            first_line = next(
                (ln.strip() for ln in cleaned.splitlines() if ln.strip()),
                cleaned[:120],
            )
            return LintResult(
                skipped=True,
                message=f"{base_cmd} not usable: {first_line[:200]}",
            )

        return LintResult(
            success=result.exit_code == 0,
            output=result.stdout.strip() if result.stdout.strip() else ""
        )

    def _check_lint_delta(self, path: str, pre_content: Optional[str],
                          post_content: Optional[str] = None) -> LintResult:
        """
        Run post-write syntax lint with pre-write baseline comparison.

        Two-tier strategy:

        1. **Syntax check** (in-process or shell-based, microseconds).
           Catches the bug class that motivated this layer: corrupt
           writes, mashed quotes, truncated output.  Hot path.

        2. **Delta refinement against pre-write content** when the
           syntax tier reports errors.  Filter out errors that already
           existed pre-edit so the agent isn't distracted by inherited
           state.

        Semantic diagnostics from the LSP layer are fetched separately
        via :meth:`_maybe_lsp_diagnostics` and surfaced in the
        ``lsp_diagnostics`` field on :class:`WriteResult` /
        :class:`PatchResult`.  Keeping the two channels separate lets
        the agent (and any downstream parsers) read syntax errors and
        semantic errors as independent signals.

        Args:
            path: File path (for linter selection).
            pre_content: File content BEFORE the write.  Pass None for new
                         files or when the pre-state isn't available — the
                         delta refinement is skipped and all post errors
                         are returned.
            post_content: File content AFTER the write.  Optional; if None,
                          the shell linter reads from disk (same as
                          _check_lint).

        Returns:
            LintResult.  ``output`` contains either the full post-lint
            errors (no pre-state) or just the new-error lines (delta
            refinement applied).
        """
        post = self._check_lint(path, content=post_content)

        # Hot path: clean post-write syntactically.
        if post.success or post.skipped:
            return post

        # Post-write has syntax errors.  If we have pre-content, run the
        # delta refinement to filter out pre-existing errors.
        if pre_content is None:
            return post

        pre = self._check_lint(path, content=pre_content)
        if pre.success or pre.skipped or not pre.output:
            # Pre-write was clean (or we couldn't lint it) — post errors
            # are all new.  Return the full post output.
            return post

        # Both pre- and post-write had errors.  Compute the set-difference
        # on non-empty stripped lines.  Caveat: single-error parsers
        # (ast.parse, json.loads) stop at the first error and don't report
        # later ones — if the pre-existing error blocks parsing before
        # reaching the edit region, we can't prove the edit is clean.  So
        # if every post error also appeared pre-edit, we report the file
        # as still broken but annotate that this edit introduced nothing
        # new on top — the agent knows it's inherited state, not fresh
        # damage, without silently dropping the error.
        pre_lines = {ln.strip() for ln in pre.output.splitlines() if ln.strip()}
        post_lines = [ln for ln in post.output.splitlines() if ln.strip() and ln.strip() not in pre_lines]

        if not post_lines:
            # Every error in post was also in pre — this edit didn't make
            # anything obviously worse, but the file remains broken and
            # the agent should know.
            return LintResult(
                success=False,
                output=post.output,
                message="Pre-existing lint errors — this edit didn't introduce new ones but the file is still broken.",
            )

        return LintResult(
            success=False,
            output=(
                "New lint errors introduced by this edit "
                "(pre-existing errors filtered out):\n" + "\n".join(post_lines)
            )
        )

    def _is_local_env(self) -> bool:
        """Return True iff this FileOperations is wired to a local backend."""
        env = getattr(self, "env", None)
        if env is None:
            return False
        try:
            from tools.environments.local import LocalEnvironment
        except Exception:  # noqa: BLE001
            return False
        return isinstance(env, LocalEnvironment)

    def _lsp_local_only(self) -> bool:
        """Return True iff this FileOperations is wired to a local backend.

        LSP servers run on the host process — they need access to the
        files they're linting.  Remote/sandboxed backends (Docker,
        Modal, SSH, Daytona) keep files inside the sandbox where the
        host-side LSP server can't reach them, so we skip the LSP
        path for those entirely.
        """
        return self._is_local_env()

    def _lsp_handles_extension(self, ext: str) -> bool:
        """Return True iff some registered LSP server claims this extension.

        Used to decide whether to capture pre-write content for the
        line-shift map.  Capturing is cheap (one ``cat`` on the host)
        but pointless if no LSP would ever look at the file.

        Safe to call on remote backends — the registry is purely
        in-process metadata; we still gate the actual LSP path on
        :meth:`_lsp_local_only`.
        """
        if not ext:
            return False
        try:
            from agent.lsp.servers import SERVERS
        except Exception:  # noqa: BLE001
            return False
        ext_lower = ext.lower()
        for srv in SERVERS:
            if ext_lower in srv.extensions:
                return True
        return False

    def _lsp_will_handle(self, path: str) -> bool:
        """Return True iff the LSP service is active AND will lint this file.

        Stronger than :meth:`_lsp_handles_extension` — that one only checks
        the static server registry.  This one additionally requires the
        LSP service to be configured/enabled and the file to pass
        :meth:`agent.lsp.manager.LSPService.enabled_for` (which gates on
        workspace detection, disabled-server set, and the broken-pair
        short-circuit).

        Used by :meth:`_check_lint` to decide whether to skip the per-file
        shell linter for extensions in ``_SHELL_LINTER_LSP_REDUNDANT``.

        Best-effort: any failure path returns False so the shell linter
        runs as before — never suppress lint based on an LSP probe that
        couldn't actually answer the question.
        """
        if not self._lsp_local_only():
            return False
        try:
            from agent.lsp import get_service
        except Exception:  # noqa: BLE001
            return False
        try:
            svc = get_service()
        except Exception:  # noqa: BLE001
            return False
        if svc is None:
            return False
        try:
            return bool(svc.enabled_for(path))
        except Exception:  # noqa: BLE001
            return False

    def _snapshot_lsp_baseline(self, path: str) -> None:
        """Capture pre-edit LSP diagnostics so the post-write delta is correct.

        Best-effort.  Silent on every failure path — LSP is an
        enrichment layer and must never break a write.

        Skipped entirely on non-local backends (Docker, Modal, SSH,
        etc.) — the server can't see files inside the sandbox.
        """
        if not self._lsp_local_only():
            return
        try:
            from agent.lsp import get_service
            svc = get_service()
        except Exception:  # noqa: BLE001
            return
        if svc is None:
            return
        try:
            svc.snapshot_baseline(path)
        except Exception:  # noqa: BLE001
            pass

    def _maybe_lsp_diagnostics(
        self,
        path: str,
        *,
        pre_content: Optional[str] = None,
        post_content: Optional[str] = None,
    ) -> str:
        """Best-effort LSP semantic diagnostics for ``path``.

        Returns a formatted ``<diagnostics>`` block, or empty string
        when LSP is unavailable / disabled / produced no errors.

        When both ``pre_content`` and ``post_content`` are provided,
        a line-shift map is built and passed to the LSPService so
        baseline diagnostics are remapped into post-edit coordinates
        before the set-difference.  Without this, edits that delete
        or insert lines surface every pre-existing diagnostic below
        the edit point as "introduced by this edit".

        Wraps everything in a try/except so a misbehaving LSP server
        can't break a write.  This intentionally swallows all errors
        — the calling tier already returned a clean syntax result, so
        ``""`` here just means "no extra info to add".

        Skipped entirely on non-local backends (Docker, Modal, SSH,
        etc.) — same reasoning as ``_snapshot_lsp_baseline``.
        """
        if not self._lsp_local_only():
            return ""
        try:
            from agent.lsp import get_service
        except Exception:  # noqa: BLE001
            return ""
        try:
            svc = get_service()
        except Exception:  # noqa: BLE001
            return ""
        if svc is None or not svc.enabled_for(path):
            return ""

        # Build a line-shift map when we have both pre and post — it
        # remaps baseline diagnostics into post-edit coordinates so
        # the strict (range-aware) delta key matches correctly.
        line_shift = None
        if pre_content is not None and post_content is not None and pre_content != post_content:
            try:
                from agent.lsp.range_shift import build_line_shift
                line_shift = build_line_shift(pre_content, post_content)
            except Exception:  # noqa: BLE001
                line_shift = None

        try:
            diagnostics = svc.get_diagnostics_sync(path, delta=True, line_shift=line_shift)
        except Exception:  # noqa: BLE001
            return ""
        if not diagnostics:
            return ""
        try:
            from agent.lsp.reporter import report_for_file, truncate
            block = report_for_file(path, diagnostics)
            if not block:
                return ""
            return truncate("LSP diagnostics introduced by this edit:\n" + block)
        except Exception:  # noqa: BLE001
            return ""
    
    # =========================================================================
    # SEARCH Implementation
    # =========================================================================
    
    def search(self, pattern: str, path: str = ".", target: str = "content",
               file_glob: Optional[str] = None, limit: int = 50, offset: int = 0,
               output_mode: str = "content", context: int = 0) -> SearchResult:
        """
        Search for content or files.
        
        Args:
            pattern: Regex (for content) or glob pattern (for files)
            path: Directory/file to search (default: cwd)
            target: "content" (grep) or "files" (glob)
            file_glob: File pattern filter for content search (e.g., "*.py")
            limit: Max results (default 50)
            offset: Skip first N results
            output_mode: "content", "files_only", or "count"
            context: Lines of context around matches
        
        Returns:
            SearchResult with matches or file list
        """
        offset, limit = normalize_search_pagination(offset, limit)

        # Expand ~ and other shell paths
        path = self._expand_path(path)
        
        # Validate that the path exists before searching. On the local backend
        # do this in-process: the POSIX `test -e` / `ls | head` probes below
        # cannot run under Windows PowerShell and would falsely report every
        # path as missing.
        if self._is_local_env():
            if not os.path.exists(path):
                return self._path_not_found_result(path)
        else:
            check = self._exec(f"test -e {self._escape_shell_arg(path)} && echo exists || echo not_found")
            if "not_found" in check.stdout:
                # Try to suggest nearby paths
                parent = os.path.dirname(path) or "."
                basename_query = os.path.basename(path)
                hint_parts = [f"Path not found: {path}"]
                # Check if parent directory exists and list similar entries
                parent_check = self._exec(
                    f"test -d {self._escape_shell_arg(parent)} && echo yes || echo no"
                )
                if "yes" in parent_check.stdout and basename_query:
                    ls_result = self._exec(
                        f"ls -1 {self._escape_shell_arg(parent)} 2>/dev/null | head -20"
                    )
                    if ls_result.exit_code == 0 and ls_result.stdout.strip():
                        lower_q = basename_query.lower()
                        candidates = []
                        for entry in ls_result.stdout.strip().split('\n'):
                            if not entry:
                                continue
                            le = entry.lower()
                            if lower_q in le or le in lower_q or le.startswith(lower_q[:3]):
                                candidates.append(os.path.join(parent, entry))
                        if candidates:
                            hint_parts.append(
                                "Similar paths: " + ", ".join(candidates[:5])
                            )
                return SearchResult(
                    error=". ".join(hint_parts),
                    total_count=0
                )

        if target == "files":
            return self._search_files(pattern, path, limit, offset)
        else:
            return self._search_content(pattern, path, file_glob, limit, offset, 
                                        output_mode, context)
    
    def _search_files(self, pattern: str, path: str, limit: int, offset: int) -> SearchResult:
        """Search for files by name pattern (glob-like)."""
        # Auto-prepend **/ for recursive search if not already present
        if not pattern.startswith('**/') and '/' not in pattern:
            search_pattern = pattern
        else:
            search_pattern = pattern.split('/')[-1]

        search_root = Path(path)
        has_hidden_path_ancestor = any(
            part not in {".", ".."} and part.startswith(".")
            for part in search_root.parts
        )

        # Local backend: never shell out to POSIX `find` or the `command -v`
        # probe (both break on Windows PowerShell). Use rg directly when it is
        # on PATH, otherwise a portable in-process Python walk. This is the path
        # that makes search work on a stock Windows install (GitHub #334).
        if self._is_local_env():
            if shutil.which("rg"):
                return self._search_files_rg(search_pattern, path, limit, offset)
            return self._search_files_python(
                search_pattern, path, limit, offset, has_hidden_path_ancestor
            )

        # Remote backends (docker/ssh/modal/daytona) have a guaranteed POSIX
        # shell. Prefer ripgrep: respects .gitignore, excludes hidden dirs by
        # default, parallel traversal (~200x faster than find on wide trees).
        if self._has_command('rg'):
            return self._search_files_rg(search_pattern, path, limit, offset)

        # Fallback: find (slower, no .gitignore awareness)
        if not self._has_command('find'):
            return SearchResult(
                error="File search requires 'rg' (ripgrep) or 'find'. "
                      "Install ripgrep for best results: "
                      "https://github.com/BurntSushi/ripgrep#installation"
            )

        # Exclude hidden directories (matching ripgrep's default behavior).
        hidden_exclude = "-not -path '*/.*'" if not has_hidden_path_ancestor else ""
        hidden_filter_expr = f" {hidden_exclude}" if hidden_exclude else ""

        # Use shell pagination for standard roots. For hidden roots, gather full
        # output so we can re-apply hidden-descendant filtering while allowing
        # explicit hidden-root searches.
        pagination_expr = ""
        if not has_hidden_path_ancestor:
            pagination_expr = f" | tail -n +{offset + 1} | head -n {limit}"

        cmd = f"find {self._escape_shell_arg(path)}{hidden_filter_expr} -type f -name {self._escape_shell_arg(search_pattern)} " \
              f"-printf '%T@ %p\\n' 2>/dev/null | sort -rn{pagination_expr}"

        result = self._exec(cmd, timeout=60)
        stdout, limit_reason = _search_stdout_and_limit(result)

        if not stdout.strip() and not limit_reason:
            # Try without -printf (BSD find compatibility -- macOS)
            cmd_simple = f"find {self._escape_shell_arg(path)}{hidden_filter_expr} -type f -name {self._escape_shell_arg(search_pattern)} " \
                        f"2>/dev/null | sort -rn{pagination_expr}"
            result = self._exec(cmd_simple, timeout=60)
            stdout, limit_reason = _search_stdout_and_limit(result)

        files = []
        for line in stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split(' ', 1)
            if len(parts) == 2 and parts[0].replace('.', '').isdigit():
                files.append(parts[1])
            else:
                files.append(line)

        # For explicit hidden roots, find's path-based filtering excludes every
        # file under the hidden path. Apply descendant filtering after command
        # execution so only the explicit root ancestry is bypassed.
        if has_hidden_path_ancestor:
            normalized_root = search_root.resolve()
            filtered_files = []
            for file_path in files:
                try:
                    rel_parts = Path(file_path).resolve().relative_to(normalized_root).parts
                except ValueError:
                    rel_parts = Path(file_path).parts
                if any(part not in {".", ".."} and part.startswith(".") for part in rel_parts):
                    continue
                filtered_files.append(file_path)
            files = filtered_files[offset:offset + limit]
        # pagination for standard roots is already applied in shell

        return SearchResult(
            files=files,
            total_count=len(files),
            truncated=bool(limit_reason),
            limit_reason=limit_reason,
        )

    def _search_files_rg(self, pattern: str, path: str, limit: int, offset: int) -> SearchResult:
        """Search for files by name using ripgrep's --files mode.

        Prefers ripgrepy on local backends; falls back to shell rg for remotes.
        """
        if self._is_local_env() and shutil.which("rg"):
            return self._search_files_rg_ripgrepy(pattern, path, limit, offset)
        return self._search_files_rg_shell(pattern, path, limit, offset)

    def _search_files_rg_ripgrepy(self, pattern: str, path: str, limit: int,
                                   offset: int) -> SearchResult:
        """Search for files using ripgrepy's command builder (local only).

        ripgrepy always appends ``(pattern, path)`` in ``run()``, which is
        incompatible with ``--files``. We build the command object but run it
        manually via ``subprocess.run`` without calling ``rg.run()``.
        """
        from ripgrepy import Ripgrepy, RipGrepNotFound

        # rg --files -g uses glob patterns; wrap bare names so they match
        # at any depth (equivalent to find -name).
        if '/' not in pattern and not pattern.startswith('*'):
            glob_pattern = f"*{pattern}"
        else:
            glob_pattern = pattern

        try:
            # Dummy pattern for init; --files ignores the pattern anyway.
            rg = Ripgrepy(".", path)
        except RipGrepNotFound:
            return self._search_files_rg_shell(pattern, path, limit, offset)

        rg.files().glob(glob_pattern).sortr("modified")

        # Run manually: ripgrepy's run() would append the dummy pattern and
        # path, producing ``rg . <path> --files --glob …`` which is invalid.
        cmd = rg.command + [path]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                stdin=subprocess.DEVNULL,
            )
        except Exception:  # noqa: BLE001
            return self._search_files_rg_shell(pattern, path, limit, offset)

        if proc.returncode != 0:
            # --sortr may have failed on older rg; retry without it.
            rg_unsorted = Ripgrepy(".", path)
            rg_unsorted.files().glob(glob_pattern)
            cmd_unsorted = rg_unsorted.command + [path]
            try:
                proc = subprocess.run(
                    cmd_unsorted,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    stdin=subprocess.DEVNULL,
                )
            except Exception:  # noqa: BLE001
                return self._search_files_rg_shell(pattern, path, limit, offset)

            if proc.returncode != 0:
                return self._search_files_rg_shell(pattern, path, limit, offset)

        all_files = [f for f in proc.stdout.strip().split('\n') if f]
        fetch_limit = limit + offset
        page = all_files[offset:offset + limit]

        return SearchResult(
            files=page,
            total_count=len(all_files),
            truncated=len(all_files) >= fetch_limit,
        )

    def _search_files_rg_shell(self, pattern: str, path: str, limit: int,
                                offset: int) -> SearchResult:
        """Search for files by name using ripgrep via shell (remote fallback)."""
        if self._is_local_env():
            # Reached only if ripgrepy errored despite rg on PATH. Don't run a
            # POSIX `rg … | head` pipeline under Windows PowerShell — degrade to
            # the portable in-process walk instead.
            has_hidden = any(
                part not in {".", ".."} and part.startswith(".")
                for part in Path(path).parts
            )
            return self._search_files_python(pattern, path, limit, offset, has_hidden)

        # rg --files -g uses glob patterns; wrap bare names so they match
        # at any depth (equivalent to find -name).
        if '/' not in pattern and not pattern.startswith('*'):
            glob_pattern = f"*{pattern}"
        else:
            glob_pattern = pattern

        fetch_limit = limit + offset
        # Try mtime-sorted first (rg 13+); fall back to unsorted if not supported.
        cmd_sorted = (
            f"rg --files --sortr=modified -g {self._escape_shell_arg(glob_pattern)} "
            f"{self._escape_shell_arg(path)} 2>/dev/null "
            f"| head -n {fetch_limit}"
        )
        result = self._exec(cmd_sorted, timeout=60)
        stdout, limit_reason = _search_stdout_and_limit(result)
        all_files = [f for f in stdout.strip().split('\n') if f]

        if not all_files and not limit_reason:
            # --sortr may have failed on older rg; retry without it.
            cmd_plain = (
                f"rg --files -g {self._escape_shell_arg(glob_pattern)} "
                f"{self._escape_shell_arg(path)} 2>/dev/null "
                f"| head -n {fetch_limit}"
            )
            result = self._exec(cmd_plain, timeout=60)
            stdout, limit_reason = _search_stdout_and_limit(result)
            all_files = [f for f in stdout.strip().split('\n') if f]

        page = all_files[offset:offset + limit]

        return SearchResult(
            files=page,
            total_count=len(all_files),
            truncated=len(all_files) >= fetch_limit or bool(limit_reason),
            limit_reason=limit_reason,
        )
    
    def _search_content(self, pattern: str, path: str, file_glob: Optional[str],
                        limit: int, offset: int, output_mode: str, context: int) -> SearchResult:
        """Search for content inside files (grep-like)."""
        # Local backend: bypass the POSIX `command -v` probe (broken on Windows
        # PowerShell). Use rg directly when present, else a portable in-process
        # Python scan — so content search works on a stock Windows install
        # without ripgrep/grep (GitHub #334).
        if self._is_local_env():
            if shutil.which("rg"):
                result = self._search_with_rg(pattern, path, file_glob, limit, offset,
                                              output_mode, context)
            else:
                result = self._search_content_python(pattern, path, file_glob, limit,
                                                     offset, output_mode, context)
            return _maybe_warn_line_oriented_newline_pattern(result, pattern)

        # Remote backends: try ripgrep first (fast), fallback to grep.
        if self._has_command('rg'):
            result = self._search_with_rg(pattern, path, file_glob, limit, offset,
                                          output_mode, context)
        elif self._has_command('grep'):
            result = self._search_with_grep(pattern, path, file_glob, limit, offset,
                                            output_mode, context)
        else:
            # Neither rg nor grep available on the remote backend.
            return SearchResult(
                error="Content search requires ripgrep (rg) or grep. "
                      "Install ripgrep: https://github.com/BurntSushi/ripgrep#installation"
            )

        return _maybe_warn_line_oriented_newline_pattern(result, pattern)
    
    def _search_with_rg(self, pattern: str, path: str, file_glob: Optional[str],
                        limit: int, offset: int, output_mode: str, context: int) -> SearchResult:
        """Search using ripgrep — prefers ripgrepy on local backends."""
        if self._is_local_env() and shutil.which("rg"):
            return self._search_with_rg_ripgrepy(
                pattern, path, file_glob, limit, offset, output_mode, context
            )
        return self._search_with_rg_shell(
            pattern, path, file_glob, limit, offset, output_mode, context
        )

    def _search_with_rg_ripgrepy(self, pattern: str, path: str, file_glob: Optional[str],
                                  limit: int, offset: int, output_mode: str,
                                  context: int) -> SearchResult:
        """Search using ripgrepy (local backends only).

        We build the command via ripgrepy's chainable API but execute via
        ``subprocess.run`` ourselves so we can capture stdout and stderr
        separately.  ripgrepy's own ``run()`` discards stdout when the exit
        code is non-zero, which prevents us from recovering partial matches
        on exit 2.
        """
        from ripgrepy import Ripgrepy, RipGrepNotFound

        try:
            rg = Ripgrepy(pattern, path)
        except RipGrepNotFound:
            return self._search_with_rg_shell(
                pattern, path, file_glob, limit, offset, output_mode, context
            )

        rg.line_number().no_heading().with_filename()

        if context > 0:
            rg.context(context)

        if file_glob:
            rg.glob(file_glob)

        if output_mode == "files_only":
            rg.files_with_matches()
        elif output_mode == "count":
            rg.count_matches()

        # Build the full command list without mutating rg's internal state.
        full_cmd = list(rg.command) + [rg.regex_pattern, rg.path]
        try:
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                stdin=subprocess.DEVNULL,
            )
        except Exception:  # noqa: BLE001
            return self._search_with_rg_shell(
                pattern, path, file_glob, limit, offset, output_mode, context
            )

        stdout = proc.stdout
        stderr = proc.stderr

        # rg exit codes: 0=matches, 1=no matches, 2=error.
        if proc.returncode == 2:
            # Partial error: some files matched but one was unreadable.
            # Keep stdout (the real matches) and discard stderr.
            if not stdout.strip():
                error_msg = stderr.strip() or stdout.strip() or "Search error"
                return SearchResult(error=f"Search failed: {error_msg}", total_count=0)
        elif proc.returncode not in (0, 1):
            error_msg = stderr.strip() or stdout.strip() or "Search error"
            return SearchResult(error=f"Search failed: {error_msg}", total_count=0)

        output = stdout
        # For content mode with context, rg emits separator lines ("--")
        # between groups, so we grab generously and filter in Python.
        if output_mode == "content" and context > 0:
            lines = output.strip().split('\n')
            # Keep only real output lines (match shape, context shape, or "--")
            payload_lines = [
                ln for ln in lines
                if ln and (ln == "--" or _SEARCH_OUTPUT_RE.match(ln))
            ]
            output = '\n'.join(payload_lines)

        return _parse_search_content_output(output, output_mode, context, limit, offset)

    def _search_with_rg_shell(self, pattern: str, path: str, file_glob: Optional[str],
                              limit: int, offset: int, output_mode: str, context: int) -> SearchResult:
        """Search using ripgrep via shell (remote backends or ripgrepy fallback)."""
        if self._is_local_env():
            # Reached only if ripgrepy errored despite rg on PATH. Avoid the
            # POSIX `set -o pipefail; rg … | head` pipeline under PowerShell —
            # degrade to the portable in-process content scan.
            return self._search_content_python(
                pattern, path, file_glob, limit, offset, output_mode, context
            )
        cmd_parts = ["rg", "--line-number", "--no-heading", "--with-filename"]
        
        # Add context if requested
        if context > 0:
            cmd_parts.extend(["-C", str(context)])
        
        # Add file glob filter (must be quoted to prevent shell expansion)
        if file_glob:
            cmd_parts.extend(["--glob", self._escape_shell_arg(file_glob)])
        
        # Output mode handling
        if output_mode == "files_only":
            cmd_parts.append("-l")  # Files only
        elif output_mode == "count":
            cmd_parts.append("-c")  # Count per file
        
        # Add pattern and path
        cmd_parts.append(self._escape_shell_arg(pattern))
        cmd_parts.append(self._escape_shell_arg(path))
        
        # Fetch extra rows so we can report the true total before slicing.
        # For context mode, rg emits separator lines ("--") between groups,
        # so we grab generously and filter in Python.
        fetch_limit = limit + offset + 200 if context > 0 else limit + offset
        cmd_parts.extend(["|", "head", "-n", str(fetch_limit)])
        
        # `set -o pipefail` so rg's exit status propagates through `| head`.
        # Without it the pipeline reports head's status (0), masking rg's
        # error code (2) and making the guard below unreachable. rg handles a
        # truncating head cleanly (exit 0 on SIGPIPE), so pipefail does not
        # introduce false errors on a successful-but-truncated search.
        cmd = "set -o pipefail; " + " ".join(cmd_parts)
        result = self._exec(cmd, timeout=60)
        stdout, limit_reason = _search_stdout_and_limit(result)

        # _exec merges stderr into stdout (stderr=subprocess.STDOUT), so rg's
        # diagnostic lines ("rg: <file>: <error>", "rg: regex parse error:")
        # are interleaved with match output. Split them out: diagnostics must
        # not be parsed as matches, and on a hard error they ARE the message.
        diagnostics, payload = _split_tool_diagnostics(stdout)

        # rg exit codes: 0=matches found, 1=no matches, 2=error. rg returns 2
        # even on partial errors (e.g. one unreadable file in a tree that
        # otherwise matched), so only surface an error when exit==2 AND no
        # usable match payload remains. Otherwise we keep the real matches.
        if result.exit_code == 2 and not payload.strip():
            error_msg = diagnostics.strip() or result.stdout.strip() or "Search error"
            return SearchResult(error=f"Search failed: {error_msg}", total_count=0)

        return _parse_search_content_output(
            payload, output_mode, context, limit, offset, limit_reason
        )

    def _search_with_grep(self, pattern: str, path: str, file_glob: Optional[str],
                          limit: int, offset: int, output_mode: str, context: int) -> SearchResult:
        """Fallback search using grep."""
        cmd_parts = ["grep", "-rnH"]  # -H forces filename even for single-file searches
        
        # Exclude hidden directories (matching ripgrep's default behavior).
        # This prevents searching inside .hub/index-cache/, .git/, etc.
        cmd_parts.append("--exclude-dir='.*'")
        
        # Add context if requested
        if context > 0:
            cmd_parts.extend(["-C", str(context)])
        
        # Add file pattern filter (must be quoted to prevent shell expansion)
        if file_glob:
            cmd_parts.extend(["--include", self._escape_shell_arg(file_glob)])
        
        # Output mode handling
        if output_mode == "files_only":
            cmd_parts.append("-l")
        elif output_mode == "count":
            cmd_parts.append("-c")
        
        # Add pattern and path
        cmd_parts.append(self._escape_shell_arg(pattern))
        cmd_parts.append(self._escape_shell_arg(path))
        
        # Fetch generously so we can compute total before slicing
        fetch_limit = limit + offset + (200 if context > 0 else 0)
        cmd_parts.extend(["|", "head", "-n", str(fetch_limit)])
        
        # `set -o pipefail` so grep's exit status propagates through `| head`
        # (without it the pipeline reports head's 0, masking grep's error 2).
        # A truncating head makes grep exit 141 (SIGPIPE) on an otherwise
        # successful search; the strict `== 2` guard below ignores that, so
        # pipefail does not turn truncated results into false errors.
        cmd = "set -o pipefail; " + " ".join(cmd_parts)
        result = self._exec(cmd, timeout=60)
        stdout, limit_reason = _search_stdout_and_limit(result)

        # _exec merges stderr into stdout, so grep's diagnostic lines
        # ("grep: <file>: <error>") are interleaved with matches. Split them
        # out so they're never parsed as matches and so a hard error has a
        # clean message.
        diagnostics, payload = _split_tool_diagnostics(stdout)

        # grep exit codes: 0=matches found, 1=no matches, 2=error. grep
        # returns 2 on partial errors (e.g. an unreadable file) even when
        # other files matched, so only surface an error when exit==2 AND no
        # usable match payload remains.
        if result.exit_code == 2 and not payload.strip():
            error_msg = diagnostics.strip() or result.stdout.strip() or "Search error"
            return SearchResult(error=f"Search failed: {error_msg}", total_count=0)

        stdout = payload
        return _parse_search_content_output(
            stdout, output_mode, context, limit, offset, limit_reason
        )

    # =========================================================================
    # Portable in-process search fallback (local backend without ripgrep)
    #
    # These run no shell at all — they walk the tree with os.walk and match in
    # Python — so they work on a stock Windows install where rg/grep/find are
    # absent and the POSIX `command -v` probe can't even run (GitHub #334).
    # They cannot honor .gitignore the way ripgrep does; instead they prune the
    # usual vendored/cache dirs (`_FALLBACK_PRUNE_DIRS`) and hidden dirs, and
    # cap files scanned (`_FALLBACK_MAX_FILES_SCANNED`) so wide trees stay fast.
    # =========================================================================

    def _path_not_found_result(self, path: str) -> SearchResult:
        """In-process 'path not found' result with nearby-name suggestions."""
        parent = os.path.dirname(path) or "."
        basename_query = os.path.basename(path)
        hint_parts = [f"Path not found: {path}"]
        if basename_query and os.path.isdir(parent):
            try:
                entries = sorted(os.listdir(parent))[:200]
            except OSError:
                entries = []
            lower_q = basename_query.lower()
            candidates = []
            for entry in entries:
                le = entry.lower()
                if lower_q in le or le in lower_q or le.startswith(lower_q[:3]):
                    candidates.append(os.path.join(parent, entry))
                if len(candidates) >= 5:
                    break
            if candidates:
                hint_parts.append("Similar paths: " + ", ".join(candidates[:5]))
        return SearchResult(error=". ".join(hint_parts), total_count=0)

    def _collect_fallback_files(self, path: str,
                               has_hidden_ancestor: bool) -> tuple[List[str], bool]:
        """Walk *path* and return (file_paths, hit_scan_cap).

        Prunes ``_FALLBACK_PRUNE_DIRS`` and hidden dirs/files. The
        ``has_hidden_ancestor`` flag only controls whether the search root
        itself is allowed (it is passed in by the caller by deciding to enter
        the root); hidden descendants are excluded either way, matching
        ripgrep's default behavior and the POSIX ``find`` fallback path.
        Stops once ``_FALLBACK_MAX_FILES_SCANNED`` paths have been collected,
        reporting that via the second return value.
        """
        if os.path.isfile(path):
            return [path], False

        paths: List[str] = []
        for root, dirs, files in os.walk(path):  # topdown=True so dirs[:] prunes
            kept_dirs = []
            for d in dirs:
                if d in _FALLBACK_PRUNE_DIRS:
                    continue
                if d.startswith('.'):
                    continue
                kept_dirs.append(d)
            dirs[:] = kept_dirs

            for f in files:
                if f.startswith('.'):
                    continue
                paths.append(os.path.join(root, f))
                if len(paths) >= _FALLBACK_MAX_FILES_SCANNED:
                    return paths, True
        return paths, False

    def _search_files_python(self, pattern: str, path: str, limit: int, offset: int,
                             has_hidden_path_ancestor: bool) -> SearchResult:
        """File-name search via os.walk + fnmatch (mirrors the rg --files path)."""
        # Mirror _search_files_rg's glob: a bare name matches at any depth.
        if '/' not in pattern and not pattern.startswith('*'):
            glob_pattern = f"*{pattern}"
        else:
            glob_pattern = pattern

        all_paths, hit_cap = self._collect_fallback_files(path, has_hidden_path_ancestor)
        matched = [
            p for p in all_paths
            if fnmatch.fnmatch(os.path.basename(p), glob_pattern)
        ]

        def _mtime(p: str) -> float:
            try:
                return os.path.getmtime(p)
            except OSError:
                return 0.0

        # Newest first, matching rg --sortr=modified.
        matched.sort(key=_mtime, reverse=True)

        total = len(matched)
        page = matched[offset:offset + limit]
        limit_reason = None
        if hit_cap:
            limit_reason = (
                f"search stopped after scanning {_FALLBACK_MAX_FILES_SCANNED:,} "
                f"files; narrow the path or pattern"
            )
        return SearchResult(
            files=page,
            total_count=total,
            truncated=total > offset + limit or hit_cap,
            limit_reason=limit_reason,
        )

    def _search_content_python(self, pattern: str, path: str, file_glob: Optional[str],
                               limit: int, offset: int, output_mode: str,
                               context: int) -> SearchResult:
        """Content search via os.walk + per-line regex (mirrors the rg path)."""
        # Line-oriented, like rg/grep without -U: a pattern that needs to match
        # a newline can't match within a single line, so the line-oriented tools
        # reject it and return nothing. Mirror that (the caller then attaches the
        # explanatory warning via _maybe_warn_line_oriented_newline_pattern).
        if _pattern_has_regex_newline(pattern):
            return SearchResult(total_count=0)
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return SearchResult(error=f"Invalid search pattern: {exc}", total_count=0)

        has_hidden_ancestor = any(
            part not in {".", ".."} and part.startswith(".")
            for part in Path(path).parts
        )
        all_paths, hit_cap = self._collect_fallback_files(path, has_hidden_ancestor)
        if file_glob:
            all_paths = [
                p for p in all_paths
                if fnmatch.fnmatch(os.path.basename(p), file_glob)
            ]

        matches: List[SearchMatch] = []
        files_with_matches: List[str] = []
        counts: Dict[str, int] = {}

        for fp in all_paths:
            ext = os.path.splitext(fp)[1].lower()
            if ext in BINARY_EXTENSIONS:
                continue
            try:
                if os.path.getsize(fp) > _FALLBACK_MAX_CONTENT_BYTES:
                    continue
                with open(fp, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:  # NUL byte => treat as binary
                continue
            lines = raw.decode("utf-8", errors="replace").splitlines()

            matched_nums = [i for i, ln in enumerate(lines) if regex.search(ln)]
            if not matched_nums:
                continue

            if output_mode == "files_only":
                files_with_matches.append(fp)
                continue
            if output_mode == "count":
                counts[fp] = len(matched_nums)
                continue

            # content mode: emit matched lines plus any context window, deduped
            # and in line order (rg merges overlapping context the same way).
            wanted: set = set()
            for ln in matched_nums:
                if context > 0:
                    lo = max(0, ln - context)
                    hi = min(len(lines) - 1, ln + context)
                else:
                    lo = hi = ln
                wanted.update(range(lo, hi + 1))
            for k in sorted(wanted):
                matches.append(SearchMatch(
                    path=fp,
                    line_number=k + 1,
                    content=lines[k][:500],
                ))

        limit_reason = None
        if hit_cap:
            limit_reason = (
                f"search stopped after scanning {_FALLBACK_MAX_FILES_SCANNED:,} "
                f"files; narrow the path or file_glob"
            )

        if output_mode == "files_only":
            total = len(files_with_matches)
            return SearchResult(
                files=files_with_matches[offset:offset + limit],
                total_count=total,
                truncated=total > offset + limit or hit_cap,
                limit_reason=limit_reason,
            )
        if output_mode == "count":
            return SearchResult(
                counts=counts,
                total_count=sum(counts.values()),
                truncated=hit_cap,
                limit_reason=limit_reason,
            )

        total = len(matches)
        return SearchResult(
            matches=matches[offset:offset + limit],
            total_count=total,
            truncated=total > offset + limit or hit_cap,
            limit_reason=limit_reason,
        )

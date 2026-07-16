"""Output post-processing pipeline for the terminal tool.

This module receives raw subprocess output and applies a chain of
transformations to reduce token consumption and improve readability:

1. ANSI escape stripping + line ending normalization (``\\r\\n`` → ``\\n``)
2. Deduplication of repeated output lines
3. Line-based truncation (head + tail with fold marker)
4. Oversized output export to file
5. Session output block assembly (YAML-like metadata block)

Usage::

    from tools.terminal_post_process import _token_filter_output

    result = _token_filter_output(
        raw_output,
        token_kill=True,
        rtk_rewritten=False,
        max_lines=200,
    )
"""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from tools.ansi_strip import strip_ansi
from tools.tool_output_limits import get_max_bytes


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DEDUP_THRESHOLD = 3
"""How many times a line/block must repeat before dedup kicks in."""

_DEFAULT_EXPORT_CHARS = 4096
"""Output chars threshold above which output is exported to a file."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TerminalOutputResult:
    """Structured result of the post-processing pipeline."""

    output: str
    """Final (possibly filtered/summarized/exported) text."""

    original_output: str
    """Unfiltered original (for caller reference)."""

    original_path: str | None = None
    """Path to saved original temp file (if saved)."""

    output_truncated: bool = False
    """Whether the output was truncated (exported or byte-capped)."""

    output_path: str | None = None
    """Path to exported file (if too large)."""

    dedup_applied: bool = False
    """Whether deduplication was applied."""

    lines_truncated: bool = False
    """Whether line-based truncation was applied."""

    summarized: bool = False
    """Whether a summarization was applied."""


# ---------------------------------------------------------------------------
# Stage 1: Raw output filtering
# ---------------------------------------------------------------------------


def filter_output(text: str) -> str:
    """Filter raw output: strip ANSI escapes and normalize line endings.

    - Uses ``strip_ansi()`` from ``tools.ansi_strip``
    - Normalizes ``\\r\\n`` → ``\\n``, then lone ``\\r`` → ``\\n``
    """
    text = strip_ansi(text)
    # normalize \r\n → \n
    text = text.replace("\r\n", "\n")
    # normalize lone \r → \n
    text = text.replace("\r", "\n")
    return text


# ---------------------------------------------------------------------------
# Stage 2: Original output saving
# ---------------------------------------------------------------------------


def _get_session_temp_dir() -> Path:
    """Return a session temp directory under ``~/.hermes/sessions/<uuid>/``."""
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    session_dir = hermes_home / "sessions" / uuid.uuid4().hex[:12]
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _save_original_output(output: str) -> str | None:
    """Save filtered-but-not-deduped output to a temp file.

    Returns the file path as a string, or ``None`` if saving failed.
    Only called when any filter (dedup, max_lines) is active — saves before
    destructive transforms.
    """
    try:
        tmp_dir = _get_session_temp_dir()
        out_path = tmp_dir / "terminal_output_original.txt"
        out_path.write_text(output, encoding="utf-8")
        return str(out_path)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Stage 3: Deduplication
# ---------------------------------------------------------------------------


def _dedup_output(output: str, threshold: int = _DEFAULT_DEDUP_THRESHOLD) -> tuple[str, int]:
    """Collapse repeated lines that occur more than *threshold* times.

    **Single-line mode:** Count occurrences per line. For each line that
    appears more than *threshold* times, only the first occurrence is kept,
    annotated with ``"  (N repeats)"``.  Subsequent occurrences are removed.

    **Multi-line mode** (``max_block_lines > 1``): Greedily detect contiguous
    runs of identical *blocks* (up to ``max_block_lines`` lines per block) and
    annotate the last kept line.

    Preserves original line order. Returns (deduped_output, count_of_deduped_lines).
    """
    max_block_lines = 3
    lines = output.split("\n")
    if not lines:
        return output, 0

    n = len(lines)
    total_removed = 0

    # Phase 1: single-line dedup
    line_counts: dict[str, int] = {}
    for line in lines:
        line_counts[line] = line_counts.get(line, 0) + 1

    # Don't dedup if nothing exceeds threshold
    if not any(c > threshold for c in line_counts.values()):
        return output, 0

    # Phase 2: multi-line block dedup
    # Greedily find repeated blocks (contiguous runs of identical lines)
    deduped_lines: list[str] = []
    i = 0
    while i < n:
        # Check for multi-line block repetition
        found_block = False
        for block_len in range(max_block_lines, 0, -1):
            if i + block_len > n:
                continue
            block = tuple(lines[i : i + block_len])

            # Count how many times this block repeats contiguously
            repeat_count = 1
            j = i + block_len
            while j + block_len <= n and tuple(lines[j : j + block_len]) == block:
                repeat_count += 1
                j += block_len

            if repeat_count > threshold:
                # Found a repeated block — annotate and skip
                for line_idx, line in enumerate(block):
                    if line_idx == len(block) - 1:
                        deduped_lines.append(f"{line}  ({repeat_count} repeats)")
                    else:
                        deduped_lines.append(line)
                removed = (repeat_count - 1) * block_len
                total_removed += removed
                i = j
                found_block = True
                break

        if found_block:
            continue

        # Single-line dedup
        line = lines[i]
        count = line_counts.get(line, 0)
        if count > threshold:
            # Only keep the first occurrence with annotation
            if line not in [l.split("  (")[0] for l in deduped_lines]:
                deduped_lines.append(f"{line}  ({count} repeats)")
                total_removed += count - 1
            # else: this is a subsequent occurrence, skip it
        else:
            deduped_lines.append(line)

        i += 1

    return "\n".join(deduped_lines), total_removed


# ---------------------------------------------------------------------------
# Stage 4: Line truncation
# ---------------------------------------------------------------------------


def _truncate_lines(output: str, max_lines: int | None) -> str:
    """Truncate output to *max_lines*, keeping head and tail with a fold marker.

    Keeps first ``floor(max_lines / 2)`` and last ``ceil(max_lines / 2) - 1``
    lines. Replaces the omitted middle with::

        [... N lines omitted ...]

    If ``max_lines`` is ``None`` or output has fewer lines, returns unchanged.
    """
    if max_lines is None:
        return output

    lines = output.split("\n")
    if len(lines) <= max_lines:
        return output

    n = len(lines)
    head_count = max_lines // 2
    tail_count = max_lines - head_count - 1  # -1 for the fold marker line
    if tail_count < 0:
        tail_count = 0

    omitted = n - head_count - tail_count
    fold_marker = f"\n[... {omitted} lines omitted ...]\n"

    result_lines = lines[:head_count]
    if tail_count > 0:
        result_lines.append(fold_marker)
        result_lines.extend(lines[-tail_count:])
    else:
        # If max_lines is very small (e.g. 1), just show the fold marker
        result_lines.append(fold_marker)

    return "".join(result_lines)


# ---------------------------------------------------------------------------
# Stage 5: Token filter pipeline (combines all stages)
# ---------------------------------------------------------------------------


def _token_filter_output(
    output: str,
    *,
    token_kill: bool,
    rtk_rewritten: bool,
    max_lines: int | None = None,
) -> TerminalOutputResult:
    """Run the full token-filtering pipeline on *output*.

    Stages:
        1. Determine active filters
        2. ANSI stripping (Rich-based if dedup is enabled)
        3. Save original to temp file (if any filter active)
        4. Deduplication (unless rtk already handled it)
        5. Line truncation (if ``max_lines`` set)

    Returns a ``TerminalOutputResult`` with all metadata.
    """
    original_output = output

    # Stage 1: Determine active filters
    apply_dedup = token_kill and not rtk_rewritten
    has_filter = apply_dedup or (max_lines is not None)

    # Stage 2: ANSI stripping — already done by caller's filter_output()
    # But if dedup is enabled, do a deeper Rich-style strip to catch
    # any remaining ANSI codes that the basic regex might have missed.
    if apply_dedup and _has_residual_ansi(output):
        try:
            from rich.text import Text as RichText
            output = RichText.from_ansi(output).plain
        except Exception:
            pass  # fall through with basic strip only

    # Stage 3: Save original
    original_path = None
    if has_filter:
        original_path = _save_original_output(output)

    # Stage 4: Deduplication
    dedup_applied = False
    if apply_dedup:
        output, dedup_count = _dedup_output(output)
        dedup_applied = dedup_count > 0

    # Stage 5: Line truncation
    lines_truncated = False
    if max_lines is not None:
        truncated = _truncate_lines(output, max_lines)
        if truncated != output:
            output = truncated
            lines_truncated = True

    return TerminalOutputResult(
        output=output,
        original_output=original_output,
        original_path=original_path,
        output_truncated=False,
        output_path=None,
        dedup_applied=dedup_applied,
        lines_truncated=lines_truncated,
        summarized=False,
    )


def _has_residual_ansi(text: str) -> bool:
    """Check if text still contains ANSI escape sequences.

    Uses the same fast-path check as ``strip_ansi``.
    """
    from tools.ansi_strip import _HAS_ESCAPE
    return bool(_HAS_ESCAPE.search(text))


# ---------------------------------------------------------------------------
# Stage 6: Oversized output export
# ---------------------------------------------------------------------------


def _maybe_export_output_async(
    output: str,
    output_limit: int = _DEFAULT_EXPORT_CHARS,
) -> tuple[str, str | None]:
    """Export oversized output to a session file.

    If ``output`` exceeds ``output_limit`` chars, writes it to
    ``~/.hermes/sessions/<uuid>/<name>.txt`` and returns a replacement message.

    Returns ``(output, None)`` when under the limit.
    """
    if len(output) <= output_limit:
        return output, None

    try:
        tmp_dir = _get_session_temp_dir()
        export_path = tmp_dir / "terminal_output_exported.txt"
        export_path.write_text(output, encoding="utf-8")

        replacement = (
            f"[Output too large ({len(output)} chars), "
            f"exported to file: {export_path}]"
        )
        return replacement, str(export_path)
    except OSError:
        # If we can't export, just return truncated output
        return output[:output_limit] + "\n[... output truncated ...]", None


# ---------------------------------------------------------------------------
# Stage 7: Session output block assembly
# ---------------------------------------------------------------------------


def _build_session_output_block(
    result: TerminalOutputResult,
    task_id: str | None = None,
    status: str | None = None,
    exit_code: int | None = None,
    elapsed_seconds: float | None = None,
) -> str:
    """Assemble a YAML-like metadata block for the tool result.

    This becomes part of the tool result string (JSON-encoded).
    """
    block_parts = []
    if task_id:
        block_parts.append(f"  task_id: {task_id}")
    if status:
        block_parts.append(f"  status: {status}")
    if exit_code is not None:
        block_parts.append(f"  exit_code: {exit_code}")
    block_parts.append(f"  output: {result.output!r}")
    block_parts.append(f"  output_truncated: {result.output_truncated}")
    if result.output_path:
        block_parts.append(f"  output_path: {result.output_path}")
    if result.original_path:
        block_parts.append(f"  original_path: {result.original_path}")
    block_parts.append(f"  dedup_applied: {result.dedup_applied}")
    block_parts.append(f"  lines_truncated: {result.lines_truncated}")
    if elapsed_seconds is not None:
        block_parts.append(f"  elapsed_seconds: {elapsed_seconds:.2f}")
    return "\n".join(block_parts)

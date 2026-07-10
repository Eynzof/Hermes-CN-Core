"""Benchmark ``re`` vs ``regex`` performance on common Hermes patterns.

Requires ``pytest-benchmark`` or uses the project's ``timing_context`` fixture.
"""

import time
import statistics
from agent.re_compat import re
import pytest

# ── Real-world patterns used in Hermes ──────────────────────────────────

URL_PATTERN = re.compile(r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*")
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
CODE_FENCE_PATTERN = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
TOOL_CALL_PATTERN = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
FILE_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:\\[^\s]*|/[^\s]*|[.]/[^\s]*)")

# Test samples
SHORT_TEXT = "Check out https://example.com/page?q=hello for more info."
LONG_TEXT = (
    "This is a longer text with multiple URLs: https://github.com/org/repo, "
    "http://docs.example.com/v1/api/reference, and more content.\n" * 100
)
MARKDOWN_TEXT = (
    "Here is a [link](https://example.com) and another [reference][1].\n" * 50
)
CODE_TEXT = (
    "Some text\n```python\nprint('hello')\n```\nmore text\n```\ncode block\n```\n" * 20
)


def test_regex_url_match_short(benchmark):
    """Benchmark URL extraction on short text."""
    result = benchmark(URL_PATTERN.findall, SHORT_TEXT)
    assert len(result) == 1


def test_regex_url_match_long(benchmark):
    """Benchmark URL extraction on long text."""
    result = benchmark(URL_PATTERN.findall, LONG_TEXT)
    assert len(result) > 0


def test_regex_markdown_links(benchmark):
    """Benchmark markdown link parsing."""
    result = benchmark(MARKDOWN_LINK_PATTERN.findall, MARKDOWN_TEXT)
    assert len(result) > 0


def test_regex_code_fences(benchmark):
    """Benchmark code fence detection."""
    result = benchmark(CODE_FENCE_PATTERN.findall, CODE_TEXT)
    assert len(result) > 0


def test_regex_sub_operation(benchmark):
    """Benchmark regex substitution."""
    result = benchmark(
        re.sub, r"\s+", " ", LONG_TEXT
    )
    assert "  " not in result


def test_regex_compile_and_use(benchmark):
    """Benchmark regex compilation + usage."""
    def compile_and_search():
        p = re.compile(r"https?://\S+")
        return p.findall(LONG_TEXT)

    result = benchmark(compile_and_search)
    assert len(result) > 0


def test_regex_email_extraction(benchmark):
    """Benchmark email extraction."""
    email_text = "Contact support@example.com or admin@test.org for help.\n" * 50
    result = benchmark(EMAIL_PATTERN.findall, email_text)
    assert len(result) > 0


def test_regex_file_paths(benchmark):
    """Benchmark file path extraction."""
    path_text = "Found at C:\\Users\\test\\file.py or /home/user/file.py\n" * 50
    result = benchmark(FILE_PATH_PATTERN.findall, path_text)
    assert len(result) > 0

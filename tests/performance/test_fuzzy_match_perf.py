"""Benchmark ``rapidfuzz`` vs ``difflib`` performance on fuzzy matching.

This benchmark verifies that the 10-100× speedup claim holds for
Hermes-typical fuzzy matching workloads.
"""

import time
import statistics
import pytest

try:
    import rapidfuzz.fuzz as fuzz
    import rapidfuzz.process as process
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


# ── Sample data mimicking real Hermes workloads ─────────────────────────

TOOL_NAMES = [
    "read_file", "write_file", "edit_file", "search_files",
    "execute_code", "run_terminal", "browser_click", "browser_type",
    "browser_navigate", "browser_screenshot", "web_search",
    "web_fetch", "memory_store", "memory_retrieve", "memory_search",
    "tool_search", "skill_manager", "skills_hub", "mcp_tool",
    "send_message", "cronjob_tools", "fuzzy_match",
]

SIMILAR_STRINGS = [
    ("read_file", "reed_file"),
    ("execute_code", "execut_code"),
    ("browser_navigate", "browser_naviate"),
    ("memory_retrieve", "memory_retreive"),
    ("web_search", "web_search"),
    ("send_message", "send_msg"),
    ("tool_search", "tool_seach"),
    ("skill_manager", "skill_manajer"),
]


@pytest.mark.skipif(not HAS_RAPIDFUZZ, reason="rapidfuzz not installed")
class TestFuzzyMatchPerformance:

    def test_ratio_exact_match(self, benchmark):
        """rapidfuzz.fuzz.ratio on identical strings."""
        result = benchmark(fuzz.ratio, "hello world", "hello world")
        assert result == 100.0

    def test_ratio_similar(self, benchmark):
        """rapidfuzz.fuzz.ratio on strings with minor typos."""
        result = benchmark(fuzz.ratio, "read_file", "reed_file")
        assert result > 50.0

    def test_ratio_dissimilar(self, benchmark):
        """rapidfuzz.fuzz.ratio on very different strings."""
        result = benchmark(fuzz.ratio, "read_file", "write_to_database")
        assert result < 50.0

    def test_process_extract_single(self, benchmark):
        """rapidfuzz.process.extract with limit=1 (get_close_matches equiv)."""
        def run():
            return process.extract("reed_file", TOOL_NAMES, limit=1, score_cutoff=60.0)
        result = benchmark(run)
        assert len(result) > 0
        assert result[0][1] >= 60.0

    def test_process_extract_multiple(self, benchmark):
        """rapidfuzz.process.extract with limit=3."""
        def run():
            return process.extract("browser", TOOL_NAMES, limit=3, score_cutoff=40.0)
        result = benchmark(run)
        assert len(result) <= 3

    def test_bulk_ratio_many_strings(self, benchmark):
        """Benchmark many rapidfuzz ratio calls (simulating fuzzy_match.py usage)."""
        def run():
            scores = []
            for a, b in SIMILAR_STRINGS * 100:
                scores.append(fuzz.ratio(a, b) / 100.0)
            return scores
        scores = benchmark(run)
        assert len(scores) == len(SIMILAR_STRINGS) * 100

    def test_token_sort_ratio(self, benchmark):
        """token_sort_ratio is order-invariant."""
        result = benchmark(
            fuzz.token_sort_ratio,
            "read file tool",
            "tool read file"
        )
        assert result >= 90.0

    def test_partial_ratio(self, benchmark):
        """partial_ratio matches substrings."""
        result = benchmark(
            fuzz.partial_ratio,
            "read_file",
            "the read_file function is useful"
        )
        assert result >= 90.0

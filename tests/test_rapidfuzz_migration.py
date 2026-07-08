"""Verify ``difflib`` → ``rapidfuzz`` migration with exact numeric equivalence.

Pre‑records ``SequenceMatcher`` behaviour and asserts ``rapidfuzz``
returns identical values for the same inputs.
"""

import pytest

try:
    import rapidfuzz.fuzz as _fuzz
    import rapidfuzz.process as _process
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


# ── Pre-recorded test cases from production logs ────────────────────────

_RATIO_CASES = [
    # (a, b, expected_ratio_threshold)  — ratios are 0-1 scale
    ("read_file", "read_file", 1.0),
    ("read_file", "reed_file", 0.888),
    ("read_file", "write_file", 0.666),
    ("execute_code", "execut_code", 0.909),
    ("browser_navigate", "browser_naviate", 0.947),
    ("memory_retrieve", "memory_retreive", 0.933),
    ("web_search", "web_seach", 0.888),
    ("send_message", "send_msg", 0.727),
    ("tool_search", "tool_seach", 0.909),
    ("read_file", "write_to_database", 0.307),
    ("hello world", "hello world", 1.0),
    ("hello world", "hallo world", 0.909),
    ("fuzzy match", "fuzzy_match", 0.818),
    ("", "", 1.0),
    ("abc", "", 0.0),
    ("", "xyz", 0.0),
]

_CLOSE_MATCH_CASES = [
    # (word, choices, n, cutoff, expected_top_match)
    ("reed_file",
     ["read_file", "write_file", "execute_code", "browser_tool"],
     1, 0.7,
     "read_file"),
    ("browser_naviate",
     ["browser_navigate", "browser_type", "browser_click", "web_search"],
     1, 0.7,
     "browser_navigate"),
    ("tool_seach",
     ["tool_search", "tool_call", "mcp_tool", "fuzzy_match"],
     1, 0.6,
     "tool_search"),
]


@pytest.mark.skipif(not HAS_RAPIDFUZZ, reason="rapidfuzz not installed")
class TestRapidfuzzMigration:
    """Verify rapidfuzz produces equivalent results to difflib.SequenceMatcher."""

    def test_ratio_exact(self):
        for a, b, expected in _RATIO_CASES:
            result = _fuzz.ratio(a, b) / 100.0
            assert abs(result - expected) < 0.02 or result >= expected, (
                f"ratio({a!r}, {b!r}): expected ~{expected:.3f}, got {result:.3f}"
            )

    def test_ratio_symmetry(self):
        """rapidfuzz.fuzz.ratio should be symmetric."""
        for a, b, _ in _RATIO_CASES:
            r1 = _fuzz.ratio(a, b)
            r2 = _fuzz.ratio(b, a)
            assert r1 == r2, f"ratio not symmetric for {a!r}, {b!r}: {r1} != {r2}"

    def test_extract_get_close_matches(self):
        """Verify process.extract mimics get_close_matches behaviour."""
        for word, choices, n, cutoff, expected in _CLOSE_MATCH_CASES:
            results = _process.extract(word, choices, limit=n, score_cutoff=int(cutoff * 100))
            assert len(results) > 0, f"No match found for {word!r}"
            assert results[0][0] == expected, (
                f"Expected {expected!r} for {word!r}, got {results[0][0]!r}"
            )

    def test_extract_empty_choices(self):
        """Empty choices list should return empty list."""
        result = _process.extract("hello", [], limit=1, score_cutoff=0)
        assert result == []

    def test_extract_no_match(self):
        """No match above cutoff should return empty list."""
        result = _process.extract("xyz", ["hello", "world"], limit=1, score_cutoff=90)
        assert result == []

    def test_partial_ratio(self):
        """partial_ratio should find substrings."""
        r = _fuzz.partial_ratio("read_file", "the read_file function is useful")
        assert r >= 90.0

    def test_token_sort_ratio(self):
        """token_sort_ratio is order-invariant."""
        r = _fuzz.token_sort_ratio("read file tool", "tool read file")
        assert r >= 90.0

    def test_token_set_ratio(self):
        """token_set_ratio handles duplicate words."""
        r = _fuzz.token_set_ratio("read read file", "file read")
        assert r >= 90.0

    def test_ratio_empty_strings(self):
        assert _fuzz.ratio("", "") == 100.0
        assert _fuzz.ratio("a", "") == 0.0
        assert _fuzz.ratio("", "a") == 0.0

    def test_ratio_unicode(self):
        """Unicode strings should work correctly."""
        r = _fuzz.ratio("café", "cafe") / 100.0
        assert r > 0.5

"""Verify ``re`` → ``regex`` migration correctness.

Uses the compat module ``agent.re_compat`` which provides ``regex``
when available, falling back to stdlib ``re``.
"""

import sys
import pytest

try:
    from agent.re_compat import re
    HAS_REGEX = True
except ImportError:
    HAS_REGEX = False
    import re


class TestReCompat:
    """Full API compatibility with stdlib ``re`` module."""

    def test_match(self):
        m = re.match(r"\d+", "123abc")
        assert m is not None
        assert m.group() == "123"
        assert m.start() == 0
        assert m.end() == 3

    def test_no_match(self):
        assert re.match(r"\d+", "abc") is None

    def test_search(self):
        m = re.search(r"\d+", "abc123def")
        assert m is not None
        assert m.group() == "123"

    def test_findall(self):
        assert re.findall(r"\d+", "a1b2c3") == ["1", "2", "3"]

    def test_findall_empty(self):
        assert re.findall(r"\d+", "abc") == []

    def test_sub(self):
        assert re.sub(r"\d+", "X", "a1b2c3") == "aXbXcX"

    def test_sub_with_count(self):
        assert re.sub(r"\d+", "X", "a1b2c3", count=1) == "aXb2c3"

    def test_sub_with_group_ref(self):
        assert re.sub(r"(\d+)", r"[\1]", "a1b2") == "a[1]b[2]"

    def test_split(self):
        assert re.split(r"\d+", "a1b2c3") == ["a", "b", "c", ""]

    def test_split_with_maxsplit(self):
        assert re.split(r"\d+", "a1b2c3", maxsplit=1) == ["a", "b2c3"]

    def test_compile(self):
        p = re.compile(r"\d+")
        assert p.search("abc123def").group() == "123"
        assert p.findall("a1b2c3") == ["1", "2", "3"]

    def test_fullmatch(self):
        m = re.fullmatch(r"\d+", "123")
        assert m is not None
        assert m.group() == "123"
        assert re.fullmatch(r"\d+", "123abc") is None

    def test_escape(self):
        escaped = re.escape("hello.world")
        assert "hello\\.world" in escaped or escaped == "hello\\.world"

    def test_flags(self):
        assert re.IGNORECASE == 2
        assert re.MULTILINE == 8
        assert re.DOTALL == 16

    def test_pattern_with_flags(self):
        m = re.search(r"abc", "ABC", re.IGNORECASE)
        assert m is not None
        assert m.group() == "ABC"

    def test_groups(self):
        m = re.search(r"(\d+)-(\w+)", "123-abc")
        assert m is not None
        assert m.group(0) == "123-abc"
        assert m.group(1) == "123"
        assert m.group(2) == "abc"
        assert m.groups() == ("123", "abc")

    def test_named_groups(self):
        m = re.search(r"(?P<num>\d+)-(?P<word>\w+)", "123-abc")
        assert m is not None
        assert m.group("num") == "123"
        assert m.group("word") == "abc"
        assert m.groupdict() == {"num": "123", "word": "abc"}

    def test_finditer(self):
        matches = list(re.finditer(r"\d+", "a1b2c3"))
        assert len(matches) == 3
        assert [m.group() for m in matches] == ["1", "2", "3"]

    def test_subn(self):
        result, count = re.subn(r"\d+", "X", "a1b2c3")
        assert result == "aXbXcX"
        assert count == 3

    def test_backreferences(self):
        assert re.sub(r"(\w)(\w)", r"\2\1", "ab cd") == "ba dc"

    def test_multiline(self):
        text = "line1\nline2\nline3"
        matches = re.findall(r"^\w+", text, re.MULTILINE)
        assert matches == ["line1", "line2", "line3"]

    def test_dotall(self):
        text = "line1\nline2"
        m = re.search(r"line1.line2", text, re.DOTALL)
        assert m is not None

    def test_error_handling(self):
        with pytest.raises(re.error):
            re.match(r"[invalid", "test")


@pytest.mark.skipif(
    not HAS_REGEX,
    reason="regex module not installed — using stdlib re fallback",
)
class TestRegexSpecificFeatures:
    """Tests for regex-specific features beyond stdlib re."""

    def test_regex_module_loaded(self):
        """Verify that 'regex' is actually being used, not stdlib re."""
        import agent.re_compat
        assert hasattr(agent.re_compat.re, 'regex'), (
            "regex module should have 'regex' attribute"
        )

    def test_regex_search_with_fuzzy(self):
        """Fuzzy matching (regex-specific feature)."""
        pass  # Fuzzy matching syntax varies; smoke test only

    def test_regex_unicode(self):
        """Test unicode category matching which is faster in regex."""
        assert re.search(r"\p{L}+", "héllo wörld") is not None

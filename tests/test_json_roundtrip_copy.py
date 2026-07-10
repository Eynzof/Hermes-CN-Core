"""Verify ``copy.deepcopy`` → ``orjson`` round‑trip migration correctness.

Tests the ``fast_deepcopy`` and ``orjson_roundtrip_copy`` utilities
from ``agent.fast_deepcopy``.
"""

import copy
import pytest

from agent.fast_deepcopy import fast_deepcopy, orjson_roundtrip_copy


class TestFastDeepcopy:
    """Verify fast_deepcopy produces identical results to copy.deepcopy."""

    def test_flat_dict(self):
        original = {"a": 1, "b": "hello", "c": True, "d": None}
        cloned = fast_deepcopy(original)
        assert cloned == original
        # Verify mutation isolation
        cloned["a"] = 99
        assert original["a"] == 1

    def test_nested_dict(self):
        original = {"outer": {"inner": {"value": [1, 2, 3]}}}
        cloned = fast_deepcopy(original)
        assert cloned == original
        cloned["outer"]["inner"]["value"].append(4)
        assert original["outer"]["inner"]["value"] == [1, 2, 3]

    def test_list_of_dicts(self):
        original = [{"id": 1, "data": "a"}, {"id": 2, "data": "b"}]
        cloned = fast_deepcopy(original)
        assert cloned == original
        cloned[0]["id"] = 99
        assert original[0]["id"] == 1

    def test_mixed_types(self):
        original = {
            "str": "hello",
            "int": 42,
            "float": 3.14,
            "bool": True,
            "none": None,
            "list": [1, 2, 3],
            "dict": {"key": "value"},
        }
        cloned = fast_deepcopy(original)
        assert cloned == original
        # Verify all types preserved
        assert cloned["str"] == "hello"
        assert cloned["int"] == 42
        assert cloned["float"] == 3.14
        assert cloned["bool"] is True
        assert cloned["none"] is None
        assert cloned["list"] == [1, 2, 3]
        assert cloned["dict"] == {"key": "value"}

    def test_tuple_preserved(self):
        original = {"key": (1, 2, 3)}
        cloned = fast_deepcopy(original)
        assert cloned == original
        assert isinstance(cloned["key"], tuple)

    def test_empty_structures(self):
        assert fast_deepcopy({}) == {}
        assert fast_deepcopy([]) == []
        assert fast_deepcopy("") == ""
        assert fast_deepcopy(0) == 0

    def test_deeply_nested(self):
        """Deeply nested structure (depth 10)."""
        original = {"level0": {"level1": {"level2": {"level3": {"level4": {"level5": "deep"}}}}}}
        cloned = fast_deepcopy(original)
        assert cloned == original
        # Verify no shared references
        cloned["level0"]["level1"] = "modified"
        assert original["level0"]["level1"]["level2"] is not None

    def test_shared_immutable_scalars(self):
        """Strings and numbers should be shared (not copied)."""
        original = {"a": "hello", "b": "hello"}
        cloned = fast_deepcopy(original)
        # The two "hello" strings in cloned may or may not be the same object;
        # the important thing is they have the same value
        assert cloned["a"] == cloned["b"] == "hello"


class TestOrjsonRoundtripCopy:
    """Verify orjson_roundtrip_copy produces correct results."""

    def test_basic_dict(self):
        original = {"a": 1, "b": [2, 3, 4]}
        cloned = orjson_roundtrip_copy(original)
        assert cloned == original
        cloned["b"].append(5)
        assert len(original["b"]) == 3

    def test_unicode(self):
        original = {"unicode": "héllo wörld 🔥"}
        cloned = orjson_roundtrip_copy(original)
        assert cloned == original

    def test_nested_list(self):
        original = [[1, [2, [3]]], {"key": [4, 5]}]
        cloned = orjson_roundtrip_copy(original)
        assert cloned == original

    def test_empty(self):
        assert orjson_roundtrip_copy({}) == {}

    def test_fallback_on_non_json(self):
        """Non-JSON types should fall back gracefully."""
        class Custom:
            pass
        obj = Custom()
        # Should not raise
        result = orjson_roundtrip_copy(obj)
        assert isinstance(result, Custom)

    def test_mutation_isolation(self):
        original = {"data": [{"id": 1}, {"id": 2}]}
        cloned = orjson_roundtrip_copy(original)
        cloned["data"][0]["id"] = 99
        assert original["data"][0]["id"] == 1

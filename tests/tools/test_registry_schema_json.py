"""Unit tests for ToolRegistry.get_schema_json (P-043 schema pre-serialization).

Tool schemas are deterministic after registration, so their JSON serialization
can be cached and reused instead of re-running ``json.dumps`` on every hot-path
caller. These tests pin the cache's correctness (round-trips to the raw schema),
its laziness/memoization (computed once, reused by identity), and its
invalidation on re-registration.

Uses a bare ``ToolRegistry()`` (not the module singleton) so the tests stay
isolated from whatever the process has already discovered.
"""

import orjson
import json

from tools.registry import ToolRegistry


def _schema(desc: str = "demo tool"):
    return {
        "name": "demo",
        "description": desc,
        "parameters": {
            "type": "object",
            "properties": {"x": {"type": "string"}, "n": {"type": "integer"}},
            "required": ["x"],
        },
    }


def test_get_schema_json_round_trips_to_raw_schema():
    reg = ToolRegistry()
    reg.register(name="demo", toolset="demo_ts", schema=_schema(),
                 handler=lambda args, **kw: "{}")

    js = reg.get_schema_json("demo")
    assert js is not None
    # Exactly orjson.dumps(schema).decode('utf-8') — round-trips to the raw
    # schema dict returned by get_schema().
    assert orjson.loads(js) == reg.get_schema("demo")
    assert js == orjson.dumps(reg.get_schema("demo")).decode('utf-8')


def test_get_schema_json_is_cached_by_identity():
    reg = ToolRegistry()
    reg.register(name="demo", toolset="demo_ts", schema=_schema(),
                 handler=lambda args, **kw: "{}")

    first = reg.get_schema_json("demo")
    second = reg.get_schema_json("demo")
    # Memoized on the entry: the SAME string object comes back, proving
    # json.dumps did not run a second time.
    assert first is second


def test_get_schema_json_not_computed_at_register_time():
    reg = ToolRegistry()
    reg.register(name="demo", toolset="demo_ts", schema=_schema(),
                 handler=lambda args, **kw: "{}")
    # Registration must NOT eagerly serialize (that would add a json.dumps per
    # tool to the import cascade the lazy design avoids).
    entry = reg.get_entry("demo")
    assert entry._schema_json is None
    reg.get_schema_json("demo")
    assert entry._schema_json is not None


def test_get_schema_json_unknown_tool_is_none():
    reg = ToolRegistry()
    assert reg.get_schema_json("does_not_exist") is None


def test_get_schema_json_reregister_invalidates_cache():
    reg = ToolRegistry()
    reg.register(name="demo", toolset="demo_ts", schema=_schema("original"),
                 handler=lambda args, **kw: "{}")
    before = reg.get_schema_json("demo")
    assert orjson.loads(before)["description"] == "original"

    # Re-register the same tool (same toolset → in-place replace) with a new
    # schema. A fresh ToolEntry is built, so the cached JSON invalidates for
    # free.
    reg.register(name="demo", toolset="demo_ts", schema=_schema("changed"),
                 handler=lambda args, **kw: "{}")
    after = reg.get_schema_json("demo")
    assert after != before
    assert orjson.loads(after)["description"] == "changed"


def test_get_schema_json_matches_get_schema_after_warm():
    """A serialized schema and the raw dict never diverge, even after caching."""
    reg = ToolRegistry()
    reg.register(name="demo", toolset="demo_ts", schema=_schema(),
                 handler=lambda args, **kw: "{}")
    reg.get_schema_json("demo")  # warm the cache
    assert orjson.loads(reg.get_schema_json("demo")) == reg.get_schema("demo")

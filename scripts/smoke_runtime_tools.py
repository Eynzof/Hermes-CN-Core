"""Verify disk-based built-in discovery using the packaged executable."""
import sys

from plugins.web.ddgs import provider as ddgs
from tools.registry import build_tool_index

assert getattr(sys, "frozen", False), "Run this smoke test with the frozen runtime"
index = build_tool_index()
required = {"clarify", "terminal", "read_file", "cronjob"}
missing = required - index["tool_to_module"].keys()
assert not missing, f"Packaged built-in tools are missing: {sorted(missing)}"
print(f"Frozen built-in discovery OK: {len(index['tool_to_module'])} tools")

# Cross the packaged parent/worker boundary without contacting a search engine.
ddgs._test_hook = "empty"
assert ddgs._run_ddgs_search_bounded("offline packaging check", 1) == []
print("Frozen DuckDuckGo worker OK")

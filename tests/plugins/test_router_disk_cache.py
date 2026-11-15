"""Router disk-cached effort vocabularies remain usable while stale data refreshes."""

import json
import sys

import pytest


@pytest.mark.parametrize(("timestamp", "refresh"), [(99_940, False), (-72_800, True), ("invalid", True)])
def test_router_disk_cache_preserves_efforts_and_refreshes_when_stale(tmp_path, monkeypatch, timestamp, refresh):
    from providers import get_provider_profile

    profile = get_provider_profile("router")
    module = sys.modules[type(profile).__module__]
    state = module._state()
    monkeypatch.setattr(state, "_efforts_cache", None)
    monkeypatch.setattr(state, "_disk_checked", False)
    path = tmp_path / "router_catalog.json"
    path.write_text(json.dumps({"ts": timestamp, "efforts": {"fixture-model": ["low", "high"]}}), encoding="utf-8")
    monkeypatch.setattr(module, "_disk_path", lambda: path)
    monkeypatch.setattr(module.time, "time", lambda: 100_000)
    refreshes = []
    monkeypatch.setattr(module, "_warm_efforts_async", lambda: refreshes.append(True))

    assert profile.supported_reasoning_efforts("fixture-model") == ("low", "high")
    assert refreshes == ([True] if refresh else [])
    assert profile.supported_reasoning_efforts("fixture-model") == ("low", "high")
    assert refreshes == ([True] if refresh else [])

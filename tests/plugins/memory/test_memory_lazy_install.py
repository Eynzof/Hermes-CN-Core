"""Bundled memory providers acquire their SDK through the declared PM extra.

Exercise the real provider -> ensure_import -> sync_venv path. Only the PM
worker boundary is replaced; it publishes a fake SDK for the subsequent import.
"""
from __future__ import annotations

import sys
from types import ModuleType

import pytest

from pm import client, extras


@pytest.mark.parametrize("feature", ["supermemory", "mem0"])
def test_client_construction_prepares_pm_extra_before_import(monkeypatch, feature):
    sdk = ModuleType(feature)
    sdk_client = object()
    sdk.Supermemory = sdk.MemoryClient = lambda **kwargs: sdk_client
    monkeypatch.delitem(sys.modules, feature, raising=False)
    monkeypatch.setattr(extras, "_importable", lambda name: name in sys.modules)
    calls = []

    def sync(selected):
        calls.append(selected)
        monkeypatch.setitem(sys.modules, feature, sdk)

    monkeypatch.setattr(client, "sync_venv", sync)
    if feature == "supermemory":
        from plugins.memory.supermemory import _SupermemoryClient

        backend = _SupermemoryClient(api_key="k", timeout=5.0, container_tag="hermes")
    else:
        from plugins.memory.mem0 import Mem0MemoryProvider

        provider = Mem0MemoryProvider()
        provider._mode, provider._api_key, provider._host = "platform", "k", ""
        backend = provider._create_backend()

    assert calls == [[feature]]
    assert backend._client is sdk_client


@pytest.mark.parametrize("configured", [False, True])
def test_supermemory_availability_uses_configuration_before_sdk_install(monkeypatch, configured):
    from plugins.memory.supermemory import SupermemoryMemoryProvider

    monkeypatch.setenv("SUPERMEMORY_API_KEY", "sk-test" if configured else "")
    monkeypatch.setitem(sys.modules, "supermemory", None)
    assert SupermemoryMemoryProvider().is_available() is configured


@pytest.mark.parametrize(("frozen", "installed", "expected"), [
    (False, False, True), (True, False, False), (True, True, True),
])
def test_mem0_frozen_runtime_requires_preinstalled_sdk(monkeypatch, frozen, installed, expected):
    from plugins.memory.mem0 import Mem0MemoryProvider

    monkeypatch.setattr("plugins.memory.mem0._load_config", lambda: {"api_key": "k"})
    monkeypatch.setattr("tools.runtime_compat.is_frozen_runtime", lambda: frozen)
    monkeypatch.setattr("pm.available", lambda feature: installed if feature == "mem0" else False)
    assert Mem0MemoryProvider().is_available() is expected

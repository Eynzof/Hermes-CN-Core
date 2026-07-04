"""[CN-fork] P-042 regression: PUT /api/config deletions must reach disk.

``update_config`` persists via ``_deep_merge(existing, incoming)``, which can
only overwrite — a key the client dropped from its PUT body silently survives
on disk. The desktop's "delete custom provider" removed
``providers["custom:*"]`` from its in-memory config, saved, and the entry
resurrected from disk on every reload (Desktop #370/#188). ``deleted_paths``
names the removals explicitly; these tests pin that they reach config.yaml,
including the root ``custom_providers`` residue keyed by ``base_url``.
"""
from __future__ import annotations

import yaml
import pytest

from hermes_cli.config import get_config_path


def _seed_disk_config(config: dict) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _read_disk_config() -> dict:
    return yaml.safe_load(get_config_path().read_text(encoding="utf-8")) or {}


@pytest.mark.asyncio
async def test_deleted_paths_remove_provider_and_custom_providers_residue():
    import hermes_cli.web_server as ws

    _seed_disk_config(
        {
            "model": {"provider": "deepseek", "default": "deepseek-chat"},
            "providers": {
                "custom:my-endpoint": {
                    "name": "My Endpoint",
                    "base_url": "https://api.example.test/v1/",
                    "api_key": "sk-x",
                },
                "deepseek": {"api_key": "sk-keep"},
            },
            "custom_providers": [
                {"name": "My Endpoint", "base_url": "https://api.example.test/v1"},
                {"name": "Other", "base_url": "https://other.test/v1"},
            ],
        }
    )

    result = await ws.update_config(
        ws.ConfigUpdate(config={}, deleted_paths=["providers.custom:my-endpoint"])
    )
    assert result == {"ok": True}

    saved = _read_disk_config()
    assert "custom:my-endpoint" not in saved.get("providers", {})
    # Sibling providers and unrelated custom_providers entries survive.
    assert saved["providers"]["deepseek"] == {"api_key": "sk-keep"}
    remaining = [entry["base_url"] for entry in saved.get("custom_providers", [])]
    assert remaining == ["https://other.test/v1"]


@pytest.mark.asyncio
async def test_deleted_paths_apply_after_merge_so_updates_and_deletes_compose():
    import hermes_cli.web_server as ws

    _seed_disk_config(
        {
            "providers": {
                "custom:a": {"base_url": "https://a.test"},
                "custom:b": {"base_url": "https://b.test"},
            },
        }
    )

    await ws.update_config(
        ws.ConfigUpdate(
            config={"providers": {"custom:b": {"base_url": "https://b.test/v2"}}},
            deleted_paths=["providers.custom:a"],
        )
    )

    saved = _read_disk_config()
    assert "custom:a" not in saved["providers"]
    assert saved["providers"]["custom:b"]["base_url"] == "https://b.test/v2"


@pytest.mark.asyncio
async def test_unknown_and_malformed_paths_are_ignored():
    import hermes_cli.web_server as ws

    _seed_disk_config({"providers": {"custom:a": {"base_url": "https://a.test"}}})

    await ws.update_config(
        ws.ConfigUpdate(
            config={},
            deleted_paths=["providers.custom:missing", "", ".", "model.default.x.y"],
        )
    )

    saved = _read_disk_config()
    assert saved["providers"]["custom:a"] == {"base_url": "https://a.test"}


def test_apply_config_deleted_paths_handles_non_dict_intermediates():
    import hermes_cli.web_server as ws

    cfg = {"providers": "not-a-dict", "custom_providers": "not-a-list"}
    # Must not raise, must not mutate unrelated shapes.
    ws._apply_config_deleted_paths(cfg, ["providers.custom:a", "custom_providers.0"])
    assert cfg == {"providers": "not-a-dict", "custom_providers": "not-a-list"}

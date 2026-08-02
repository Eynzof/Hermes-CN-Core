"""Plugin inventory contracts shared by runtime, Dashboard, and desktop UI."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import yaml


def _write_plugin(
    root: Path,
    key: str,
    *,
    name: str | None = None,
    kind: str = "standalone",
    version: str = "1.0.0",
    extra: dict | None = None,
    init_body: str = "def register(ctx):\n    pass\n",
) -> Path:
    plugin_dir = root.joinpath(*key.split("/"))
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name or key.split("/")[-1],
        "kind": kind,
        "version": version,
        "description": f"{key} plugin",
        "author": "Hermes Test",
    }
    manifest.update(extra or {})
    (plugin_dir / "plugin.yaml").write_text(
        yaml.safe_dump(manifest),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(init_body, encoding="utf-8")
    return plugin_dir


def test_canonical_scan_deduplicates_sources_and_keeps_nested_metadata(
    tmp_path, monkeypatch
):
    from hermes_cli import plugins

    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    project_root = tmp_path / "project"
    _write_plugin(bundled, "image_gen/example", version="1.0.0")
    _write_plugin(user, "image_gen/example", version="2.0.0")
    _write_plugin(
        project_root / ".hermes" / "plugins",
        "image_gen/example",
        version="3.0.0",
        extra={
            "provides_tools": ["example_tool"],
            "hooks": ["on_session_start"],
            "requires_env": [{"name": "EXAMPLE_API_KEY"}],
        },
    )

    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: bundled)
    monkeypatch.setattr(plugins.importlib.metadata, "entry_points", lambda: [])
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "1")
    monkeypatch.chdir(project_root)

    manifests = plugins.scan_plugin_manifests(user_dir=user)
    matches = [m for m in manifests if m.key == "image_gen/example"]

    assert len(matches) == 1
    manifest = matches[0]
    assert manifest.source == "project"
    assert manifest.version == "3.0.0"
    assert manifest.provides_tools == ["example_tool"]
    assert manifest.provides_hooks == ["on_session_start"]
    assert manifest.requires_env == [{"name": "EXAMPLE_API_KEY"}]


def test_inventory_scan_never_imports_plugin_code(tmp_path, monkeypatch):
    from hermes_cli import plugins

    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    marker = tmp_path / "executed.txt"
    _write_plugin(
        user,
        "safe-scan",
        init_body=f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
    )
    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: bundled)
    monkeypatch.setattr(plugins.importlib.metadata, "entry_points", lambda: [])

    manifests = plugins.scan_plugin_manifests(user_dir=user)

    assert any(m.key == "safe-scan" for m in manifests)
    assert not marker.exists()


def test_inventory_honors_project_gate_and_reports_bad_manifest(
    tmp_path, monkeypatch, caplog
):
    from hermes_cli import plugins

    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    project_root = tmp_path / "project"
    _write_plugin(project_root / ".hermes" / "plugins", "project-only")
    bad = user / "broken"
    bad.mkdir(parents=True)
    (bad / "plugin.yaml").write_text(": : bad [[[", encoding="utf-8")

    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: bundled)
    monkeypatch.setattr(plugins.importlib.metadata, "entry_points", lambda: [])
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("HERMES_ENABLE_PROJECT_PLUGINS", raising=False)

    with caplog.at_level(logging.WARNING, logger="hermes_cli.plugins"):
        manifests = plugins.scan_plugin_manifests(user_dir=user)

    assert all(m.key != "project-only" for m in manifests)
    assert any("Failed to parse" in record.message for record in caplog.records)

    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "true")
    manifests = plugins.scan_plugin_manifests(user_dir=user)
    assert any(m.key == "project-only" for m in manifests)


def test_inventory_status_rules_cover_auto_provider_and_explicit_disable():
    from hermes_cli.plugins import PluginManifest
    from hermes_cli.web_server import _plugin_inventory_status

    backend = PluginManifest(
        name="openai",
        key="image_gen/openai",
        kind="backend",
        source="bundled",
    )
    provider = PluginManifest(
        name="honcho",
        key="memory/honcho",
        kind="exclusive",
        source="bundled",
    )

    assert _plugin_inventory_status(backend, set(), set()) == (
        "auto",
        "auto-active",
        True,
    )
    assert _plugin_inventory_status(provider, set(), set()) == (
        "provider-managed",
        "provider-managed",
        False,
    )
    assert _plugin_inventory_status(backend, {"image_gen/openai"}, {"openai"}) == (
        "disabled",
        "disabled",
        True,
    )


def test_plugins_hub_keeps_legacy_fields_and_adds_inventory_contract(
    tmp_path, monkeypatch
):
    from hermes_cli import plugins, plugins_cmd, web_server

    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    manifest = plugins.PluginManifest(
        name="demo",
        key="demo",
        kind="standalone",
        source="user",
        path=str(plugin_dir),
        version="2.0.0",
        description="Demo plugin",
        author="Hermes Test",
        provides_tools=["demo_tool"],
        provides_hooks=["on_session_start"],
        requires_env=["DEMO_API_KEY"],
    )
    monkeypatch.delenv("DEMO_API_KEY", raising=False)
    monkeypatch.setattr(web_server, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(web_server, "_get_dashboard_plugins", lambda: [])
    monkeypatch.setattr(web_server, "load_config", lambda: {"dashboard": {}})
    monkeypatch.setattr(web_server, "_discover_memory_provider_statuses", lambda: [])
    monkeypatch.setattr(plugins, "scan_plugin_manifests", lambda **_kwargs: [manifest])
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", lambda: {"demo"})
    monkeypatch.setattr(plugins_cmd, "_get_disabled_set", lambda: set())
    monkeypatch.setattr(plugins_cmd, "_get_current_memory_provider", lambda: "")
    monkeypatch.setattr(plugins_cmd, "_get_current_context_engine", lambda: "compressor")
    monkeypatch.setattr(plugins_cmd, "_discover_context_engines", lambda: [])

    payload = web_server._merged_plugins_hub()
    row = payload["plugins"][0]

    assert row["name"] == "demo"
    assert row["runtime_status"] == "enabled"
    assert row["can_remove"] is True
    assert row["key"] == "demo"
    assert row["kind"] == "standalone"
    assert row["config_status"] == "enabled"
    assert row["effective_status"] == "enabled"
    assert row["can_toggle"] is True
    assert row["provides_tools"] == ["demo_tool"]
    assert row["provides_hooks"] == ["on_session_start"]
    assert row["requires_env"] == ["DEMO_API_KEY"]
    assert row["missing_env"] == ["DEMO_API_KEY"]


def test_dashboard_toggle_normalizes_aliases_and_rejects_provider_managed(monkeypatch):
    from hermes_cli import plugins_cmd

    manifest = SimpleNamespace(
        name="web-firecrawl",
        key="web/firecrawl",
        kind="backend",
    )
    enabled = {"firecrawl", "web-firecrawl"}
    disabled = {"web-firecrawl"}
    saved: dict[str, set] = {}
    monkeypatch.setattr(plugins_cmd, "_resolve_plugin_manifest", lambda _name: manifest)
    monkeypatch.setattr(plugins_cmd, "_get_enabled_set", lambda: set(enabled))
    monkeypatch.setattr(plugins_cmd, "_get_disabled_set", lambda: set(disabled))
    monkeypatch.setattr(plugins_cmd, "_save_enabled_set", lambda value: saved.__setitem__("enabled", set(value)))
    monkeypatch.setattr(plugins_cmd, "_save_disabled_set", lambda value: saved.__setitem__("disabled", set(value)))

    result = plugins_cmd.dashboard_set_agent_plugin_enabled("firecrawl", enabled=True)

    assert result == {"ok": True, "name": "web/firecrawl", "unchanged": False}
    assert saved["enabled"] == {"web/firecrawl"}
    assert saved["disabled"] == set()

    manifest.kind = "model-provider"
    rejected = plugins_cmd.dashboard_set_agent_plugin_enabled("web/firecrawl", enabled=False)
    assert rejected["ok"] is False
    assert "provider settings" in rejected["error"]


def test_dashboard_remove_cleans_plugin_directory_and_config(tmp_path, monkeypatch):
    from hermes_cli import plugins, plugins_cmd

    bundled = tmp_path / "bundled"
    user = tmp_path / "plugins"
    plugin_dir = _write_plugin(user, "observability/sample", name="sample-plugin")
    config_path = Path(plugins_cmd.get_hermes_home()) / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "plugins": {
                "enabled": ["observability/sample", "sample", "sample-plugin", "keep"],
                "disabled": ["sample-plugin", "keep-disabled"],
                "entries": {
                    "observability/sample": {"allow_tool_override": True},
                    "sample-plugin": {"setting": True},
                    "keep": {"setting": True},
                },
            }
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(plugins, "get_bundled_plugins_dir", lambda: bundled)
    monkeypatch.setattr(plugins.importlib.metadata, "entry_points", lambda: [])
    monkeypatch.setattr(plugins_cmd, "_plugins_dir", lambda: user)

    result = plugins_cmd.dashboard_remove_user_plugin("observability/sample")

    assert result == {"ok": True, "name": "observability/sample"}
    assert not plugin_dir.exists()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["plugins"]["enabled"] == ["keep"]
    assert config["plugins"]["disabled"] == ["keep-disabled"]
    assert config["plugins"]["entries"] == {"keep": {"setting": True}}

from pathlib import Path

import pytest

from scripts.stage_frozen_plugins import stage_frozen_plugins


def test_stage_frozen_plugins_keeps_runtime_python_and_dashboard_assets(tmp_path: Path):
    source = tmp_path / "plugins"
    plugin = source / "kanban"
    dashboard = plugin / "dashboard"
    dashboard_dist = dashboard / "dist"
    dashboard_dist.mkdir(parents=True)
    (plugin / "__init__.py").write_text("PLUGIN = True\n", encoding="utf-8")
    (plugin / "plugin.yaml").write_text("name: kanban\n", encoding="utf-8")
    (dashboard / "plugin_api.py").write_text("router = None\n", encoding="utf-8")
    (dashboard_dist / "index.js").write_text("export {};\n", encoding="utf-8")
    (plugin / "tests").mkdir()
    (plugin / "tests" / "test_plugin.py").write_text("assert True\n", encoding="utf-8")
    (plugin / "__pycache__").mkdir()
    (plugin / "__pycache__" / "cached.pyc").write_bytes(b"cache")

    output = tmp_path / "staged"
    stage_frozen_plugins(source, output)

    assert (output / "kanban" / "__init__.py").is_file()
    assert (output / "kanban" / "plugin.yaml").is_file()
    assert (output / "kanban" / "dashboard" / "plugin_api.py").is_file()
    assert (output / "kanban" / "dashboard" / "dist" / "index.js").is_file()
    assert not (output / "kanban" / "tests").exists()
    assert not (output / "kanban" / "__pycache__").exists()


def test_stage_frozen_plugins_refuses_to_overwrite_existing_output(tmp_path: Path):
    source = tmp_path / "plugins"
    source.mkdir()
    output = tmp_path / "staged"
    output.mkdir()

    with pytest.raises(FileExistsError):
        stage_frozen_plugins(source, output)

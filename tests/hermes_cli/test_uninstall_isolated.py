from pathlib import Path

from hermes_cli.uninstall import _data_dir_inside_install, _path_contains


def test_path_contains_handles_nested_paths(tmp_path: Path) -> None:
    parent = tmp_path / "hermes-agent"
    child = parent / "data"
    child.mkdir(parents=True)

    assert _path_contains(parent, child)
    assert not _path_contains(child, parent)


def test_data_dir_inside_install_detects_self_contained_layout(tmp_path: Path) -> None:
    project_root = tmp_path / "hermes-agent"
    hermes_home = project_root / "data"
    hermes_home.mkdir(parents=True)

    assert _data_dir_inside_install(project_root, hermes_home)


def test_data_dir_inside_install_ignores_external_hermes_home(tmp_path: Path) -> None:
    project_root = tmp_path / "hermes-agent"
    hermes_home = tmp_path / "hermes-data"
    project_root.mkdir()
    hermes_home.mkdir()

    assert not _data_dir_inside_install(project_root, hermes_home)

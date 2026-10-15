"""The published source launcher does not require PATH utilities such as realpath."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from hermes_cli import _launchers
from tests.hermes_cli.test_source_launcher_publication import fixture_tree, select_generation


@pytest.mark.platforms("posix")
def test_source_launcher_starts_without_realpath_or_legacy_console_scripts(tmp_path, monkeypatch):
    root, _home, interpreter = fixture_tree(tmp_path, monkeypatch)
    select_generation(root, "ready", "selected")
    out = tmp_path / "commands"
    _launchers.ensure_install_launchers(root, out)
    env = dict(os.environ, PATH="", PYTHONHOME=str(tmp_path / "foreign-python"),
               PYTHONPATH=str(tmp_path / "foreign-dependencies"))
    result = subprocess.run([str(out / "hermes"), "--version"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 7, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["argv"] == ["--version"]
    assert receipt["value"] == "selected"
    assert Path(receipt["exe"]).samefile(interpreter)
    assert not (root / "venv").exists()

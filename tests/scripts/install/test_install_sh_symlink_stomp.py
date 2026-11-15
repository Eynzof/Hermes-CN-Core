"""Regression #21454: launcher publication must not overwrite old symlink targets."""
import json
import os
import subprocess

import pytest

from hermes_cli import _launchers
from tests.hermes_cli.test_source_launcher_publication import fixture_tree, select_generation


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("name", ["hermes", "hermes-acp"])
def test_publication_replaces_legacy_symlink_without_touching_console_script(tmp_path, monkeypatch, name):
    root, _home, _interpreter = fixture_tree(tmp_path, monkeypatch)
    select_generation(root, "ready", "selected")
    legacy = root / "venv" / "bin" / name
    legacy.parent.mkdir(parents=True)
    original = "#!/usr/bin/env python\n# old console script must stay intact\n"
    legacy.write_text(original, encoding="utf-8")
    out = tmp_path / "commands"
    out.mkdir()
    launcher = out / name
    launcher.symlink_to(legacy)

    assert str(launcher) in _launchers.ensure_install_launchers(root, out)
    assert legacy.read_text(encoding="utf-8") == original
    assert not launcher.is_symlink()
    assert os.access(launcher, os.X_OK)
    result = subprocess.run([str(launcher), "forwarded argument"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 7, result.stdout + result.stderr
    assert json.loads(result.stdout)["argv"] == ["forwarded argument"]

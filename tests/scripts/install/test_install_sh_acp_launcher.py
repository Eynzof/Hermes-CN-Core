"""User PATH launchers preserve ACP/agent entrypoints after PM publication."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from hermes_cli import _launchers
from tests.hermes_cli.test_source_launcher_publication import fixture_tree, select_generation


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("name,module", [("hermes-acp", "acp_adapter.entry"), ("hermes-agent", "run_agent")])
def test_published_convenience_dispatches_to_its_entrypoint(tmp_path, monkeypatch, name, module):
    root, _home, _interpreter = fixture_tree(tmp_path, monkeypatch)
    select_generation(root, "ready", "selected")
    entry = root / ("acp_adapter/entry.py" if name == "hermes-acp" else "run_agent.py")
    body = (
        "import json, sys\n"
        "def main():\n"
        f"    print(json.dumps({{'entry': {module!r}, 'argv': sys.argv[1:]}}))\n"
        "    return 0\n"
        "if __name__ == '__main__': sys.exit(main())\n"
    )
    entry.write_text(body, encoding="utf-8")
    result = _launchers.expose_cli(root)
    assert result["ok"], result
    launcher = tmp_path / ".local" / "bin" / name
    assert os.access(launcher, os.X_OK)
    run = subprocess.run([str(launcher), "spaces and café", ""], cwd=tmp_path,
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout) == {"entry": module, "argv": ["spaces and café", ""]}


@pytest.mark.platforms("posix")
def test_hermes_agent_launcher_cleanup_on_uninstall(tmp_path, monkeypatch):
    from hermes_cli.uninstall import remove_wrapper_script

    local_shim = tmp_path / ".local" / "bin" / "hermes-agent"
    local_shim.parent.mkdir(parents=True)
    local_shim.write_text("#!/usr/bin/env bash\nexec hermes-agent\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    assert local_shim in remove_wrapper_script()
